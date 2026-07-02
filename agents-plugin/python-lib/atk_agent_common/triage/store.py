"""Persistence for daily triage results → story.agent_triage_daily.

Same Postgres connection Story uses (plugin param `triage_connection`), same
credential path as audit.py. One row per (day, host) per run; re-runs the same
day overwrite (idempotent daily scenario)."""

import json
import logging

logger = logging.getLogger('atk-agents')

_SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS story;
CREATE TABLE IF NOT EXISTS story.agent_triage_daily (
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


def persist_sweep(connection_name, rows, run_id, llm_id=None):
    """Upsert one row per host for today. `rows` = sweep rows (+ optional
    'recommendation' added by the runnable). Returns count written."""
    from .. import audit
    conn = audit._connect(connection_name)
    written = 0
    try:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA_SQL)
            for row in rows:
                cur.execute(
                    'INSERT INTO story.agent_triage_daily '
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
