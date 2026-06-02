"""Plugin macro: run a DB Health operation against the target host's runtimedb.

Runs as the `dataiku` service account (impersonate=false) so psycopg2 can
authenticate via the local PostgreSQL socket + .pgpass without needing the
admin to paste an unencrypted password into plugin settings.

Operations:
  - test-password : connect, run `SELECT 1`, return {ok, server_version?}
  - run-query     : execute a read-only SELECT/EXPLAIN/SHOW and return rows
  - list-tables   : list schemas + table counts (admin overview)
"""
import json
import re

from dataiku.runnables import Runnable


_READ_ONLY_RE = re.compile(r'^\s*(select|explain|show|with)\b', re.IGNORECASE)


def _load_connection_credentials(connection_name):
    """Resolve psycopg2 connect kwargs from a DSS connection."""
    import dataiku
    client = dataiku.api_client()
    conn = client.get_connection(connection_name)
    info = conn.get_info()
    params = (info.get('params') or {}) if isinstance(info, dict) else {}
    return {
        'host': params.get('host') or 'localhost',
        'port': int(params.get('port') or 5432),
        'dbname': params.get('db') or params.get('dbname') or 'dataiku',
        'user': params.get('user') or 'dataiku',
        'password': params.get('password') or None,
    }


def _connect(connection_name, password_override):
    import psycopg2
    kwargs = _load_connection_credentials(connection_name) if connection_name else {
        'host': '/var/run/postgresql', 'dbname': 'dataiku', 'user': 'dataiku',
    }
    if password_override:
        kwargs['password'] = password_override
    return psycopg2.connect(**{k: v for k, v in kwargs.items() if v is not None})


def _op_test_password(connection_name, password):
    try:
        conn = _connect(connection_name, password)
        try:
            cur = conn.cursor()
            cur.execute('SELECT version()')
            row = cur.fetchone()
            version = row[0] if row else None
        finally:
            conn.close()
        return {'ok': True, 'server_version': version}
    except Exception as exc:
        return {'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:300]}'}


def _op_run_query(connection_name, password, sql):
    if not sql:
        return {'ok': False, 'error': 'sql is required for run-query'}
    if not _READ_ONLY_RE.match(sql):
        return {'ok': False, 'error': 'Only SELECT / EXPLAIN / SHOW / WITH are allowed'}
    try:
        conn = _connect(connection_name, password)
        try:
            cur = conn.cursor()
            cur.execute(sql)
            cols = [d[0] for d in (cur.description or [])]
            rows = [list(r) for r in cur.fetchmany(10000)]
        finally:
            conn.close()
        rows_json_safe = [
            [v if isinstance(v, (str, int, float, bool, type(None))) else str(v) for v in r]
            for r in rows
        ]
        return {'ok': True, 'columns': cols, 'rows': rows_json_safe, 'rowCount': len(rows_json_safe)}
    except Exception as exc:
        return {'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:300]}'}


def _op_list_tables(connection_name, password):
    sql = (
        "SELECT table_schema, COUNT(*) AS table_count "
        "FROM information_schema.tables "
        "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
        "GROUP BY table_schema ORDER BY table_schema"
    )
    return _op_run_query(connection_name, password, sql)


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        op = (self.config.get('operation') or '').strip()
        connection = (self.config.get('connection') or '').strip() or None
        password = (self.config.get('password') or '').strip() or None
        sql = self.config.get('sql') or ''

        if op == 'test-password':
            result = _op_test_password(connection, password)
        elif op == 'run-query':
            result = _op_run_query(connection, password, sql)
        elif op == 'list-tables':
            result = _op_list_tables(connection, password)
        else:
            result = {'ok': False, 'error': f'Unknown operation: {op!r}'}

        return json.dumps(result)
