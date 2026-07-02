"""Story Postgres access — connection resolution via a DSS connection.

Same credential path proven in python-runnables/dbhealth-query/runnable.py:
`client.get_connection(name).get_info()` (works for the `dataiku` service
account without exposing get_definition()-level secrets).

Transactions are the caller's job: connections are opened with
autocommit=False and every (instance, source) collection unit commits its
data + cursor atomically. All SQL through these connections must be
parameterized (see sqlgen.py) — never interpolate values.
"""
from typing import Any, Optional

DEFAULT_STATEMENT_TIMEOUT_MS = 120000


def resolve_connection_params(connection_name: str, client: Optional[Any] = None) -> dict:
    """psycopg2 connect kwargs from a DSS PostgreSQL connection definition."""
    if client is None:
        import dataiku
        client = dataiku.api_client()
    info = client.get_connection(connection_name).get_info()
    params = (info.get('params') or {}) if isinstance(info, dict) else {}
    return {
        'host': params.get('host') or 'localhost',
        'port': int(params.get('port') or 5432),
        'dbname': params.get('db') or params.get('database') or params.get('dbname') or 'postgres',
        'user': params.get('user') or 'dataiku',
        'password': params.get('password') or None,
    }


def connect(connection_name: str, client: Optional[Any] = None,
            statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS) -> Any:
    """Open a psycopg2 connection (autocommit=False) to the Story database."""
    if not connection_name:
        raise ValueError('Story is not configured: no PostgreSQL connection selected '
                         "(plugin settings → Story → 'Story PostgreSQL Connection')")
    import psycopg2
    kwargs = resolve_connection_params(connection_name, client=client)
    conn = psycopg2.connect(
        options='-c statement_timeout=%d' % int(statement_timeout_ms),
        **{k: v for k, v in kwargs.items() if v is not None}
    )
    conn.autocommit = False
    return conn
