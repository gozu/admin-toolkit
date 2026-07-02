"""Agent-action audit trail → Postgres (same connection Story uses).

Every execute-admin-action call lands one row in story.agent_actions —
success or failure — before the result is returned to the agent. Connection
resolution mirrors adk_backend/story/db.py (client.get_connection().get_info(),
the credential path proven for the dataiku service account).
"""

import json
import logging

logger = logging.getLogger('atk-agents')

_SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS story;
CREATE TABLE IF NOT EXISTS story.agent_actions (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    agent TEXT NOT NULL,
    llm_id TEXT,
    host TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    params TEXT,
    token_hash TEXT,
    status TEXT NOT NULL,
    result_snippet TEXT
);
"""


def _connect(connection_name):
    import dataiku
    import psycopg2
    client = dataiku.api_client()
    info = client.get_connection(connection_name).get_info()
    params = (info.get('params') or {}) if isinstance(info, dict) else {}
    conn = psycopg2.connect(
        options='-c statement_timeout=30000',
        host=params.get('host') or 'localhost',
        port=int(params.get('port') or 5432),
        dbname=params.get('db') or params.get('database') or params.get('dbname') or 'postgres',
        user=params.get('user') or 'dataiku',
        **({'password': params['password']} if params.get('password') else {}))
    conn.autocommit = False
    return conn


def record(connection_name, agent, llm_id, host, action, target, params, token_hash,
           status, result_snippet):
    """Insert one audit row; returns the row id, or None (logged) if the audit
    store is unavailable — the caller decides whether that blocks execution."""
    if not connection_name:
        logger.warning('agent-actions audit skipped: no triage_connection configured')
        return None
    try:
        conn = _connect(connection_name)
    except Exception as exc:
        logger.error('agent-actions audit connect failed: %s', exc)
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA_SQL)
            cur.execute(
                'INSERT INTO story.agent_actions '
                '(agent, llm_id, host, action, target, params, token_hash, status, result_snippet) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                (agent, llm_id, host, action, json.dumps(target, default=str),
                 json.dumps(params, default=str) if params else None,
                 token_hash, status, (result_snippet or '')[:2000]))
            row_id = cur.fetchone()[0]
        conn.commit()
        return row_id
    except Exception as exc:
        logger.error('agent-actions audit insert failed: %s', exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        conn.close()
