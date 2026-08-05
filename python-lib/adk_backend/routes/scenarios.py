"""Projects -> Scenarios: configured schedules plus live trigger state.

Ported from the diag-parser Scenario Schedules card. The dump build parsed
`config/projects/*/scenarios/*.json` client-side; here the same normalized
trigger shape is assembled live from the public API, which also hands over
state a dump can never carry:

  - `project.list_scenarios()` is one call per project and brings the live
    half: `active`, `running`, `nextRun` (DSS's own computation of the next
    fire, in ms — truth, unlike the frontend's synthetic projection, which
    ignores per-trigger timezones), `markedAsTest` and `automationLocal`.
  - The structured trigger params the schedule math needs (frequency, hour,
    minute, daysOfWeek, timezone, startingFrom) exist only in per-scenario
    settings, so every scenario costs one extra fetch — hence SSE: a cheap
    inventory event lands first and the per-project sweep streams behind it,
    mirroring the App Instances page.

The settings fetch also rides along `reporters` (how a failure would be
heard about) and `versionTag` (who last touched it), both free once the
call is paid for.
"""

import json
import logging
import time
from concurrent.futures import as_completed
from typing import Any, Dict, List, Optional

from flask import Blueprint, g

from adk_backend.clients import ThreadPoolExecutor
from adk_backend.utils import _parallel_workers, _sse_response

bp = Blueprint('scenarios', __name__)

_LOGGER = logging.getLogger(__name__)


def _normalize_trigger(raw: Any) -> Dict[str, Any]:
    """Same shape the diag-parser ScenariosParser emitted, so the ported
    schedule math consumes it unchanged. Temporal params are only present on
    `type == 'temporal'` triggers."""
    if not isinstance(raw, dict):
        raw = {}
    trigger: Dict[str, Any] = {
        'type': str(raw.get('type') or 'unknown'),
        'name': raw.get('name') or None,
        'active': raw.get('active') is True,
    }
    if trigger['type'] == 'temporal':
        params = raw.get('params') if isinstance(raw.get('params'), dict) else {}
        repeat = params.get('repeatFrequency')
        trigger['temporal'] = {
            'frequency': str(params.get('frequency') or 'Daily'),
            'repeatFrequency': repeat if isinstance(repeat, int) else 1,
            'daysOfWeek': params.get('daysOfWeek') if isinstance(params.get('daysOfWeek'), list) else None,
            'monthlyRunOn': params.get('monthlyRunOn') if isinstance(params.get('monthlyRunOn'), str) else None,
            'hour': params.get('hour') if isinstance(params.get('hour'), int) else None,
            'minute': params.get('minute') if isinstance(params.get('minute'), int) else None,
            'timezone': params.get('timezone') if isinstance(params.get('timezone'), str) else None,
            'startingFrom': params.get('startingFrom') if isinstance(params.get('startingFrom'), str) else None,
        }
    return trigger


def _listing_row(project_key: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    """The live half, straight off the scenario listing (verified on DSS 14.7:
    nextRun is ms since epoch, 0 = nothing scheduled)."""
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
        'reporters': 0,
        'activeReporters': 0,
        'lastModifiedOn': None,
        'lastModifiedBy': None,
        'settingsError': None,
    }


def _enrich_from_settings(project: Any, row: Dict[str, Any]) -> None:
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
    row['triggers'] = [_normalize_trigger(t) for t in triggers_raw]
    row['hasTimeSchedule'] = any(
        t['type'] == 'temporal' and t['active'] for t in row['triggers'])
    run_as = raw.get('runAsUser')
    row['runAsUser'] = run_as if isinstance(run_as, str) and run_as else None

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


def _scan_project(client: Any, project_key: str) -> Dict[str, Any]:
    """All scenarios of one project: listing row + settings enrichment each.
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
        _enrich_from_settings(project, row)
        out['scenarios'].append(row)
    return out


@bp.route('/api/scenarios/scan')
def api_scenarios_scan():
    """Stream every project's scenarios (listing + per-scenario settings) via SSE."""
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

        yield "event: inventory\ndata: %s\n\n" % json.dumps({'projectsToScan': len(keys)})

        scanned = 0
        total_scenarios = 0
        failed_projects: List[Dict[str, str]] = []
        workers = max(1, min(8, _parallel_workers()))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_scan_project, client, key): key for key in keys}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    row = future.result()
                except Exception as exc:
                    _LOGGER.exception("[scenarios] worker failed for %s", key)
                    row = {'projectKey': key, 'scenarios': [],
                           'error': '%s: %s' % (type(exc).__name__, str(exc)[:200])}
                scanned += 1
                total_scenarios += len(row['scenarios'])
                if row['error']:
                    failed_projects.append({'projectKey': key, 'error': row['error']})
                yield "event: project\ndata: %s\n\n" % json.dumps({
                    'projectKey': key,
                    'scenarios': row['scenarios'],
                    'error': row['error'],
                    'scanned': scanned,
                })

        yield "event: done\ndata: %s\n\n" % json.dumps({
            'projectsScanned': scanned,
            'scenarios': total_scenarios,
            'failedProjects': failed_projects,
            'totalMs': int((time.time() - t0) * 1000),
        })

    return _sse_response(generate)
