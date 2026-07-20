"""Persistence for daily triage results → agents.agent_triage_daily.

Same Postgres connection as the audit trail (plugin param
`triage_connection`), same credential path as audit.py. One row per
(day, host) per run; re-runs the same day overwrite (idempotent daily
scenario)."""

import json
import logging

from .. import audit

logger = logging.getLogger('atk-agents')

_SCHEMA_SQL = audit._MIGRATE_LEGACY_SQL + """
CREATE TABLE IF NOT EXISTS agents.agent_triage_daily (
    day DATE NOT NULL,
    host_id TEXT NOT NULL,
    score INTEGER,
    status TEXT,
    category_scores TEXT,
    top_issues TEXT,
    recommendation TEXT,
    llm_id TEXT,
    run_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (day, host_id)
);
"""


def fetch_previous_scores(connection_name):
    """{host_id: score} from each host's most recent BEFORE-today row —
    powers the digest's 'vs yesterday' deltas. Empty dict on any failure
    (first run, table missing, connection down): deltas are decoration,
    never a sweep dependency."""
    try:
        conn = audit._connect(connection_name)
    except Exception as exc:
        logger.info('[triage-store] previous scores unavailable: %s', exc)
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT DISTINCT ON (host_id) host_id, score '
                'FROM agents.agent_triage_daily '
                'WHERE day < CURRENT_DATE AND score IS NOT NULL '
                'ORDER BY host_id, day DESC')
            return {row[0]: row[1] for row in cur.fetchall()}
    except Exception as exc:
        logger.info('[triage-store] previous scores unavailable: %s', exc)
        return {}
    finally:
        conn.close()


def persist_sweep(connection_name, rows, run_id, llm_id=None):
    """Upsert one row per host for today. `rows` = sweep rows (+ optional
    'recommendation' added by the runnable). Returns count written."""
    conn = audit._connect(connection_name)
    written = 0
    try:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA_SQL)
            for row in rows:
                cur.execute(
                    'INSERT INTO agents.agent_triage_daily '
                    '(day, host_id, score, status, category_scores, top_issues, '
                    ' recommendation, llm_id, run_id) '
                    'VALUES (CURRENT_DATE,%s,%s,%s,%s,%s,%s,%s,%s) '
                    'ON CONFLICT (day, host_id) DO UPDATE SET '
                    ' score=EXCLUDED.score, status=EXCLUDED.status, '
                    ' category_scores=EXCLUDED.category_scores, top_issues=EXCLUDED.top_issues, '
                    ' recommendation=EXCLUDED.recommendation, llm_id=EXCLUDED.llm_id, '
                    ' run_id=EXCLUDED.run_id, created_at=now()',
                    (row.get('host'),
                     row.get('score') if isinstance(row.get('score'), (int, float)) else None,
                     row.get('status'),
                     json.dumps(row.get('categoryScores'), default=str) if row.get('categoryScores') else None,
                     json.dumps(row.get('topIssues'), default=str) if row.get('topIssues') else None,
                     row.get('recommendation'),
                     llm_id, run_id))
                written += 1
        conn.commit()
        return written
    finally:
        conn.close()
