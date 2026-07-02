"""Story Postgres schema — DDL, migrations, ensure_schema.

The schema lives entirely in the `story` schema of the configured PostgreSQL
connection. ensure_schema() runs at the start of every story-collect run:
- every DDL_V1 statement is IF NOT EXISTS, so a fresh DB and an up-to-date DB
  both no-op cleanly;
- MIGRATIONS is a list of (version, [statements]) applied strictly in order
  for versions greater than the stored schema_version. Adding schema change N
  means appending (N, [...]) and bumping SCHEMA_VERSION — never editing DDL_V1.
"""
from typing import Any, List, Tuple

SCHEMA_VERSION = 1

DDL_V1: List[str] = [
    "CREATE SCHEMA IF NOT EXISTS story",

    # Bookkeeping: single-row-per-key metadata (schema_version lives here).
    """
    CREATE TABLE IF NOT EXISTS story.schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,

    # Durable per-(instance, source) collection cursors + statuses. Written
    # even when a run fails (in its own small transaction), so the Setup page
    # never depends on the macro's return value surviving a raise.
    """
    CREATE TABLE IF NOT EXISTS story.ingest_runs (
        instance_id TEXT NOT NULL,
        source TEXT NOT NULL,
        cursor_value TEXT,
        last_run_at TIMESTAMPTZ,
        last_status TEXT,
        last_error TEXT,
        last_rows_written INTEGER,
        PRIMARY KEY (instance_id, source)
    )
    """,

    # Per-day, per-user, per-project UI activity (viewing ⊇ developing).
    """
    CREATE TABLE IF NOT EXISTS story.user_activity_daily (
        day DATE NOT NULL,
        instance_id TEXT NOT NULL,
        login TEXT NOT NULL,
        project_key TEXT NOT NULL,
        viewing_actions INTEGER NOT NULL DEFAULT 0,
        developing_actions INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (day, instance_id, login, project_key)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_user_activity_daily_instance_day
        ON story.user_activity_daily (instance_id, day)
    """,

    # Raw msgType counts per day/project — taxonomy applied at query time.
    """
    CREATE TABLE IF NOT EXISTS story.audit_event_counts (
        day DATE NOT NULL,
        instance_id TEXT NOT NULL,
        project_key TEXT NOT NULL,
        msg_type TEXT NOT NULL,
        event_count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (day, instance_id, project_key, msg_type)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_audit_event_counts_instance_day
        ON story.audit_event_counts (instance_id, day)
    """,

    # One license snapshot per instance per day (same-day re-run overwrites).
    """
    CREATE TABLE IF NOT EXISTS story.license_snapshots (
        snapshot_date DATE NOT NULL,
        instance_id TEXT NOT NULL,
        dss_version TEXT,
        license_kind TEXT,
        expires_on TEXT,
        users_total INTEGER,
        addons JSONB,
        raw JSONB,
        PRIMARY KEY (snapshot_date, instance_id)
    )
    """,

    # Per-profile license caps vs usage (cap NULL = unlimited).
    """
    CREATE TABLE IF NOT EXISTS story.license_profile_caps (
        snapshot_date DATE NOT NULL,
        instance_id TEXT NOT NULL,
        profile TEXT NOT NULL,
        cap INTEGER,
        used INTEGER,
        PRIMARY KEY (snapshot_date, instance_id, profile)
    )
    """,

    # Object counts per project/type per day (kept forever).
    """
    CREATE TABLE IF NOT EXISTS story.object_inventory_daily (
        snapshot_date DATE NOT NULL,
        instance_id TEXT NOT NULL,
        project_key TEXT NOT NULL,
        object_type TEXT NOT NULL,
        object_count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (snapshot_date, instance_id, project_key, object_type)
    )
    """,

    # Item-level inventory (pruned past the retention window).
    """
    CREATE TABLE IF NOT EXISTS story.object_inventory_items (
        snapshot_date DATE NOT NULL,
        instance_id TEXT NOT NULL,
        project_key TEXT NOT NULL,
        object_type TEXT NOT NULL,
        object_id TEXT NOT NULL,
        name TEXT,
        subtype TEXT,
        PRIMARY KEY (snapshot_date, instance_id, project_key, object_type, object_id)
    )
    """,
]

# (version, [statements]) applied in ascending order when stored version < v.
MIGRATIONS: List[Tuple[int, List[str]]] = []


def get_schema_version(conn: Any) -> int:
    """Stored schema version, 0 when the schema/meta table doesn't exist yet."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT to_regclass('story.schema_meta')"
        )
        row = cur.fetchone()
        if not row or row[0] is None:
            return 0
        cur.execute("SELECT value FROM story.schema_meta WHERE key = %s", ('schema_version',))
        row = cur.fetchone()
        try:
            return int(row[0]) if row else 0
        except (TypeError, ValueError):
            return 0


def ensure_schema(conn: Any) -> int:
    """Create/upgrade the story schema. Commits on success, rolls back on error.

    Returns the resulting schema version.
    """
    try:
        current = get_schema_version(conn)
        with conn.cursor() as cur:
            for stmt in DDL_V1:
                cur.execute(stmt)
            for version, statements in MIGRATIONS:
                if version <= current:
                    continue
                for stmt in statements:
                    cur.execute(stmt)
            cur.execute(
                """
                INSERT INTO story.schema_meta (key, value, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value, updated_at = now()
                """,
                ('schema_version', str(SCHEMA_VERSION)),
            )
        conn.commit()
        return SCHEMA_VERSION
    except Exception:
        conn.rollback()
        raise
