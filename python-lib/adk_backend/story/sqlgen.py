"""Pure SQL builders for Story writes — no DB connection, fully testable.

Every builder returns (sql, params_or_rows). All writes are upserts on the
table's full primary key; per-day audit tables are rewritten via
delete_day_sql + the matching upsert so a re-run of the same day can never
double-count. Values always travel as parameters — no interpolation.

The multi-row upserts are shaped for psycopg2.extras.execute_values:
    execute_values(cur, sql, rows)  # sql contains a single VALUES %s
"""
from typing import Any, Dict, List, Sequence, Tuple

SOURCES = ('audit', 'license', 'inventory')

# Object types tracked by the inventory source.
INVENTORY_OBJECT_TYPES = ('project', 'dataset', 'recipe', 'scenario', 'saved_model', 'webapp')


def _upsert_values_sql(table: str, columns: Sequence[str], pk: Sequence[str]) -> str:
    updates = [c for c in columns if c not in pk]
    set_clause = ', '.join('{0} = EXCLUDED.{0}'.format(c) for c in updates)
    return (
        'INSERT INTO {table} ({cols}) VALUES %s '
        'ON CONFLICT ({pk}) DO UPDATE SET {set_clause}'.format(
            table=table,
            cols=', '.join(columns),
            pk=', '.join(pk),
            set_clause=set_clause,
        )
    )


def delete_day_sql(table: str) -> Tuple[str, str]:
    """Whole-day rewrite precursor for the per-day audit tables."""
    if table not in ('story.user_activity_daily', 'story.audit_event_counts'):
        raise ValueError('delete_day_sql: unsupported table %r' % table)
    return ('DELETE FROM {0} WHERE day = %s AND instance_id = %s'.format(table),
            'day, instance_id')


def user_activity_upsert(rows: List[Dict[str, Any]], day: str, instance_id: str):
    sql = _upsert_values_sql(
        'story.user_activity_daily',
        ('day', 'instance_id', 'login', 'project_key', 'viewing_actions', 'developing_actions'),
        ('day', 'instance_id', 'login', 'project_key'),
    )
    values = [
        (day, instance_id, r['login'], r['projectKey'],
         int(r['viewingActions']), int(r['developingActions']))
        for r in rows
    ]
    return sql, values


def event_counts_upsert(rows: List[Dict[str, Any]], day: str, instance_id: str):
    sql = _upsert_values_sql(
        'story.audit_event_counts',
        ('day', 'instance_id', 'project_key', 'msg_type', 'event_count'),
        ('day', 'instance_id', 'project_key', 'msg_type'),
    )
    values = [
        (day, instance_id, r['projectKey'], r['msgType'], int(r['count']))
        for r in rows
    ]
    return sql, values


def license_snapshot_upsert(snapshot: Dict[str, Any], snapshot_date: str, instance_id: str):
    sql = _upsert_values_sql(
        'story.license_snapshots',
        ('snapshot_date', 'instance_id', 'dss_version', 'license_kind',
         'expires_on', 'users_total', 'addons', 'raw'),
        ('snapshot_date', 'instance_id'),
    )
    values = [(
        snapshot_date, instance_id,
        snapshot.get('dssVersion'), snapshot.get('licenseKind'),
        snapshot.get('expiresOn'), snapshot.get('usersTotal'),
        snapshot.get('addonsJson'), snapshot.get('rawJson'),
    )]
    return sql, values


def license_caps_upsert(rows: List[Dict[str, Any]], snapshot_date: str, instance_id: str):
    sql = _upsert_values_sql(
        'story.license_profile_caps',
        ('snapshot_date', 'instance_id', 'profile', 'cap', 'used'),
        ('snapshot_date', 'instance_id', 'profile'),
    )
    values = [
        (snapshot_date, instance_id, r['profile'], r.get('cap'), r.get('used'))
        for r in rows
    ]
    return sql, values


def inventory_counts_upsert(rows: List[Dict[str, Any]], snapshot_date: str, instance_id: str):
    sql = _upsert_values_sql(
        'story.object_inventory_daily',
        ('snapshot_date', 'instance_id', 'project_key', 'object_type', 'object_count'),
        ('snapshot_date', 'instance_id', 'project_key', 'object_type'),
    )
    values = [
        (snapshot_date, instance_id, r['projectKey'], r['objectType'], int(r['count']))
        for r in rows
    ]
    return sql, values


def inventory_items_upsert(rows: List[Dict[str, Any]], snapshot_date: str, instance_id: str):
    sql = _upsert_values_sql(
        'story.object_inventory_items',
        ('snapshot_date', 'instance_id', 'project_key', 'object_type',
         'object_id', 'name', 'subtype'),
        ('snapshot_date', 'instance_id', 'project_key', 'object_type', 'object_id'),
    )
    values = [
        (snapshot_date, instance_id, r['projectKey'], r['objectType'],
         r['objectId'], r.get('name'), r.get('subtype'))
        for r in rows
    ]
    return sql, values


def inventory_items_prune_sql() -> str:
    """Prune item-level rows older than the retention window (counts are kept
    forever). Parameter: retention days (int)."""
    return (
        "DELETE FROM story.object_inventory_items "
        "WHERE snapshot_date < (CURRENT_DATE - (%s)::integer)"
    )


def ingest_run_upsert(instance_id: str, source: str, status: str,
                      cursor_value: Any = None, error: Any = None,
                      rows_written: int = 0):
    """Single-row cursor/status upsert (plain execute, not execute_values)."""
    if source not in SOURCES:
        raise ValueError('ingest_run_upsert: unknown source %r' % source)
    sql = (
        'INSERT INTO story.ingest_runs '
        '(instance_id, source, cursor_value, last_run_at, last_status, last_error, last_rows_written) '
        'VALUES (%s, %s, %s, now(), %s, %s, %s) '
        'ON CONFLICT (instance_id, source) DO UPDATE SET '
        'cursor_value = COALESCE(EXCLUDED.cursor_value, story.ingest_runs.cursor_value), '
        'last_run_at = EXCLUDED.last_run_at, '
        'last_status = EXCLUDED.last_status, '
        'last_error = EXCLUDED.last_error, '
        'last_rows_written = EXCLUDED.last_rows_written'
    )
    params = (instance_id, source, cursor_value, status,
              str(error)[:2000] if error else None, int(rows_written))
    return sql, params
