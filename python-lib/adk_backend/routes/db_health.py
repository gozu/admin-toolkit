"""DB Health routes — PostgreSQL runtime-DB diagnostics and maintenance."""
import logging
import os
import re
import subprocess
import sys
import tempfile
from typing import Any, Dict

from flask import Blueprint, g, jsonify, request

from adk_backend.caching import _cache_get
from adk_backend.clients import _safe_request_host_id
from adk_backend.macros import _dbhealth_macro
from adk_backend.utils import advanced

bp = Blueprint('db_health', __name__)
_LOGGER = logging.getLogger(__name__)


# ── DB Health ──

_PG_DRIVER = None  # 'psycopg2' | None
_PG_DRIVER_CHECKED = False
_PG_DRIVER_LOG = []  # tracks every attempt for UI visibility
_dbhealth_log = logging.getLogger(__name__)
_DBHEALTH_CONFIG = None  # cached DbHealthConfig


def _get_dbhealth_config():
    """Get cached DB Health plugin config (connection name + password)."""
    global _DBHEALTH_CONFIG
    if _DBHEALTH_CONFIG is None:
        try:
            from db_adapter import load_dbhealth_config
            _DBHEALTH_CONFIG = load_dbhealth_config()
        except Exception:
            from dataclasses import dataclass
            from typing import Optional as Opt
            @dataclass(frozen=True)
            class _Fallback:
                connection_name: Opt[str] = None
                password: Opt[str] = None
            _DBHEALTH_CONFIG = _Fallback()
    return _DBHEALTH_CONFIG


def _ensure_pg_driver():
    """Try to get psycopg2, or auto-install it. Logs every attempt to _PG_DRIVER_LOG."""
    global _PG_DRIVER, _PG_DRIVER_CHECKED
    if _PG_DRIVER_CHECKED:
        return _PG_DRIVER
    _PG_DRIVER_CHECKED = True
    log = _PG_DRIVER_LOG

    # 1. Try psycopg2 (already installed)
    try:
        import psycopg2  # noqa: F401
        _PG_DRIVER = 'psycopg2'
        log.append('[OK] psycopg2 already installed')
        return _PG_DRIVER
    except ImportError as exc:
        log.append('[FAIL] import psycopg2: %s' % exc)

    # 2. Try pip install with multiple strategies to dodge permission issues (AlmaLinux 9 / RHEL 9)
    _tmp_target = os.path.join(tempfile.gettempdir(), 'dku_psycopg2')
    _datadir_target = os.path.join(os.environ.get('DIP_HOME', '/tmp'), 'lib', 'python', 'psycopg2')
    install_attempts = [
        ('pip install (default)', [sys.executable, '-m', 'pip', 'install', 'psycopg2-binary', '--quiet']),
        ('pip install --user', [sys.executable, '-m', 'pip', 'install', 'psycopg2-binary', '--quiet', '--user']),
        ('pip install --break-system-packages', [sys.executable, '-m', 'pip', 'install', 'psycopg2-binary', '--quiet', '--break-system-packages']),
        ('pip install --target %s' % _tmp_target, [sys.executable, '-m', 'pip', 'install', 'psycopg2-binary', '--quiet', '--target', _tmp_target]),
        ('pip install --target %s' % _datadir_target, [sys.executable, '-m', 'pip', 'install', 'psycopg2-binary', '--quiet', '--target', _datadir_target]),
        ('pip install --prefix %s' % sys.prefix, [sys.executable, '-m', 'pip', 'install', 'psycopg2-binary', '--quiet', '--prefix', sys.prefix]),
    ]
    for label, cmd in install_attempts:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                log.append('[FAIL] %s: %s' % (label, (result.stderr.strip() or 'exit %d' % result.returncode)[:150]))
                continue
            # --target installs need the path on sys.path before import works
            for tgt in (_tmp_target, _datadir_target):
                if tgt not in sys.path and os.path.isdir(tgt):
                    sys.path.insert(0, tgt)
            try:
                import psycopg2  # noqa: F401
                _PG_DRIVER = 'psycopg2'
                log.append('[OK] %s — import succeeded' % label)
                return _PG_DRIVER
            except ImportError as exc:
                log.append('[FAIL] %s — pip succeeded but import failed: %s' % (label, exc))
        except Exception as exc:
            log.append('[FAIL] %s: %s' % (label, str(exc)[:150]))

    # 3. Try adding common site-packages paths and re-importing
    _pyver_short = sys.version[:3]
    _pyver_tuple = '%d.%d' % sys.version_info[:2]
    for extra_path in [
        '/usr/lib/python3/dist-packages',
        '/usr/local/lib/python3/dist-packages',
        '/usr/lib64/python%s/site-packages' % _pyver_tuple,
        '/usr/lib/python%s/site-packages' % _pyver_tuple,
        '/usr/local/lib64/python%s/site-packages' % _pyver_tuple,
        '/usr/local/lib/python%s/site-packages' % _pyver_tuple,
        os.path.expanduser('~/.local/lib/python%s/site-packages' % _pyver_tuple),
        os.path.expanduser('~/.local/lib64/python%s/site-packages' % _pyver_tuple),
        os.path.join(sys.prefix, 'lib', 'python%s' % _pyver_short, 'site-packages'),
        os.path.join(sys.prefix, 'lib', 'python%s' % _pyver_tuple, 'site-packages'),
        os.path.join(sys.prefix, 'lib64', 'python%s' % _pyver_tuple, 'site-packages'),
        _tmp_target,
        _datadir_target,
    ]:
        if not os.path.isdir(extra_path):
            log.append('[SKIP] path probe %s — not a directory' % extra_path)
            continue
        if extra_path in sys.path:
            log.append('[SKIP] path probe %s — already in sys.path' % extra_path)
            continue
        sys.path.insert(0, extra_path)
        try:
            __import__('psycopg2')
            _PG_DRIVER = 'psycopg2'
            log.append('[OK] path probe %s — import succeeded' % extra_path)
            return _PG_DRIVER
        except ImportError as exc:
            log.append('[FAIL] path probe %s: %s' % (extra_path, exc))

    log.append('[RESULT] All attempts failed — will need user-provided password for psql fallback')
    _PG_DRIVER = None
    return _PG_DRIVER


def _get_pg_conn_params(connection_name: str) -> dict:
    """Extract PG connection params from a DSS connection definition."""
    client = g.client
    defn = client.get_connection(connection_name).get_definition()
    params = defn.get('params', {})
    return {
        'host': params.get('host', 'localhost'),
        'port': int(params.get('port', 5432)),
        'dbname': params.get('db', params.get('database', params.get('dbname', ''))),
        'user': params.get('user', ''),
        'password': params.get('password', ''),
    }


def _pg_direct_connect(connection_name: str, user_password: str = ''):
    """Get a PG connection with autocommit using psycopg2."""
    p = _get_pg_conn_params(connection_name)
    driver = _ensure_pg_driver()
    if driver == 'psycopg2':
        import psycopg2
        pw = user_password or p['password']
        conn = psycopg2.connect(
            host=p['host'], port=p['port'], dbname=p['dbname'],
            user=p['user'], password=pw,
            options='-c statement_timeout=60000',
        )
        conn.autocommit = True
        return conn
    raise ImportError("No PG driver available")


def _pg_exec_ddl(connection_name: str, sql_template: str, table_name: str, user_password: str = ''):
    """Execute a DDL-like statement (VACUUM/ANALYZE) that needs autocommit.
    Tries: 1) psycopg2 with autocommit, 2) psql CLI with user-provided password.
    If psycopg2 is not available and no password is provided, returns needsPassword.

    Phase 2 short-circuit: VACUUM/ANALYZE on a remote host is not supported.
    The dbhealth-query macro's _READ_ONLY_RE rejects writes, and routing
    through local psycopg2 would either fail (firewall) or target the wrong
    database silently. Surface a clear error instead.
    """
    if _safe_request_host_id() != 'local':
        return {
            'success': False,
            'error': 'Maintenance writes (VACUUM/ANALYZE) on remote hosts are not yet supported. '
                     'Run them from the local DSS or via the host\'s own maintenance tooling.',
            'remoteUnsupported': True,
        }

    safe_table = '"%s"' % table_name.replace('"', '""')
    full_sql = sql_template.replace('{}', safe_table)
    p = _get_pg_conn_params(connection_name)
    errors = []

    # Strategy 1: psycopg2 with autocommit
    driver = _ensure_pg_driver()
    if driver:
        try:
            conn = _pg_direct_connect(connection_name, user_password=user_password)
            try:
                import psycopg2.sql as pg2sql
                with conn.cursor() as cur:
                    cur.execute(pg2sql.SQL(sql_template).format(pg2sql.Identifier(table_name)))
                return {'success': True, 'method': driver}
            finally:
                conn.close()
        except Exception as exc:
            err_str = str(exc).lower()
            if not user_password and ('password authentication failed' in err_str or 'fe_sendauth' in err_str):
                return {'needsPassword': True, 'reason': 'Database auth failed — please provide the password'}
            errors.append('%s: %s' % (driver, str(exc)))

    # Strategy 2: psql CLI with user-provided password
    if not user_password:
        return {'needsPassword': True, 'reason': 'psycopg2 not available — please provide the database password'}

    try:
        psql_cmd = ['psql', '-h', str(p['host']), '-p', str(p['port']),
                    '-U', p['user'], '-d', p['dbname'], '-c', full_sql]
        result = subprocess.run(psql_cmd, env=dict(os.environ, PGPASSWORD=user_password),
                                capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            return {'success': True, 'method': 'psql'}
        errors.append('psql: %s' % (result.stderr.strip() or result.stdout.strip())[:200])
    except FileNotFoundError:
        errors.append('psql: not found on this server')
    except Exception as exc:
        errors.append('psql: %s' % str(exc))

    raise RuntimeError('All methods failed: ' + '; '.join(errors))


def _list_pg_connections() -> list:
    """Return PostgreSQL connections with metadata."""
    def _loader():
        try:
            client = g.client
            all_conns = client.list_connections()
            result = []
            items = all_conns.items() if isinstance(all_conns, dict) else [(c.get('name'), c) for c in all_conns]
            for name, info in items:
                if not isinstance(info, dict):
                    continue
                conn_type = info.get('type', '')
                if conn_type != 'PostgreSQL':
                    continue
                params = info.get('params', {})
                result.append({
                    'name': name,
                    'type': conn_type,
                    'host': params.get('host', ''),
                    'port': params.get('port', 5432),
                    'db': params.get('db', params.get('database', params.get('dbname', ''))),
                })
            return result
        except Exception as exc:
            logging.getLogger(__name__).warning("[db-health] list_connections failed: %s", exc)
            return []
    return _cache_get('_pg_connections', 300, _loader)


def _sanitize_pg_error(err_msg: str) -> str:
    """Strip internal paths and IPs from PostgreSQL error messages."""
    sanitized = re.sub(r'(/[^\s:]+)+', '<path>', str(err_msg))
    sanitized = re.sub(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', '<ip>', sanitized)
    return sanitized


def _validate_pg_connection(connection_name: str):
    """Validate connection name against known PostgreSQL connections. Returns error response or None."""
    if not connection_name:
        return jsonify({'error': 'Missing connection parameter'}), 400
    known = [c['name'] for c in _list_pg_connections()]
    if connection_name not in known:
        return jsonify({'error': 'Unknown or non-PostgreSQL connection'}), 400
    return None


_ACTUAL_READ_METHOD = {}  # tracks what actually worked per connection


class _NeedsPasswordError(RuntimeError):
    """Raised when DB auth fails and user must provide password."""
    pass


def _pg_query_rows(connection_name: str, sql: str, user_password: str = ''):
    """Execute a read query. Routes through the dbhealth-query macro when the
    active host is remote (so psycopg2 + .pgpass run on the target host's
    service account). Local path tries psycopg2 then psql fallback."""
    if _safe_request_host_id() != 'local':
        result = _dbhealth_macro(
            g.client,
            operation='run-query',
            sql=sql,
            connection=connection_name,
            password=user_password,
        )
        if not result.get('ok'):
            err = (result.get('error') or '').lower()
            if 'password authentication failed' in err or 'fe_sendauth' in err:
                raise _NeedsPasswordError(f"remote dbhealth auth failed: {result.get('error')}")
            raise RuntimeError(f"remote dbhealth query failed: {result.get('error')}")
        cols = result.get('columns') or []
        rows = result.get('rows') or []
        _ACTUAL_READ_METHOD[connection_name] = 'macro:dbhealth-query'
        return [dict(zip(cols, r)) for r in rows]

    driver = _ensure_pg_driver()
    if driver:
        try:
            conn = _pg_direct_connect(connection_name, user_password=user_password)
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    cols = [d[0] for d in cur.description]
                    _ACTUAL_READ_METHOD[connection_name] = driver
                    return [dict(zip(cols, row)) for row in cur.fetchall()]
            finally:
                conn.close()
        except Exception as exc:
            err_str = str(exc).lower()
            # Auth failure with stored password — ask user for the real one
            if not user_password and ('password authentication failed' in err_str or 'fe_sendauth' in err_str):
                raise _NeedsPasswordError("psycopg2 auth failed: %s" % exc)
            raise RuntimeError("psycopg2 query failed: %s" % exc)

    # psycopg2 not available — try psql with user-provided password
    if not user_password:
        raise _NeedsPasswordError("psycopg2 not available — password required for psql fallback")
    p = _get_pg_conn_params(connection_name)
    psql_cmd = [
        'psql', '-h', str(p['host']), '-p', str(p['port']),
        '-U', p['user'], '-d', p['dbname'],
        '-F', '\t', '--no-align', '-c', sql,
    ]
    try:
        result = subprocess.run(psql_cmd, env=dict(os.environ, PGPASSWORD=user_password),
                                capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError("psql: %s" % (result.stderr.strip() or 'exit %d' % result.returncode)[:200])
        all_lines = [l for l in result.stdout.strip().split('\n') if l.strip()]
        if len(all_lines) < 2:
            _ACTUAL_READ_METHOD[connection_name] = 'psql'
            return []
        headers = all_lines[0].split('\t')
        rows = []
        for line in all_lines[1:]:
            if line.startswith('(') and line.endswith(')'):
                continue
            vals = line.split('\t')
            rows.append(dict(zip(headers, vals)))
        _ACTUAL_READ_METHOD[connection_name] = 'psql'
        return rows
    except FileNotFoundError:
        raise RuntimeError("psql CLI not found on this server")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("psql failed: %s" % exc)


@bp.route('/api/tools/db-health/connections')
def api_db_health_connections():
    try:
        cfg = _get_dbhealth_config()
        return jsonify({
            'connections': _list_pg_connections(),
            'configuredConnection': cfg.connection_name or '',
            'hasConfiguredPassword': bool(cfg.password),
        })
    except Exception as exc:
        return jsonify({'error': _sanitize_pg_error(str(exc))}), 500


@bp.route('/api/tools/db-health/overview')
def api_db_health_overview():
    connection_name = request.args.get('connection', '')
    user_password = request.args.get('password', '') or _get_dbhealth_config().password or '' or _get_dbhealth_config().password or ''
    validation = _validate_pg_connection(connection_name)
    if validation:
        return validation

    driver = _ensure_pg_driver()

    warnings = []
    query_method = driver or ('psql' if user_password else 'none')
    result = {
        'dbSize': '', 'dbSizeBytes': 0, 'version': '',
        'tableCount': 0, 'totalDeadTuples': 0, 'totalLiveTuples': 0,
        'canWrite': False, 'queryMethod': query_method,
        'driverLog': list(_PG_DRIVER_LOG),
        'warnings': warnings,
    }
    try:
        rows = _pg_query_rows(connection_name,
            "SELECT pg_size_pretty(pg_database_size(current_database())) as db_size,"
            " pg_database_size(current_database()) as db_size_bytes,"
            " current_setting('server_version') as version",
            user_password=user_password)
        if rows:
            result['dbSize'] = str(rows[0].get('db_size', ''))
            result['dbSizeBytes'] = int(rows[0].get('db_size_bytes', 0))
            result['version'] = str(rows[0].get('version', ''))
    except _NeedsPasswordError as exc:
        return jsonify({
            'needsPassword': True,
            'driverLog': list(_PG_DRIVER_LOG),
            'reason': str(exc),
        })
    except Exception as exc:
        warnings.append('Could not fetch database size: %s' % _sanitize_pg_error(str(exc)))

    try:
        rows = _pg_query_rows(connection_name,
            "SELECT count(*) as table_count, coalesce(sum(n_dead_tup),0) as total_dead,"
            " coalesce(sum(n_live_tup),0) as total_live"
            " FROM pg_stat_user_tables",
            user_password=user_password)
        if rows:
            result['tableCount'] = int(rows[0].get('table_count', 0))
            result['totalDeadTuples'] = int(rows[0].get('total_dead', 0))
            result['totalLiveTuples'] = int(rows[0].get('total_live', 0))
    except Exception as exc:
        warnings.append('Could not fetch table stats: %s' % _sanitize_pg_error(str(exc)))

    # Detect write access — use same query path that already works for reads
    try:
        write_rows = _pg_query_rows(connection_name,
            "SELECT current_user as cu, current_setting('is_superuser') as su",
            user_password=user_password)
        if write_rows:
            cu = write_rows[0].get('cu', '')
            su = write_rows[0].get('su', '')
            if su == 'on':
                result['canWrite'] = True
        if not result['canWrite']:
            try:
                maint_rows = _pg_query_rows(connection_name,
                    "SELECT pg_has_role(current_user, 'pg_maintain', 'MEMBER') as m",
                    user_password=user_password)
                if maint_rows and maint_rows[0].get('m'):
                    result['canWrite'] = True
            except Exception:
                pass  # pg_maintain role may not exist on PG < 15
    except Exception:
        pass

    result['warnings'] = warnings
    return jsonify(result)


@bp.route('/api/tools/db-health/tables')
def api_db_health_tables():
    connection_name = request.args.get('connection', '')
    user_password = request.args.get('password', '') or _get_dbhealth_config().password or ''
    validation = _validate_pg_connection(connection_name)
    if validation:
        return validation
    warnings = []
    tables = []
    try:
        rows = _pg_query_rows(connection_name,
            "SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) as total_size,"
            " pg_total_relation_size(relid) as total_size_bytes,"
            " n_live_tup, n_dead_tup,"
            " CASE WHEN n_live_tup + n_dead_tup > 0"
            "      THEN round(n_dead_tup::numeric / (n_live_tup + n_dead_tup), 4)"
            "      ELSE 0 END as bloat_ratio,"
            " last_vacuum, last_autovacuum, last_analyze"
            " FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC",
            user_password=user_password)
        for r in rows:
            tables.append({
                'name': str(r.get('relname', '')),
                'totalSize': str(r.get('total_size', '')),
                'totalSizeBytes': int(r.get('total_size_bytes', 0)),
                'rowCount': int(r.get('n_live_tup', 0)),
                'deadTuples': int(r.get('n_dead_tup', 0)),
                'bloatRatio': float(r.get('bloat_ratio', 0)),
                'lastVacuum': str(r.get('last_vacuum', '') or ''),
                'lastAutovacuum': str(r.get('last_autovacuum', '') or ''),
                'lastAnalyze': str(r.get('last_analyze', '') or ''),
            })
    except Exception as exc:
        warnings.append('Could not fetch table details: %s' % _sanitize_pg_error(str(exc)))
    return jsonify({'tables': tables, 'warnings': warnings})


@bp.route('/api/tools/db-health/per-project')
def api_db_health_per_project():
    connection_name = request.args.get('connection', '')
    user_password = request.args.get('password', '') or _get_dbhealth_config().password or ''
    validation = _validate_pg_connection(connection_name)
    if validation:
        return validation
    warnings = []
    result = {'projects': [], 'system': {}, 'isRuntimeDb': False, 'warnings': warnings}
    try:
        # Detect RuntimeDB by checking for known tables
        detect_rows = _pg_query_rows(connection_name,
            "SELECT count(*) as cnt FROM pg_tables"
            " WHERE schemaname='public' AND lower(tablename) IN ('dss_metadata', 'scenario_runs', 'job')",
            user_password=user_password)
        is_runtime = detect_rows and int(detect_rows[0].get('cnt', 0)) >= 2
        result['isRuntimeDb'] = is_runtime

        # Get all tables with sizes
        table_rows = _pg_query_rows(connection_name,
            "SELECT relname, pg_total_relation_size(relid) as size_bytes, n_live_tup"
            " FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC",
            user_password=user_password)

        if not is_runtime:
            # Not RuntimeDB — all tables go to system bucket
            system_tables = []
            total_bytes = 0
            for r in table_rows:
                sz = int(r.get('size_bytes', 0))
                total_bytes += sz
                system_tables.append({
                    'name': str(r.get('relname', '')),
                    'sizeBytes': sz,
                    'rowCount': int(r.get('n_live_tup', 0)),
                })
            result['system'] = {'tables': system_tables, 'totalBytes': total_bytes}
            result['warnings'] = warnings
            return jsonify(result)

        # RuntimeDB — find project columns
        col_rows = _pg_query_rows(connection_name,
            "SELECT table_name, column_name FROM information_schema.columns"
            " WHERE table_schema='public'"
            " AND (column_name ILIKE '%%projectkey%%' OR column_name ILIKE '%%project_key%%')",
            user_password=user_password)
        table_project_col = {}
        for r in col_rows:
            tname = str(r.get('table_name', ''))
            cname = str(r.get('column_name', ''))
            if tname and cname:
                table_project_col[tname.lower()] = {'table': tname, 'column': cname}

        project_sizes: Dict[str, Dict[str, Any]] = {}
        system_tables = []
        system_total = 0

        for r in table_rows:
            relname = str(r.get('relname', ''))
            sz = int(r.get('size_bytes', 0))
            row_count = int(r.get('n_live_tup', 0))
            lookup = table_project_col.get(relname.lower())
            if not lookup:
                system_total += sz
                system_tables.append({'name': relname, 'sizeBytes': sz, 'rowCount': row_count})
                continue
            # Query per-project breakdown for this table
            try:
                proj_rows = _pg_query_rows(connection_name,
                    "SELECT \"%s\" as pkey, count(*) as cnt FROM \"%s\" GROUP BY \"%s\""
                    % (lookup['column'], lookup['table'], lookup['column']),
                    user_password=user_password)
                total_rows = sum(int(pr.get('cnt', 0)) for pr in proj_rows)
                for pr in proj_rows:
                    pkey = str(pr.get('pkey', '') or 'Unknown')
                    cnt = int(pr.get('cnt', 0))
                    # Estimate size proportional to row count
                    est_size = int(sz * cnt / total_rows) if total_rows > 0 else 0
                    if pkey not in project_sizes:
                        project_sizes[pkey] = {'projectKey': pkey, 'sizeBytes': 0, 'tableCount': 0, 'rowCount': 0}
                    project_sizes[pkey]['sizeBytes'] += est_size
                    project_sizes[pkey]['tableCount'] += 1
                    project_sizes[pkey]['rowCount'] += cnt
            except Exception as exc:
                warnings.append('Could not break down table %s: %s' % (relname, _sanitize_pg_error(str(exc))))
                system_total += sz
                system_tables.append({'name': relname, 'sizeBytes': sz, 'rowCount': row_count})

        result['projects'] = sorted(project_sizes.values(), key=lambda p: p['sizeBytes'], reverse=True)
        result['system'] = {'tables': system_tables, 'totalBytes': system_total}
    except Exception as exc:
        warnings.append('Per-project query failed: %s' % _sanitize_pg_error(str(exc)))
    result['warnings'] = warnings
    return jsonify(result)


@bp.route('/api/tools/db-health/vacuum', methods=['POST'])
@advanced
def api_db_health_vacuum():
    body = request.get_json(force=True, silent=True) or {}
    connection_name = body.get('connection', '')
    table_name = body.get('table', '')
    validation = _validate_pg_connection(connection_name)
    if validation:
        return validation
    if not table_name:
        return jsonify({'error': 'Missing table parameter'}), 400

    user_password = body.get('password', '') or _get_dbhealth_config().password or ''

    # Whitelist: validate table name against pg_stat_user_tables
    try:
        valid_tables = _pg_query_rows(connection_name,
            "SELECT relname FROM pg_stat_user_tables",
            user_password=user_password)
        valid_names = {str(r.get('relname', '')) for r in valid_tables}
        if table_name not in valid_names:
            return jsonify({'error': 'Invalid table name'}), 400
    except Exception as exc:
        return jsonify({'error': 'Could not validate table: %s' % _sanitize_pg_error(str(exc))}), 500

    try:
        result = _pg_exec_ddl(connection_name, "VACUUM {}", table_name, user_password=user_password)
        return jsonify(result)
    except Exception as exc:
        return jsonify({'error': _sanitize_pg_error(str(exc))}), 500


@bp.route('/api/tools/db-health/analyze', methods=['POST'])
@advanced
def api_db_health_analyze():
    body = request.get_json(force=True, silent=True) or {}
    connection_name = body.get('connection', '')
    table_name = body.get('table', '')
    validation = _validate_pg_connection(connection_name)
    if validation:
        return validation
    if not table_name:
        return jsonify({'error': 'Missing table parameter'}), 400

    user_password = body.get('password', '') or _get_dbhealth_config().password or ''

    # Whitelist: validate table name against pg_stat_user_tables
    try:
        valid_tables = _pg_query_rows(connection_name,
            "SELECT relname FROM pg_stat_user_tables",
            user_password=user_password)
        valid_names = {str(r.get('relname', '')) for r in valid_tables}
        if table_name not in valid_names:
            return jsonify({'error': 'Invalid table name'}), 400
    except Exception as exc:
        return jsonify({'error': 'Could not validate table: %s' % _sanitize_pg_error(str(exc))}), 500

    try:
        result = _pg_exec_ddl(connection_name, "ANALYZE {}", table_name, user_password=user_password)
        return jsonify(result)
    except Exception as exc:
        return jsonify({'error': _sanitize_pg_error(str(exc))}), 500
