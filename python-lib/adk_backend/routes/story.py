"""Story routes — Postgres-persisted scheduled analytics (experimental).

Deliberately hub-scoped: every endpoint is @local_only (the Story database
already contains ALL instances' data keyed by instance_id, so routing these
reads through X-DSS-Host-Id would be wrong — the remote host has no Story DB).
Kept separate from /api/adoption per its v1.1 docstring: adoption is the
persistent git spine, Story is the scheduled high-res audit/license/inventory
layer.
"""
import logging
from typing import Any, Dict, List, Optional

from flask import Blueprint, g, jsonify, request

from adk_backend.caching import _cache_get, _cache_pop
from adk_backend.story import db as story_db
from adk_backend.story import provision as story_provision
from adk_backend.story.classification import taxonomy_for
from adk_backend.story.hosts import _remote_presets
from adk_backend.story.schema import get_schema_version
from adk_backend.utils import local_only
from db_adapter import load_story_config

bp = Blueprint('story', __name__)
_LOGGER = logging.getLogger(__name__)

_STATUS_CACHE_KEY = 'story_status'
_STATUS_TTL = 30
_MAX_QUERY_DAYS = 365
_MAX_ROWS = 20000


def _client() -> Any:
    return g.client


def _days_param(default: int = 30) -> int:
    try:
        days = int(request.args.get('days', default))
    except (TypeError, ValueError):
        days = default
    return max(1, min(days, _MAX_QUERY_DAYS))


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _query(conn: Any, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in (cur.description or [])]
        rows = cur.fetchmany(_MAX_ROWS)
    return [{col: _json_safe(v) for col, v in zip(cols, row)} for row in rows]


def _open_story_conn():
    """(conn, error_response). Story unconfigured / DB down → clean 4xx/5xx."""
    cfg = load_story_config(client=_client())
    if not cfg.connection_name:
        return None, (jsonify({'error': 'story-not-configured'}), 400)
    try:
        conn = story_db.connect(cfg.connection_name, client=_client())
    except Exception as exc:
        return None, (jsonify({'error': 'story-db-unreachable',
                               'detail': '%s: %s' % (type(exc).__name__, str(exc)[:300])}), 502)
    return conn, None


def _find_story_scenario(client: Any) -> Optional[Any]:
    project = client.get_project(story_provision.MACRO_PROJECT_KEY)
    for scenario_info in project.list_scenarios() or []:
        if scenario_info.get('name') == story_provision.SCENARIO_NAME:
            return project.get_scenario(scenario_info.get('id'))
    return None


def _scenario_status(client: Any, recipient: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {'exists': False, 'active': False, 'triggerHour': None,
                           'reporterVerified': False, 'reporterShape': None, 'lastRun': None}
    try:
        scenario = _find_story_scenario(client)
    except Exception as exc:
        out['error'] = '%s: %s' % (type(exc).__name__, str(exc)[:200])
        return out
    if scenario is None:
        return out
    out['exists'] = True
    try:
        settings = scenario.get_settings()
        out['active'] = bool(settings.get_raw().get('active'))
        for trigger in settings.raw_triggers or []:
            params = trigger.get('params') or {}
            if params.get('hour') is not None:
                out['triggerHour'] = params.get('hour')
                break
        reporters = list(getattr(settings, 'raw_reporters', None) or [])
        out['reporterVerified'] = any(
            story_provision._reporter_matches(entry, recipient) for entry in reporters)
        if out['reporterVerified']:
            primary = any('outcome' in str(entry.get('runConditionExpression') or '')
                          for entry in reporters if isinstance(entry, dict))
            out['reporterShape'] = 'primary' if primary else 'fallback'
    except Exception as exc:
        out['error'] = '%s: %s' % (type(exc).__name__, str(exc)[:200])
    try:
        runs = scenario.get_last_runs(limit=1)
        if runs:
            run = runs[0]
            raw = getattr(run, 'run', None) or {}
            out['lastRun'] = {
                'outcome': (raw.get('result') or {}).get('outcome') if isinstance(raw, dict) else None,
                'start': raw.get('start') if isinstance(raw, dict) else None,
            }
    except Exception:
        pass  # older DSS run payloads vary; lastRun stays None
    return out


def _load_status() -> Dict[str, Any]:
    client = _client()
    cfg = load_story_config(client=client)
    status: Dict[str, Any] = {
        'configured': bool(cfg.connection_name),
        'connection': cfg.connection_name or '',
        'alertEmail': cfg.alert_email,
        'dbOk': False,
        'schemaVersion': 0,
        'ingest': [],
        'hosts': [{'id': 'local', 'label': 'Local (hub)'}],
        'scenario': {'exists': False, 'active': False, 'triggerHour': None,
                     'reporterVerified': False, 'reporterShape': None, 'lastRun': None},
    }
    try:
        status['hosts'].extend(
            {'id': p['id'], 'label': p['label']} for p in _remote_presets(client))
    except Exception:
        pass  # presets unreadable → hub-only fleet shown

    if cfg.connection_name:
        try:
            conn = story_db.connect(cfg.connection_name, client=client)
            try:
                status['schemaVersion'] = get_schema_version(conn)
                status['dbOk'] = True
                if status['schemaVersion'] >= 1:
                    status['ingest'] = _query(conn, (
                        'SELECT instance_id, source, cursor_value, last_run_at, '
                        'last_status, last_error, last_rows_written '
                        'FROM story.ingest_runs ORDER BY instance_id, source'))
            finally:
                conn.close()
        except Exception as exc:
            status['dbError'] = '%s: %s' % (type(exc).__name__, str(exc)[:300])

    status['scenario'] = _scenario_status(client, cfg.alert_email)
    return status


@bp.route('/api/story/status')
@local_only
def api_story_status():
    return jsonify(_cache_get(_STATUS_CACHE_KEY, _STATUS_TTL, _load_status))


@bp.route('/api/story/provision', methods=['POST'])
@local_only
def api_story_provision():
    cfg = load_story_config(client=_client())
    result = story_provision.provision_all(_client(), cfg)
    _cache_pop(_STATUS_CACHE_KEY)
    return jsonify(result)


@bp.route('/api/story/run-now', methods=['POST'])
@local_only
def api_story_run_now():
    try:
        scenario = _find_story_scenario(_client())
    except Exception as exc:
        return jsonify({'error': '%s: %s' % (type(exc).__name__, str(exc)[:300])}), 502
    if scenario is None:
        return jsonify({'error': 'story-scenario-missing',
                        'detail': 'Provision Story first.'}), 409
    trigger_fire = scenario.run()
    _cache_pop(_STATUS_CACHE_KEY)
    run_id = None
    for attr in ('runId', 'run_id', 'id'):
        run_id = getattr(trigger_fire, attr, None)
        if run_id:
            break
    # Fire-and-forget: durable progress/status lives in story.ingest_runs,
    # which the Setup page polls via /api/story/status.
    return jsonify({'ok': True, 'runId': _json_safe(run_id)})


@bp.route('/api/story/user-activity')
@local_only
def api_story_user_activity():
    days = _days_param(30)
    instance = (request.args.get('instance') or '').strip()
    conn, error = _open_story_conn()
    if error:
        return error
    try:
        instance_clause = ' AND instance_id = %s' if instance else ''
        instance_params: tuple = (instance,) if instance else ()
        daily = _query(conn, (
            'SELECT day, instance_id, '
            'COUNT(DISTINCT login) AS active_users, '
            'SUM(viewing_actions) AS viewing_actions, '
            'SUM(developing_actions) AS developing_actions, '
            "COUNT(DISTINCT login) FILTER (WHERE developing_actions > 0) AS developing_users "
            'FROM story.user_activity_daily '
            'WHERE day >= (CURRENT_DATE - (%s)::integer)' + instance_clause +
            ' GROUP BY day, instance_id ORDER BY day, instance_id'),
            (days,) + instance_params)
        users = _query(conn, (
            'SELECT day, instance_id, login, project_key, viewing_actions, developing_actions '
            'FROM story.user_activity_daily '
            'WHERE day >= (CURRENT_DATE - (%s)::integer)' + instance_clause +
            ' ORDER BY day DESC, viewing_actions DESC'),
            (days,) + instance_params)
        return jsonify({'days': daily, 'users': users, 'windowDays': days})
    finally:
        conn.close()


@bp.route('/api/story/event-counts')
@local_only
def api_story_event_counts():
    days = _days_param(30)
    conn, error = _open_story_conn()
    if error:
        return error
    try:
        rows = _query(conn, (
            'SELECT day, instance_id, msg_type, SUM(event_count) AS event_count '
            'FROM story.audit_event_counts '
            'WHERE day >= (CURRENT_DATE - (%s)::integer) '
            'GROUP BY day, instance_id, msg_type ORDER BY day, event_count DESC'),
            (days,))
        # Taxonomy is applied at query time — evolving buckets never needs a backfill.
        for row in rows:
            row['taxonomy'] = taxonomy_for(row.get('msg_type') or '')
        return jsonify({'rows': rows, 'windowDays': days})
    finally:
        conn.close()


@bp.route('/api/story/licenses')
@local_only
def api_story_licenses():
    conn, error = _open_story_conn()
    if error:
        return error
    try:
        latest = _query(conn, (
            'SELECT DISTINCT ON (instance_id) '
            'snapshot_date, instance_id, dss_version, license_kind, expires_on, '
            'users_total, addons '
            'FROM story.license_snapshots ORDER BY instance_id, snapshot_date DESC'))
        caps = _query(conn, (
            'SELECT snapshot_date, instance_id, profile, cap, used '
            'FROM story.license_profile_caps '
            'ORDER BY snapshot_date, instance_id, profile'))
        return jsonify({'latest': latest, 'caps': caps})
    finally:
        conn.close()


@bp.route('/api/story/inventory')
@local_only
def api_story_inventory():
    days = _days_param(90)
    conn, error = _open_story_conn()
    if error:
        return error
    try:
        trends = _query(conn, (
            'SELECT snapshot_date, instance_id, object_type, SUM(object_count) AS object_count '
            'FROM story.object_inventory_daily '
            'WHERE snapshot_date >= (CURRENT_DATE - (%s)::integer) '
            'GROUP BY snapshot_date, instance_id, object_type '
            'ORDER BY snapshot_date, instance_id, object_type'),
            (days,))
        latest = _query(conn, (
            'SELECT d.snapshot_date, d.instance_id, d.project_key, d.object_type, d.object_count '
            'FROM story.object_inventory_daily d '
            'JOIN (SELECT instance_id, MAX(snapshot_date) AS snapshot_date '
            '      FROM story.object_inventory_daily GROUP BY instance_id) m '
            'ON d.instance_id = m.instance_id AND d.snapshot_date = m.snapshot_date '
            'ORDER BY d.instance_id, d.project_key, d.object_type'))
        return jsonify({'trends': trends, 'latestByProject': latest, 'windowDays': days})
    finally:
        conn.close()
