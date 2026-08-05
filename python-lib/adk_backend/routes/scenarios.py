"""Projects -> Scenarios: configured schedules plus live trigger state.

Ported from the diag-parser Scenario Schedules card. The dump build parsed
`config/projects/*/scenarios/*.json` client-side; here the same normalized
trigger shape is assembled live from the public API, which also hands over
state a dump can never carry:

  - `project.list_scenarios()` is one call per project and brings the live
    half: `active`, `running`, `nextRun` (DSS's own computation of the next
    fire, in ms — truth, unlike the frontend's synthetic projection),
    `markedAsTest` and `automationLocal`.
  - The structured trigger params the schedule math needs (frequency, hour,
    minute, daysOfWeek, timezone, startingFrom) exist only in per-scenario
    settings, so every scenario costs one settings fetch.
  - `get_last_runs()` is one more call per scenario and is the enrichment a
    diagnostic dump explicitly could not offer: real outcomes, durations and
    failure streaks.

Hence SSE: a cheap inventory event lands first and the per-project sweep
streams behind it, mirroring the App Instances page. Cross-scenario verdicts
(broken/dormant `follow_scenariorun` chains) need the whole sweep, so they
ride the `done` event and the client patches them onto rows already sent —
the same late-verdict pattern App Instances uses for orphans.

Trigger timezones: each temporal trigger may carry an IANA timezone (or
'SERVER'). The frontend's clustering math works in server time, so every
temporal trigger ships `serverShiftMinutes` = (server UTC offset − trigger tz
UTC offset) at current DST rules; 0 when SERVER/unknown.

Verified live on DSS 14.7 (akaos): listing `nextRun` is ms (0 = none);
`get_last_runs` returns newest-first, each raw run has `start`, `end`
(absent/0 while running) and `result.outcome` in SUCCESS/WARNING/FAILED/
ABORTED (absent while running); `follow_scenariorun` params are
`{projectKey, scenarioId, outcomeFilter}` (one target per trigger, per
FollowScenarioRunTriggerParams in dataiku-dip.jar); `list_users()` rows carry
`login` + `enabled`.
"""

import json
import logging
import time
from concurrent.futures import as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, g

from adk_backend.clients import ThreadPoolExecutor
from adk_backend.utils import _parallel_workers, _sse_response

bp = Blueprint('scenarios', __name__)

_LOGGER = logging.getLogger(__name__)

# Completed-run outcomes that count toward a failure streak. WARNING is a
# completed run with warnings — not a failure.
_FAILURE_OUTCOMES = ('FAILED', 'ABORTED')

_RUNS_LIMIT = 10


def _server_tz() -> Tuple[str, int]:
    """(tz label, UTC offset in minutes) of the DSS host — the backend runs on
    it, so the process-local zone IS the server zone."""
    now = datetime.now().astimezone()
    offset = now.utcoffset()
    minutes = int(offset.total_seconds() // 60) if offset else 0
    return now.tzname() or 'server', minutes


def _tz_offset_minutes(tz_name: str) -> Optional[int]:
    """Current UTC offset of an IANA zone, or None when unresolvable (bad name
    or a code env without zoneinfo)."""
    try:
        from zoneinfo import ZoneInfo
        offset = datetime.now(ZoneInfo(tz_name)).utcoffset()
        return int(offset.total_seconds() // 60) if offset else 0
    except Exception:
        return None


def _normalize_trigger(raw: Any, server_offset_minutes: int) -> Dict[str, Any]:
    """Same shape the diag-parser ScenariosParser emitted, so the ported
    schedule math consumes it unchanged — plus `serverShiftMinutes` on
    temporal params and the `follow` target on follow_scenariorun triggers."""
    if not isinstance(raw, dict):
        raw = {}
    trigger: Dict[str, Any] = {
        'type': str(raw.get('type') or 'unknown'),
        'name': raw.get('name') or None,
        'active': raw.get('active') is True,
    }
    params = raw.get('params') if isinstance(raw.get('params'), dict) else {}
    if trigger['type'] == 'temporal':
        repeat = params.get('repeatFrequency')
        tz_name = params.get('timezone') if isinstance(params.get('timezone'), str) else None
        shift = 0
        if tz_name and tz_name != 'SERVER':
            tz_offset = _tz_offset_minutes(tz_name)
            if tz_offset is not None:
                shift = server_offset_minutes - tz_offset
        trigger['temporal'] = {
            'frequency': str(params.get('frequency') or 'Daily'),
            'repeatFrequency': repeat if isinstance(repeat, int) else 1,
            'daysOfWeek': params.get('daysOfWeek') if isinstance(params.get('daysOfWeek'), list) else None,
            'monthlyRunOn': params.get('monthlyRunOn') if isinstance(params.get('monthlyRunOn'), str) else None,
            'hour': params.get('hour') if isinstance(params.get('hour'), int) else None,
            'minute': params.get('minute') if isinstance(params.get('minute'), int) else None,
            'timezone': tz_name,
            'startingFrom': params.get('startingFrom') if isinstance(params.get('startingFrom'), str) else None,
            'serverShiftMinutes': shift,
        }
    elif trigger['type'] == 'follow_scenariorun':
        trigger['follow'] = {
            'projectKey': str(params.get('projectKey') or ''),
            'scenarioId': str(params.get('scenarioId') or ''),
            'outcomeFilter': params.get('outcomeFilter') or None,
        }
    return trigger


def _listing_row(project_key: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    """The live half, straight off the scenario listing."""
    next_run = raw.get('nextRun')
    return {
        'projectKey': project_key,
        'id': str(raw.get('id') or ''),
        'name': str(raw.get('name') or raw.get('id') or ''),
        'scenarioType': str(raw.get('type') or ''),
        'active': raw.get('active') is True,
        'running': raw.get('running') is True,
        'nextRun': next_run if isinstance(next_run, int) and next_run > 0 else None,
        'markedAsTest': raw.get('markedAsTest') is True,
        'automationLocal': raw.get('automationLocal') is True,
        'triggerDigest': str(raw.get('triggerDigest') or ''),
        # Filled from the per-scenario settings fetch below.
        'triggers': [],
        'hasTimeSchedule': False,
        'runAsUser': None,
        'runAsInvalid': None,
        'reporters': 0,
        'activeReporters': 0,
        'lastModifiedOn': None,
        'lastModifiedBy': None,
        'settingsError': None,
        # Filled from the per-scenario run-history fetch below.
        'lastRunOutcome': None,
        'lastRunStart': None,
        'lastRunEnd': None,
        'failureStreak': 0,
        'avgDurationMs': None,
        'runsSampled': 0,
        'recentOutcomes': [],
        'runsError': None,
    }


def _enrich_from_settings(project: Any, row: Dict[str, Any],
                          server_offset_minutes: int,
                          users_by_login: Optional[Dict[str, bool]]) -> None:
    """Fold the settings payload into a listing row. A failed fetch degrades to
    a listing-only row (settingsError set) — the live columns still render."""
    try:
        raw = project.get_scenario(row['id']).get_settings().get_raw()
    except Exception as exc:
        row['settingsError'] = '%s: %s' % (type(exc).__name__, str(exc)[:200])
        return
    if not isinstance(raw, dict):
        row['settingsError'] = 'settings payload is not a dict'
        return

    triggers_raw = raw.get('triggers') if isinstance(raw.get('triggers'), list) else []
    row['triggers'] = [_normalize_trigger(t, server_offset_minutes) for t in triggers_raw]
    row['hasTimeSchedule'] = any(
        t['type'] == 'temporal' and t['active'] for t in row['triggers'])
    run_as = raw.get('runAsUser')
    row['runAsUser'] = run_as if isinstance(run_as, str) and run_as else None
    # Only an explicitly-set run-as login is checked; the implicit default is
    # version-dependent and guessing it would flag healthy scenarios. No user
    # list (call failed) ⇒ unknown, never flagged.
    if row['runAsUser'] and users_by_login is not None:
        if row['runAsUser'] not in users_by_login:
            row['runAsInvalid'] = 'missing'
        elif not users_by_login[row['runAsUser']]:
            row['runAsInvalid'] = 'disabled'

    reporters = raw.get('reporters') if isinstance(raw.get('reporters'), list) else []
    row['reporters'] = len(reporters)
    row['activeReporters'] = sum(
        1 for r in reporters if isinstance(r, dict) and r.get('active') is True)

    version_tag = raw.get('versionTag') if isinstance(raw.get('versionTag'), dict) else {}
    modified_on = version_tag.get('lastModifiedOn')
    row['lastModifiedOn'] = modified_on if isinstance(modified_on, int) and modified_on > 0 else None
    modified_by = version_tag.get('lastModifiedBy')
    if isinstance(modified_by, dict) and modified_by.get('login'):
        row['lastModifiedBy'] = str(modified_by['login'])


def _enrich_from_runs(project: Any, row: Dict[str, Any]) -> None:
    """Real run history — the enrichment a diagnostic dump could not offer.
    Aggregates only completed runs (a run still executing has no outcome);
    in-flight state is already on the row via `running`."""
    try:
        runs = project.get_scenario(row['id']).get_last_runs(limit=_RUNS_LIMIT)
    except Exception as exc:
        row['runsError'] = '%s: %s' % (type(exc).__name__, str(exc)[:200])
        return

    completed = []
    for run in runs:
        raw = getattr(run, 'run', None)
        if not isinstance(raw, dict):
            continue
        result = raw.get('result') if isinstance(raw.get('result'), dict) else None
        if not result or not result.get('outcome'):
            continue  # still running
        start = raw.get('start')
        end = raw.get('end')
        completed.append({
            'outcome': str(result['outcome']),
            'start': start if isinstance(start, int) else None,
            'end': end if isinstance(end, int) and end > 0 else None,
        })
    # get_last_runs returns newest-first (verified live); sort defensively so
    # the streak walk never depends on server ordering.
    completed.sort(key=lambda r: r['start'] or 0, reverse=True)

    if not completed:
        return
    newest = completed[0]
    row['lastRunOutcome'] = newest['outcome']
    row['lastRunStart'] = newest['start']
    row['lastRunEnd'] = newest['end']
    row['recentOutcomes'] = [r['outcome'] for r in completed]
    row['runsSampled'] = len(completed)

    streak = 0
    for run in completed:
        if run['outcome'] in _FAILURE_OUTCOMES:
            streak += 1
        else:
            break
    row['failureStreak'] = streak

    durations = [r['end'] - r['start'] for r in completed
                 if r['start'] and r['end'] and r['end'] > r['start']]
    if durations:
        row['avgDurationMs'] = int(sum(durations) / len(durations))


def _scan_project(client: Any, project_key: str, server_offset_minutes: int,
                  users_by_login: Optional[Dict[str, bool]]) -> Dict[str, Any]:
    """All scenarios of one project: listing row + settings + run history each.
    A project we cannot read degrades to an error row — never an exception
    that kills the stream."""
    out: Dict[str, Any] = {'projectKey': project_key, 'scenarios': [], 'error': None}
    try:
        project = client.get_project(project_key)
        listing = project.list_scenarios(as_type='listitems') or []
    except Exception as exc:
        out['error'] = '%s: %s' % (type(exc).__name__, str(exc)[:200])
        return out

    for item in listing:
        raw = item if isinstance(item, dict) else getattr(item, '_data', None)
        if not isinstance(raw, dict) or not raw.get('id'):
            continue
        row = _listing_row(project_key, raw)
        _enrich_from_settings(project, row, server_offset_minutes, users_by_login)
        _enrich_from_runs(project, row)
        out['scenarios'].append(row)
    return out


def _chain_issues(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Follow-the-leader triggers whose target cannot start the chain.

    'missing'  — the followed scenario no longer exists; the trigger will
                 never fire again.
    'dormant'  — the target exists but is disabled or has no active trigger
                 of its own, so the chain only moves when someone runs the
                 target by hand.
    Callers must only invoke this on a COMPLETE sweep: with failed projects a
    missing target is indistinguishable from an unread one.
    """
    by_key = {(r['projectKey'], r['id']): r for r in rows}
    issues: List[Dict[str, Any]] = []
    for row in rows:
        for trigger in row['triggers']:
            if trigger['type'] != 'follow_scenariorun' or not trigger['active']:
                continue
            follow = trigger.get('follow') or {}
            target_project = follow.get('projectKey') or row['projectKey']
            target_id = follow.get('scenarioId') or ''
            target = by_key.get((target_project, target_id))
            kind = None
            if target is None:
                kind = 'missing'
            elif not target['active'] or not any(t['active'] for t in target['triggers']):
                kind = 'dormant'
            if kind:
                issues.append({
                    'projectKey': row['projectKey'],
                    'id': row['id'],
                    'targetProjectKey': target_project,
                    'targetScenarioId': target_id,
                    'kind': kind,
                })
    return issues


@bp.route('/api/scenarios/scan')
def api_scenarios_scan():
    """Stream every project's scenarios (listing + settings + runs) via SSE."""
    def generate():
        t0 = time.time()
        client = g.client

        try:
            projects = client.list_projects() or []
        except Exception as exc:
            _LOGGER.warning("[scenarios] list_projects failed: %s", exc)
            yield "event: error\ndata: %s\n\n" % json.dumps(
                {'error': 'list_projects failed: %s' % str(exc)[:200]})
            return
        keys = [str(p.get('projectKey')) for p in projects
                if isinstance(p, dict) and p.get('projectKey')]

        server_tz_name, server_offset_minutes = _server_tz()

        users_by_login: Optional[Dict[str, bool]] = None
        try:
            users_by_login = {
                str(u['login']): u.get('enabled', True) is not False
                for u in (client.list_users() or [])
                if isinstance(u, dict) and u.get('login')
            }
        except Exception as exc:
            _LOGGER.warning("[scenarios] list_users failed (run-as checks off): %s", exc)

        yield "event: inventory\ndata: %s\n\n" % json.dumps({
            'projectsToScan': len(keys),
            'serverTz': server_tz_name,
            'serverTzOffsetMinutes': server_offset_minutes,
            'usersChecked': users_by_login is not None,
        })

        scanned = 0
        all_rows: List[Dict[str, Any]] = []
        failed_projects: List[Dict[str, str]] = []
        workers = max(1, min(8, _parallel_workers()))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_scan_project, client, key, server_offset_minutes, users_by_login): key
                for key in keys
            }
            for future in as_completed(futures):
                key = futures[future]
                try:
                    row = future.result()
                except Exception as exc:
                    _LOGGER.exception("[scenarios] worker failed for %s", key)
                    row = {'projectKey': key, 'scenarios': [],
                           'error': '%s: %s' % (type(exc).__name__, str(exc)[:200])}
                scanned += 1
                all_rows.extend(row['scenarios'])
                if row['error']:
                    failed_projects.append({'projectKey': key, 'error': row['error']})
                yield "event: project\ndata: %s\n\n" % json.dumps({
                    'projectKey': key,
                    'scenarios': row['scenarios'],
                    'error': row['error'],
                    'scanned': scanned,
                })

        # Chain verdicts need the whole estate: a follower in project A can
        # point at a scenario in project B. With any project unread the
        # verdicts stay null (unknown) — never guessed.
        chain_issues = _chain_issues(all_rows) if not failed_projects else None

        yield "event: done\ndata: %s\n\n" % json.dumps({
            'projectsScanned': scanned,
            'scenarios': len(all_rows),
            'failedProjects': failed_projects,
            'chainIssues': chain_issues,
            'totalMs': int((time.time() - t0) * 1000),
        })

    return _sse_response(generate)
