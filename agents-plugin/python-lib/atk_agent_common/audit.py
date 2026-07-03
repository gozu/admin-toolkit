"""Agent-action audit trail → Postgres (plugin param `triage_connection`).

Every execute-admin-action call lands one row in agents.agent_actions —
success or failure — before the result is returned to the agent. Connection
resolution mirrors the toolkit's adk_backend/agents_db.py
(client.get_connection().get_info(), the credential path proven for the
dataiku service account).
"""

import json
import logging

logger = logging.getLogger('atk-agents')

# One-time move of tables written under the removed Story feature's schema.
# Runs in every schema-ensure; the guards make it a no-op once migrated.
_MIGRATE_LEGACY_SQL = """
CREATE SCHEMA IF NOT EXISTS agents;
DO $$
BEGIN
    IF to_regclass('story.agent_actions') IS NOT NULL
       AND to_regclass('agents.agent_actions') IS NULL THEN
        EXECUTE 'ALTER TABLE story.agent_actions SET SCHEMA agents';
    END IF;
    IF to_regclass('story.agent_triage_daily') IS NOT NULL
       AND to_regclass('agents.agent_triage_daily') IS NULL THEN
        EXECUTE 'ALTER TABLE story.agent_triage_daily SET SCHEMA agents';
    END IF;
END $$;
"""

_SCHEMA_SQL = _MIGRATE_LEGACY_SQL + """
CREATE TABLE IF NOT EXISTS agents.agent_actions (
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
CREATE TABLE IF NOT EXISTS agents.settings_changes (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    host TEXT NOT NULL,
    item_key TEXT NOT NULL,
    before TEXT,
    after TEXT,
    agent TEXT,
    actor TEXT,
    audit_id BIGINT
);
CREATE INDEX IF NOT EXISTS settings_changes_item_idx
    ON agents.settings_changes (item_key, id DESC);
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


def record_settings_changes(connection_name, host, changes, agent=None, actor=None,
                            audit_id=None):
    """Persist settings-change history rows (K97 doctrine: prior value recorded,
    restorable from the last 50 per item). `changes` = [{itemKey, before,
    after}]. Returns the number of rows written, or None when the audit store
    is unavailable — callers surface that as a history warning, never a block."""
    if not connection_name or not changes:
        if not connection_name:
            logger.warning('settings-change history skipped: no triage_connection configured')
        return None if not connection_name else 0
    try:
        conn = _connect(connection_name)
    except Exception as exc:
        logger.error('settings-change history connect failed: %s', exc)
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA_SQL)
            for change in changes:
                cur.execute(
                    'INSERT INTO agents.settings_changes '
                    '(host, item_key, before, after, agent, actor, audit_id) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s)',
                    (host, change['itemKey'],
                     json.dumps(change.get('before'), default=str),
                     json.dumps(change.get('after'), default=str),
                     agent, actor, audit_id))
        conn.commit()
        return len(changes)
    except Exception as exc:
        logger.error('settings-change history insert failed: %s', exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        conn.close()


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
                'INSERT INTO agents.agent_actions '
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
