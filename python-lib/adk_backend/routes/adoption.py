"""Adoption / engagement analytics — persistent spine (v1, pure DSS API).

The whole feature rests on a two-speed data model. This v1 route uses only the
*persistent* layer: project git history (author == DSS login, unbounded to
years), user `creationDate`, and the `list_users_activity()` snapshot. No audit
access, no host macro — the git logs it aggregates are already fetched and cached
by the projects catalog, so this is a cached read + a pure roll-up.

The recent high-res audit layer (DAU, login events) is a separate v1.1 increment
behind a new macro. Keeping the two layers in different endpoints is what makes
window-honesty structural rather than a disclaimer.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from flask import Blueprint, g, jsonify

from adk_backend.caching import _cache_get
from adk_backend.clients import _adoption_git_aggregate, _sdk_fetch
from adk_backend.settings import _BACKEND_SETTINGS, _outreach_thresholds

bp = Blueprint('adoption', __name__)
_LOGGER = logging.getLogger(__name__)

_ADOPTION_CACHE_KEY = 'adoption'


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
    for u in users:
        login = u.get('login') or ''
        if not login:
            continue
        display_by_login[login] = u.get('displayName') or login
        created = u.get('creationDate')
        creation_by_login[login] = created
        month = _creation_month(created)
        if month:
            cohort_counts[month] = cohort_counts.get(month, 0) + 1
    cohorts = [{'month': m, 'newUsers': cohort_counts[m]} for m in sorted(cohort_counts.keys())]

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
        'builderRecency': builder_recency,
    }


@bp.route('/api/adoption')
def api_adoption():
    client = g.client

    def loader():
        return _adoption_data(client)

    ttl = int(_BACKEND_SETTINGS.get('cache_ttl_projects', 600))
    data = _cache_get(_ADOPTION_CACHE_KEY, ttl, loader)
    return jsonify(data)
