"""Adoption / engagement analytics — persistent spine (v1, pure DSS API).

The whole feature rests on a two-speed data model. This v1 route uses only the
*persistent* layer: project git history (author == DSS login, unbounded to
years), user `creationDate`, and the `list_users_activity()` snapshot. No audit
access, no host macro — the git logs it aggregates are already fetched and cached
by the projects catalog, so this is a cached read + a pure roll-up.

Two deeper layers live behind host macros, each with its own endpoint so
window-honesty stays structural rather than a disclaimer:
- /api/adoption/inventory — config-tree object inventory (full history of
  SURVIVING objects; deleted work is invisible — survivorship bias).
- /api/adoption/events — audit-log msgType event mix (whatever window the
  rotated audit files still cover).
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from flask import Blueprint, g, jsonify

from adk_backend.caching import _cache_get
from adk_backend.clients import _adoption_git_aggregate, _sdk_fetch
from adk_backend.macros import _adoption_events_macro, _adoption_inventory_macro
from adk_backend.settings import _BACKEND_SETTINGS, _outreach_thresholds

bp = Blueprint('adoption', __name__)
_LOGGER = logging.getLogger(__name__)

_ADOPTION_CACHE_KEY = 'adoption'
_ADOPTION_INVENTORY_CACHE_KEY = 'adoption_inventory'
_ADOPTION_EVENTS_CACHE_KEY = 'adoption_events'
# Recent-activity pulse: ask for 3 days back; the tail-scan stops wherever the
# rotated files run out and reports what it actually covered.
_PULSE_WINDOW_HOURS = 72
_PULSE_TTL_SECONDS = 60


def _creation_month(created_ms: Any) -> Optional[str]:
    """'YYYY-MM' onboarding cohort bucket for a user creationDate (ms epoch)."""
    try:
        dt = datetime.fromtimestamp(int(created_ms) / 1000.0, tz=timezone.utc)
        return "%04d-%02d" % (dt.year, dt.month)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _activity_dict(activity: Any) -> Dict[str, Any]:
    """`list_users_activity()` yields DSSUserActivity objects whose payload is the
    `.activity` dict; tolerate a raw dict too."""
    inner = getattr(activity, 'activity', None)
    if isinstance(inner, dict):
        return inner
    return activity if isinstance(activity, dict) else {}


def _licensing_summary(client: Any) -> Optional[Dict[str, Any]]:
    """Licensed seat limits + validity from get_licensing_status(), or None when
    the key can't read it. Shape verified live (akaos, DSS 14): limits live in
    limits.licensedProfiles[profile].licensedLimit (-1 = no limit); there are NO
    per-profile usage counts in the payload — usage joins from list_users."""
    try:
        lic = client.get_licensing_status() or {}
    except Exception:
        return None
    base = lic.get('base') or {}
    content = base.get('licenseContent') or {}
    profiles = []
    for name, row in ((lic.get('limits') or {}).get('licensedProfiles') or {}).items():
        if not isinstance(row, dict):
            continue
        profiles.append({'profile': name, 'licensedLimit': row.get('licensedLimit')})
    return {
        'valid': bool(base.get('valid')),
        'expired': bool(base.get('expired')),
        'expiresOnMs': base.get('expiresOn'),
        'licenseKind': content.get('licenseKind'),
        'communityEdition': bool(base.get('community')),
        'profiles': profiles,
    }


def _adoption_data(client: Any) -> Dict[str, Any]:
    """Assemble the persistent adoption payload: git spine + active/total (matched
    to the Inactive-Projects threshold) + onboarding cohorts + per-user recency."""
    agg = _adoption_git_aggregate(client)
    project_rows: List[Dict[str, Any]] = agg.get('projectRows', [])

    # Active vs total projects (#5) — same rule as /api/tools/inactive-projects so
    # the headline KPI matches that tool exactly (last activity within threshold).
    threshold_days = _outreach_thresholds.get('inactive_project_days', 180)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    active_count = 0
    for row in project_rows:
        last_ms = row.get('lastCommitMs')
        is_active = False
        if last_ms is not None:
            days = (now_ms - int(last_ms)) / (1000 * 60 * 60 * 24)
            is_active = days < threshold_days
        row['active'] = is_active
        if is_active:
            active_count += 1

    users = _sdk_fetch(
        'list_users',
        _BACKEND_SETTINGS['cache_ttl_users'],
        lambda: client.list_users(),
    ) or []
    activities = _sdk_fetch(
        'list_users_activity',
        _BACKEND_SETTINGS['cache_ttl_users'],
        lambda: client.list_users_activity(),
    ) or []

    # Onboarding cohorts (#) — user creationDate bucketed by month.
    cohort_counts: Dict[str, int] = {}
    creation_by_login: Dict[str, Any] = {}
    display_by_login: Dict[str, str] = {}
    profile_by_login: Dict[str, str] = {}
    profile_counts: Dict[str, int] = {}
    groups_by_login: Dict[str, List[str]] = {}
    for u in users:
        login = u.get('login') or ''
        if not login:
            continue
        display_by_login[login] = u.get('displayName') or login
        profile = u.get('userProfile') or ''
        profile_by_login[login] = profile
        if profile:
            profile_counts[profile] = profile_counts.get(profile, 0) + 1
        groups_by_login[login] = [g for g in (u.get('groups') or []) if g]
        created = u.get('creationDate')
        creation_by_login[login] = created
        month = _creation_month(created)
        if month:
            cohort_counts[month] = cohort_counts.get(month, 0) + 1
    cohorts = [{'month': m, 'newUsers': cohort_counts[m]} for m in sorted(cohort_counts.keys())]

    # Most active groups — roll each builder's git activity up to their DSS
    # groups (`user.groups` is a plain list of names). A builder in N groups
    # counts toward all N (commit shares can overlap, like the native DSS
    # group model itself). Builders whose account is gone have no groups row —
    # their work still counts in totals/trend, just not here.
    builder_stats: List[Dict[str, Any]] = agg.get('builderStats', [])
    builder_projects: Dict[str, Any] = agg.get('builderProjects', {})
    builder_monthly: Dict[str, Dict[str, int]] = agg.get('builderMonthlyCommits', {})
    stats_by_login = {b['login']: b for b in builder_stats}
    group_rows: Dict[str, Dict[str, Any]] = {}
    for login, group_names in groups_by_login.items():
        stat = stats_by_login.get(login)
        for name in group_names:
            row = group_rows.setdefault(name, {
                'name': name, 'memberCount': 0, 'builderCount': 0, 'commits': 0,
                'projects': set(), 'lastCommitMs': None, 'monthlyCommits': {},
            })
            row['memberCount'] += 1
            if stat is None:
                continue
            row['builderCount'] += 1
            row['commits'] += stat['commits']
            row['projects'].update(builder_projects.get(login, ()))
            monthly = row['monthlyCommits']
            for month, n in builder_monthly.get(login, {}).items():
                monthly[month] = monthly.get(month, 0) + n
            last = stat.get('lastCommitMs')
            if last is not None and (row['lastCommitMs'] is None or last > row['lastCommitMs']):
                row['lastCommitMs'] = last
    groups = []
    for row in group_rows.values():
        row['projectCount'] = len(row.pop('projects'))
        groups.append(row)
    groups.sort(key=lambda r: (-r['commits'], -r['memberCount'], r['name']))

    # Top builders — leaderboard with display names joined in (a departed
    # builder keeps their login as the display name).
    top_builders = [
        {**b, 'displayName': display_by_login.get(b['login'], b['login'])}
        for b in builder_stats
    ]

    # Login recency reframed (#1) — persistent snapshot presented as "last active",
    # never a fabricated login count (login-success events undercount 10-100x).
    builder_recency: List[Dict[str, Any]] = []
    for a in activities:
        act = _activity_dict(a)
        login = act.get('login') or ''
        if not login:
            continue
        builder_recency.append({
            'login': login,
            'displayName': display_by_login.get(login, login),
            'lastSuccessfulLogin': act.get('lastSuccessfulLogin'),
            'lastSessionActivity': act.get('lastSessionActivity'),
            'creationDate': creation_by_login.get(login),
            'userProfile': profile_by_login.get(login) or None,
        })

    totals = dict(agg.get('totals', {}))
    totals['activeProjectCount'] = active_count
    totals['inactiveThresholdDays'] = threshold_days

    return {
        'ok': True,
        'generatedAtMs': now_ms,
        'totals': totals,
        'monthlyTrend': agg.get('monthlyTrend', []),
        'projectRows': project_rows,
        'cohorts': cohorts,
        'repeatBuilders': agg.get('repeatBuilders', {'total': 0, 'single': 0, 'repeat': 0}),
        # login → 'YYYY-MM' → commits, for the new/returning/lapsed lifecycle
        # view. Already aggregated by _adoption_git_aggregate for the group
        # roll-up — exposing it costs nothing extra.
        'builderMonthly': agg.get('builderMonthlyCommits', {}),
        'builderRecency': builder_recency,
        'groups': groups,
        'builderStats': top_builders,
        # Licensed seat limits (None when the key can't read licensing) + the
        # actual per-profile seat usage from the same list_users snapshot.
        'licensing': _licensing_summary(client),
        'profileCounts': profile_counts,
    }


@bp.route('/api/adoption')
def api_adoption():
    client = g.client

    def loader():
        return _adoption_data(client)

    ttl = int(_BACKEND_SETTINGS.get('cache_ttl_projects', 600))
    data = _cache_get(_ADOPTION_CACHE_KEY, ttl, loader)
    return jsonify(data)


@bp.route('/api/adoption/inventory')
def api_adoption_inventory():
    """Config-tree object inventory (macro): full history of surviving objects.

    Cache entries are scoped per active host: _cache_get routes every key
    through caching._cache_key, which prefixes the request's X-DSS-Host-Id
    (set on _THREAD_LOCAL by @before_request) — a remote host's inventory is
    never served for the local one, matching /api/adoption and /api/cru.
    """
    client = g.client

    def loader():
        return _adoption_inventory_macro(client)

    ttl = int(_BACKEND_SETTINGS.get('cache_ttl_projects', 600))
    data = _cache_get(_ADOPTION_INVENTORY_CACHE_KEY, ttl, loader)
    return jsonify(data)


@bp.route('/api/adoption/events')
def api_adoption_events():
    """Recent-activity pulse (macro, mode=recent): a cheap reverse tail-scan of
    the newest audit files, covering the last ~72h or however far the rotated
    files actually reach — the payload reports the MEASURED window, never the
    requested one (20 rotations can cover under 24h on a busy instance).

    Host-scoped cache: see api_adoption_inventory — _cache_key prefixes the
    active host id, so entries never cross hosts. Short TTL: this is the one
    fast-moving card on the page.
    """
    client = g.client

    def loader():
        return _adoption_events_macro(client, mode='recent', window_hours=_PULSE_WINDOW_HOURS)

    data = _cache_get(_ADOPTION_EVENTS_CACHE_KEY, _PULSE_TTL_SECONDS, loader)
    return jsonify(data)
