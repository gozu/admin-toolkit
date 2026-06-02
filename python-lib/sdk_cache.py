"""SQL-backed API result cache with in-flight deduplication for DSS SDK calls.

Uses Dataiku SQLExecutor2.query_to_df() exclusively — no raw connections,
no cursors.  Database-agnostic: no PostgreSQL-specific syntax.
"""

import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

_log = logging.getLogger(__name__)

_IN_FLIGHT: Dict[Tuple[str, str], threading.Event] = {}
_IN_FLIGHT_LOCK = threading.Lock()

_EXC_LOG_MAX_CHARS = 500


def _brief(exc: Any) -> str:
    """Condense a SQL driver exception to a single short line.

    JDBC/psycopg2 exceptions often embed the full failing query, which for
    bulk cache writes is thousands of characters of `project_git_log:...`
    keys and makes backend.log unreadable. Keep the root cause, drop the rest.
    """
    s = str(exc).replace('\n', ' ').replace('\r', ' ')
    if len(s) > _EXC_LOG_MAX_CHARS:
        return s[:_EXC_LOG_MAX_CHARS] + f"... [{len(s) - _EXC_LOG_MAX_CHARS} chars truncated]"
    return s


def _L(val) -> str:
    """Convert a Python value to a safe SQL literal string."""
    if val is None:
        return 'NULL'
    if isinstance(val, bool):
        return 'TRUE' if val else 'FALSE'
    if isinstance(val, (int, float)):
        if isinstance(val, float) and (val != val):  # NaN
            return 'NULL'
        return str(val)
    return "'" + str(val).replace("'", "''") + "'"


def _q(prefix: Optional[str], table: str, schema: Optional[str] = None) -> str:
    """Prefix a table name, optionally schema-qualified."""
    name = f"{prefix}_{table}" if prefix else table
    if schema:
        return f"{schema}.{name}"
    return name


class SdkApiCache:
    def __init__(self, connection_name: Optional[str], table_prefix: Optional[str] = None, schema: Optional[str] = None):
        self._conn = connection_name
        self._prefix = table_prefix
        self._schema = schema
        self._mem: Dict[Tuple[str, str], Tuple[float, Any]] = {}  # (instance_id, key) → (fetched_at, value)
        self._mem_lock = threading.Lock()
        self._stats = {'hits_mem': 0, 'misses': 0, 'writes': 0, 'sql_ms': 0.0}
        self._stats_lock = threading.Lock()
        self._init_tables()

    def _init_tables(self) -> None:
        if not self._conn:
            return
        tbl = _q(self._prefix, 'api_cache', self._schema)
        sql = (
            f"CREATE TABLE IF NOT EXISTS {tbl} ("
            f"  instance_id   VARCHAR(255)  NOT NULL,"
            f"  cache_key     VARCHAR(255)  NOT NULL,"
            f"  fetched_at    BIGINT        NOT NULL,"
            f"  ttl_seconds   INT           NOT NULL,"
            f"  response_json TEXT          NOT NULL,"
            f"  PRIMARY KEY (instance_id, cache_key)"
            f")"
        )
        try:
            import dataiku
            executor = dataiku.SQLExecutor2(connection=self._conn)
            executor.query_to_df("SELECT 1", pre_queries=[sql], post_queries=['COMMIT'])
            if self._schema:
                self._migrate_from_default_schema(executor)
        except Exception as exc:
            _log.warning("[sdk_cache] _init_tables failed: %s", _brief(exc))

    def _migrate_from_default_schema(self, executor) -> None:
        """Copy api_cache data from default schema to configured schema, then drop old."""
        old = _q(self._prefix, 'api_cache')
        new = _q(self._prefix, 'api_cache', self._schema)
        try:
            old_df = executor.query_to_df(f"SELECT COUNT(*) AS cnt FROM {old}")
            old_count = old_df.to_dict('records')[0]['cnt'] if old_df is not None and not old_df.empty else 0
            if old_count == 0:
                return
        except Exception:
            return  # old table doesn't exist
        try:
            new_df = executor.query_to_df(f"SELECT COUNT(*) AS cnt FROM {new}")
            new_count = new_df.to_dict('records')[0]['cnt'] if new_df is not None and not new_df.empty else 0
            if new_count > 0:
                return  # already has data
        except Exception:
            return
        try:
            executor.query_to_df("SELECT 1",
                                 pre_queries=[f"INSERT INTO {new} SELECT * FROM {old}"],
                                 post_queries=['COMMIT'])
            verify_df = executor.query_to_df(f"SELECT COUNT(*) AS cnt FROM {new}")
            verify_count = verify_df.to_dict('records')[0]['cnt'] if verify_df is not None and not verify_df.empty else 0
            if verify_count != old_count:
                _log.error("[sdk_cache] schema migration: row count mismatch (old=%d, new=%d) — keeping old table",
                           old_count, verify_count)
                return
            executor.query_to_df("SELECT 1",
                                 pre_queries=[f"DROP TABLE {old}"],
                                 post_queries=['COMMIT'])
            _log.info("[sdk_cache] schema migration: migrated %d rows from %s → %s", old_count, old, new)
        except Exception as exc:
            _log.error("[sdk_cache] schema migration failed: %s", _brief(exc))

    def get_or_fetch(
        self,
        instance_id: str,
        cache_key: str,
        ttl_seconds: int,
        fetch_fn: Callable[[], Any],
        deadline_ts: Optional[float] = None,
    ) -> Any:
        # 0. Check L1 memory cache
        mem_key = (instance_id, cache_key)
        with self._mem_lock:
            entry = self._mem.get(mem_key)
            if entry is not None:
                fetched_at, value = entry
                if (time.time() - fetched_at) < ttl_seconds:
                    with self._stats_lock:
                        self._stats['hits_mem'] += 1
                    return value

        # 1. Check _IN_FLIGHT — if same key already being fetched, wait
        flight_key = (instance_id, cache_key)
        with _IN_FLIGHT_LOCK:
            if flight_key in _IN_FLIGHT:
                event = _IN_FLIGHT[flight_key]
                is_owner = False
            else:
                event = threading.Event()
                _IN_FLIGHT[flight_key] = event
                is_owner = True

        if not is_owner:
            timeout = max(1.0, deadline_ts - time.time()) if deadline_ts else 120.0
            event.wait(timeout=timeout)
            # Check L1 — owner should have populated it
            with self._mem_lock:
                entry = self._mem.get(mem_key)
                if entry is not None:
                    fetched_at, value = entry
                    if (time.time() - fetched_at) < ttl_seconds:
                        return value
            return fetch_fn()  # fallback if owner failed

        # 2. This thread is the owner — make the API call
        try:
            result = fetch_fn()
            with self._mem_lock:
                self._mem[mem_key] = (time.time(), result)
            with self._stats_lock:
                self._stats['misses'] += 1
            return result
        finally:
            with _IN_FLIGHT_LOCK:
                _IN_FLIGHT.pop(flight_key, None)
            event.set()  # wake up all waiters

    def flush_to_sql(self, instance_id: str, ttl_seconds: int) -> None:
        """Persist all L1 memory entries to SQL in one batch for historical analysis."""
        with self._mem_lock:
            items = {ck: v for (iid, ck), (_, v) in self._mem.items() if iid == instance_id}
        if items:
            self.set_many(instance_id, items, ttl_seconds)

    def set_many(self, instance_id: str, items: Dict[str, Any], ttl_seconds: int) -> None:
        """Batch-write multiple cache entries in one SQL round-trip.

        Per-value size cap: values whose serialised JSON exceeds _MAX_VALUE_BYTES
        are kept in the L1 memory cache but not persisted to SQL. This prevents
        the whole INSERT from silently failing when one oversized blob sneaks in
        (e.g. a 123 MB bulk git-log payload).
        """
        if not items:
            return
        now = time.time()
        _MAX_VALUE_BYTES = 1_000_000
        # Populate L1 only for JSON-serializable values
        serializable: Dict[str, str] = {}
        oversized_keys: List[str] = []
        for k, v in items.items():
            try:
                rj = json.dumps(v)
            except Exception:
                continue
            if len(rj) > _MAX_VALUE_BYTES:
                oversized_keys.append(k)
                continue
            serializable[k] = rj
        if oversized_keys:
            _log.warning(
                "[sdk_cache] set_many: skipped %d oversized key(s) (>%d bytes), kept in L1 only: %s",
                len(oversized_keys), _MAX_VALUE_BYTES, oversized_keys[:5],
            )
        with self._mem_lock:
            for k in serializable:
                self._mem[(instance_id, k)] = (now, items[k])
            # Still cache oversized values in L1 so callers don't pay refetch cost.
            for k in oversized_keys:
                self._mem[(instance_id, k)] = (now, items[k])
        if not self._conn:
            return
        tbl = _q(self._prefix, 'api_cache', self._schema)
        now_ms = int(now * 1000)
        keys = list(items.keys())
        t0 = time.time()
        try:
            import dataiku
            executor = dataiku.SQLExecutor2(connection=self._conn)
            key_list = ', '.join(_L(k) for k in keys)
            del_sql = (
                f"DELETE FROM {tbl}"
                f" WHERE instance_id = {_L(instance_id)}"
                f" AND cache_key IN ({key_list})"
            )
            executor.query_to_df(del_sql)
            values = []
            for k, rj in serializable.items():
                values.append(
                    f"({_L(instance_id)}, {_L(k)}, {_L(now_ms)}, {_L(ttl_seconds)}, {_L(rj)})"
                )
            if values:
                ins_sql = (
                    f"INSERT INTO {tbl}"
                    f" (instance_id, cache_key, fetched_at, ttl_seconds, response_json)"
                    f" VALUES {', '.join(values)}"
                )
                executor.query_to_df(ins_sql)
            elapsed_ms = (time.time() - t0) * 1000.0
            with self._stats_lock:
                self._stats['sql_ms'] += elapsed_ms
                self._stats['writes'] += len(values)
            _log.debug("[perf:sdk_cache:sql] BATCH_INSERT keys=%d elapsed=%.1fms", len(values), elapsed_ms)
        except Exception as exc:
            elapsed_ms = (time.time() - t0) * 1000.0
            with self._stats_lock:
                self._stats['sql_ms'] += elapsed_ms
            _log.warning("[sdk_cache] set_many failed (keys=%d): %s", len(serializable), _brief(exc))

    def get(self, instance_id: str, cache_key: str, ttl_seconds: int) -> Optional[Any]:
        """Read-only check of L1 memory cache. Returns None on miss."""
        return self.get_mem(instance_id, cache_key, ttl_seconds)

    def get_mem(self, instance_id: str, cache_key: str, ttl_seconds: int) -> Optional[Any]:
        """L1 memory-only cache check. No SQL. Returns None on miss."""
        mem_key = (instance_id, cache_key)
        with self._mem_lock:
            entry = self._mem.get(mem_key)
            if entry is not None:
                fetched_at, value = entry
                if (time.time() - fetched_at) < ttl_seconds:
                    with self._stats_lock:
                        self._stats['hits_mem'] += 1
                    return value
                del self._mem[mem_key]  # evict expired entry
        return None

    def get_stats(self) -> Dict[str, Any]:
        with self._stats_lock:
            stats = dict(self._stats)
        stats['sql_ms'] = round(stats['sql_ms'], 1)
        return stats

    def get_cache_keys(self) -> list:
        now = time.time()
        with self._mem_lock:
            return [
                {'key': f"{iid}:{ck}", 'age_s': round(now - fetched_at, 1)}
                for (iid, ck), (fetched_at, _) in self._mem.items()
            ]

    def invalidate_all(self, instance_id: str) -> None:
        with self._mem_lock:
            to_remove = [k for k in self._mem if k[0] == instance_id]
            for k in to_remove:
                del self._mem[k]
        if not self._conn:
            return
        tbl = _q(self._prefix, 'api_cache', self._schema)
        sql = f"DELETE FROM {tbl} WHERE instance_id = {_L(instance_id)}"
        try:
            import dataiku
            executor = dataiku.SQLExecutor2(connection=self._conn)
            executor.query_to_df(sql)
            _log.info("[sdk_cache] invalidated all for instance_id=%s", instance_id)
        except Exception as exc:
            _log.warning("[sdk_cache] invalidate_all failed: %s", _brief(exc))
