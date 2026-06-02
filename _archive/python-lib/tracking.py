"""
Tracking database for diagnostic run persistence, issue lifecycle management,
and outreach audit trail.

SQLite-backed, WAL mode, single connection with threading lock.
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from trends_registry import TREND_SNAPSHOT_TABLES
except Exception:
    TREND_SNAPSHOT_TABLES = ()


# Section-to-campaign dependency mapping.
# Auto-resolution for a campaign is only safe when ALL its required sections
# completed successfully.
CAMPAIGN_REQUIRED_SECTIONS: Dict[str, List[str]] = {
    'project': ['projects', 'project_footprint', 'code_envs'],
    'code_env': ['projects', 'code_envs'],
    'code_studio': ['projects', 'project_footprint'],
    'auto_scenario': ['projects', 'scenarios'],
    'scenario_frequency': ['projects', 'scenarios'],
    'scenario_failing': ['projects', 'scenarios'],
    'disabled_user': ['projects', 'users', 'project_footprint'],
    'deprecated_code_env': ['code_envs'],
    'default_code_env': ['projects', 'code_envs'],
    'empty_project': ['projects', 'project_footprint'],
    'large_flow': ['projects', 'project_footprint'],
    'orphan_notebooks': ['projects', 'project_footprint'],
    'overshared_project': ['projects'],
    'inactive_project': ['projects'],
    'unused_code_env': ['code_envs'],
}

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instances (
    instance_id     TEXT PRIMARY KEY,
    instance_url    TEXT NOT NULL,
    install_id      TEXT,
    node_id         TEXT,
    company_name    TEXT,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id         TEXT NOT NULL REFERENCES instances(instance_id),
    dss_host_id         TEXT NOT NULL DEFAULT 'local',
    run_at              TEXT NOT NULL,
    dss_version         TEXT,
    python_version      TEXT,
    health_score        REAL,
    health_status       TEXT,
    user_count          INTEGER,
    enabled_user_count  INTEGER,
    project_count       INTEGER,
    code_env_count      INTEGER,
    plugin_count        INTEGER,
    connection_count    INTEGER,
    cluster_count       INTEGER,
    coverage_status     TEXT NOT NULL DEFAULT 'complete',
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS run_health_metrics (
    run_id                      INTEGER PRIMARY KEY REFERENCES runs(run_id),
    cpu_cores                   INTEGER,
    memory_total_mb             INTEGER,
    memory_used_mb              INTEGER,
    memory_available_mb         INTEGER,
    swap_total_mb               INTEGER,
    swap_used_mb                INTEGER,
    max_filesystem_pct          REAL,
    max_filesystem_mount        TEXT,
    backend_heap_mb             INTEGER,
    jek_heap_mb                 INTEGER,
    fek_heap_mb                 INTEGER,
    open_files_limit            INTEGER,
    version_currency_score      REAL,
    system_capacity_score       REAL,
    configuration_score         REAL,
    security_isolation_score    REAL,
    license_named_users_pct         REAL,
    license_concurrent_users_pct    REAL,
    license_projects_pct            REAL,
    license_connections_pct         REAL,
    license_expiry_date             TEXT
);

CREATE TABLE IF NOT EXISTS run_campaign_summaries (
    run_id          INTEGER NOT NULL REFERENCES runs(run_id),
    campaign_id     TEXT NOT NULL,
    finding_count   INTEGER NOT NULL,
    recipient_count INTEGER NOT NULL,
    PRIMARY KEY (run_id, campaign_id)
);

CREATE TABLE IF NOT EXISTS run_sections (
    run_id          INTEGER NOT NULL REFERENCES runs(run_id),
    section_key     TEXT NOT NULL,
    status          TEXT NOT NULL,
    is_complete     INTEGER NOT NULL DEFAULT 1,
    error_message   TEXT,
    PRIMARY KEY (run_id, section_key)
);

CREATE TABLE IF NOT EXISTS findings (
    finding_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(run_id),
    campaign_id     TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    entity_key      TEXT NOT NULL,
    entity_name     TEXT,
    owner_login     TEXT NOT NULL,
    owner_email     TEXT,
    metrics_json    TEXT,
    UNIQUE(run_id, campaign_id, entity_type, entity_key)
);

CREATE INDEX IF NOT EXISTS idx_findings_run_campaign ON findings(run_id, campaign_id);
CREATE INDEX IF NOT EXISTS idx_findings_entity ON findings(campaign_id, entity_type, entity_key);
CREATE INDEX IF NOT EXISTS idx_findings_owner ON findings(owner_login);

CREATE TABLE IF NOT EXISTS issues (
    issue_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id         TEXT NOT NULL REFERENCES instances(instance_id),
    campaign_id         TEXT NOT NULL,
    entity_type         TEXT NOT NULL,
    entity_key          TEXT NOT NULL,
    owner_login         TEXT NOT NULL,
    owner_email         TEXT,
    status              TEXT NOT NULL DEFAULT 'open',
    first_detected_run  INTEGER NOT NULL REFERENCES runs(run_id),
    first_detected_at   TEXT NOT NULL,
    last_detected_run   INTEGER REFERENCES runs(run_id),
    last_detected_at    TEXT,
    resolved_run        INTEGER REFERENCES runs(run_id),
    resolved_at         TEXT,
    resolution_reason   TEXT,
    times_regressed     INTEGER NOT NULL DEFAULT 0,
    times_emailed       INTEGER NOT NULL DEFAULT 0,
    last_emailed_at     TEXT,
    UNIQUE(instance_id, campaign_id, entity_type, entity_key)
);

CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(instance_id, status);
CREATE INDEX IF NOT EXISTS idx_issues_owner ON issues(owner_login, status);
CREATE INDEX IF NOT EXISTS idx_issues_campaign ON issues(instance_id, campaign_id, status);

CREATE TABLE IF NOT EXISTS outreach_emails (
    email_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER REFERENCES runs(run_id),
    campaign_id     TEXT NOT NULL,
    recipient_login TEXT NOT NULL,
    recipient_email TEXT NOT NULL,
    sent_at         TEXT NOT NULL,
    status          TEXT NOT NULL,
    error_message   TEXT,
    subject         TEXT NOT NULL,
    channel_id      TEXT,
    sent_by         TEXT
);

CREATE INDEX IF NOT EXISTS idx_emails_recipient ON outreach_emails(recipient_login, campaign_id);
CREATE INDEX IF NOT EXISTS idx_emails_run ON outreach_emails(run_id);
CREATE INDEX IF NOT EXISTS idx_emails_sent_at ON outreach_emails(sent_at);

CREATE TABLE IF NOT EXISTS outreach_email_issues (
    email_id    INTEGER NOT NULL REFERENCES outreach_emails(email_id),
    issue_id    INTEGER NOT NULL REFERENCES issues(issue_id),
    PRIMARY KEY (email_id, issue_id)
);

CREATE INDEX IF NOT EXISTS idx_email_issues_issue ON outreach_email_issues(issue_id);

CREATE TABLE IF NOT EXISTS known_users (
    instance_id     TEXT NOT NULL REFERENCES instances(instance_id),
    login           TEXT NOT NULL,
    email           TEXT,
    display_name    TEXT,
    user_profile    TEXT,
    enabled         INTEGER NOT NULL DEFAULT 1,
    first_seen_run  INTEGER NOT NULL REFERENCES runs(run_id),
    last_seen_run   INTEGER NOT NULL REFERENCES runs(run_id),
    PRIMARY KEY (instance_id, login)
);

CREATE TABLE IF NOT EXISTS known_projects (
    instance_id     TEXT NOT NULL REFERENCES instances(instance_id),
    project_key     TEXT NOT NULL,
    name            TEXT,
    owner_login     TEXT,
    first_seen_run  INTEGER NOT NULL REFERENCES runs(run_id),
    last_seen_run   INTEGER NOT NULL REFERENCES runs(run_id),
    PRIMARY KEY (instance_id, project_key)
);

CREATE TABLE IF NOT EXISTS issue_notes (
    note_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id    INTEGER NOT NULL REFERENCES issues(issue_id),
    created_at  TEXT NOT NULL,
    created_by  TEXT,
    note        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_issue ON issue_notes(issue_id);

-- Views

INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (1, datetime('now'));

CREATE VIEW IF NOT EXISTS v_user_compliance AS
SELECT
    i.owner_login,
    i.owner_email,
    i.campaign_id,
    COUNT(CASE WHEN i.status = 'open' THEN 1 END)     AS open_issues,
    COUNT(CASE WHEN i.status = 'resolved' THEN 1 END)  AS resolved_issues,
    COUNT(CASE WHEN i.status = 'regressed' THEN 1 END) AS regressed_issues,
    SUM(i.times_emailed)                                AS total_emails_sent,
    MAX(i.last_emailed_at)                              AS last_emailed,
    MIN(i.first_detected_at)                            AS earliest_issue
FROM issues i
GROUP BY i.owner_login, i.campaign_id;

CREATE VIEW IF NOT EXISTS v_stale_outreach AS
SELECT
    i.issue_id,
    i.campaign_id,
    i.entity_type,
    i.entity_key,
    i.owner_login,
    i.owner_email,
    i.first_detected_at,
    i.times_emailed,
    i.last_emailed_at,
    CAST(julianday('now') - julianday(i.last_emailed_at) AS INTEGER) AS days_since_email,
    CAST(julianday('now') - julianday(i.first_detected_at) AS INTEGER) AS days_open
FROM issues i
WHERE i.status IN ('open', 'regressed')
  AND i.times_emailed > 0
ORDER BY days_since_email DESC;

CREATE VIEW IF NOT EXISTS v_run_deltas AS
SELECT
    r.run_id,
    r.run_at,
    r.instance_id,
    rcs.campaign_id,
    rcs.finding_count,
    LAG(rcs.finding_count) OVER (
        PARTITION BY r.instance_id, rcs.campaign_id
        ORDER BY r.run_id
    ) AS prev_finding_count,
    rcs.finding_count - COALESCE(LAG(rcs.finding_count) OVER (
        PARTITION BY r.instance_id, rcs.campaign_id
        ORDER BY r.run_id
    ), 0) AS delta
FROM runs r
JOIN run_campaign_summaries rcs ON r.run_id = rcs.run_id;

INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (1, datetime('now'));
"""

_SCHEMA_V2 = [
    "ALTER TABLE issues ADD COLUMN entity_name TEXT",
    "ALTER TABLE issues ADD COLUMN metrics_json TEXT",
    "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (2, datetime('now'))",
]

_SCHEMA_V3 = [
    """CREATE TABLE IF NOT EXISTS campaign_settings (
        campaign_id TEXT PRIMARY KEY,
        enabled     INTEGER NOT NULL DEFAULT 1,
        updated_at  TEXT NOT NULL
    )""",
    "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (3, datetime('now'))",
]

_SCHEMA_V6 = [
    """CREATE TABLE IF NOT EXISTS user_snapshots (
        run_id       INTEGER NOT NULL REFERENCES runs(run_id),
        instance_id  TEXT NOT NULL,
        login        TEXT NOT NULL,
        display_name TEXT,
        email        TEXT,
        user_profile TEXT,
        enabled      INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (run_id, instance_id, login)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_user_snapshots_instance_run ON user_snapshots(instance_id, run_id)",
    """CREATE TABLE IF NOT EXISTS project_snapshots (
        run_id       INTEGER NOT NULL REFERENCES runs(run_id),
        instance_id  TEXT NOT NULL,
        project_key  TEXT NOT NULL,
        name         TEXT,
        owner_login  TEXT,
        PRIMARY KEY (run_id, instance_id, project_key)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_project_snapshots_instance_run ON project_snapshots(instance_id, run_id)",
    """CREATE TABLE IF NOT EXISTS run_plugins (
        run_id    INTEGER NOT NULL REFERENCES runs(run_id),
        plugin_id TEXT NOT NULL,
        label     TEXT,
        version   TEXT,
        is_dev    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (run_id, plugin_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_run_plugins_plugin ON run_plugins(plugin_id)",
    """CREATE TABLE IF NOT EXISTS run_connections (
        run_id          INTEGER NOT NULL REFERENCES runs(run_id),
        connection_name TEXT NOT NULL,
        connection_type TEXT,
        PRIMARY KEY (run_id, connection_name)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_run_connections_type ON run_connections(connection_type)",
    "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (6, datetime('now'))",
]

_SCHEMA_V4 = [
    """CREATE TABLE IF NOT EXISTS campaign_exemptions (
        exemption_id  INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id   TEXT NOT NULL,
        entity_type   TEXT NOT NULL DEFAULT 'project',
        entity_key    TEXT NOT NULL,
        reason        TEXT,
        created_at    TEXT NOT NULL,
        UNIQUE(campaign_id, entity_type, entity_key)
    )""",
    "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (4, datetime('now'))",
]

_SCHEMA_V7 = [
    # -- V7: trends snapshot tables for datasets, recipes, GenAI metadata, git commits --
    """CREATE TABLE IF NOT EXISTS run_datasets (
        run_id          INTEGER NOT NULL REFERENCES runs(run_id),
        instance_id     TEXT NOT NULL,
        project_key     TEXT NOT NULL,
        dataset_name    TEXT NOT NULL,
        dataset_type    TEXT,
        connection_name TEXT,
        PRIMARY KEY (run_id, instance_id, project_key, dataset_name)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_run_datasets_type ON run_datasets(dataset_type)",
    """CREATE TABLE IF NOT EXISTS run_recipes (
        run_id       INTEGER NOT NULL REFERENCES runs(run_id),
        instance_id  TEXT NOT NULL,
        project_key  TEXT NOT NULL,
        recipe_name  TEXT NOT NULL,
        recipe_type  TEXT,
        PRIMARY KEY (run_id, instance_id, project_key, recipe_name)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_run_recipes_type ON run_recipes(recipe_type)",
    """CREATE TABLE IF NOT EXISTS run_llms (
        run_id        INTEGER NOT NULL REFERENCES runs(run_id),
        instance_id   TEXT NOT NULL,
        llm_id        TEXT NOT NULL,
        llm_type      TEXT,
        friendly_name TEXT,
        PRIMARY KEY (run_id, instance_id, llm_id)
    )""",
    """CREATE TABLE IF NOT EXISTS run_agents (
        run_id       INTEGER NOT NULL REFERENCES runs(run_id),
        instance_id  TEXT NOT NULL,
        project_key  TEXT NOT NULL,
        agent_id     TEXT NOT NULL,
        agent_name   TEXT,
        PRIMARY KEY (run_id, instance_id, project_key, agent_id)
    )""",
    """CREATE TABLE IF NOT EXISTS run_agent_tools (
        run_id       INTEGER NOT NULL REFERENCES runs(run_id),
        instance_id  TEXT NOT NULL,
        project_key  TEXT NOT NULL,
        tool_id      TEXT NOT NULL,
        tool_type    TEXT,
        PRIMARY KEY (run_id, instance_id, project_key, tool_id)
    )""",
    """CREATE TABLE IF NOT EXISTS run_knowledge_banks (
        run_id       INTEGER NOT NULL REFERENCES runs(run_id),
        instance_id  TEXT NOT NULL,
        project_key  TEXT NOT NULL,
        kb_id        TEXT NOT NULL,
        kb_name      TEXT,
        PRIMARY KEY (run_id, instance_id, project_key, kb_id)
    )""",
    """CREATE TABLE IF NOT EXISTS run_git_commits (
        run_id       INTEGER NOT NULL REFERENCES runs(run_id),
        instance_id  TEXT NOT NULL,
        project_key  TEXT NOT NULL,
        commit_hash  TEXT NOT NULL,
        author       TEXT,
        committed_at TEXT,
        PRIMARY KEY (run_id, instance_id, project_key, commit_hash)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_run_git_commits_author ON run_git_commits(author)",
    "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (7, datetime('now'))",
    # TODO: Add data retention/pruning for V7 tables (datasets, recipes, git_commits can grow large)
]

_SCHEMA_V8 = [
    "ALTER TABLE run_health_metrics ADD COLUMN general_settings_json TEXT",
    "ALTER TABLE run_health_metrics ADD COLUMN java_memory_raw TEXT",
    "ALTER TABLE run_health_metrics ADD COLUMN code_envs_json TEXT",
    "ALTER TABLE run_health_metrics ADD COLUMN log_errors_json TEXT",
    "ALTER TABLE run_health_metrics ADD COLUMN project_footprint_json TEXT",
    "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (8, datetime('now'))",
]

_SCHEMA_V9 = [
    """CREATE TABLE IF NOT EXISTS run_connection_health (
        run_id          INTEGER NOT NULL REFERENCES runs(run_id),
        connection_name TEXT NOT NULL,
        connection_type TEXT,
        status          TEXT NOT NULL,
        error_category  TEXT,
        error_message   TEXT,
        PRIMARY KEY (run_id, connection_name)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_run_conn_health_status ON run_connection_health(status)",
    """CREATE TABLE IF NOT EXISTS run_db_health (
        run_id          INTEGER NOT NULL REFERENCES runs(run_id),
        table_name      TEXT NOT NULL,
        schema_name     TEXT,
        table_size      INTEGER,
        row_count       INTEGER,
        dead_tuples     INTEGER,
        bloat_pct       REAL,
        last_vacuum     TEXT,
        last_autovacuum TEXT,
        last_analyze    TEXT,
        PRIMARY KEY (run_id, table_name)
    )""",
    "ALTER TABLE run_health_metrics ADD COLUMN db_health_json TEXT",
    "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (9, datetime('now'))",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class TrackingDB:
    """SQLite-backed tracking database for diagnostic runs and issue lifecycles."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                timeout=30,
            )
            self._conn.execute('PRAGMA journal_mode=WAL')
            self._conn.execute('PRAGMA foreign_keys=ON')
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_SCHEMA_V1)
            # V2 migration: add entity_name + metrics_json to issues
            v = self._conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0] or 1
            if v < 2:
                for stmt in _SCHEMA_V2:
                    try:
                        self._conn.execute(stmt)
                    except Exception:
                        pass  # column may already exist
                self._conn.commit()
            # V3 migration: campaign_settings table
            v = self._conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0] or 1
            if v < 3:
                for stmt in _SCHEMA_V3:
                    try:
                        self._conn.execute(stmt)
                    except Exception:
                        pass  # table may already exist
                self._conn.commit()
            # V4 migration: campaign_exemptions table
            v = self._conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0] or 1
            if v < 4:
                for stmt in _SCHEMA_V4:
                    try:
                        self._conn.execute(stmt)
                    except Exception:
                        pass  # table may already exist
                self._conn.commit()
            # V5 migration: snapshot data columns on run_health_metrics
            v = self._conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0] or 1
            if v < 5:
                for col in [
                    'plugins_json TEXT', 'connections_json TEXT',
                    'filesystem_mounts_json TEXT', 'user_profile_stats_json TEXT',
                    'os_info TEXT', 'spark_version TEXT',
                ]:
                    try:
                        self._conn.execute(f'ALTER TABLE run_health_metrics ADD COLUMN {col}')
                    except Exception:
                        pass  # column may already exist
                self._conn.execute("INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (5, datetime('now'))")
                self._conn.commit()
            # V6 migration: over-time snapshot tables
            v = self._conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0] or 1
            if v < 6:
                for stmt in _SCHEMA_V6:
                    try:
                        self._conn.execute(stmt)
                    except Exception:
                        pass  # table/index may already exist
                self._conn.commit()
            # V7 migration: trends snapshot tables (datasets, recipes, GenAI, git)
            v = self._conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0] or 1
            if v < 7:
                for stmt in _SCHEMA_V7:
                    try:
                        self._conn.execute(stmt)
                    except Exception:
                        pass  # table/index may already exist
                self._conn.commit()
            # V8 migration: extended snapshot columns
            v = self._conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0] or 1
            if v < 8:
                for stmt in _SCHEMA_V8:
                    try:
                        self._conn.execute(stmt)
                    except Exception:
                        pass  # column may already exist
                self._conn.commit()
            # V9 migration: connection health + DB health snapshots
            v = self._conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0] or 1
            if v < 9:
                for stmt in _SCHEMA_V9:
                    try:
                        self._conn.execute(stmt)
                    except Exception:
                        pass  # table/index/column may already exist
                self._conn.commit()
        return self._conn

    # ------------------------------------------------------------------
    # ingest_run — the core lifecycle engine
    # ------------------------------------------------------------------

    def ingest_run(
        self,
        instance_id: str,
        instance_url: str,
        install_id: Optional[str],
        node_id: Optional[str],
        run_data: Dict[str, Any],
        findings: List[Dict[str, Any]],
        users: List[Dict[str, Any]],
        projects: List[Dict[str, Any]],
        sections: Dict[str, Dict[str, Any]],
        health_metrics: Optional[Dict[str, Any]] = None,
        campaign_summaries: Optional[List[Dict[str, Any]]] = None,
        snapshot_data: Optional[Dict[str, Any]] = None,
        dss_host_id: str = 'local',
    ) -> int:
        """Ingest a complete run. Returns the new run_id."""
        now = _now_iso()

        with self._lock:
            conn = self._get_conn()
            conn.execute('BEGIN IMMEDIATE')
            try:
                # 1. UPSERT instance
                conn.execute(
                    """INSERT INTO instances (instance_id, instance_url, install_id, node_id, first_seen_at, last_seen_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(instance_id) DO UPDATE SET
                           instance_url = excluded.instance_url,
                           install_id = COALESCE(excluded.install_id, instances.install_id),
                           node_id = COALESCE(excluded.node_id, instances.node_id),
                           last_seen_at = excluded.last_seen_at""",
                    (instance_id, instance_url, install_id, node_id, now, now),
                )

                # 2. INSERT run
                cur = conn.execute(
                    """INSERT INTO runs (instance_id, dss_host_id, run_at, dss_version, python_version,
                           health_score, health_status, user_count, enabled_user_count,
                           project_count, code_env_count, plugin_count, connection_count,
                           cluster_count, coverage_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        instance_id,
                        dss_host_id or 'local',
                        now,
                        run_data.get('dss_version'),
                        run_data.get('python_version'),
                        run_data.get('health_score'),
                        run_data.get('health_status'),
                        run_data.get('user_count'),
                        run_data.get('enabled_user_count'),
                        run_data.get('project_count'),
                        run_data.get('code_env_count'),
                        run_data.get('plugin_count'),
                        run_data.get('connection_count'),
                        run_data.get('cluster_count'),
                        run_data.get('coverage_status', 'complete'),
                    ),
                )
                run_id = cur.lastrowid

                # 3. INSERT run_health_metrics
                if health_metrics:
                    snap = snapshot_data or {}
                    conn.execute(
                        """INSERT INTO run_health_metrics (
                               run_id, cpu_cores, memory_total_mb, memory_used_mb,
                               memory_available_mb, swap_total_mb, swap_used_mb,
                               max_filesystem_pct, max_filesystem_mount,
                               backend_heap_mb, jek_heap_mb, fek_heap_mb,
                               open_files_limit,
                               version_currency_score, system_capacity_score,
                               configuration_score, security_isolation_score,
                               license_named_users_pct, license_concurrent_users_pct,
                               license_projects_pct, license_connections_pct,
                               license_expiry_date,
                               plugins_json, connections_json,
                               filesystem_mounts_json, user_profile_stats_json,
                               os_info, spark_version,
                               general_settings_json, java_memory_raw,
                               code_envs_json, log_errors_json,
                               project_footprint_json, db_health_json)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            run_id,
                            health_metrics.get('cpu_cores'),
                            health_metrics.get('memory_total_mb'),
                            health_metrics.get('memory_used_mb'),
                            health_metrics.get('memory_available_mb'),
                            health_metrics.get('swap_total_mb'),
                            health_metrics.get('swap_used_mb'),
                            health_metrics.get('max_filesystem_pct'),
                            health_metrics.get('max_filesystem_mount'),
                            health_metrics.get('backend_heap_mb'),
                            health_metrics.get('jek_heap_mb'),
                            health_metrics.get('fek_heap_mb'),
                            health_metrics.get('open_files_limit'),
                            health_metrics.get('version_currency_score'),
                            health_metrics.get('system_capacity_score'),
                            health_metrics.get('configuration_score'),
                            health_metrics.get('security_isolation_score'),
                            health_metrics.get('license_named_users_pct'),
                            health_metrics.get('license_concurrent_users_pct'),
                            health_metrics.get('license_projects_pct'),
                            health_metrics.get('license_connections_pct'),
                            health_metrics.get('license_expiry_date'),
                            json.dumps(snap['plugins']) if snap.get('plugins') is not None else None,
                            json.dumps(snap['connections']) if snap.get('connections') is not None else None,
                            json.dumps(snap['filesystem_mounts']) if snap.get('filesystem_mounts') is not None else None,
                            json.dumps(snap['user_profile_stats']) if snap.get('user_profile_stats') is not None else None,
                            snap.get('os_info'),
                            snap.get('spark_version'),
                            json.dumps(snap['general_settings']) if snap.get('general_settings') is not None else None,
                            snap.get('java_memory_raw'),
                            json.dumps(snap['code_envs']) if snap.get('code_envs') is not None else None,
                            json.dumps(snap['log_errors']) if snap.get('log_errors') is not None else None,
                            json.dumps(snap['project_footprint']) if snap.get('project_footprint') is not None else None,
                            json.dumps(snap['db_health']['summary']) if isinstance(snap.get('db_health'), dict) and snap['db_health'].get('summary') else None,
                        ),
                    )

                # 4. INSERT run_campaign_summaries
                if campaign_summaries:
                    for cs in campaign_summaries:
                        conn.execute(
                            """INSERT INTO run_campaign_summaries (run_id, campaign_id, finding_count, recipient_count)
                               VALUES (?, ?, ?, ?)""",
                            (run_id, cs['campaign_id'], cs['finding_count'], cs['recipient_count']),
                        )

                # 5. UPSERT known_users
                for user in users:
                    login = user.get('login')
                    if not login:
                        continue
                    conn.execute(
                        """INSERT INTO known_users (instance_id, login, email, display_name, user_profile, enabled, first_seen_run, last_seen_run)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(instance_id, login) DO UPDATE SET
                               email = COALESCE(excluded.email, known_users.email),
                               display_name = COALESCE(excluded.display_name, known_users.display_name),
                               user_profile = COALESCE(excluded.user_profile, known_users.user_profile),
                               enabled = excluded.enabled,
                               last_seen_run = excluded.last_seen_run""",
                        (
                            instance_id,
                            login,
                            user.get('email'),
                            user.get('display_name') or user.get('displayName'),
                            user.get('user_profile') or user.get('userProfile'),
                            1 if user.get('enabled', True) else 0,
                            run_id,
                            run_id,
                        ),
                    )

                # 6. UPSERT known_projects
                for proj in projects:
                    pkey = proj.get('project_key') or proj.get('projectKey')
                    if not pkey:
                        continue
                    conn.execute(
                        """INSERT INTO known_projects (instance_id, project_key, name, owner_login, first_seen_run, last_seen_run)
                           VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT(instance_id, project_key) DO UPDATE SET
                               name = COALESCE(excluded.name, known_projects.name),
                               owner_login = COALESCE(excluded.owner_login, known_projects.owner_login),
                               last_seen_run = excluded.last_seen_run""",
                        (
                            instance_id,
                            pkey,
                            proj.get('name'),
                            proj.get('owner') or proj.get('owner_login'),
                            run_id,
                            run_id,
                        ),
                    )

                # 6a. INSERT user_snapshots (per-run roster for over-time tracking)
                for user in users:
                    login = user.get('login')
                    if not login:
                        continue
                    conn.execute(
                        """INSERT OR IGNORE INTO user_snapshots
                               (run_id, instance_id, login, display_name, email, user_profile, enabled)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (run_id, instance_id, login,
                         user.get('display_name') or user.get('displayName'),
                         user.get('email'),
                         user.get('user_profile') or user.get('userProfile'),
                         1 if user.get('enabled', True) else 0),
                    )

                # 6b. INSERT project_snapshots (per-run roster for over-time tracking)
                for proj in projects:
                    pkey = proj.get('project_key') or proj.get('projectKey')
                    if not pkey:
                        continue
                    conn.execute(
                        """INSERT OR IGNORE INTO project_snapshots
                               (run_id, instance_id, project_key, name, owner_login)
                           VALUES (?, ?, ?, ?, ?)""",
                        (run_id, instance_id, pkey,
                         proj.get('name'),
                         proj.get('owner') or proj.get('owner_login')),
                    )

                # 6c. INSERT run_plugins (normalized from snapshot_data)
                snap = snapshot_data or {}
                for plugin in (snap.get('plugins') or []):
                    if not isinstance(plugin, dict):
                        continue
                    pid = plugin.get('id')
                    if not pid:
                        continue
                    conn.execute(
                        """INSERT OR IGNORE INTO run_plugins
                               (run_id, plugin_id, label, version, is_dev)
                           VALUES (?, ?, ?, ?, ?)""",
                        (run_id, pid, plugin.get('label'),
                         plugin.get('installedVersion'),
                         1 if plugin.get('isDev') else 0),
                    )

                # 6d. INSERT run_connections (normalized from snapshot_data)
                snap_conns = snap.get('connections')
                conn_details = (snap_conns.get('details') or []) if isinstance(snap_conns, dict) else []
                for c in conn_details:
                    if not isinstance(c, dict):
                        continue
                    cname = c.get('name')
                    if not cname:
                        continue
                    conn.execute(
                        """INSERT OR IGNORE INTO run_connections
                               (run_id, connection_name, connection_type)
                           VALUES (?, ?, ?)""",
                        (run_id, cname, c.get('type')),
                    )

                # 6e. INSERT run_datasets (V7 trends snapshots)
                for ds in (snap.get('datasets') or []):
                    if not isinstance(ds, dict):
                        continue
                    ds_name = ds.get('dataset_name') or ds.get('name')
                    if not ds_name:
                        continue
                    conn.execute(
                        """INSERT OR IGNORE INTO run_datasets
                               (run_id, instance_id, project_key, dataset_name, dataset_type, connection_name)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (run_id, instance_id,
                         ds.get('project_key', ''),
                         ds_name,
                         ds.get('dataset_type') or ds.get('type'),
                         ds.get('connection_name')),
                    )

                # 6f. INSERT run_recipes (V7 trends snapshots)
                for rec in (snap.get('recipes') or []):
                    if not isinstance(rec, dict):
                        continue
                    rname = rec.get('recipe_name') or rec.get('name')
                    if not rname:
                        continue
                    conn.execute(
                        """INSERT OR IGNORE INTO run_recipes
                               (run_id, instance_id, project_key, recipe_name, recipe_type)
                           VALUES (?, ?, ?, ?, ?)""",
                        (run_id, instance_id,
                         rec.get('project_key', ''),
                         rname,
                         rec.get('recipe_type') or rec.get('type')),
                    )

                # 6g. INSERT run_llms (V7 trends snapshots)
                for llm in (snap.get('llms') or []):
                    if not isinstance(llm, dict):
                        continue
                    lid = llm.get('llm_id') or llm.get('id')
                    if not lid:
                        continue
                    conn.execute(
                        """INSERT OR IGNORE INTO run_llms
                               (run_id, instance_id, llm_id, llm_type, friendly_name)
                           VALUES (?, ?, ?, ?, ?)""",
                        (run_id, instance_id, lid,
                         llm.get('llm_type') or llm.get('type'),
                         llm.get('friendly_name') or llm.get('friendlyName')),
                    )

                # 6h. INSERT run_agents (V7 trends snapshots)
                for ag in (snap.get('agents') or []):
                    if not isinstance(ag, dict):
                        continue
                    aid = ag.get('agent_id') or ag.get('id')
                    if not aid:
                        continue
                    conn.execute(
                        """INSERT OR IGNORE INTO run_agents
                               (run_id, instance_id, project_key, agent_id, agent_name)
                           VALUES (?, ?, ?, ?, ?)""",
                        (run_id, instance_id,
                         ag.get('project_key', ''),
                         aid,
                         ag.get('agent_name') or ag.get('name')),
                    )

                # 6i. INSERT run_agent_tools (V7 trends snapshots)
                for at in (snap.get('agent_tools') or []):
                    if not isinstance(at, dict):
                        continue
                    tid = at.get('tool_id') or at.get('id')
                    if not tid:
                        continue
                    conn.execute(
                        """INSERT OR IGNORE INTO run_agent_tools
                               (run_id, instance_id, project_key, tool_id, tool_type)
                           VALUES (?, ?, ?, ?, ?)""",
                        (run_id, instance_id,
                         at.get('project_key', ''),
                         tid,
                         at.get('tool_type') or at.get('type')),
                    )

                # 6j. INSERT run_knowledge_banks (V7 trends snapshots)
                for kb in (snap.get('knowledge_banks') or []):
                    if not isinstance(kb, dict):
                        continue
                    kid = kb.get('kb_id') or kb.get('id')
                    if not kid:
                        continue
                    conn.execute(
                        """INSERT OR IGNORE INTO run_knowledge_banks
                               (run_id, instance_id, project_key, kb_id, kb_name)
                           VALUES (?, ?, ?, ?, ?)""",
                        (run_id, instance_id,
                         kb.get('project_key', ''),
                         kid,
                         kb.get('kb_name') or kb.get('name')),
                    )

                # 6k. INSERT run_git_commits (V7 trends snapshots)
                for gc in (snap.get('git_commits') or []):
                    if not isinstance(gc, dict):
                        continue
                    chash = gc.get('commit_hash') or gc.get('commit')
                    if not chash:
                        continue
                    conn.execute(
                        """INSERT OR IGNORE INTO run_git_commits
                               (run_id, instance_id, project_key, commit_hash, author, committed_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (run_id, instance_id,
                         gc.get('project_key', ''),
                         chash,
                         gc.get('author'),
                         gc.get('committed_at')),
                    )

                # 6l. INSERT run_connection_health (V9 trends snapshots)
                for ch in (snap.get('connection_health') or []):
                    if not isinstance(ch, dict):
                        continue
                    cname = ch.get('name')
                    if not cname:
                        continue
                    conn.execute(
                        """INSERT OR IGNORE INTO run_connection_health
                               (run_id, connection_name, connection_type, status, error_category, error_message)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (run_id, cname, ch.get('type'),
                         ch.get('status', 'skipped'),
                         ch.get('error_category'), ch.get('error_message')),
                    )

                # 6m. INSERT run_db_health (V9 trends snapshots)
                db_health = snap.get('db_health')
                if isinstance(db_health, dict):
                    for tbl in (db_health.get('tables') or []):
                        if not isinstance(tbl, dict):
                            continue
                        tname = tbl.get('table_name')
                        if not tname:
                            continue
                        conn.execute(
                            """INSERT OR IGNORE INTO run_db_health
                                   (run_id, table_name, schema_name, table_size, row_count,
                                    dead_tuples, bloat_pct, last_vacuum, last_autovacuum, last_analyze)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (run_id, tname, tbl.get('schema_name'),
                             tbl.get('table_size'), tbl.get('row_count'),
                             tbl.get('dead_tuples'), tbl.get('bloat_pct'),
                             tbl.get('last_vacuum'), tbl.get('last_autovacuum'),
                             tbl.get('last_analyze')),
                        )

                # 7. INSERT findings + lifecycle issues
                finding_keys_this_run = set()  # (campaign_id, entity_type, entity_key)
                entities_this_run = set()      # (entity_type, entity_key)
                for f in findings:
                    campaign_id = f.get('campaign_id', '')
                    entity_type = f.get('entity_type', '')
                    entity_key = f.get('entity_key', '')
                    owner_login = f.get('owner_login', '')
                    if not campaign_id or not entity_key or not owner_login:
                        continue

                    finding_keys_this_run.add((campaign_id, entity_type, entity_key))
                    entities_this_run.add((entity_type, entity_key))

                    metrics = f.get('metrics_json')
                    if isinstance(metrics, dict):
                        metrics = json.dumps(metrics)

                    conn.execute(
                        """INSERT OR IGNORE INTO findings
                               (run_id, campaign_id, entity_type, entity_key, entity_name, owner_login, owner_email, metrics_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (run_id, campaign_id, entity_type, entity_key,
                         f.get('entity_name'), owner_login, f.get('owner_email'), metrics),
                    )

                    # Issue lifecycle
                    row = conn.execute(
                        """SELECT issue_id, status FROM issues
                           WHERE instance_id = ? AND campaign_id = ? AND entity_type = ? AND entity_key = ?""",
                        (instance_id, campaign_id, entity_type, entity_key),
                    ).fetchone()

                    if row is None:
                        conn.execute(
                            """INSERT INTO issues
                                   (instance_id, campaign_id, entity_type, entity_key,
                                    owner_login, owner_email, status,
                                    first_detected_run, first_detected_at,
                                    last_detected_run, last_detected_at,
                                    entity_name, metrics_json)
                               VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)""",
                            (instance_id, campaign_id, entity_type, entity_key,
                             owner_login, f.get('owner_email'),
                             run_id, now, run_id, now,
                             f.get('entity_name'), metrics),
                        )
                    elif row['status'] == 'resolved':
                        conn.execute(
                            """UPDATE issues SET
                                   status = 'regressed',
                                   times_regressed = times_regressed + 1,
                                   last_detected_run = ?,
                                   last_detected_at = ?,
                                   resolved_run = NULL,
                                   resolved_at = NULL,
                                   resolution_reason = NULL,
                                   owner_login = ?,
                                   owner_email = ?,
                                   entity_name = ?,
                                   metrics_json = ?
                               WHERE issue_id = ?""",
                            (run_id, now, owner_login, f.get('owner_email'),
                             f.get('entity_name'), metrics, row['issue_id']),
                        )
                    else:
                        conn.execute(
                            """UPDATE issues SET
                                   last_detected_run = ?,
                                   last_detected_at = ?,
                                   owner_login = ?,
                                   owner_email = ?,
                                   entity_name = ?,
                                   metrics_json = ?
                               WHERE issue_id = ?""",
                            (run_id, now, owner_login, f.get('owner_email'),
                             f.get('entity_name'), metrics, row['issue_id']),
                        )

                # 8. INSERT run_sections
                for section_key, sec_info in sections.items():
                    sec_status = sec_info.get('status', 'success')
                    is_complete = 1 if sec_status == 'success' else 0
                    conn.execute(
                        """INSERT OR IGNORE INTO run_sections (run_id, section_key, status, is_complete, error_message)
                           VALUES (?, ?, ?, ?, ?)""",
                        (run_id, section_key, sec_status, is_complete, sec_info.get('error_message')),
                    )

                # Update coverage_status based on section results
                all_statuses = [s.get('status', 'success') for s in sections.values()]
                if all(s == 'success' for s in all_statuses):
                    coverage = 'complete'
                elif any(s == 'error' for s in all_statuses):
                    coverage = 'failed'
                else:
                    coverage = 'partial'
                conn.execute(
                    'UPDATE runs SET coverage_status = ? WHERE run_id = ?',
                    (coverage, run_id),
                )

                # 9. Coverage-gated auto-resolve
                # Build set of safe campaigns
                complete_sections = set()
                for section_key, sec_info in sections.items():
                    if sec_info.get('status') == 'success':
                        complete_sections.add(section_key)

                safe_campaigns = set()
                for campaign_id, required in CAMPAIGN_REQUIRED_SECTIONS.items():
                    if all(s in complete_sections for s in required):
                        safe_campaigns.add(campaign_id)

                if safe_campaigns:
                    placeholders = ','.join('?' * len(safe_campaigns))
                    open_issues = conn.execute(
                        f"""SELECT issue_id, campaign_id, entity_type, entity_key
                            FROM issues
                            WHERE instance_id = ?
                              AND status IN ('open', 'regressed')
                              AND campaign_id IN ({placeholders})""",
                        [instance_id] + sorted(safe_campaigns),
                    ).fetchall()

                    for issue in open_issues:
                        key = (issue['campaign_id'], issue['entity_type'], issue['entity_key'])
                        if key in finding_keys_this_run:
                            continue  # still present, don't resolve

                        # Determine resolution reason: if the entity still
                        # appears in any finding this run → whitelisted (condition
                        # cleared); otherwise → deleted (entity gone).
                        etype = issue['entity_type']
                        ekey = issue['entity_key']
                        if (etype, ekey) in entities_this_run:
                            reason = 'condition_cleared'
                        else:
                            reason = 'entity_deleted'

                        conn.execute(
                            """UPDATE issues SET
                                   status = 'resolved',
                                   resolved_run = ?,
                                   resolved_at = ?,
                                   resolution_reason = ?
                               WHERE issue_id = ?""",
                            (run_id, now, reason, issue['issue_id']),
                        )

                conn.commit()
                return run_id

            except Exception:
                conn.rollback()
                raise

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def list_runs(self, instance_id: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._get_conn()
            if instance_id:
                rows = conn.execute(
                    'SELECT * FROM runs WHERE instance_id = ? ORDER BY run_id DESC LIMIT ? OFFSET ?',
                    (instance_id, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT * FROM runs ORDER BY run_id DESC LIMIT ? OFFSET ?',
                    (limit, offset),
                ).fetchall()
            return [dict(r) for r in rows]

    def find_closest_run(self, instance_id: str, target_iso: str) -> Optional[int]:
        """Find the run_id closest to the target ISO datetime (at or before)."""
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                'SELECT run_id FROM runs WHERE instance_id = ? AND run_at <= ? '
                'ORDER BY run_at DESC LIMIT 1',
                (instance_id, target_iso),
            ).fetchone()
            if row:
                return row['run_id']
            # Fallback: get the earliest run if target is before all runs
            row = conn.execute(
                'SELECT run_id FROM runs WHERE instance_id = ? ORDER BY run_at ASC LIMIT 1',
                (instance_id,),
            ).fetchone()
            return row['run_id'] if row else None

    def get_trends_snapshot(self, run_id: int) -> Optional[Dict[str, Any]]:
        """Return all snapshot data for a given run_id (for trends comparison)."""
        with self._lock:
            conn = self._get_conn()
            run = conn.execute('SELECT * FROM runs WHERE run_id = ?', (run_id,)).fetchone()
            if not run:
                return None
            result: Dict[str, Any] = {'run': dict(run)}

            # Health metrics
            hm = conn.execute(
                'SELECT * FROM run_health_metrics WHERE run_id = ?', (run_id,),
            ).fetchone()
            if hm:
                hm_dict = dict(hm)
                for jcol in ('plugins_json', 'connections_json', 'filesystem_mounts_json',
                              'user_profile_stats_json', 'general_settings_json',
                              'code_envs_json', 'log_errors_json', 'project_footprint_json',
                              'db_health_json'):
                    if hm_dict.get(jcol):
                        try:
                            hm_dict[jcol] = json.loads(hm_dict[jcol])
                        except (json.JSONDecodeError, TypeError):
                            pass
                result['health_metrics'] = hm_dict
            else:
                result['health_metrics'] = None

            for spec in TREND_SNAPSHOT_TABLES:
                try:
                    result[spec.key] = [dict(r) for r in conn.execute(
                        f'SELECT * FROM {spec.table} WHERE run_id = ?', (run_id,),
                    ).fetchall()]
                except Exception:
                    result[spec.key] = []

            return result

    def get_run(self, run_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute('SELECT * FROM runs WHERE run_id = ?', (run_id,)).fetchone()
            if not row:
                return None
            result = dict(row)

            # Campaign summaries
            summaries = conn.execute(
                'SELECT * FROM run_campaign_summaries WHERE run_id = ?', (run_id,),
            ).fetchall()
            result['campaign_summaries'] = [dict(s) for s in summaries]

            # Health metrics
            hm = conn.execute(
                'SELECT * FROM run_health_metrics WHERE run_id = ?', (run_id,),
            ).fetchone()
            if hm:
                hm_dict = dict(hm)
                for jcol in ('plugins_json', 'connections_json', 'filesystem_mounts_json',
                              'user_profile_stats_json', 'general_settings_json',
                              'code_envs_json', 'log_errors_json', 'project_footprint_json'):
                    if hm_dict.get(jcol):
                        try:
                            hm_dict[jcol] = json.loads(hm_dict[jcol])
                        except (json.JSONDecodeError, TypeError):
                            pass
                result['health_metrics'] = hm_dict
            else:
                result['health_metrics'] = None

            # Sections
            secs = conn.execute(
                'SELECT * FROM run_sections WHERE run_id = ?', (run_id,),
            ).fetchall()
            result['sections'] = [dict(s) for s in secs]

            return result

    def list_issues(
        self,
        instance_id: Optional[str] = None,
        status: Optional[str] = None,
        campaign_id: Optional[str] = None,
        owner_login: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._get_conn()
            conditions = []
            params: list = []
            if instance_id:
                conditions.append('instance_id = ?')
                params.append(instance_id)
            if status:
                conditions.append('status = ?')
                params.append(status)
            if campaign_id:
                conditions.append('campaign_id = ?')
                params.append(campaign_id)
            if owner_login:
                conditions.append('owner_login = ?')
                params.append(owner_login)

            where = (' WHERE ' + ' AND '.join(conditions)) if conditions else ''
            params.extend([limit, offset])
            rows = conn.execute(
                f'SELECT * FROM issues{where} ORDER BY issue_id DESC LIMIT ? OFFSET ?',
                params,
            ).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                if d.get('metrics_json'):
                    try:
                        d['metrics_json'] = json.loads(d['metrics_json'])
                    except (json.JSONDecodeError, TypeError):
                        pass
                results.append(d)
            return results

    def get_issue(self, issue_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute('SELECT * FROM issues WHERE issue_id = ?', (issue_id,)).fetchone()
            if not row:
                return None
            result = dict(row)

            # Finding history
            findings = conn.execute(
                """SELECT f.* FROM findings f
                   WHERE f.campaign_id = ? AND f.entity_type = ? AND f.entity_key = ?
                   ORDER BY f.run_id DESC""",
                (row['campaign_id'], row['entity_type'], row['entity_key']),
            ).fetchall()
            result['finding_history'] = [dict(f) for f in findings]

            # Email history
            emails = conn.execute(
                """SELECT oe.* FROM outreach_emails oe
                   JOIN outreach_email_issues oei ON oe.email_id = oei.email_id
                   WHERE oei.issue_id = ?
                   ORDER BY oe.sent_at DESC""",
                (issue_id,),
            ).fetchall()
            result['email_history'] = [dict(e) for e in emails]

            # Notes
            notes = conn.execute(
                'SELECT * FROM issue_notes WHERE issue_id = ? ORDER BY created_at DESC',
                (issue_id,),
            ).fetchall()
            result['notes'] = [dict(n) for n in notes]

            return result

    def list_all_user_compliance(self, instance_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._get_conn()
            if instance_id:
                rows = conn.execute(
                    'SELECT * FROM v_user_compliance WHERE owner_login IN '
                    '(SELECT DISTINCT owner_login FROM issues WHERE instance_id = ?)',
                    (instance_id,),
                ).fetchall()
            else:
                rows = conn.execute('SELECT * FROM v_user_compliance').fetchall()
            return [dict(r) for r in rows]

    def get_user_compliance(self, login: str) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                'SELECT * FROM v_user_compliance WHERE owner_login = ?',
                (login,),
            ).fetchall()
            return [dict(r) for r in rows]

    def add_issue_note(self, issue_id: int, note: str, created_by: Optional[str] = None) -> int:
        now = _now_iso()
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                'INSERT INTO issue_notes (issue_id, created_at, created_by, note) VALUES (?, ?, ?, ?)',
                (issue_id, now, created_by, note),
            )
            conn.commit()
            return cur.lastrowid

    def compare_runs(self, run_id_1: int, run_id_2: int) -> Dict[str, Any]:
        with self._lock:
            conn = self._get_conn()
            r1 = conn.execute('SELECT * FROM runs WHERE run_id = ?', (run_id_1,)).fetchone()
            r2 = conn.execute('SELECT * FROM runs WHERE run_id = ?', (run_id_2,)).fetchone()
            if not r1 or not r2:
                return {'error': 'One or both runs not found'}

            # Campaign summaries for both
            s1 = conn.execute(
                'SELECT campaign_id, finding_count, recipient_count FROM run_campaign_summaries WHERE run_id = ?',
                (run_id_1,),
            ).fetchall()
            s2 = conn.execute(
                'SELECT campaign_id, finding_count, recipient_count FROM run_campaign_summaries WHERE run_id = ?',
                (run_id_2,),
            ).fetchall()

            map1 = {r['campaign_id']: dict(r) for r in s1}
            map2 = {r['campaign_id']: dict(r) for r in s2}
            all_campaigns = sorted(set(list(map1.keys()) + list(map2.keys())))

            deltas = []
            for cid in all_campaigns:
                fc1 = map1.get(cid, {}).get('finding_count', 0)
                fc2 = map2.get(cid, {}).get('finding_count', 0)
                rc1 = map1.get(cid, {}).get('recipient_count', 0)
                rc2 = map2.get(cid, {}).get('recipient_count', 0)
                deltas.append({
                    'campaign_id': cid,
                    'run1_findings': fc1,
                    'run2_findings': fc2,
                    'finding_delta': fc2 - fc1,
                    'run1_recipients': rc1,
                    'run2_recipients': rc2,
                    'recipient_delta': rc2 - rc1,
                })

            # Issues resolved between runs
            resolved = conn.execute(
                """SELECT * FROM issues
                   WHERE resolved_run = ? AND first_detected_run <= ?""",
                (run_id_2, run_id_1),
            ).fetchall()

            # Issues opened between runs
            opened = conn.execute(
                """SELECT * FROM issues
                   WHERE first_detected_run = ?""",
                (run_id_2,),
            ).fetchall()

            # Issues regressed between runs
            regressed = conn.execute(
                """SELECT * FROM issues
                   WHERE status = 'regressed' AND last_detected_run = ?""",
                (run_id_2,),
            ).fetchall()

            return {
                'run1': dict(r1),
                'run2': dict(r2),
                'campaign_deltas': deltas,
                'issues_resolved': [dict(r) for r in resolved],
                'issues_opened': [dict(r) for r in opened],
                'issues_regressed': [dict(r) for r in regressed],
            }

    # ── Exhaustive compare helpers ──

    def _query_ds_rows(self, conn, table: str, run_id: int) -> List[Dict[str, Any]]:
        """Query all rows for a run from a per-run dataset table."""
        try:
            return [dict(r) for r in conn.execute(
                f'SELECT * FROM {table} WHERE run_id = ?', (run_id,)
            ).fetchall()]
        except Exception:
            return []

    def _query_ds_count(self, conn, table: str, run_id: int) -> int:
        try:
            return conn.execute(f'SELECT COUNT(*) FROM {table} WHERE run_id = ?', (run_id,)).fetchone()[0]
        except Exception:
            return 0

    def _query_lifecycle(self, conn, table: str, instance_id: str) -> List[Dict[str, Any]]:
        try:
            if table in ('issues', 'known_users', 'known_projects'):
                return [dict(r) for r in conn.execute(
                    f'SELECT * FROM {table} WHERE instance_id = ?', (instance_id,)
                ).fetchall()]
            elif table == 'outreach_emails':
                return [dict(r) for r in conn.execute(
                    f'SELECT * FROM {table} WHERE run_id IN '
                    f'(SELECT run_id FROM runs WHERE instance_id = ?)', (instance_id,)
                ).fetchall()]
            elif table == 'outreach_email_issues':
                return [dict(r) for r in conn.execute(
                    f'SELECT * FROM {table} WHERE email_id IN '
                    f'(SELECT email_id FROM outreach_emails WHERE run_id IN '
                    f'(SELECT run_id FROM runs WHERE instance_id = ?))', (instance_id,)
                ).fetchall()]
            elif table == 'issue_notes':
                return [dict(r) for r in conn.execute(
                    f'SELECT * FROM {table} WHERE issue_id IN '
                    f'(SELECT issue_id FROM issues WHERE instance_id = ?)', (instance_id,)
                ).fetchall()]
            return [dict(r) for r in conn.execute(f'SELECT * FROM {table}').fetchall()]
        except Exception:
            return []

    # ── Step 23: compare_runs_full ──

    def compare_runs_full(self, run_id_1: int, run_id_2: int) -> Dict[str, Any]:
        from compare_registry import (
            DATASET_REGISTRY, SUPPORT_CURRENT_ONLY, SUPPORT_LIFECYCLE,
            KIND_SCALAR, KIND_KEYED_TABLE, KIND_INTERVAL_EVENTS, KIND_METADATA,
            check_run_has_v6_data, check_run_has_v7_data, dataset_available_for_run,
            compare_scalar, diff_keyed_tables,
            classify_issues, classify_interval_events, classify_issue_notes,
            classify_known_entities, build_summary_stats, build_coverage_warnings,
        )
        with self._lock:
            conn = self._get_conn()
            r1 = conn.execute('SELECT * FROM runs WHERE run_id = ?', (run_id_1,)).fetchone()
            r2 = conn.execute('SELECT * FROM runs WHERE run_id = ?', (run_id_2,)).fetchone()
            if not r1 or not r2:
                return {'error': 'One or both runs not found'}
            r1, r2 = dict(r1), dict(r2)
            swapped = False
            if run_id_1 < run_id_2:
                run_id_1, run_id_2 = run_id_2, run_id_1
                r1, r2 = r2, r1
                swapped = True
            instance_id = r1.get('instance_id')

            # Row counts for availability heuristics
            rc1: Dict[str, int] = {}
            rc2: Dict[str, int] = {}
            for e in DATASET_REGISTRY:
                if e['support'] in (SUPPORT_CURRENT_ONLY, SUPPORT_LIFECYCLE):
                    continue
                t = e['table']
                rc1[t] = self._query_ds_count(conn, t, run_id_1)
                rc2[t] = self._query_ds_count(conn, t, run_id_2)

            h6_1 = check_run_has_v6_data(rc1)
            h6_2 = check_run_has_v6_data(rc2)
            h7_1 = check_run_has_v7_data(rc1)
            h7_2 = check_run_has_v7_data(rc2)

            first_v6_run_id = None
            first_v7_run_id = None
            if h6_1 or h6_2:
                row = conn.execute(
                    "SELECT MIN(run_id) FROM user_snapshots WHERE run_id IN "
                    "(SELECT run_id FROM runs WHERE instance_id = ?)", (instance_id,)
                ).fetchone()
                first_v6_run_id = row[0] if row and row[0] else None
            if h7_1 or h7_2:
                row = conn.execute(
                    "SELECT MIN(run_id) FROM run_datasets WHERE run_id IN "
                    "(SELECT run_id FROM runs WHERE instance_id = ?)", (instance_id,)
                ).fetchone()
                first_v7_run_id = row[0] if row and row[0] else None

            datasets = []
            for e in DATASET_REGISTRY:
                a1 = dataset_available_for_run(e, r1, rc1, h6_1, h7_1, first_v6_run_id, first_v7_run_id)
                a2 = dataset_available_for_run(e, r2, rc2, h6_2, h7_2, first_v6_run_id, first_v7_run_id)
                ds: Dict[str, Any] = {
                    'datasetId': e['dataset_id'], 'label': e['label'],
                    'category': e['category'], 'kind': e['kind'],
                    'support': e['support'],
                    'availableInRun1': a1, 'availableInRun2': a2,
                    'run1Count': 0, 'run2Count': 0,
                    'added': 0, 'removed': 0, 'changed': 0, 'unchanged': 0,
                    'notes': None,
                }

                if e['support'] == SUPPORT_CURRENT_ONLY:
                    try:
                        total = conn.execute(f"SELECT COUNT(*) FROM {e['table']}").fetchone()[0]
                    except Exception:
                        total = 0
                    ds['run1Count'] = total
                    ds['run2Count'] = total
                    ds['notes'] = 'Current-only: not historically versioned'

                elif e['kind'] == KIND_SCALAR:
                    if a1 and a2:
                        row1 = conn.execute(f"SELECT * FROM {e['table']} WHERE run_id = ?", (run_id_1,)).fetchone()
                        row2 = conn.execute(f"SELECT * FROM {e['table']} WHERE run_id = ?", (run_id_2,)).fetchone()
                        row1 = dict(row1) if row1 else None
                        row2 = dict(row2) if row2 else None
                        ds['run1Count'] = 1 if row1 else 0
                        ds['run2Count'] = 1 if row2 else 0
                        if row1 and row2:
                            _, ch, unch = compare_scalar(row1, row2, e['columns'])
                            ds['changed'] = ch
                            ds['unchanged'] = unch
                    else:
                        ds['run1Count'] = 1 if a1 else 0
                        ds['run2Count'] = 1 if a2 else 0
                        ds['notes'] = 'Not available for %s' % ('run 2' if a1 else 'run 1')

                elif e['kind'] == KIND_KEYED_TABLE:
                    if a1 and a2:
                        rows1 = self._query_ds_rows(conn, e['table'], run_id_1)
                        rows2 = self._query_ds_rows(conn, e['table'], run_id_2)
                        ds.update(diff_keyed_tables(rows1, rows2, e['key_fields'], e['columns']))
                    elif a1:
                        ds['run1Count'] = rc1.get(e['table'], 0)
                        ds['added'] = ds['run1Count']
                        ds['notes'] = 'Not available for run 2'
                    elif a2:
                        ds['run2Count'] = rc2.get(e['table'], 0)
                        ds['removed'] = ds['run2Count']
                        ds['notes'] = 'Not available for run 1'
                    else:
                        ds['notes'] = 'Not available for either run'

                elif e['kind'] == KIND_INTERVAL_EVENTS:
                    did = e['dataset_id']
                    if did == 'issues':
                        all_rows = self._query_lifecycle(conn, 'issues', instance_id)
                        _, ic = classify_issues(all_rows, run_id_1, run_id_2)
                        ds['run1Count'] = ic.get('existed_in_both', 0) + ic.get('opened_between_runs', 0) + ic.get('visible_only_in_run1', 0)
                        ds['run2Count'] = ic.get('existed_in_both', 0) + ic.get('visible_only_in_run2', 0) + ic.get('resolved_between_runs', 0)
                        ds['added'] = ic.get('opened_between_runs', 0)
                        ds['removed'] = ic.get('resolved_between_runs', 0)
                        ds['changed'] = ic.get('regressed_between_runs', 0)
                        ds['unchanged'] = ic.get('existed_in_both', 0)
                    elif did == 'outreach_emails':
                        all_rows = self._query_lifecycle(conn, 'outreach_emails', instance_id)
                        _, ec = classify_interval_events(all_rows, run_id_1, run_id_2, 'run_id')
                        ds['run1Count'] = ec.get('before_run2', 0) + ec.get('event_between_runs', 0)
                        ds['run2Count'] = ec.get('before_run2', 0)
                        ds['added'] = ec.get('event_between_runs', 0)
                    elif did == 'outreach_email_issues':
                        joined_sql = (
                            "SELECT oei.email_id, oei.issue_id, oe.run_id, "
                            "oe.campaign_id, oe.recipient_login, oe.sent_at, oe.status AS email_status, "
                            "i.entity_type, i.entity_key, i.status AS issue_status "
                            "FROM outreach_email_issues oei "
                            "JOIN outreach_emails oe ON oei.email_id = oe.email_id "
                            "JOIN issues i ON oei.issue_id = i.issue_id "
                            "WHERE oe.run_id IN (SELECT run_id FROM runs WHERE instance_id = ?)"
                        )
                        joined_rows = [dict(r) for r in conn.execute(joined_sql, (instance_id,)).fetchall()]
                        _, ec = classify_interval_events(joined_rows, run_id_1, run_id_2, 'run_id')
                        ds['run1Count'] = ec.get('before_run2', 0) + ec.get('event_between_runs', 0)
                        ds['run2Count'] = ec.get('before_run2', 0)
                        ds['added'] = ec.get('event_between_runs', 0)
                        ds['notes'] = 'Joined: email + issue details'
                    elif did in ('known_users', 'known_projects'):
                        all_rows = self._query_lifecycle(conn, e['table'], instance_id)
                        kc = classify_known_entities(all_rows, run_id_1, run_id_2, e['key_fields'])
                        ds['run1Count'] = kc.get('run1Count', 0)
                        ds['run2Count'] = kc.get('run2Count', 0)
                        ds['added'] = kc.get('added', 0)
                        ds['removed'] = kc.get('removed', 0)
                        ds['unchanged'] = kc.get('existed_in_both', 0)
                    elif did == 'issue_notes':
                        all_rows = self._query_lifecycle(conn, 'issue_notes', instance_id)
                        _, nc = classify_issue_notes(all_rows, r1.get('run_at', ''), r2.get('run_at', ''))
                        ds['run1Count'] = nc.get('visible_at_run1', 0)
                        ds['run2Count'] = nc.get('visible_at_run2', 0)
                        ds['added'] = nc.get('created_between_runs', 0)

                datasets.append(ds)

            sections1 = self._query_ds_rows(conn, 'run_sections', run_id_1)
            sections2 = self._query_ds_rows(conn, 'run_sections', run_id_2)

            return {
                'run1': r1,
                'run2': r2,
                'swapped': swapped,
                'summary': build_summary_stats(r1, r2),
                'datasets': datasets,
                'coverageWarnings': build_coverage_warnings(r1, r2, sections1, sections2),
            }

    # ── Step 24: get_compare_dataset_detail ──

    def get_compare_dataset_detail(
        self, run_id_1: int, run_id_2: int, dataset_id: str,
        change_type: str = 'all', page: int = 1, page_size: int = 100,
        search: Optional[str] = None, sort: Optional[str] = None,
    ) -> Dict[str, Any]:
        from compare_registry import (
            REGISTRY_BY_ID, KIND_SCALAR, KIND_KEYED_TABLE,
            KIND_INTERVAL_EVENTS, KIND_METADATA,
            compare_scalar, diff_keyed_table_detail,
            classify_issues, classify_interval_events, classify_issue_notes,
            reconstruct_as_of, map_filter_to_lifecycle,
        )
        entry = REGISTRY_BY_ID.get(dataset_id)
        if not entry:
            return {'error': f'Unknown dataset: {dataset_id}'}

        with self._lock:
            conn = self._get_conn()
            r1 = conn.execute('SELECT * FROM runs WHERE run_id = ?', (run_id_1,)).fetchone()
            r2 = conn.execute('SELECT * FROM runs WHERE run_id = ?', (run_id_2,)).fetchone()
            if not r1 or not r2:
                return {'error': 'One or both runs not found'}
            r1, r2 = dict(r1), dict(r2)
            if run_id_1 < run_id_2:
                run_id_1, run_id_2 = run_id_2, run_id_1
                r1, r2 = r2, r1
            instance_id = r1.get('instance_id')

            result: Dict[str, Any] = {
                'datasetId': dataset_id,
                'columns': entry['columns'],
                'keyFields': entry['key_fields'],
                'support': entry['support'],
                'kind': entry['kind'],
                'notes': None,
            }

            if entry['kind'] == KIND_SCALAR:
                row1 = conn.execute(f"SELECT * FROM {entry['table']} WHERE run_id = ?", (run_id_1,)).fetchone()
                row2 = conn.execute(f"SELECT * FROM {entry['table']} WHERE run_id = ?", (run_id_2,)).fetchone()
                fields, ch, unch = compare_scalar(
                    dict(row1) if row1 else None,
                    dict(row2) if row2 else None,
                    entry['columns'],
                )
                result['fields'] = fields
                result['changed'] = ch
                result['unchanged'] = unch
                result['page'] = 1
                result['pageSize'] = len(fields)
                result['totalRows'] = len(fields)

            elif entry['kind'] == KIND_KEYED_TABLE:
                rows1 = self._query_ds_rows(conn, entry['table'], run_id_1)
                rows2 = self._query_ds_rows(conn, entry['table'], run_id_2)
                detail = diff_keyed_table_detail(
                    rows1, rows2, entry['key_fields'], entry['columns'],
                    change_type=change_type, search=search, sort=sort,
                    page=page, page_size=page_size,
                )
                result.update(detail)
                result['changeType'] = change_type

            elif entry['kind'] == KIND_INTERVAL_EVENTS:
                did = entry['dataset_id']
                rows: List[Dict[str, Any]] = []

                if did == 'issues':
                    all_rows = self._query_lifecycle(conn, 'issues', instance_id)
                    rows, _ = classify_issues(all_rows, run_id_1, run_id_2)
                elif did == 'outreach_emails':
                    all_rows = self._query_lifecycle(conn, 'outreach_emails', instance_id)
                    rows, _ = classify_interval_events(all_rows, run_id_1, run_id_2, 'run_id')
                elif did == 'outreach_email_issues':
                    joined_sql = (
                        "SELECT oei.email_id, oei.issue_id, oe.run_id, "
                        "oe.campaign_id, oe.recipient_login, oe.sent_at, oe.status AS email_status, "
                        "i.entity_type, i.entity_key, i.status AS issue_status "
                        "FROM outreach_email_issues oei "
                        "JOIN outreach_emails oe ON oei.email_id = oe.email_id "
                        "JOIN issues i ON oei.issue_id = i.issue_id "
                        "WHERE oe.run_id IN (SELECT run_id FROM runs WHERE instance_id = ?)"
                    )
                    joined_rows = [dict(r) for r in conn.execute(joined_sql, (instance_id,)).fetchall()]
                    rows, _ = classify_interval_events(joined_rows, run_id_1, run_id_2, 'run_id')
                    result['columns'] = [
                        'email_id', 'issue_id', 'campaign_id', 'recipient_login',
                        'sent_at', 'email_status', 'entity_type', 'entity_key', 'issue_status',
                    ]
                    result['notes'] = 'Joined: email + issue details'
                elif did in ('known_users', 'known_projects'):
                    all_rows = self._query_lifecycle(conn, entry['table'], instance_id)
                    as1 = reconstruct_as_of(all_rows, run_id_1)
                    as2 = reconstruct_as_of(all_rows, run_id_2)
                    cols = [c for c in entry['columns'] if c not in ('first_seen_run', 'last_seen_run')]
                    detail = diff_keyed_table_detail(
                        as1, as2, entry['key_fields'], cols,
                        change_type=change_type, search=search, sort=sort,
                        page=page, page_size=page_size,
                    )
                    result.update(detail)
                    result['changeType'] = change_type
                    result['notes'] = 'Reconstructed as-of each run'
                    return result
                elif did == 'issue_notes':
                    all_rows = self._query_lifecycle(conn, 'issue_notes', instance_id)
                    rows, _ = classify_issue_notes(all_rows, r1.get('run_at', ''), r2.get('run_at', ''))

                # Common pagination for lifecycle rows
                if change_type and change_type != 'all':
                    lc_values = map_filter_to_lifecycle(entry['dataset_id'], change_type)
                    if lc_values is not None:
                        rows = [r for r in rows if r.get('_lifecycle') in lc_values]
                    else:
                        rows = [r for r in rows if r.get('_lifecycle') == change_type]
                if search:
                    sl = search.lower()
                    rows = [r for r in rows if any(sl in str(v).lower() for v in r.values())]
                total = len(rows)
                start = (page - 1) * page_size
                result['rows'] = rows[start:start + page_size]
                result['page'] = page
                result['pageSize'] = page_size
                result['totalRows'] = total
                result['changeType'] = change_type

            elif entry['kind'] == KIND_METADATA:
                try:
                    all_rows = [dict(r) for r in conn.execute(
                        f"SELECT * FROM {entry['table']}"
                    ).fetchall()]
                except Exception:
                    all_rows = []
                if search:
                    sl = search.lower()
                    all_rows = [r for r in all_rows if any(sl in str(v).lower() for v in r.values())]
                total = len(all_rows)
                start = (page - 1) * page_size
                result['rows'] = all_rows[start:start + page_size]
                result['page'] = page
                result['pageSize'] = page_size
                result['totalRows'] = total
                result['changeType'] = change_type
                result['notes'] = 'Current-only: not historically versioned'

            return result

    def get_dashboard(self, instance_id: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            conn = self._get_conn()
            cond = ' WHERE instance_id = ?' if instance_id else ''
            params: list = [instance_id] if instance_id else []

            total_runs = conn.execute(
                f'SELECT COUNT(*) AS cnt FROM runs{cond}', params,
            ).fetchone()['cnt']

            open_issues = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM issues{' WHERE instance_id = ? AND' if instance_id else ' WHERE'} status IN ('open', 'regressed')",
                params,
            ).fetchone()['cnt']

            resolved_issues = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM issues{' WHERE instance_id = ? AND' if instance_id else ' WHERE'} status = 'resolved'",
                params,
            ).fetchone()['cnt']

            total_emails = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM outreach_emails{' WHERE run_id IN (SELECT run_id FROM runs WHERE instance_id = ?)' if instance_id else ''}",
                params,
            ).fetchone()['cnt']

            # By-campaign breakdown
            campaign_cond = ' WHERE instance_id = ?' if instance_id else ''
            campaign_stats = conn.execute(
                f"""SELECT campaign_id,
                       COUNT(CASE WHEN status IN ('open', 'regressed') THEN 1 END) AS open_count,
                       COUNT(CASE WHEN status = 'resolved' THEN 1 END) AS resolved_count,
                       COUNT(*) AS total_count
                    FROM issues{campaign_cond}
                    GROUP BY campaign_id""",
                params,
            ).fetchall()

            # Latest run
            latest_run = conn.execute(
                f'SELECT * FROM runs{cond} ORDER BY run_id DESC LIMIT 1', params,
            ).fetchone()

            # Stale outreach count
            stale = conn.execute(
                'SELECT COUNT(*) AS cnt FROM v_stale_outreach',
            ).fetchone()['cnt']

            return {
                'total_runs': total_runs,
                'open_issues': open_issues,
                'resolved_issues': resolved_issues,
                'total_emails': total_emails,
                'stale_outreach_count': stale,
                'campaign_stats': [dict(r) for r in campaign_stats],
                'latest_run': dict(latest_run) if latest_run else None,
            }

    # ------------------------------------------------------------------
    # Campaign settings (enable/disable toggles)
    # ------------------------------------------------------------------

    def get_campaign_settings(self) -> Dict[str, bool]:
        """Return {campaign_id: enabled} for all rows in campaign_settings."""
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute('SELECT campaign_id, enabled FROM campaign_settings').fetchall()
            return {r['campaign_id']: bool(r['enabled']) for r in rows}

    def set_campaign_enabled(self, campaign_id: str, enabled: bool) -> None:
        """UPSERT a campaign's enabled state."""
        now = _now_iso()
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO campaign_settings (campaign_id, enabled, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(campaign_id) DO UPDATE SET enabled = excluded.enabled, updated_at = excluded.updated_at""",
                (campaign_id, 1 if enabled else 0, now),
            )
            conn.commit()

    def get_disabled_campaigns(self) -> set:
        """Return set of campaign_ids where enabled=0."""
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute('SELECT campaign_id FROM campaign_settings WHERE enabled = 0').fetchall()
            return {r['campaign_id'] for r in rows}

    # ------------------------------------------------------------------
    # Campaign exemptions
    # ------------------------------------------------------------------

    def get_exemptions(self, campaign_id: str = None) -> list:
        """List all exemptions, optionally filtered by campaign_id."""
        with self._lock:
            conn = self._get_conn()
            if campaign_id:
                rows = conn.execute(
                    'SELECT * FROM campaign_exemptions WHERE campaign_id = ? ORDER BY created_at DESC',
                    (campaign_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT * FROM campaign_exemptions ORDER BY created_at DESC',
                ).fetchall()
            return [dict(r) for r in rows]

    def get_exemption_set(self) -> set:
        """Return set of (campaign_id, entity_key) tuples for fast lookup during ingest."""
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute('SELECT campaign_id, entity_key FROM campaign_exemptions').fetchall()
            return {(r['campaign_id'], r['entity_key']) for r in rows}

    def add_exemption(self, campaign_id: str, entity_key: str, reason: str = None) -> dict:
        """Upsert an exemption. Returns the exemption row."""
        now = _now_iso()
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO campaign_exemptions (campaign_id, entity_type, entity_key, reason, created_at)
                   VALUES (?, 'project', ?, ?, ?)
                   ON CONFLICT(campaign_id, entity_type, entity_key) DO UPDATE SET
                       reason = excluded.reason,
                       created_at = excluded.created_at""",
                (campaign_id, entity_key, reason, now),
            )
            conn.commit()
            row = conn.execute(
                'SELECT * FROM campaign_exemptions WHERE campaign_id = ? AND entity_key = ?',
                (campaign_id, entity_key),
            ).fetchone()
            return dict(row) if row else {}

    def remove_exemption(self, exemption_id: int) -> bool:
        """Delete an exemption by ID. Returns True if deleted."""
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute('DELETE FROM campaign_exemptions WHERE exemption_id = ?', (exemption_id,))
            conn.commit()
            return cur.rowcount > 0

    def resolve_issue_ids_for_preview(
        self,
        instance_id: str,
        campaign_id: str,
        entity_keys: List[Tuple[str, str]],
    ) -> List[int]:
        """Find open issue IDs matching campaign + entity type/key pairs."""
        if not entity_keys:
            return []
        with self._lock:
            conn = self._get_conn()
            issue_ids = []
            for entity_type, entity_key in entity_keys:
                row = conn.execute(
                    """SELECT issue_id FROM issues
                       WHERE instance_id = ? AND campaign_id = ?
                         AND entity_type = ? AND entity_key = ?
                         AND status IN ('open', 'regressed')""",
                    (instance_id, campaign_id, entity_type, entity_key),
                ).fetchone()
                if row:
                    issue_ids.append(row['issue_id'])
            return issue_ids

    def record_email_send(
        self,
        run_id: Optional[int],
        campaign_id: str,
        recipient_login: str,
        recipient_email: str,
        status: str,
        subject: str,
        linked_issue_ids: List[int],
        error_message: Optional[str] = None,
        channel_id: Optional[str] = None,
        sent_by: Optional[str] = None,
    ) -> Optional[int]:
        now = _now_iso()
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    """INSERT INTO outreach_emails
                           (run_id, campaign_id, recipient_login, recipient_email,
                            sent_at, status, error_message, subject, channel_id, sent_by)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (run_id, campaign_id, recipient_login, recipient_email,
                     now, status, error_message, subject, channel_id, sent_by),
                )
                email_id = cur.lastrowid

                if linked_issue_ids and status == 'sent':
                    for iid in linked_issue_ids:
                        conn.execute(
                            'INSERT OR IGNORE INTO outreach_email_issues (email_id, issue_id) VALUES (?, ?)',
                            (email_id, iid),
                        )
                    placeholders = ','.join('?' * len(linked_issue_ids))
                    conn.execute(
                        f"""UPDATE issues SET
                                times_emailed = times_emailed + 1,
                                last_emailed_at = ?
                            WHERE issue_id IN ({placeholders})""",
                        [now] + linked_issue_ids,
                    )

                conn.commit()
                return email_id
            except Exception:
                conn.rollback()
                raise


# ------------------------------------------------------------------
# Finding extraction from outreach data
# ------------------------------------------------------------------

def extract_findings_from_outreach_data(
    data: Dict[str, Any],
    disabled_campaigns: Optional[set] = None,
    exemptions: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """Convert outreach-data response into a flat findings list for ingestion.

    Args:
        exemptions: set of (campaign_id, entity_key) tuples to exclude from findings.
    """
    findings: List[Dict[str, Any]] = []

    # campaign → (recipients_key, entity_type, entity_extraction_fn)
    _CAMPAIGN_MAP = {
        'project': ('projectRecipients', 'project', _extract_project_findings),
        'code_env': ('codeEnvRecipients', 'project', _extract_project_findings_code_env),
        'code_studio': ('codeStudioRecipients', 'project', _extract_project_findings_code_studio),
        'auto_scenario': ('autoScenarioRecipients', 'scenario', _extract_scenario_findings),
        'scenario_frequency': ('scenarioFrequencyRecipients', 'scenario', _extract_scenario_findings_freq),
        'scenario_failing': ('scenarioFailingRecipients', 'scenario', _extract_scenario_findings_failing),
        'disabled_user': ('disabledUserRecipients', 'project', _extract_project_findings_disabled),
        'deprecated_code_env': ('deprecatedCodeEnvRecipients', 'code_env', _extract_deprecated_env_findings),
        'default_code_env': ('defaultCodeEnvRecipients', 'project', _extract_project_findings_default_env),
        'empty_project': ('emptyProjectRecipients', 'project', _extract_project_findings_empty),
        'large_flow': ('largeFlowRecipients', 'project', _extract_project_findings_large_flow),
        'orphan_notebooks': ('orphanNotebookRecipients', 'project', _extract_project_findings_orphan),
        'overshared_project': ('oversharedProjectRecipients', 'project', _extract_project_findings_overshared),
        'inactive_project': ('inactiveProjectRecipients', 'project', _extract_inactive_project_findings),
        'unused_code_env': ('unusedCodeEnvRecipients', 'code_env', _extract_unused_code_env_findings),
    }

    for campaign_id, (recipients_key, entity_type, extractor) in _CAMPAIGN_MAP.items():
        if disabled_campaigns and campaign_id in disabled_campaigns:
            continue
        recipients = data.get(recipients_key) or []
        for recipient in recipients:
            if not isinstance(recipient, dict):
                continue
            owner = str(recipient.get('owner') or recipient.get('recipientKey') or '')
            email = str(recipient.get('email') or owner)
            extracted = extractor(recipient, campaign_id, entity_type, owner, email)
            if exemptions:
                extracted = [f for f in extracted if (f['campaign_id'], f['entity_key']) not in exemptions]
            findings.extend(extracted)

    return findings


def _extract_project_findings(recipient, campaign_id, entity_type, owner, email):
    results = []
    for proj in (recipient.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        pkey = str(proj.get('projectKey') or '')
        if not pkey:
            continue
        results.append({
            'campaign_id': campaign_id,
            'entity_type': entity_type,
            'entity_key': pkey,
            'entity_name': proj.get('name'),
            'owner_login': owner,
            'owner_email': email,
            'metrics_json': {'codeEnvCount': proj.get('codeEnvCount'), 'totalGB': proj.get('totalGB')},
        })
    return results


def _extract_project_findings_code_env(recipient, campaign_id, entity_type, owner, email):
    results = []
    for proj in (recipient.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        pkey = str(proj.get('projectKey') or '')
        if not pkey:
            continue
        results.append({
            'campaign_id': campaign_id,
            'entity_type': entity_type,
            'entity_key': pkey,
            'entity_name': proj.get('name'),
            'owner_login': owner,
            'owner_email': email,
            'metrics_json': {'mismatchedEnvCount': proj.get('mismatchedCodeEnvCount')},
        })
    return results


def _extract_project_findings_code_studio(recipient, campaign_id, entity_type, owner, email):
    results = []
    for proj in (recipient.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        pkey = str(proj.get('projectKey') or '')
        if not pkey:
            continue
        results.append({
            'campaign_id': campaign_id,
            'entity_type': entity_type,
            'entity_key': pkey,
            'entity_name': proj.get('name'),
            'owner_login': owner,
            'owner_email': email,
            'metrics_json': {'codeStudioCount': proj.get('codeStudioCount'), 'totalGB': proj.get('totalGB')},
        })
    return results


def _extract_scenario_findings(recipient, campaign_id, entity_type, owner, email):
    results = []
    for proj in (recipient.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        pkey = str(proj.get('projectKey') or '')
        for sc in (proj.get('autoScenarios') or []):
            if not isinstance(sc, dict):
                continue
            sc_id = str(sc.get('id') or '')
            if not pkey or not sc_id:
                continue
            results.append({
                'campaign_id': campaign_id,
                'entity_type': entity_type,
                'entity_key': f'{pkey}:{sc_id}',
                'entity_name': sc.get('name'),
                'owner_login': owner,
                'owner_email': email,
                'metrics_json': {
                    'name': sc.get('name'),
                    'type': sc.get('type'),
                    'triggerCount': sc.get('triggerCount'),
                },
            })
    return results


def _extract_scenario_findings_freq(recipient, campaign_id, entity_type, owner, email):
    results = []
    for proj in (recipient.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        pkey = str(proj.get('projectKey') or '')
        for sc in (proj.get('autoScenarios') or []):
            if not isinstance(sc, dict):
                continue
            sc_id = str(sc.get('id') or '')
            if not pkey or not sc_id:
                continue
            results.append({
                'campaign_id': campaign_id,
                'entity_type': entity_type,
                'entity_key': f'{pkey}:{sc_id}',
                'entity_name': sc.get('name'),
                'owner_login': owner,
                'owner_email': email,
                'metrics_json': {
                    'minTriggerMinutes': sc.get('minTriggerMinutes'),
                    'triggerType': sc.get('type'),
                },
            })
    return results


def _extract_scenario_findings_failing(recipient, campaign_id, entity_type, owner, email):
    results = []
    for proj in (recipient.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        pkey = str(proj.get('projectKey') or '')
        for sc in (proj.get('autoScenarios') or []):
            if not isinstance(sc, dict):
                continue
            sc_id = str(sc.get('id') or '')
            if not pkey or not sc_id:
                continue
            results.append({
                'campaign_id': campaign_id,
                'entity_type': entity_type,
                'entity_key': f'{pkey}:{sc_id}',
                'entity_name': sc.get('name'),
                'owner_login': owner,
                'owner_email': email,
                'metrics_json': {
                    'lastOutcome': sc.get('lastOutcome'),
                },
            })
    return results


def _extract_project_findings_disabled(recipient, campaign_id, entity_type, owner, email):
    results = []
    for proj in (recipient.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        pkey = str(proj.get('projectKey') or '')
        if not pkey:
            continue
        results.append({
            'campaign_id': campaign_id,
            'entity_type': entity_type,
            'entity_key': pkey,
            'entity_name': proj.get('name'),
            'owner_login': owner,
            'owner_email': email,
            'metrics_json': {'totalGB': proj.get('totalGB')},
        })
    return results


def _extract_deprecated_env_findings(recipient, campaign_id, entity_type, owner, email):
    results = []
    for env in (recipient.get('codeEnvs') or []):
        if not isinstance(env, dict):
            continue
        env_name = str(env.get('name') or env.get('key') or '')
        if not env_name:
            continue
        results.append({
            'campaign_id': campaign_id,
            'entity_type': entity_type,
            'entity_key': env_name,
            'entity_name': env_name,
            'owner_login': owner,
            'owner_email': email,
            'metrics_json': {
                'pythonVersion': env.get('pythonVersion'),
                'language': env.get('language'),
                'impactedProjects': env.get('impactedProjects'),
            },
        })
    return results


def _extract_project_findings_default_env(recipient, campaign_id, entity_type, owner, email):
    results = []
    for proj in (recipient.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        pkey = str(proj.get('projectKey') or '')
        if not pkey:
            continue
        results.append({
            'campaign_id': campaign_id,
            'entity_type': entity_type,
            'entity_key': pkey,
            'entity_name': proj.get('name'),
            'owner_login': owner,
            'owner_email': email,
            'metrics_json': {'codeEnvCount': proj.get('codeEnvCount')},
        })
    return results


def _extract_project_findings_empty(recipient, campaign_id, entity_type, owner, email):
    results = []
    for proj in (recipient.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        pkey = str(proj.get('projectKey') or '')
        if not pkey:
            continue
        results.append({
            'campaign_id': campaign_id,
            'entity_type': entity_type,
            'entity_key': pkey,
            'entity_name': proj.get('name'),
            'owner_login': owner,
            'owner_email': email,
            'metrics_json': {'totalGB': proj.get('totalGB')},
        })
    return results


def _extract_project_findings_large_flow(recipient, campaign_id, entity_type, owner, email):
    results = []
    for proj in (recipient.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        pkey = str(proj.get('projectKey') or '')
        if not pkey:
            continue
        results.append({
            'campaign_id': campaign_id,
            'entity_type': entity_type,
            'entity_key': pkey,
            'entity_name': proj.get('name'),
            'owner_login': owner,
            'owner_email': email,
            'metrics_json': {'totalObjects': proj.get('totalObjects')},
        })
    return results


def _extract_project_findings_orphan(recipient, campaign_id, entity_type, owner, email):
    results = []
    for proj in (recipient.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        pkey = str(proj.get('projectKey') or '')
        if not pkey:
            continue
        results.append({
            'campaign_id': campaign_id,
            'entity_type': entity_type,
            'entity_key': pkey,
            'entity_name': proj.get('name'),
            'owner_login': owner,
            'owner_email': email,
            'metrics_json': {
                'notebookCount': proj.get('notebookCount'),
                'recipeCount': proj.get('recipeCount'),
            },
        })
    return results


def _extract_inactive_project_findings(recipient, campaign_id, entity_type, owner, email):
    results = []
    for proj in (recipient.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        pkey = str(proj.get('projectKey') or '')
        if not pkey:
            continue
        results.append({
            'campaign_id': campaign_id,
            'entity_type': entity_type,
            'entity_key': pkey,
            'entity_name': proj.get('name'),
            'owner_login': owner,
            'owner_email': email,
            'metrics_json': {'daysInactive': proj.get('daysInactive')},
        })
    return results


def _extract_unused_code_env_findings(recipient, campaign_id, entity_type, owner, email):
    results = []
    for env in (recipient.get('codeEnvs') or []):
        if not isinstance(env, dict):
            continue
        env_key = str(env.get('key') or env.get('name') or '')
        if not env_key:
            continue
        results.append({
            'campaign_id': campaign_id,
            'entity_type': entity_type,
            'entity_key': env_key,
            'entity_name': env.get('name'),
            'owner_login': owner,
            'owner_email': email,
            'metrics_json': {
                'name': env.get('name'),
                'language': env.get('language'),
            },
        })
    return results


def _extract_project_findings_overshared(recipient, campaign_id, entity_type, owner, email):
    results = []
    for proj in (recipient.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        pkey = str(proj.get('projectKey') or '')
        if not pkey:
            continue
        results.append({
            'campaign_id': campaign_id,
            'entity_type': entity_type,
            'entity_key': pkey,
            'entity_name': proj.get('name'),
            'owner_login': owner,
            'owner_email': email,
            'metrics_json': {'permissionCount': proj.get('permissionCount')},
        })
    return results
