"""Engine/session lifecycle for chat persistence — Agent Hub's app_paths.py
URL building, without Flask-SQLAlchemy init_app: a standalone create_engine +
sessionmaker so sessions work in SSE generators and ThreadPoolExecutor workers.

Lazy, lock-guarded init on the first chat request (not at boot): resolves the
plugin config (OFF | LOCAL SQLite in the workload folder | REMOTE DSS SQL
connection, PostgreSQL/MSSQL only), exports TABLES_PREFIX / DB_SCHEMA, imports
the models (which read them at import time), and create_all(checkfirst=True) —
the same idempotent-create pattern as atk_agent_common/audit.py, no alembic.

The DB always lives on the LOCAL hub (plugin settings + the selected
connection are local); the active remote host only scopes rows via host_id.
"""

import logging
import os
import re
import threading
from contextlib import contextmanager
from urllib.parse import quote_plus

_LOGGER = logging.getLogger(__name__)

_PREFIX_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]*$')
_DEFAULT_PREFIX = 'atk_chat_'
_SQLITE_FILENAME = 'atk_chat.db'

_INIT_LOCK = threading.Lock()
_STATE = {
    'ready': False,
    'engine': None,
    'session_factory': None,
    'models': None,
    'mode': None,
    'error': None,
}


class ChatPersistenceError(RuntimeError):
    """Raised when chat persistence is enabled but the store can't come up."""


# ── URL building (Agent Hub app_paths.py, PostgreSQL + MSSQL verbatim) ──────

def get_postgres_db_url(conn_params):
    user = quote_plus(conn_params.get('user', ''))
    password = quote_plus(conn_params.get('password', ''))
    host = conn_params.get('host', '')
    port = conn_params.get('port', 5432)
    dbname = conn_params.get('db', '')
    return 'postgresql://%s:%s@%s:%s/%s' % (user, password, host, port, dbname)


def get_mssql_db_url(conn_params):
    user = quote_plus(conn_params.get('user', ''))
    password = quote_plus(conn_params.get('password', ''))
    host = conn_params.get('host', 'localhost')
    port = conn_params.get('port', 1433)
    dbname = conn_params.get('db', '')
    charset = conn_params.get('charset', 'utf8')
    return 'mssql+pymssql://%s:%s@%s:%s/%s?charset=%s' % (
        user, password, host, port, dbname, charset)


def get_remote_db_url(conn_type, conn_params, conn_info=None):
    """Build a remote database URL from DSS connection details."""
    if conn_type == 'postgresql':
        return get_postgres_db_url(conn_params)
    if conn_type in ('mssql', 'sqlserver'):
        return get_mssql_db_url(conn_params)
    raise ChatPersistenceError(
        'Unsupported chat DB connection type: %s (PostgreSQL or SQL Server only)'
        % conn_type)


def _remote_schema(conn_type, conn_params):
    """Schema per dialect (Agent Hub get_db_schema): Postgres namingRule
    default public, MSSQL schema default dbo."""
    if conn_type == 'postgresql':
        return (conn_params.get('namingRule') or {}).get('schemaName') or 'public'
    if conn_type in ('mssql', 'sqlserver'):
        return conn_params.get('schema') or 'dbo'
    return None


def normalize_tables_prefix(raw):
    """Validate/normalize the configured prefix (Agent Hub get_tables_prefix):
    lowercase, ^[a-zA-Z][a-zA-Z0-9_]*$ (SQL-injection guard), trailing '_'."""
    prefix = (raw or '').strip() or _DEFAULT_PREFIX
    prefix = prefix.lower()
    if not _PREFIX_RE.match(prefix):
        raise ChatPersistenceError(
            "Invalid chat tables prefix '%s': only alphanumeric characters and "
            'underscores are allowed' % prefix)
    if not prefix.endswith('_'):
        prefix += '_'
    return prefix


def _sqlite_db_path():
    """LOCAL storage: atk_chat.db in the webapp's workload folder (verified
    present on DSS 14.7 — dataiku.core.workload_local_folder)."""
    from dataiku.core import workload_local_folder
    folder = workload_local_folder.get_workload_local_folder_path()
    return os.path.join(folder, _SQLITE_FILENAME)


def _resolve_url_and_schema(cfg):
    """(db_url, schema) for the configured mode. REMOTE resolves the DSS
    connection via the LOCAL client — plugin settings name a local connection."""
    if cfg.mode == 'LOCAL':
        return 'sqlite:///%s' % _sqlite_db_path(), None
    if not cfg.connection_name:
        raise ChatPersistenceError(
            'Chat persistence is set to Remote SQL but no connection is selected '
            'in the Admin Toolkit plugin settings.')
    from adk_backend.clients import _local_thread_client
    conn_info = _local_thread_client().get_connection(cfg.connection_name).get_info()
    conn_type = str(conn_info['type']).lower()
    conn_params = conn_info.get_params()
    return (get_remote_db_url(conn_type, conn_params, conn_info),
            _remote_schema(conn_type, conn_params))


# ── lifecycle ────────────────────────────────────────────────────────────────

def _initialize(cfg):
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    prefix = normalize_tables_prefix(cfg.tables_prefix)
    db_url, schema = _resolve_url_and_schema(cfg)

    # Models read these at import time (Agent Hub-verbatim); chat/db.py is the
    # only importer of chat/models.py so the order is guaranteed.
    os.environ['TABLES_PREFIX'] = prefix
    if schema:
        os.environ['DB_SCHEMA'] = schema
    else:
        os.environ.pop('DB_SCHEMA', None)
    from adk_backend.chat import models

    is_sqlite = db_url.startswith('sqlite:')
    engine_kwargs = {} if is_sqlite else {'pool_pre_ping': True}
    engine = create_engine(db_url, **engine_kwargs)
    if is_sqlite:
        @event.listens_for(engine, 'connect')
        def _set_sqlite_pragma(dbapi_conn, _record):
            # WAL: concurrent readers during writes (backend threads + any
            # sibling webapp process on the same workload folder).
            cursor = dbapi_conn.cursor()
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.close()

    models.db.metadata.create_all(engine, checkfirst=True)

    _STATE.update({
        'ready': True,
        'engine': engine,
        'session_factory': sessionmaker(bind=engine),
        'models': models,
        'mode': cfg.mode,
        'error': None,
    })
    _LOGGER.info('chat persistence up: mode=%s prefix=%s dialect=%s',
                 cfg.mode, prefix, engine.dialect.name)


def ensure_ready(cfg):
    """Idempotent lazy init; raises ChatPersistenceError when the configured
    store can't come up. One store per process lifetime: models bind
    TABLES_PREFIX/DB_SCHEMA at import, so a storage-mode/prefix change in
    plugin settings takes effect on the next backend restart."""
    if _STATE['ready']:
        if _STATE['mode'] != cfg.mode:
            _LOGGER.warning(
                'chat persistence mode changed in plugin settings (%s -> %s); '
                'restart the webapp backend to apply', _STATE['mode'], cfg.mode)
        return
    with _INIT_LOCK:
        if _STATE['ready']:
            return
        try:
            _initialize(cfg)
        except ChatPersistenceError:
            raise
        except Exception as exc:
            raise ChatPersistenceError(
                'chat persistence init failed: %s: %s'
                % (type(exc).__name__, str(exc)[:300])) from exc


def get_models():
    models = _STATE['models']
    if models is None:
        raise ChatPersistenceError('chat persistence not initialized')
    return models


@contextmanager
def session_scope():
    """Transactional session: commit on success, rollback on error, always
    close — safe inside request handlers and worker threads alike."""
    factory = _STATE['session_factory']
    if factory is None:
        raise ChatPersistenceError('chat persistence not initialized')
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_for_tests():
    """Drop the module-level engine/session state and force a fresh models
    import (unit tests only — models bind prefix/schema at import time)."""
    import sys
    engine = _STATE.get('engine')
    if engine is not None:
        try:
            engine.dispose()
        except Exception:
            pass
    _STATE.update({'ready': False, 'engine': None, 'session_factory': None,
                   'models': None, 'mode': None, 'error': None})
    sys.modules.pop('adk_backend.chat.models', None)
