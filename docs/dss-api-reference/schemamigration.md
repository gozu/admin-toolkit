# Plan: Schema Support + Legacy Data Migration

## Context

Currently the admin can only set a table prefix (e.g. `adtk` → `adtk_runs`), but all tables land in the database's default schema (usually `public`). The user wants the ability to isolate plugin tables in a dedicated PostgreSQL schema (e.g. `admin_toolkit.adtk_runs`). When an admin sets a schema after running without one, existing data must be migrated via copy + drop.

**Files:** `plugin.json`, `python-lib/db_adapter.py`, `python-lib/sql_tracking.py`, `python-lib/sdk_cache.py`

### 2a. Plugin config — `plugin.json`
Add new param between `tracking_db_connection` and `tracking_table_prefix`:
```json
{
    "name": "tracking_schema",
    "label": "Schema Name",
    "description": "PostgreSQL schema for tracking tables (e.g. 'admin_toolkit'). Leave empty to use the connection's default schema.",
    "type": "STRING",
    "mandatory": false,
    "defaultValue": ""
}
```

### 2b. Config dataclass — `python-lib/db_adapter.py`
- Add `schema: Optional[str] = None` to `TrackingBackendConfig`
- In `load_tracking_backend_config()`: read `tracking_schema`, validate with `_SCHEMA_RE = re.compile(r'^[a-z][a-z0-9_]{0,62}$')`
- Pass `schema` through factory functions:
  ```python
  def create_sdk_cache(config):
      return SdkApiCache(config.connection_name, config.table_prefix, config.schema)

  def create_tracking_backend(config, sqlite_path):
      if config.connection_name:
          return SQLTrackingDB(config.connection_name, config.table_prefix, config.schema)
      ...
  ```

### 2c. Table name helper — both `sql_tracking.py` and `sdk_cache.py`
Update `_q()` in both files to accept optional schema:
```python
def _q(prefix: Optional[str], table: str, schema: Optional[str] = None) -> str:
    name = f"{prefix}_{table}" if prefix else table
    if schema:
        return f"{schema}.{name}"
    return name
```

### 2d. Thread schema through constructors
**`sql_tracking.py`:**
- `__init__(self, connection_name, table_prefix=None, schema=None)` → store `self._schema`
- `_t(self, table)` → `return _q(self._prefix, table, self._schema)`

**`sdk_cache.py`:**
- `__init__(self, connection_name, table_prefix=None, schema=None)` → store `self._schema`
- All `_q(self._prefix, 'api_cache')` calls → `_q(self._prefix, 'api_cache', self._schema)`

### 2e. Migration logic — `sql_tracking.py` (new method `_migrate_from_default_schema`)
Runs inside `_init_tables()`, after table creation in the new schema, only when `self._schema` is set.

**Tables affected (16 total):**
- `sql_tracking.py` (15): all tables in `_ALL_TABLES`
- `sdk_cache.py` (1): `api_cache`

**Per-table migration (with row count validation):**
```
For each table in _ALL_TABLES:
  1. old = _q(prefix, table)           # unqualified — hits default search_path
     new = _q(prefix, table, schema)   # schema-qualified
  2. old_count = SELECT COUNT(*) FROM {old}
     - If fails (table doesn't exist), skip this table
  3. new_count = SELECT COUNT(*) FROM {new}
     - If new_count > 0, skip (don't overwrite existing data)
  4. INSERT INTO {new} SELECT * FROM {old}
  5. verify_count = SELECT COUNT(*) FROM {new}
     - If verify_count != old_count, log ERROR, do NOT drop old table, continue
  6. DROP TABLE {old}
  7. Log: migrated {old_count} rows from {old} → {new}
```

Each table wrapped in its own try/except — partial migration is safe because step 3 makes it idempotent (tables already populated are skipped on next run).

### 2f. Migration logic — `sdk_cache.py`
Same pattern for `api_cache` table only, inside `_init_tables()` after CREATE TABLE succeeds.

## Verification

- Deploy with `make deploy`
- **Without schema configured:** behavior unchanged — tables in default search path as before
- **With schema configured (fresh install):** tables created in `schema.prefix_table`, no migration attempted
- **With schema configured (existing data):** on startup, per-table migration runs:
  - Old unqualified tables copied to schema-qualified tables
  - Row counts validated before DROP
  - Mismatched counts → ERROR log, old table preserved
  - Matched counts → old table dropped, migration logged
- Verify via psql: `\dt schema_name.*` shows all 16 tables
- Verify data preserved: `SELECT COUNT(*) FROM schema.adtk_runs` matches pre-migration count
