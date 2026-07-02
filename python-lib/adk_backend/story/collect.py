"""Story collection — pull license / inventory / audit data per host and
persist it to Postgres with per-(instance, source) transactional cursors.

Isolation model (the part Pulse got wrong):
- each (host, source) unit runs in its own try/except: one broken host never
  blocks the others;
- a unit's data AND its cursor commit in the SAME transaction — either both
  land or neither does;
- on failure the transaction rolls back, then the failed ingest_runs row is
  written in its own small transaction, so the Setup page shows the error even
  though the run as a whole raises (which is what fires the failure email).

Audit idempotency: whole days strictly greater than the stored cursor are
rewritten via DELETE(day, instance) + upsert; days at or before the cursor are
never touched; the cursor advances only to yesterday (last complete UTC day).
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from adk_backend.story import sqlgen
from adk_backend.story.aggregate import FORMAT_VERSION
from adk_backend.story.classification import VOCAB_VERSION

_LOGGER = logging.getLogger(__name__)

MACRO_PROJECT_KEY = 'ADMINTOOLKIT'
AUDIT_MACRO_ID = 'pyrunnable_admin-toolkit_story-audit-aggregate'

SOURCES = sqlgen.SOURCES  # ('audit', 'license', 'inventory')

# Verified live (see also Pulse's data-gather-instance): licenseContent
# properties carry per-profile caps as maxFullDesigners-style keys and/or
# users.profiles.<PROFILE>.max keys; newer payloads may use base.profileLimits.
_PROFILE_LABEL_MAP = {
    'Full Designers': 'FULL_DESIGNER',
    'Advanced Analytics Designers': 'ADVANCED_ANALYTICS_DESIGNER',
    'Data Designers': 'DATA_DESIGNER',
    'Governance Managers': 'GOVERNANCE_MANAGER',
    'Readers': 'READER',
    'AI Consumers': 'AI_CONSUMER',
    'AI Access Users': 'AI_ACCESS_USER',
    'Technical Accounts': 'TECHNICAL_ACCOUNT',
}


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def _yesterday_utc() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime('%Y-%m-%d')


def _to_int_or_none(value: Any) -> Optional[int]:
    if value is None or value == '':
        return None
    if isinstance(value, str) and value.strip().lower() == 'unlimited':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _camel_to_profile(key: str) -> str:
    body = key[len('max'):]
    parts: List[str] = []
    current: List[str] = []
    for char in body:
        if char.isupper() and current:
            parts.append(''.join(current))
            current = [char]
        else:
            current.append(char)
    if current:
        parts.append(''.join(current))
    label = ' '.join(parts)
    return _PROFILE_LABEL_MAP.get(label, label.upper().replace(' ', '_'))


def _expires_iso(expires_on: Any) -> Optional[str]:
    """base.expiresOn is a ms epoch (verified live); tolerate a string too."""
    if expires_on is None:
        return None
    if isinstance(expires_on, (int, float)):
        try:
            return datetime.fromtimestamp(expires_on / 1000.0, tz=timezone.utc).strftime('%Y-%m-%d')
        except (OverflowError, OSError, ValueError):
            return str(expires_on)
    return str(expires_on)


def _dss_version(client: Any) -> Optional[str]:
    """Best-effort enrichment — never fails the license source over a version."""
    try:
        info = client.get_instance_info()
        raw = getattr(info, 'raw', None)
        if not isinstance(raw, dict):
            raw = info if isinstance(info, dict) else {}
        return raw.get('dssVersion')
    except Exception:
        return None


def collect_license(client: Any) -> Dict[str, Any]:
    """License snapshot + per-profile caps/usage for one host."""
    payload = client.get_licensing_status()
    if not isinstance(payload, dict) or not payload:
        raise RuntimeError('get_licensing_status returned an empty payload')
    base = payload.get('base') or {}
    content = base.get('licenseContent') or {}
    properties = content.get('properties') or {}

    users = client.list_users() or []
    used_by_profile: Dict[str, int] = {}
    for user in users:
        if not isinstance(user, dict) or not user.get('enabled', True):
            continue
        profile = str(user.get('userProfile') or user.get('resultingUserProfile') or '').strip()
        if profile:
            used_by_profile[profile] = used_by_profile.get(profile, 0) + 1

    caps_by_profile: Dict[str, Optional[int]] = {}
    profile_limits = base.get('profileLimits') or {}
    if isinstance(profile_limits, dict) and profile_limits:
        for profile_name, limit_payload in profile_limits.items():
            limit_payload = limit_payload or {}
            licensed = limit_payload.get('licensed') or {}
            resolved = str(
                licensed.get('profile') or limit_payload.get('profile') or profile_name
            ).strip()
            if resolved:
                caps_by_profile[resolved] = _to_int_or_none(licensed.get('licensedLimit'))
    else:
        for key, value in properties.items():
            if key.startswith('max'):
                caps_by_profile[_camel_to_profile(key)] = _to_int_or_none(value)
            elif key.startswith('users.profiles.') and key.endswith('.max'):
                profile = key[len('users.profiles.'):-len('.max')].strip()
                if profile:
                    caps_by_profile[profile] = _to_int_or_none(value)

    profiles = sorted(set(caps_by_profile) | set(used_by_profile))
    caps = [
        {'profile': profile,
         'cap': caps_by_profile.get(profile),
         'used': used_by_profile.get(profile, 0)}
        for profile in profiles
    ]

    addons = {
        key[len('addons.'):]: value
        for key, value in properties.items()
        if key.startswith('addons.')
    }

    snapshot = {
        'dssVersion': _dss_version(client),
        'licenseKind': content.get('licenseKind'),
        'expiresOn': _expires_iso(base.get('expiresOn')),
        'usersTotal': len(users),
        'addonsJson': json.dumps(addons),
        'rawJson': json.dumps(payload),
    }
    return {'snapshot': snapshot, 'caps': caps}


def _raw_item(item: Any) -> Dict[str, Any]:
    """list_* items are DSS*ListItem objects on current dataikuapi (verified),
    plain dicts on older ones."""
    if isinstance(item, dict):
        return item
    if hasattr(item, 'get_raw'):
        raw = item.get_raw()
        return raw if isinstance(raw, dict) else {}
    return {}


def collect_inventory(client: Any, include_items: bool = True) -> Dict[str, Any]:
    """Object counts (and optional item rows) per project for one host."""
    counts: List[Dict[str, Any]] = []
    items: List[Dict[str, Any]] = []
    projects = client.list_projects() or []
    for project_info in projects:
        if not isinstance(project_info, dict):
            continue
        project_key = str(project_info.get('projectKey') or project_info.get('key') or '').strip()
        if not project_key:
            continue
        counts.append({'projectKey': project_key, 'objectType': 'project', 'count': 1})
        if include_items:
            items.append({
                'projectKey': project_key, 'objectType': 'project', 'objectId': project_key,
                'name': project_info.get('name') or project_key, 'subtype': None,
            })
        project = client.get_project(project_key)
        listing = (
            ('dataset', project.list_datasets, 'name', 'type'),
            ('recipe', project.list_recipes, 'name', 'type'),
            ('scenario', project.list_scenarios, 'id', None),
            ('saved_model', project.list_saved_models, 'id', None),
            ('webapp', project.list_webapps, 'id', 'type'),
        )
        for object_type, lister, id_key, subtype_key in listing:
            objects = lister() or []
            counts.append({'projectKey': project_key, 'objectType': object_type,
                           'count': len(objects)})
            if not include_items:
                continue
            for obj in objects:
                raw = _raw_item(obj)
                object_id = str(raw.get(id_key) or raw.get('name') or '').strip()
                if not object_id:
                    continue
                items.append({
                    'projectKey': project_key,
                    'objectType': object_type,
                    'objectId': object_id,
                    'name': raw.get('name') or object_id,
                    'subtype': raw.get(subtype_key) if subtype_key else None,
                })
    return {'counts': counts, 'items': items}


def fetch_audit_payload(host: Dict[str, Any], since_day: Optional[str],
                        lookback_days: int, max_files: int = 0) -> Dict[str, Any]:
    """Per-day audit aggregates for one host — local direct call, remote macro."""
    if host.get('isLocal'):
        import os
        from adk_backend.story.aggregate import aggregate_audit_dir
        dip_home = os.environ.get('DIP_HOME') or os.environ.get('DKU_DIP_HOME')
        if not dip_home:
            raise RuntimeError('DIP_HOME not set — cannot locate the local audit directory')
        payload = aggregate_audit_dir(
            os.path.join(dip_home, 'run', 'audit'),
            since_day=since_day, lookback_days=lookback_days, max_files=max_files,
        )
    else:
        macro = host['client'].get_project(MACRO_PROJECT_KEY).get_macro(AUDIT_MACRO_ID)
        params = {'lookback_days': int(lookback_days), 'max_files': int(max_files)}
        if since_day:
            params['since_day'] = since_day
        run_id = macro.run(params=params, wait=True)
        payload = macro.get_result(run_id, as_type='json')
        if isinstance(payload, str):
            payload = json.loads(payload)

    if not isinstance(payload, dict):
        raise RuntimeError('audit aggregate returned non-dict: %s' % type(payload).__name__)
    if not payload.get('ok'):
        raise RuntimeError('audit aggregate failed on host %s: %s'
                           % (host['id'], payload.get('error') or 'unknown error'))
    # Version-skew guard: mixing two vocab/format versions inside the same
    # tables would silently corrupt trends. Fail loudly instead.
    if payload.get('formatVersion') != FORMAT_VERSION or payload.get('vocabVersion') != VOCAB_VERSION:
        raise RuntimeError(
            'audit aggregate version skew on host %s: got format=%s vocab=%s, '
            'hub expects format=%s vocab=%s — upgrade the admin-toolkit plugin on that host'
            % (host['id'], payload.get('formatVersion'), payload.get('vocabVersion'),
               FORMAT_VERSION, VOCAB_VERSION))
    return payload


def _get_cursor(conn: Any, instance_id: str, source: str) -> Optional[str]:
    with conn.cursor() as cur:
        cur.execute(
            'SELECT cursor_value FROM story.ingest_runs WHERE instance_id = %s AND source = %s',
            (instance_id, source))
        row = cur.fetchone()
    return row[0] if row and row[0] else None


def _execute_values(cur: Any, sql: str, values: List[tuple]) -> None:
    from psycopg2.extras import execute_values
    execute_values(cur, sql, values)


def _write_failed_run(conn: Any, instance_id: str, source: str, error: Exception) -> None:
    """After ROLLBACK: durably record the failure in its own tiny transaction."""
    try:
        sql, params = sqlgen.ingest_run_upsert(
            instance_id, source, 'failed',
            error='%s: %s' % (type(error).__name__, str(error)[:1500]))
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    except Exception:
        conn.rollback()
        _LOGGER.exception('[story] could not record failed ingest run %s/%s', instance_id, source)


def _collect_audit_unit(conn: Any, host: Dict[str, Any], cfg: Any, max_files: int = 0) -> int:
    instance_id = host['id']
    since_day = _get_cursor(conn, instance_id, 'audit')
    payload = fetch_audit_payload(
        host, since_day, int(getattr(cfg, 'audit_lookback_days', 14)), max_files=max_files)
    rows_written = 0
    with conn.cursor() as cur:
        for day in sorted(payload.get('days') or {}):
            day_data = payload['days'][day] or {}
            for table in ('story.user_activity_daily', 'story.audit_event_counts'):
                sql, _cols = sqlgen.delete_day_sql(table)
                cur.execute(sql, (day, instance_id))
            activity_rows = day_data.get('userActivity') or []
            if activity_rows:
                sql, values = sqlgen.user_activity_upsert(activity_rows, day, instance_id)
                _execute_values(cur, sql, values)
                rows_written += len(values)
            count_rows = day_data.get('eventCounts') or []
            if count_rows:
                sql, values = sqlgen.event_counts_upsert(count_rows, day, instance_id)
                _execute_values(cur, sql, values)
                rows_written += len(values)
        # Cursor: only yesterday is complete; never move backwards.
        new_cursor = _yesterday_utc()
        if since_day and since_day > new_cursor:
            new_cursor = since_day
        sql, params = sqlgen.ingest_run_upsert(
            instance_id, 'audit', 'ok', cursor_value=new_cursor, rows_written=rows_written)
        cur.execute(sql, params)
    conn.commit()
    return rows_written


def _collect_license_unit(conn: Any, host: Dict[str, Any], cfg: Any) -> int:
    instance_id = host['id']
    data = collect_license(host['client'])
    snapshot_date = _today_utc()
    rows_written = 0
    with conn.cursor() as cur:
        sql, values = sqlgen.license_snapshot_upsert(data['snapshot'], snapshot_date, instance_id)
        _execute_values(cur, sql, values)
        rows_written += 1
        if data['caps']:
            sql, values = sqlgen.license_caps_upsert(data['caps'], snapshot_date, instance_id)
            _execute_values(cur, sql, values)
            rows_written += len(values)
        sql, params = sqlgen.ingest_run_upsert(
            instance_id, 'license', 'ok', cursor_value=snapshot_date, rows_written=rows_written)
        cur.execute(sql, params)
    conn.commit()
    return rows_written


def _collect_inventory_unit(conn: Any, host: Dict[str, Any], cfg: Any) -> int:
    instance_id = host['id']
    data = collect_inventory(host['client'])
    snapshot_date = _today_utc()
    rows_written = 0
    with conn.cursor() as cur:
        if data['counts']:
            sql, values = sqlgen.inventory_counts_upsert(data['counts'], snapshot_date, instance_id)
            _execute_values(cur, sql, values)
            rows_written += len(values)
        if data['items']:
            sql, values = sqlgen.inventory_items_upsert(data['items'], snapshot_date, instance_id)
            _execute_values(cur, sql, values)
            rows_written += len(values)
        cur.execute(sqlgen.inventory_items_prune_sql(),
                    (int(getattr(cfg, 'inventory_items_retention_days', 30)),))
        sql, params = sqlgen.ingest_run_upsert(
            instance_id, 'inventory', 'ok', cursor_value=snapshot_date, rows_written=rows_written)
        cur.execute(sql, params)
    conn.commit()
    return rows_written


_UNIT_COLLECTORS: Dict[str, Callable[..., int]] = {
    'audit': _collect_audit_unit,
    'license': _collect_license_unit,
    'inventory': _collect_inventory_unit,
}


def run_collection(conn: Any, hosts: List[Dict[str, Any]], cfg: Any,
                   sources: List[str], log: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Collect every requested source from every host.

    Returns {'ok', 'results': [{host, source, status, rows, error}], 'failures'}.
    Never raises for a per-unit failure — the caller (story-collect runnable)
    inspects `ok` and raises with the summary so the scenario outcome is FAILED.
    """
    emit = log or (lambda message: _LOGGER.info('[story] %s', message))
    unknown = [s for s in sources if s not in _UNIT_COLLECTORS]
    if unknown:
        raise ValueError('Unknown Story source(s): %s' % ', '.join(unknown))

    results: List[Dict[str, Any]] = []
    for host in hosts:
        for source in sources:
            emit('collecting %s from host %s' % (source, host['id']))
            try:
                rows = _UNIT_COLLECTORS[source](conn, host, cfg)
                results.append({'host': host['id'], 'source': source,
                                'status': 'ok', 'rows': rows, 'error': None})
                emit('host %s / %s: ok (%d rows)' % (host['id'], source, rows))
            except Exception as exc:
                conn.rollback()
                _write_failed_run(conn, host['id'], source, exc)
                error = '%s: %s' % (type(exc).__name__, str(exc)[:500])
                results.append({'host': host['id'], 'source': source,
                                'status': 'failed', 'rows': 0, 'error': error})
                emit('host %s / %s: FAILED — %s' % (host['id'], source, error))
    failures = sum(1 for r in results if r['status'] != 'ok')
    return {'ok': failures == 0, 'results': results, 'failures': failures}
