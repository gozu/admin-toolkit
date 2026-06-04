import json
import base64
import hashlib
import hmac
import math
import os
import platform
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor as _BaseThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import logging

import dataiku
import dataikuapi
from dateutil import parser as dtparser
from flask import Flask, Response, abort, g, jsonify, request, stream_with_context

app = Flask(__name__)

# Suppress noisy per-request and per-project scan logging
logging.getLogger('werkzeug').setLevel(logging.WARNING)

class _SqlNoiseFilter(logging.Filter):
    """Drop repetitive Dataiku SQLExecutor log lines."""
    _PATTERNS = ("SQL query reader", "SQL query response")
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(p in msg for p in self._PATTERNS)

logging.getLogger().addFilter(_SqlNoiseFilter())

_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()
_SHARED_USAGE_SCANS: Dict[str, Dict[str, Any]] = {}
_SHARED_USAGE_SCANS_LOCK = threading.Lock()
_SDK_CACHE: Optional[Any] = None
_INSTANCE_ID_CACHED: Optional[str] = None
_THREAD_LOCAL = threading.local()
_PROGRESS: Dict[str, Dict[str, Any]] = {}
_PROGRESS_LOCK = threading.Lock()
_PROGRESS_EVENT_LIMIT = 10000
_PROGRESS_RETENTION_SEC = 1800

# ── Configurable backend settings (updated via /api/settings) ──
_BACKEND_SETTINGS: Dict[str, Any] = {
    # Concurrency
    'parallel_workers_default': 8,
    'parallel_workers_max': 32,
    'code_env_detail_workers': 8,
    # Timeouts
    'code_env_timeout_ms': 600000,
    'project_footprint_timeout_ms': 600000,
    'container_exec_timeout_ms': 600000,
    # Cache TTLs (seconds)
    'cache_ttl_overview': 600,
    'cache_ttl_connections': 600,
    'cache_ttl_users': 600,
    'cache_ttl_license': 600,
    'cache_ttl_projects': 600,
    'cache_ttl_code_envs': 600,
    'cache_ttl_usage_full': 600,
    'cache_ttl_outreach': 600,
    'cache_ttl_inactive': 600,
    'cache_ttl_plugins': 600,
    'cache_ttl_log_errors': 600,
    'cache_ttl_dir_tree': 600,
    'cache_ttl_llm_audit': 7200,
    'cache_ttl_llm_pricing': 21600,
    # LLM audit
    'llm_audit_timeout_ms': 1200000,
    'llm_audit_pricing_timeout_sec': 30,
    # Frontend API timeouts (served to frontend for sync)
    'fe_timeout_code_envs': 620000,
    'fe_timeout_project_footprint': 620000,
    'fe_timeout_container_execs': 620000,
    'fe_timeout_projects': 45000,
    'fe_timeout_logs': 30000,
    'fe_timeout_llm_analysis': 120000,
    'fe_timeout_llm_audit': 1200000,
    'sqlite_connect_timeout': 30,
    # Codenvclean
    'codenvclean_thread_max': 20,
}
_BACKEND_SETTINGS_LOCK = threading.Lock()

# Load plugin.json performance defaults and merge into _BACKEND_SETTINGS
try:
    from db_adapter import load_plugin_performance_settings as _load_perf
    _plugin_perf = _load_perf()
    if _plugin_perf:
        _BACKEND_SETTINGS.update(_plugin_perf)
except Exception:
    pass
# Snapshot after plugin merge — used as reset target
_BACKEND_SETTINGS_DEFAULTS: Dict[str, Any] = dict(_BACKEND_SETTINGS)

# Load outreach detection thresholds from plugin params
try:
    from db_adapter import load_plugin_outreach_thresholds as _load_outreach_thresh
    _outreach_thresholds: Dict[str, Any] = _load_outreach_thresh()
except Exception:
    _outreach_thresholds = {}

try:
    import llm_audit
    _llm_audit_available = True
except Exception:
    _llm_audit_available = False


# SQL connection types used by the SQL-pushdown scan to identify compatible connections.
_SQL_CONNECTION_TYPES = {
    'PostgreSQL', 'Greenplum', 'MySQL', 'MariaDB', 'SQLServer', 'Oracle',
    'Snowflake', 'BigQuery', 'Redshift', 'Teradata', 'Vertica', 'SAPHANA',
    'Synapse', 'Databricks', 'Athena', 'Trino', 'Presto', 'Exasol',
    'Netezza', 'DB2', 'SQLite',
}


# Visual / non-code recipe types that never reference a code environment.
# Skipping these avoids unnecessary per-recipe API calls.
@app.route('/__ping')
def ping():
    return jsonify({'status': 'ok'})




_SESSION_EPOCH: int = 0
_SESSION_EPOCH_LOCK = threading.Lock()
_CACHE_INFLIGHT: Dict[str, threading.Event] = {}
_CACHE_INFLIGHT_ERRORS: Dict[str, BaseException] = {}


def _get_session_epoch() -> int:
    with _SESSION_EPOCH_LOCK:
        return _SESSION_EPOCH


def _bump_session_epoch() -> int:
    global _SESSION_EPOCH
    with _SESSION_EPOCH_LOCK:
        _SESSION_EPOCH += 1
        return _SESSION_EPOCH


class CacheLoaderTimeout(Exception):
    """Raised when a waiter on an in-flight cache loader exceeds its wait budget."""
    def __init__(self, key: str, timeout: float):
        super().__init__(f"cache loader for {key!r} did not complete within {timeout:.1f}s")
        self.key = key
        self.timeout = timeout


_CACHE_WAIT_TIMEOUT = 45.0


def _cache_host_id() -> str:
    try:
        return getattr(g, 'host_id', 'local') or 'local'
    except RuntimeError:
        return getattr(_THREAD_LOCAL, 'host_id', 'local') or 'local'


def _cache_key(key: str, host_id: Optional[str] = None) -> str:
    """Scope active-host cache entries so remote scans never reuse local data."""
    hid = host_id if host_id is not None else _cache_host_id()
    return key if hid == 'local' else f'host:{hid}:{key}'


def _cache_peek(key: str, default=None):
    with _CACHE_LOCK:
        entry = _CACHE.get(_cache_key(key))
    return (entry or {}).get('value', default)


def _cache_pop(key: str) -> None:
    scoped = _cache_key(key)
    with _CACHE_LOCK:
        _CACHE.pop(scoped, None)


def _cache_pop_matching(predicate) -> None:
    host_prefix = '' if _cache_host_id() == 'local' else f'host:{_cache_host_id()}:'
    with _CACHE_LOCK:
        for stored_key in list(_CACHE.keys()):
            logical_key = str(stored_key)
            if host_prefix and logical_key.startswith(host_prefix):
                logical_key = logical_key[len(host_prefix):]
            elif host_prefix:
                continue
            if predicate(logical_key):
                _CACHE.pop(stored_key, None)


def _cache_get(key: str, ttl: int, loader):
    """Cached loader with in-flight coalescing.

    N concurrent callers with the same key result in one loader execution
    and N-1 waiters. If the loader raises, all waiters get the same error
    so no one gets stuck. Waiters time out after _CACHE_WAIT_TIMEOUT so a
    stalled loader does not pin every Flask worker.
    """
    scoped_key = _cache_key(key)
    now = time.time()
    with _CACHE_LOCK:
        entry = _CACHE.get(scoped_key)
        if entry and now - entry['ts'] < ttl:
            return entry['value']
        inflight = _CACHE_INFLIGHT.get(scoped_key)
        if inflight is None:
            inflight = threading.Event()
            _CACHE_INFLIGHT[scoped_key] = inflight
            is_loader = True
        else:
            is_loader = False

    if not is_loader:
        got = inflight.wait(timeout=_CACHE_WAIT_TIMEOUT)
        if not got:
            raise CacheLoaderTimeout(key, _CACHE_WAIT_TIMEOUT)
        with _CACHE_LOCK:
            err = _CACHE_INFLIGHT_ERRORS.pop(scoped_key, None)
            entry = _CACHE.get(scoped_key)
        if err is not None:
            raise err
        if entry is not None:
            return entry['value']
        # Fall through to retry under our own in-flight (rare: loader succeeded
        # but entry was cleared between set and our read).

    err: Optional[BaseException] = None
    value: Any = None
    try:
        value = loader()
    except BaseException as exc:
        err = exc
    finally:
        finish_ts = time.time()
        with _CACHE_LOCK:
            if err is None:
                _CACHE[scoped_key] = {'ts': finish_ts, 'value': value}
            else:
                _CACHE_INFLIGHT_ERRORS[scoped_key] = err
            _CACHE_INFLIGHT.pop(scoped_key, None)
        inflight.set()
    if err is not None:
        raise err
    return value


@app.errorhandler(CacheLoaderTimeout)
def _handle_cache_loader_timeout(exc: CacheLoaderTimeout):
    app.logger.warning("[cache] loader timeout for key=%s after %.1fs", exc.key, exc.timeout)
    return jsonify({
        'error': 'Upstream slow',
        'kind': 'cache_timeout',
        'key': exc.key,
    }), 503


def _shared_project_code_env_usage_key(project_info: Dict[str, Dict[str, str]]) -> str:
    host_id = _cache_host_id()
    project_keys = sorted(str(project_key).strip() for project_key in project_info.keys() if str(project_key).strip())
    digest = hashlib.sha1('\n'.join(project_keys).encode('utf-8')).hexdigest()
    return f"{host_id}:{len(project_keys)}:{digest}"


def _clear_shared_project_code_env_usage() -> None:
    with _SHARED_USAGE_SCANS_LOCK:
        _SHARED_USAGE_SCANS.clear()


def _cleanup_progress_locked(now_ts: float) -> None:
    stale: List[str] = []
    for endpoint, state in _PROGRESS.items():
        updated_ts = float(state.get('updatedTs') or state.get('startedTs') or now_ts)
        if (now_ts - updated_ts) > _PROGRESS_RETENTION_SEC:
            stale.append(endpoint)
    for endpoint in stale:
        _PROGRESS.pop(endpoint, None)


def _progress_key(endpoint: str) -> str:
    host_id = _cache_host_id()
    return endpoint if host_id == 'local' else f'host:{host_id}:{endpoint}'


def _start_progress(endpoint: str) -> str:
    now_ts = time.time()
    stored_endpoint = _progress_key(endpoint)
    run_id = f"{endpoint}-{int(now_ts * 1000)}-{threading.get_ident()}"
    with _PROGRESS_LOCK:
        _cleanup_progress_locked(now_ts)
        _PROGRESS[stored_endpoint] = {
            'runId': run_id,
            'status': 'running',
            'startedTs': now_ts,
            'updatedTs': now_ts,
            'events': [],
            'nextIndex': 0,
            'droppedUntil': 0,
            'summary': None,
            'error': None,
            'partialRows': [],
            'partialRowsNext': 0,
        }
    return run_id


def _append_progress_event(endpoint: str, run_id: str, event: Dict[str, Any]) -> None:
    stored_endpoint = _progress_key(endpoint)
    with _PROGRESS_LOCK:
        state = _PROGRESS.get(stored_endpoint)
        if not isinstance(state, dict):
            return
        if str(state.get('runId') or '') != str(run_id or ''):
            return

        next_index = int(state.get('nextIndex') or 0)
        entry = dict(event)
        entry['idx'] = next_index
        events = state.get('events')
        if not isinstance(events, list):
            events = []
            state['events'] = events
        events.append(entry)
        state['nextIndex'] = next_index + 1

        if len(events) > _PROGRESS_EVENT_LIMIT:
            drop_count = len(events) - _PROGRESS_EVENT_LIMIT
            first_kept_idx = int(events[drop_count].get('idx') or (next_index + 1))
            state['droppedUntil'] = first_kept_idx
            del events[:drop_count]

        state['updatedTs'] = time.time()


def _append_progress_partial_row(endpoint: str, run_id: str, row: Dict[str, Any]) -> None:
    stored_endpoint = _progress_key(endpoint)
    with _PROGRESS_LOCK:
        state = _PROGRESS.get(stored_endpoint)
        if not isinstance(state, dict):
            return
        if str(state.get('runId') or '') != str(run_id or ''):
            return
        partial_rows = state.get('partialRows')
        if not isinstance(partial_rows, list):
            partial_rows = []
            state['partialRows'] = partial_rows
        partial_rows.append(row)
        state['partialRowsNext'] = len(partial_rows)
        state['updatedTs'] = time.time()


def _finish_progress(endpoint: str, run_id: str, status: str, summary: Optional[Dict[str, Any]] = None, error: Optional[str] = None) -> None:
    stored_endpoint = _progress_key(endpoint)
    with _PROGRESS_LOCK:
        state = _PROGRESS.get(stored_endpoint)
        if not isinstance(state, dict):
            return
        if str(state.get('runId') or '') != str(run_id or ''):
            return
        state['status'] = status
        state['summary'] = summary if isinstance(summary, dict) else None
        state['error'] = str(error or '') if error else None
        state['updatedTs'] = time.time()


def _set_progress_summary(endpoint: str, run_id: str, summary: Optional[Dict[str, Any]] = None) -> None:
    if not isinstance(summary, dict):
        return
    stored_endpoint = _progress_key(endpoint)
    with _PROGRESS_LOCK:
        state = _PROGRESS.get(stored_endpoint)
        if not isinstance(state, dict):
            return
        if str(state.get('runId') or '') != str(run_id or ''):
            return
        state['summary'] = dict(summary)
        state['updatedTs'] = time.time()


def _read_progress(endpoint: str, since: int = 0, run_id: Optional[str] = None, rows_since: int = 0) -> Dict[str, Any]:
    stored_endpoint = _progress_key(endpoint)
    with _PROGRESS_LOCK:
        now_ts = time.time()
        _cleanup_progress_locked(now_ts)
        state = _PROGRESS.get(stored_endpoint)
        if not isinstance(state, dict):
            return {
                'status': 'idle',
                'events': [],
                'next': max(0, int(since)),
            }

        current_run_id = str(state.get('runId') or '')
        dropped_until = int(state.get('droppedUntil') or 0)
        if run_id and str(run_id) != current_run_id:
            return {
                'runId': current_run_id,
                'status': 'replaced',
                'droppedUntil': dropped_until,
                'events': [],
                'next': int(state.get('nextIndex') or dropped_until),
            }

        cursor = max(int(since), dropped_until)
        events_raw = state.get('events')
        events = [dict(item) for item in events_raw if isinstance(item, dict) and int(item.get('idx', -1)) >= cursor] if isinstance(events_raw, list) else []

        partial_rows_all = state.get('partialRows')
        rows_cursor = max(0, int(rows_since))
        if isinstance(partial_rows_all, list) and rows_cursor < len(partial_rows_all):
            partial_rows = list(partial_rows_all[rows_cursor:])
        else:
            partial_rows = []
        partial_rows_next = int(state.get('partialRowsNext') or 0)

        return {
            'runId': current_run_id,
            'status': str(state.get('status') or 'idle'),
            'error': state.get('error'),
            'droppedUntil': dropped_until,
            'events': events,
            'next': int(state.get('nextIndex') or cursor),
            'summary': state.get('summary') if isinstance(state.get('summary'), dict) else None,
            'partialRows': partial_rows,
            'partialRowsNext': partial_rows_next,
        }


def _dip_home() -> str:
    dip_home = os.environ.get('DIP_HOME') or os.environ.get('DSS_HOME') or '/data/dataiku/dss_data'
    if not dip_home.endswith('/'):
        dip_home += '/'
    return dip_home


def _safe_read_text(path: str) -> Optional[str]:
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            return handle.read()
    except Exception:
        return None


def _safe_read_json(path: str) -> Optional[Dict[str, Any]]:
    text = _safe_read_text(path)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _coerce_log_text(payload: Any) -> Optional[str]:
    def collect(value: Any, depth: int = 0) -> List[str]:
        if depth > 6 or value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, bytes):
            return [value.decode('utf-8', errors='replace')]
        if isinstance(value, list):
            out: List[str] = []
            for item in value:
                out.extend(collect(item, depth + 1))
            return out
        if isinstance(value, dict):
            ordered_keys = ['line', 'message', 'text', 'content', 'log', 'data', 'result', 'value', 'records', 'entries', 'lines']
            out: List[str] = []
            for key in ordered_keys:
                if key in value:
                    out.extend(collect(value.get(key), depth + 1))
            if out:
                return out
            for child in value.values():
                out.extend(collect(child, depth + 1))
            return out
        return [str(value)]

    lines = [line for line in collect(payload) if isinstance(line, str) and line.strip()]
    if not lines:
        return None
    return '\n'.join(lines)


def _run_command(cmd: List[str]) -> Optional[str]:
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        return output.decode('utf-8', errors='replace')
    except Exception:
        return None


def _format_size_kb(value: int) -> str:
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.2f} GB"
    if value >= 1024:
        return f"{value / 1024:.2f} MB"
    return f"{value} KB"


def _format_size_bytes(value: int) -> str:
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.2f} MB"
    if value >= 1024:
        return f"{value / 1024:.2f} KB"
    return f"{value} bytes"


def _format_size_human(value: int) -> str:
    if value <= 0:
        return "0 B"
    units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
    size = float(value)
    unit_idx = 0
    while size >= 1024 and unit_idx < len(units) - 1:
        size /= 1024
        unit_idx += 1
    return f"{size:.2f} {units[unit_idx]}"


_PSEUDO_FS_TYPES = {
    'autofs',
    'bpf',
    'cgroup',
    'cgroup2',
    'configfs',
    'debugfs',
    'devpts',
    'devtmpfs',
    'efivarfs',
    'fusectl',
    'hugetlbfs',
    'mqueue',
    'nsfs',
    'proc',
    'pstore',
    'ramfs',
    'rpc_pipefs',
    'securityfs',
    'sysfs',
    'tmpfs',
    'tracefs',
}


def _read_df_mount_usage() -> List[Dict[str, Any]]:
    output = _run_command(['df', '-B1', '-PT'])
    if not output:
        return []
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        return []

    mounts: List[Dict[str, Any]] = []
    for line in lines[1:]:
        parts = re.split(r"\s+", line, maxsplit=6)
        if len(parts) < 7:
            continue
        filesystem, fs_type, blocks, used, available, _capacity, mount_path = parts
        try:
            mounts.append({
                'filesystem': filesystem,
                'fsType': fs_type.lower(),
                'blocks': int(blocks),
                'used': int(used),
                'available': int(available),
                'path': os.path.abspath(mount_path),
            })
        except Exception:
            continue
    return mounts


def _is_virtual_mount(mount: Dict[str, Any]) -> bool:
    fs_type = str(mount.get('fsType') or '').lower()
    mount_path = str(mount.get('path') or '')
    if fs_type in _PSEUDO_FS_TYPES:
        return True
    for prefix in ('/proc', '/sys', '/dev', '/run'):
        if mount_path == prefix or mount_path.startswith(prefix + '/'):
            return True
    return False


def _summarize_df_mounts() -> Dict[str, Any]:
    mounts = _read_df_mount_usage()
    included: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    for mount in mounts:
        if _is_virtual_mount(mount):
            excluded.append(mount)
        else:
            included.append(mount)

    by_path: Dict[str, int] = {}
    root_used = 0
    mounted_used = 0
    top_buckets: Dict[str, Dict[str, Any]] = {}
    for mount in included:
        mount_path = str(mount.get('path') or '/')
        used = int(mount.get('used') or 0)
        by_path[mount_path] = by_path.get(mount_path, 0) + used
        if mount_path == '/':
            root_used += used
            continue
        mounted_used += used
        top = '/' + mount_path.strip('/').split('/')[0]
        bucket = top_buckets.setdefault(top, {'size': 0, 'mounts': []})
        bucket['size'] = int(bucket.get('size') or 0) + used
        bucket['mounts'].append(mount_path)

    return {
        'included': included,
        'excluded': excluded,
        'byPath': by_path,
        'rootUsed': int(root_used),
        'mountedUsed': int(mounted_used),
        'totalUsed': int(root_used + mounted_used),
        'topBuckets': top_buckets,
    }


def _make_unscanned_usage_node(parent_path: str, depth: int, size: int, label: str) -> Dict[str, Any]:
    clean_parent = parent_path.rstrip('/') or '/'
    virtual_path = '/.unscanned' if clean_parent == '/' else f"{clean_parent}/.unscanned"
    return {
        'name': label,
        'path': virtual_path,
        'size': int(max(0, size)),
        'ownSize': int(max(0, size)),
        'isDirectory': False,
        'children': [],
        'fileCount': 0,
        'depth': depth,
        'hasHiddenChildren': False,
    }


def _overlay_mount_usage_on_node(
    node: Dict[str, Any],
    node_path: str,
    depth: int,
    mount_summary: Dict[str, Any],
    debug_state: Dict[str, Any],
) -> None:
    mount_by_path = mount_summary.get('byPath') or {}
    target_used = 0
    for mount_path, used in mount_by_path.items():
        if mount_path == node_path or mount_path.startswith(node_path.rstrip('/') + '/'):
            target_used += int(used or 0)
    if target_used <= 0:
        return

    scanned = int(node.get('size') or 0)
    if target_used <= scanned:
        return

    delta = target_used - scanned
    unknown = _make_unscanned_usage_node(node_path, depth + 1, delta, '[unscanned usage]')
    if node.get('isDirectory'):
        children = list(node.get('children') or [])
        children.append(unknown)
        children.sort(key=lambda child: int(child.get('size') or 0), reverse=True)
        node['children'] = children
        node['hasHiddenChildren'] = True
    node['size'] = target_used
    debug_state['overlayUnknownBytes'] = int(debug_state.get('overlayUnknownBytes') or 0) + int(delta)


def _apply_df_overlay_to_root_tree(root_node: Dict[str, Any], debug_state: Dict[str, Any]) -> Dict[str, Any]:
    mount_summary = _summarize_df_mounts()
    included = mount_summary.get('included') or []
    excluded = mount_summary.get('excluded') or []
    top_buckets = mount_summary.get('topBuckets') or {}

    children = list(root_node.get('children') or [])
    child_by_path: Dict[str, Dict[str, Any]] = {}
    for child in children:
        child_path = str(child.get('path') or '')
        if child_path:
            child_by_path[child_path] = child

    for top_path, bucket in top_buckets.items():
        bucket_size = int((bucket or {}).get('size') or 0)
        bucket_mounts = list((bucket or {}).get('mounts') or [])
        child = child_by_path.get(top_path)
        if child is None:
            child = {
                'name': os.path.basename(top_path) or top_path,
                'path': top_path,
                'size': 0,
                'ownSize': 0,
                'isDirectory': True,
                'children': [],
                'fileCount': 0,
                'depth': 1,
                'hasHiddenChildren': True,
            }
            children.append(child)
            child_by_path[top_path] = child

        scanned_size = int(child.get('size') or 0)
        if bucket_size > scanned_size:
            delta = bucket_size - scanned_size
            unknown = _make_unscanned_usage_node(top_path, int(child.get('depth') or 1) + 1, delta, '[unscanned usage]')
            child_children = list(child.get('children') or [])
            child_children.append(unknown)
            child_children.sort(key=lambda entry: int(entry.get('size') or 0), reverse=True)
            child['children'] = child_children
            child['size'] = bucket_size
            child['hasHiddenChildren'] = True
            debug_state['overlayUnknownBytes'] = int(debug_state.get('overlayUnknownBytes') or 0) + int(delta)
        child['mountPaths'] = sorted(set(bucket_mounts))

    root_used = int(mount_summary.get('rootUsed') or 0)
    mounted_used = int(mount_summary.get('mountedUsed') or 0)
    total_used = int(mount_summary.get('totalUsed') or 0)
    mounted_top_paths = set(top_buckets.keys())
    scanned_root_used = sum(
        int(child.get('size') or 0)
        for child in children
        if str(child.get('path') or '') not in mounted_top_paths
    )
    if root_used > scanned_root_used:
        delta = root_used - scanned_root_used
        children.append(_make_unscanned_usage_node('/', 1, delta, '[unscanned rootfs usage]'))
        debug_state['overlayUnknownBytes'] = int(debug_state.get('overlayUnknownBytes') or 0) + int(delta)

    children.sort(key=lambda child: int(child.get('size') or 0), reverse=True)
    root_node['children'] = children
    if total_used > 0:
        root_node['size'] = total_used

    debug_state['dfRootUsed'] = root_used
    debug_state['dfMountedUsed'] = mounted_used
    debug_state['dfTotalUsed'] = total_used
    debug_state['dfMountsIncluded'] = [
        {
            'path': str(mount.get('path') or ''),
            'size': int(mount.get('used') or 0),
            'humanSize': _format_size_human(int(mount.get('used') or 0)),
            'fsType': str(mount.get('fsType') or ''),
        }
        for mount in sorted(included, key=lambda item: int(item.get('used') or 0), reverse=True)[:24]
    ]
    debug_state['dfMountsExcluded'] = [
        {
            'path': str(mount.get('path') or ''),
            'size': int(mount.get('used') or 0),
            'humanSize': _format_size_human(int(mount.get('used') or 0)),
            'fsType': str(mount.get('fsType') or ''),
        }
        for mount in sorted(excluded, key=lambda item: int(item.get('used') or 0), reverse=True)[:12]
    ]
    debug_state['dfTopMountBuckets'] = [
        {
            'path': path,
            'size': int(bucket.get('size') or 0),
            'humanSize': _format_size_human(int(bucket.get('size') or 0)),
            'mounts': sorted(bucket.get('mounts') or []),
        }
        for path, bucket in sorted(top_buckets.items(), key=lambda item: int((item[1] or {}).get('size') or 0), reverse=True)[:12]
    ]
    return mount_summary


def _parse_memory_info(free_output: Optional[str]) -> Dict[str, str]:
    if not free_output:
        return {}
    lines = [line.strip() for line in free_output.strip().split('\n') if line.strip()]
    if len(lines) < 2:
        return {}

    headers = re.split(r"\s+", lines[0])
    mem_values = re.split(r"\s+", lines[1])
    start_index = 1 if mem_values and mem_values[0].lower().startswith('mem') else 0

    memory_info: Dict[str, str] = {}
    for idx, header in enumerate(headers):
        value_index = idx + start_index
        if value_index >= len(mem_values):
            continue
        try:
            mb_value = int(mem_values[value_index])
        except Exception:
            continue
        if mb_value >= 1024:
            memory_info[header] = f"{round(mb_value / 1024)} GB"
        else:
            memory_info[header] = f"{mb_value:,} MB"

    if len(lines) >= 3:
        swap_values = re.split(r"\s+", lines[2])
        if len(swap_values) > 3:
            try:
                swap_total = int(swap_values[1])
                swap_used = int(swap_values[2])
                swap_free = int(swap_values[3])
            except Exception:
                swap_total = 0
                swap_used = 0
                swap_free = 0
            if swap_total > 0:
                def fmt(v: int) -> str:
                    return f"{v / 1024:.2f} GB" if v >= 1024 else f"{v:,} MB"
                memory_info['Swap total'] = fmt(swap_total)
                memory_info['Swap used'] = fmt(swap_used)
                memory_info['Swap free'] = fmt(swap_free)
            else:
                memory_info['Swap'] = 'Not configured'

    order = [
        'total', 'used', 'free', 'available', 'shared', 'buff/cache',
        'Swap', 'Swap total', 'Swap used', 'Swap free'
    ]
    ordered: Dict[str, str] = {}
    for key in order:
        if key in memory_info:
            ordered[key] = memory_info[key]
    for key, value in memory_info.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def _parse_system_limits(ulimit_output: Optional[str]) -> Dict[str, str]:
    if not ulimit_output:
        return {}
    lines = [line.strip() for line in ulimit_output.strip().split('\n') if line.strip()]
    temp_limits: Dict[str, str] = {}

    for line in lines:
        match = re.match(r"^([^()]+)\s+\(([^)]+)\)\s+(.+)$", line)
        if not match:
            continue
        name = match.group(1).strip()
        details = match.group(2).strip()
        value = match.group(3).strip()

        if value == 'unlimited':
            temp_limits[name] = 'Unlimited'
            continue
        try:
            num_value = int(value)
            if 'kbytes' in details:
                temp_limits[name] = _format_size_kb(num_value)
            elif 'bytes' in details:
                temp_limits[name] = _format_size_bytes(num_value)
            else:
                temp_limits[name] = f"{num_value:,}"
        except Exception:
            temp_limits[name] = value

    priority = [
        'open files',
        'max user processes',
        'max memory size',
        'stack size',
        'max locked memory',
        'pending signals',
    ]
    ordered: Dict[str, str] = {}
    for key in priority:
        if key in temp_limits:
            ordered[key] = temp_limits.pop(key)
    ordered.update(temp_limits)
    return ordered


def _parse_filesystem_info(df_output: Optional[str]) -> List[Dict[str, str]]:
    if not df_output:
        return []
    lines = [line.rstrip() for line in df_output.strip().split('\n') if line.strip()]
    if len(lines) < 2:
        return []

    entries: List[Dict[str, str]] = []
    i = 1
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        parts = re.split(r"\s+", line)
        has_percentage = any(re.match(r"^\d{1,3}%$", p) for p in parts)

        if not has_percentage and i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            if next_line:
                line = parts[0] + ' ' + next_line
                i += 1
        final_parts = re.split(r"\s+", line)
        percent_idx = next((idx for idx, p in enumerate(final_parts) if re.match(r"^\d{1,3}%$", p)), -1)
        if percent_idx >= 4:
            entries.append({
                'Filesystem': ' '.join(final_parts[:percent_idx - 3]),
                'Size': final_parts[percent_idx - 3],
                'Used': final_parts[percent_idx - 2],
                'Available': final_parts[percent_idx - 1],
                'Use%': final_parts[percent_idx],
                'Mounted on': ' '.join(final_parts[percent_idx + 1:]),
            })
        elif len(final_parts) >= 6 and re.match(r"^\d{1,3}%$", final_parts[4]):
            entries.append({
                'Filesystem': final_parts[0],
                'Size': final_parts[1],
                'Used': final_parts[2],
                'Available': final_parts[3],
                'Use%': final_parts[4],
                'Mounted on': ' '.join(final_parts[5:]),
            })
        i += 1

    return entries


def _get_cpu_cores() -> str:
    """Read /proc/cpuinfo to compute cores and threads, matching the parent webapp's format."""
    try:
        cpuinfo = _safe_read_text('/proc/cpuinfo')
        if not cpuinfo:
            return str(os.cpu_count() or '??')
        threads = len(re.findall(r'^processor\s*:', cpuinfo, re.MULTILINE))
        cores_match = re.search(r'^cpu cores\s*:\s*(\d+)', cpuinfo, re.MULTILINE)
        if not threads or not cores_match:
            return str(os.cpu_count() or '??')
        cores_per_socket = int(cores_match.group(1))
        physical_ids = re.findall(r'^physical id\s*:\s*(\d+)', cpuinfo, re.MULTILINE)
        sockets = len(set(physical_ids)) if physical_ids else 1
        total_cores = sockets * cores_per_socket
        if threads > total_cores:
            return f"{total_cores} Cores / {threads} Threads"
        return str(total_cores)
    except Exception:
        return str(os.cpu_count() or '??')


def _get_os_info() -> str:
    os_release = _safe_read_text('/etc/os-release')
    if os_release:
        for line in os_release.split('\n'):
            if line.startswith('PRETTY_NAME='):
                value = line.split('=', 1)[1].strip().strip('"')
                if value:
                    return value
    return platform.platform()


def _parse_supervisord_restart(log_content: Any) -> Optional[str]:
    text = _coerce_log_text(log_content)
    if not text:
        return None
    lines = text.split('\n')
    target_line = None
    for line in reversed(lines):
        if 'success: backend entered RUNNING state' in line:
            target_line = line
            break
    if not target_line:
        return None
    match = re.match(r"^(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2},\d{3})", target_line)
    if not match:
        return None
    timestamp_str = match.group(1).replace(',', '.')
    try:
        dt = datetime.fromisoformat(timestamp_str)
        return dt.strftime('%b %d, %Y, %I:%M %p')
    except Exception:
        return None


def _find_spark_version(settings: Any) -> Optional[str]:
    if isinstance(settings, dict):
        for key, value in settings.items():
            if isinstance(key, str) and key.lower() in ('spark.version', 'sparkversion'):
                return str(value)
            found = _find_spark_version(value)
            if found:
                return found
    elif isinstance(settings, list):
        for item in settings:
            found = _find_spark_version(item)
            if found:
                return found
    return None


def _format_camel_case(value: str) -> str:
    value = value.replace('.', ' ')
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value).split()
    return ' '.join(part.capitalize() for part in parts)


def _format_date_string(value: str) -> str:
    if not value:
        return value
    if len(value) == 8 and value.isdigit():
        try:
            dt = datetime.strptime(value, '%Y%m%d')
            return dt.strftime('%b %d, %Y')
        except Exception:
            return value
    return value


def _parse_license(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        'license': data or {},
        'licenseInfo': data or {},
        'company': None,
        'licenseProperties': {},
        'hasLicenseUsage': False,
    }
    if not data:
        return result

    content = data.get('content') if isinstance(data.get('content'), dict) else data
    licensee = content.get('licensee') or {}
    if isinstance(licensee, dict):
        result['company'] = licensee.get('company')

    properties = content.get('properties') or {}
    for key, value in properties.items():
        formatted_key = _format_camel_case(str(key))
        if key == 'emittedOn' and isinstance(value, str):
            formatted_value = _format_date_string(value)
        else:
            formatted_value = str(value)
        result['licenseProperties'][formatted_key] = formatted_value

    if content.get('expiresOn'):
        result['licenseProperties']['Expires On'] = _format_date_string(content['expiresOn'])

    usage = content.get('usage') or {}

    def usage_value(current: Any, limit: Any) -> Optional[str]:
        try:
            current_f = float(current)
            limit_f = float(limit)
        except Exception:
            return None
        if limit_f <= 0:
            return None
        return f"{current} / {limit} ({round((current_f / limit_f) * 100)}%)"

    if usage:
        result['hasLicenseUsage'] = True
        if usage.get('namedUsers'):
            current = usage['namedUsers'].get('current')
            limit = usage['namedUsers'].get('limit')
            value = usage_value(current, limit)
            if value:
                result['licenseProperties']['Named Users'] = value
        if usage.get('concurrentUsers'):
            current = usage['concurrentUsers'].get('current')
            limit = usage['concurrentUsers'].get('limit')
            value = usage_value(current, limit)
            if value:
                result['licenseProperties']['Concurrent Users'] = value
        if usage.get('connections'):
            current = usage['connections'].get('current')
            limit = usage['connections'].get('limit')
            value = usage_value(current, limit)
            if value:
                result['licenseProperties']['Connections'] = value
        if usage.get('projects'):
            current = usage['projects'].get('current')
            limit = usage['projects'].get('limit')
            value = usage_value(current, limit)
            if value:
                result['licenseProperties']['Projects'] = value
        if usage.get('features'):
            for feature in usage['features']:
                name = feature.get('name')
                current = feature.get('current')
                limit = feature.get('limit')
                if name:
                    value = usage_value(current, limit)
                    if value:
                        result['licenseProperties'][_format_camel_case(name)] = value

    return result


def _parse_log_errors(content: Any) -> Dict[str, Any]:
    text = _coerce_log_text(content)
    if not text:
        return {
            'formattedLogErrors': 'No log errors found',
            'rawLogErrors': [],
            'logStats': {
                'Total Lines': 0,
                'Unique Errors': 0,
                'Displayed Errors': 0,
            }
        }

    lines = text.split('\n')
    lines_before = 10
    lines_after = 100
    time_threshold = 5
    max_errors = 5
    log_levels = [r"\[ERROR\]", r"\[FATAL\]", r"\[SEVERE\]", r"\[WARN\]", r"\bERROR\b", r"\bFATAL\b", r"\bSEVERE\b", r"\bWARN\b"]
    log_level_regex = re.compile(r"(" + '|'.join(log_levels) + r")")
    timestamp_regex = re.compile(r"\[(\d{4}/\d{2}/\d{2}-\d{2}:\d{2}:\d{2}\.\d{3})\]")
    leading_timestamp_regex = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d{3})?)")

    def parse_ts(line: str) -> Optional[float]:
        match = timestamp_regex.search(line)
        if match:
            try:
                dt = datetime.strptime(match.group(1), '%Y/%m/%d-%H:%M:%S.%f')
                return dt.timestamp()
            except Exception:
                pass
        alt = leading_timestamp_regex.search(line)
        if alt:
            value = alt.group(1).replace(',', '.')
            try:
                dt = datetime.fromisoformat(value)
                return dt.timestamp()
            except Exception:
                return None
        return None

    line_count = 0
    error_count = 0
    recent_errors = []
    error_signatures = set()
    before_buffer: List[str] = []
    collecting_after = 0
    after_buffer: List[str] = []
    last_error_timestamp: Optional[float] = None
    last_error_had_real_timestamp = False
    error_line = 0
    error_timestamp_str = ''

    for line in lines:
        line_count += 1

        if collecting_after > 0:
            after_buffer.append(line)
            collecting_after -= 1
            if collecting_after == 0:
                header = "\n" + '=' * 40 + f"\nERROR FOUND AT LINE {error_line} (TIMESTAMP: {error_timestamp_str}):\n" + '=' * 40 + "\n\n\n\n"
                current_error = [header] + before_buffer + after_buffer
                recent_errors.append({'timestamp': error_timestamp_str, 'data': current_error})
                if len(recent_errors) > max_errors:
                    recent_errors.pop(0)
                after_buffer = []
                before_buffer = []
                continue

        before_buffer.append(line)
        if len(before_buffer) > lines_before:
            before_buffer.pop(0)

        if not log_level_regex.search(line):
            continue

        current_ts = parse_ts(line)
        had_real_timestamp = current_ts is not None
        if current_ts is None:
            # Keep parsing stacktraces and non-standard logs that do not carry timestamps.
            current_ts = float(line_count)

        timestamp_str = datetime.fromtimestamp(current_ts).strftime('%Y-%m-%d-%H:%M:%S')
        signature = line[-60:].strip() if len(line) > 60 else line.strip()
        if signature in error_signatures:
            error_signatures.remove(signature)

        if last_error_timestamp is not None and had_real_timestamp and last_error_had_real_timestamp:
            if current_ts - last_error_timestamp < time_threshold:
                if collecting_after > 0:
                    collecting_after = max(collecting_after, lines_after)
                    after_buffer.append(line)
                    collecting_after -= 1
                continue

        error_count += 1
        error_line = line_count
        error_timestamp_str = timestamp_str
        last_error_timestamp = current_ts
        last_error_had_real_timestamp = had_real_timestamp
        error_signatures.add(signature)

        collecting_after = lines_after
        after_buffer = [line]
        collecting_after -= 1

    if collecting_after > 0:
        header = "\n" + '=' * 40 + f"\nERROR FOUND AT LINE {error_line} (TIMESTAMP: {error_timestamp_str}):\n" + '=' * 40 + "\n\n\n\n"
        current_error = [header] + before_buffer + after_buffer
        recent_errors.append({'timestamp': error_timestamp_str, 'data': current_error})
        if len(recent_errors) > max_errors:
            recent_errors.pop(0)

    if recent_errors:
        formatted = _format_log_errors(recent_errors)
    else:
        # No regex-matched errors — show last 1000 lines raw as a fallback
        tail_lines = lines[-1000:] if len(lines) > 1000 else lines
        raw_tail = '\n'.join(tail_lines)
        escaped = (raw_tail
                   .replace('&', '&amp;')
                   .replace('<', '&lt;')
                   .replace('>', '&gt;'))
        formatted = (
            '<div class="log-error-block">'
            '<div class="log-header">No ERROR/FATAL/SEVERE/WARN patterns matched — showing last '
            f'{len(tail_lines):,} lines of backend.log</div>'
            f'<pre style="white-space:pre-wrap;word-break:break-all;font-size:12px;">{escaped}</pre>'
            '</div>'
        )
        recent_errors = [{'timestamp': 'tail', 'data': tail_lines}]

    return {
        'formattedLogErrors': formatted,
        'rawLogErrors': recent_errors,
        'logStats': {
            'Total Lines': line_count,
            'Unique Errors': error_count,
            'Displayed Errors': len(recent_errors),
        }
    }


def _format_log_errors(errors: List[Dict[str, Any]]) -> str:
    if not errors:
        return 'No log errors found'

    output = ''
    for error in errors:
        output += '<div class="log-error-block">'
        for line in error['data']:
            if 'ERROR FOUND AT LINE' in line:
                header = line.replace('=' * 40, '=' * 20)
                header_parts = header.split('\n')
                formatted_header = ''
                for part in header_parts:
                    formatted_header += '<br>' if part.strip() == '' else part + '<br>'
                formatted_header += '<br>'
                output += f'<div class="log-entry log-header">{formatted_header}</div>'
                continue

            class_name = 'log-entry'
            if '[INFO]' in line or re.search(r"\bINFO\b", line):
                class_name += ' log-info'
            elif '[WARN]' in line or re.search(r"\bWARN\b", line):
                class_name += ' log-warn'
            elif '[ERROR]' in line or re.search(r"\bERROR\b", line):
                class_name += ' log-error'
            elif '[FATAL]' in line or re.search(r"\bFATAL\b", line):
                class_name += ' log-fatal'
            elif '[SEVERE]' in line or re.search(r"\bSEVERE\b", line):
                class_name += ' log-severe'
            elif '[DEBUG]' in line or re.search(r"\bDEBUG\b", line):
                class_name += ' log-debug'
            elif '[TRACE]' in line or re.search(r"\bTRACE\b", line):
                class_name += ' log-trace'

            formatted_line = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            ts_match = re.search(r"\[(\d{4}/\d{2}/\d{2}-\d{2}:\d{2}:\d{2}\.\d{3})\]", formatted_line)
            if ts_match:
                formatted_line = formatted_line.replace(ts_match.group(0), f'<span class="log-timestamp">{ts_match.group(0)}</span>')
            else:
                start_ts_match = re.search(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d{3})?)", formatted_line)
                if start_ts_match:
                    formatted_line = formatted_line.replace(start_ts_match.group(1), f'<span class="log-timestamp">{start_ts_match.group(1)}</span>')

            level_match = re.search(r"\[(INFO|WARN|ERROR|FATAL|SEVERE|DEBUG|TRACE)\]", formatted_line)
            if level_match:
                formatted_line = formatted_line.replace(level_match.group(0), f'<span class="log-level">{level_match.group(0)}</span>')

            formatted_line = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", '<span class="hljs-number">\\g<0></span>', formatted_line)
            formatted_line = re.sub(r"\[ct: \d+\]", '<span class="hljs-number">\\g<0></span>', formatted_line)
            formatted_line = re.sub(
                r"\d+\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com\/[a-z0-9.\/-]+:[a-z0-9.\/-]+",
                '<span class="hljs-string">\\g<0></span>',
                formatted_line,
            )
            formatted_line = re.sub(
                r"\b(pod|deployment|service|node|configmap|secret|namespace|replicaset|daemonset)s?\b",
                '<span class="hljs-title">\\g<0></span>',
                formatted_line,
                flags=re.IGNORECASE,
            )
            formatted_line = re.sub(
                r"Process [a-z]+ done \(return code \d+\)|Running [a-z]+ \([^)]+\)",
                '<span class="hljs-comment">\\g<0></span>',
                formatted_line,
            )

            output += f'<div class="{class_name}">{formatted_line}</div>'
        output += '</div>'
    return output


def _build_dir_tree(
    root_path: str,
    max_depth: int,
    target_path: Optional[str] = None,
    approximate_limit: bool = False,
) -> Dict[str, Any]:
    root_path = os.path.abspath(root_path)
    target = os.path.abspath(target_path) if target_path else root_path
    if not target.startswith(root_path):
        target = root_path
    max_depth = max(1, int(max_depth or 1))

    exclude_virtual_mounts = root_path == '/'
    excluded_prefixes = ('/proc', '/sys', '/dev', '/run') if exclude_virtual_mounts else tuple()
    skip_symlink_entries = exclude_virtual_mounts

    def should_skip_path(path: str) -> bool:
        normalized = os.path.abspath(path)
        for prefix in excluded_prefixes:
            if normalized == prefix or normalized.startswith(prefix + os.sep):
                return True
        return False

    debug_state: Dict[str, Any] = {
        'rootPath': root_path,
        'targetPath': target,
        'maxDepth': max_depth,
        'approximateLimit': bool(approximate_limit),
        'excludedPrefixes': list(excluded_prefixes),
        'nodesVisited': 0,
        'dirsVisited': 0,
        'filesVisited': 0,
        'entriesScanned': 0,
        'symlinksSeen': 0,
        'skippedSymlinks': 0,
        'skippedEntries': 0,
        'statErrors': 0,
        'scanErrors': 0,
        'largeLeafs': [],
        'errors': [],
        'permissionDeniedPaths': [],
        'overlayUnknownBytes': 0,
        'topChildren': [],
        'specialMountTotals': [],
    }

    def record_error(kind: str, path: str, exc: Exception) -> None:
        if isinstance(exc, PermissionError):
            denied = debug_state['permissionDeniedPaths']
            if path not in denied and len(denied) < 16:
                denied.append(path)
        if len(debug_state['errors']) >= 12:
            return
        debug_state['errors'].append({
            'kind': kind,
            'path': path,
            'error': str(exc),
        })

    def record_large_leaf(path: str, size: int, reason: str) -> None:
        if size <= 0:
            return
        # Keep a short list to avoid bloating responses.
        items: List[Dict[str, Any]] = debug_state['largeLeafs']
        items.append({
            'path': path,
            'size': size,
            'humanSize': _format_size_human(size),
            'reason': reason,
        })
        items.sort(key=lambda item: int(item.get('size') or 0), reverse=True)
        del items[12:]

    def depth_for(path: str) -> int:
        if path == root_path:
            return 0
        relative = os.path.relpath(path, root_path)
        if relative in ('.', ''):
            return 0
        return relative.count(os.sep) + 1

    def make_node(
        path: str,
        is_directory: bool,
        size: int,
        own_size: int,
        file_count: int,
        depth: int,
        has_hidden_children: bool,
        children: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        return {
            'name': os.path.basename(path) or path,
            'path': path,
            'size': int(max(0, size)),
            'ownSize': int(max(0, own_size)),
            'isDirectory': bool(is_directory),
            'children': children or [],
            'fileCount': int(max(0, file_count)),
            'depth': int(max(0, depth)),
            'hasHiddenChildren': bool(has_hidden_children),
        }

    def summarize_directory(path: str, own_size: int) -> Dict[str, Any]:
        total_size = int(max(0, own_size))
        total_files = 0
        has_children = False

        if approximate_limit:
            def on_walk_error(exc: OSError) -> None:
                debug_state['scanErrors'] += 1
                record_error('walk', getattr(exc, 'filename', path) or path, exc)

            for walk_root, dirs, files in os.walk(path, topdown=True, followlinks=False, onerror=on_walk_error):
                filtered_dirs: List[str] = []
                for dir_name in list(dirs):
                    dir_path = os.path.join(walk_root, dir_name)
                    debug_state['entriesScanned'] += 1
                    if should_skip_path(dir_path):
                        debug_state['skippedEntries'] += 1
                        continue
                    if os.path.islink(dir_path):
                        debug_state['symlinksSeen'] += 1
                        if skip_symlink_entries:
                            debug_state['skippedSymlinks'] += 1
                            continue
                    filtered_dirs.append(dir_name)
                    has_children = True
                dirs[:] = filtered_dirs

                for file_name in files:
                    file_path = os.path.join(walk_root, file_name)
                    debug_state['entriesScanned'] += 1
                    if should_skip_path(file_path):
                        debug_state['skippedEntries'] += 1
                        continue
                    if os.path.islink(file_path):
                        debug_state['symlinksSeen'] += 1
                        if skip_symlink_entries:
                            debug_state['skippedSymlinks'] += 1
                            continue
                    has_children = True
                    try:
                        file_stat = os.lstat(file_path)
                        file_size = int(max(0, file_stat.st_size))
                        total_size += file_size
                        total_files += 1
                        debug_state['filesVisited'] += 1
                        if file_size >= 100 * 1024 ** 3:
                            record_large_leaf(file_path, file_size, 'walk-depth-limit')
                    except Exception as exc:
                        debug_state['statErrors'] += 1
                        record_error('stat', file_path, exc)
        else:
            try:
                with os.scandir(path) as it:
                    for entry in it:
                        debug_state['entriesScanned'] += 1
                        entry_path = entry.path
                        if should_skip_path(entry_path):
                            debug_state['skippedEntries'] += 1
                            continue
                        if entry.is_symlink():
                            debug_state['symlinksSeen'] += 1
                            if skip_symlink_entries:
                                debug_state['skippedSymlinks'] += 1
                                continue
                        has_children = True
                        if entry.is_file(follow_symlinks=False):
                            try:
                                entry_stat = entry.stat(follow_symlinks=False)
                                file_size = int(max(0, entry_stat.st_size))
                                total_size += file_size
                                total_files += 1
                                debug_state['filesVisited'] += 1
                            except Exception as exc:
                                debug_state['statErrors'] += 1
                                record_error('stat', entry_path, exc)
            except Exception as exc:
                debug_state['scanErrors'] += 1
                record_error('scandir', path, exc)
        return {
            'totalSize': int(max(0, total_size)),
            'totalFiles': int(max(0, total_files)),
            'hasChildren': bool(has_children),
        }

    def scan_node(path: str) -> Dict[str, Any]:
        debug_state['nodesVisited'] += 1
        node_depth = depth_for(path)

        if path != root_path and should_skip_path(path):
            debug_state['skippedEntries'] += 1
            return make_node(path, True, 0, 0, 0, node_depth, False)

        try:
            node_stat = os.lstat(path)
        except Exception as exc:
            debug_state['statErrors'] += 1
            record_error('stat', path, exc)
            return make_node(path, False, 0, 0, 0, node_depth, False)

        if os.path.islink(path):
            debug_state['symlinksSeen'] += 1
            if skip_symlink_entries and path != root_path:
                debug_state['skippedSymlinks'] += 1
                return make_node(path, False, 0, 0, 0, node_depth, False)

        is_directory = os.path.isdir(path)
        own_size = int(max(0, node_stat.st_size))
        if not is_directory:
            debug_state['filesVisited'] += 1
            if own_size >= 100 * 1024 ** 3:
                record_large_leaf(path, own_size, 'leaf-size')
            return make_node(path, False, own_size, own_size, 1, node_depth, False)

        debug_state['dirsVisited'] += 1

        if node_depth >= max_depth:
            summary = summarize_directory(path, own_size)
            return make_node(
                path,
                True,
                int(summary['totalSize']),
                own_size,
                int(summary['totalFiles']),
                node_depth,
                bool(summary['hasChildren']),
            )

        children: List[Dict[str, Any]] = []
        total_size = own_size
        total_files = 0
        has_hidden_children = False
        try:
            with os.scandir(path) as it:
                for entry in it:
                    debug_state['entriesScanned'] += 1
                    entry_path = entry.path
                    if should_skip_path(entry_path):
                        debug_state['skippedEntries'] += 1
                        continue
                    if entry.is_symlink():
                        debug_state['symlinksSeen'] += 1
                        if skip_symlink_entries:
                            debug_state['skippedSymlinks'] += 1
                            continue
                    child = scan_node(entry_path)
                    children.append(child)
                    total_size += int(child.get('size') or 0)
                    total_files += int(child.get('fileCount') or 0)
                    if child.get('hasHiddenChildren'):
                        has_hidden_children = True
        except Exception as exc:
            debug_state['scanErrors'] += 1
            record_error('scandir', path, exc)
            has_hidden_children = True

        children.sort(key=lambda child: int(child.get('size') or 0), reverse=True)
        return make_node(
            path,
            True,
            int(max(0, total_size)),
            own_size,
            int(max(0, total_files)),
            node_depth,
            has_hidden_children,
            children,
        )

    root_node = scan_node(target)
    mount_summary: Optional[Dict[str, Any]] = None
    if root_path == '/' and isinstance(root_node, dict):
        if target == root_path:
            mount_summary = _apply_df_overlay_to_root_tree(root_node, debug_state)
        else:
            mount_summary = _summarize_df_mounts()
            _overlay_mount_usage_on_node(root_node, target, depth_for(target), mount_summary, debug_state)
            if mount_summary:
                debug_state['dfRootUsed'] = int(mount_summary.get('rootUsed') or 0)
                debug_state['dfMountedUsed'] = int(mount_summary.get('mountedUsed') or 0)
                debug_state['dfTotalUsed'] = int(mount_summary.get('totalUsed') or 0)

    if isinstance(root_node, dict):
        debug_state['totalSize'] = int(root_node.get('size', 0) or 0)
        debug_state['totalFiles'] = int(root_node.get('fileCount', 0) or 0)
        top_children = (root_node.get('children') or [])[:8]
        debug_state['topChildren'] = [
            {
                'path': str(child.get('path') or ''),
                'size': int(child.get('size') or 0),
                'humanSize': _format_size_human(int(child.get('size') or 0)),
                'fileCount': int(child.get('fileCount') or 0),
            }
            for child in top_children
        ]

        if root_path == '/':
            special_totals: Dict[str, int] = {}
            for child in (root_node.get('children') or []):
                child_path = str(child.get('path') or '')
                child_size = int(child.get('size') or 0)
                for prefix in ('/proc', '/sys', '/dev', '/run'):
                    if child_path == prefix or child_path.startswith(prefix + '/'):
                        special_totals[prefix] = special_totals.get(prefix, 0) + child_size
                        break
            debug_state['specialMountTotals'] = [
                {
                    'path': key,
                    'size': value,
                    'humanSize': _format_size_human(value),
                }
                for key, value in sorted(special_totals.items(), key=lambda item: item[1], reverse=True)
            ]

    app.logger.info(
        "[dir-tree] root=%s target=%s total=%s files=%s nodes=%s dirs=%s filesVisited=%s scanned=%s symlinks=%s skippedSymlinks=%s skippedEntries=%s statErrors=%s scanErrors=%s",
        root_path,
        target,
        _format_size_human(int(debug_state.get('totalSize') or 0)),
        int(debug_state.get('totalFiles') or 0),
        int(debug_state.get('nodesVisited') or 0),
        int(debug_state.get('dirsVisited') or 0),
        int(debug_state.get('filesVisited') or 0),
        int(debug_state.get('entriesScanned') or 0),
        int(debug_state.get('symlinksSeen') or 0),
        int(debug_state.get('skippedSymlinks') or 0),
        int(debug_state.get('skippedEntries') or 0),
        int(debug_state.get('statErrors') or 0),
        int(debug_state.get('scanErrors') or 0),
    )
    if int(debug_state.get('dfTotalUsed') or 0) > 0:
        app.logger.info(
            "[dir-tree] df-overlay total=%s rootfs=%s mounted=%s included=%s excluded=%s",
            _format_size_human(int(debug_state.get('dfTotalUsed') or 0)),
            _format_size_human(int(debug_state.get('dfRootUsed') or 0)),
            _format_size_human(int(debug_state.get('dfMountedUsed') or 0)),
            len((mount_summary or {}).get('included') or debug_state.get('dfMountsIncluded') or []),
            len((mount_summary or {}).get('excluded') or debug_state.get('dfMountsExcluded') or []),
        )
    if int(debug_state.get('overlayUnknownBytes') or 0) > 0:
        app.logger.warning(
            "[dir-tree] unscanned usage overlaid: %s",
            _format_size_human(int(debug_state.get('overlayUnknownBytes') or 0)),
        )
    if debug_state.get('specialMountTotals'):
        app.logger.warning("[dir-tree] special mounts included in totals: %s", debug_state.get('specialMountTotals'))
    if debug_state.get('largeLeafs'):
        app.logger.warning("[dir-tree] large leaf entries detected: %s", debug_state.get('largeLeafs'))

    if target != root_path:
        return {'node': root_node, 'debug': debug_state}

    return {
        'root': root_node,
        'totalSize': root_node['size'],
        'totalFiles': root_node['fileCount'],
        'rootPath': root_node['path'],
        'debug': debug_state,
    }


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _parallel_workers(default: int = 8) -> int:
    raw = os.environ.get('DIAG_PARSER_MAX_WORKERS')
    if raw:
        try:
            return max(1, min(_BACKEND_SETTINGS['parallel_workers_max'], int(raw)))
        except Exception:
            pass
    return max(1, min(_BACKEND_SETTINGS['parallel_workers_max'], default))


def _record_benchmark_operation(name: str, elapsed_ms: float, calls: int = 1) -> None:
    recorder = getattr(_THREAD_LOCAL, 'bench_record_op', None)
    if not callable(recorder):
        return
    try:
        recorder(name, elapsed_ms, calls)
    except Exception:
        pass


def _bench_call(name: str, fn, *args, **kwargs):
    started = time.time()
    try:
        return fn(*args, **kwargs)
    finally:
        _record_benchmark_operation(name, (time.time() - started) * 1000.0, 1)


def _get_sdk_cache():
    global _SDK_CACHE
    if _SDK_CACHE is None:
        # The SQL-backed tracking cache was dropped; this is the in-memory cache.
        from sdk_cache import SdkApiCache
        _SDK_CACHE = SdkApiCache(None)
    return _SDK_CACHE


def _instance_id() -> str:
    global _INSTANCE_ID_CACHED
    if _INSTANCE_ID_CACHED is not None:
        return _INSTANCE_ID_CACHED
    try:
        install_ini = _safe_read_text(os.path.join(_dip_home(), 'install.ini'))
        if install_ini:
            current_section = None
            for line in install_ini.split('\n'):
                line = line.strip()
                if line.startswith('[') and line.endswith(']'):
                    current_section = line[1:-1].lower()
                    continue
                if current_section == 'general' and '=' in line:
                    key, value = [part.strip() for part in line.split('=', 1)]
                    if key.lower() == 'installid':
                        _INSTANCE_ID_CACHED = value
                        return value
    except Exception:
        pass
    return 'unknown'


def _sdk_cache_instance_id() -> str:
    """Compose the sdk_cache instance key: local install id + active host id.

    This keeps cached data isolated per (local DSS, active host) so switching
    between hosts never serves stale data from a different one.
    """
    base = _instance_id()
    try:
        host_id = getattr(g, 'host_id', 'local') or 'local'
    except RuntimeError:
        host_id = getattr(_THREAD_LOCAL, 'host_id', 'local') or 'local'
    return base if host_id == 'local' else f'{base}|{host_id}'


def _sdk_fetch(cache_key: str, ttl_seconds: int, fetch_fn, deadline_ts=None):
    t0 = time.time()
    result = _get_sdk_cache().get_or_fetch(_sdk_cache_instance_id(), cache_key, ttl_seconds, fetch_fn, deadline_ts)
    app.logger.debug("[perf:sdk_cache] GET key=%s elapsed=%.1fms", cache_key, (time.time() - t0) * 1000.0)
    return result


def _notify_progress(
    callback: Optional[Callable[..., None]],
    step: str,
    message: str,
    level: str = 'info',
    project_key: Optional[str] = None,
    elapsed_ms: Optional[float] = None,
) -> None:
    if not callable(callback):
        return
    try:
        callback(
            step=step,
            message=message,
            level=level,
            project_key=project_key,
            elapsed_ms=elapsed_ms,
        )
    except Exception:
        pass


def _local_thread_client() -> Any:
    """Return the local DSS thread-pooled client, ignoring any active remote host."""
    client = getattr(_THREAD_LOCAL, 'dss_client', None)
    if client is None:
        client = dataiku.api_client()
        # Increase connection pool to handle concurrent worker threads
        from requests.adapters import HTTPAdapter
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20)
        if hasattr(client, '_session'):
            client._session.mount('http://', adapter)
            client._session.mount('https://', adapter)
        setattr(_THREAD_LOCAL, 'dss_client', client)
    return client


def _thread_client() -> Any:
    """Return the active-host thread-pooled DSS client.

    Request-scoped handlers should prefer `g.client` (set by `_attach_client`).
    Worker threads inherit `_THREAD_LOCAL.host_id` through the host-aware
    ThreadPoolExecutor below, so legacy helpers still target the selected host.
    """
    host_id = getattr(_THREAD_LOCAL, 'host_id', None)
    if host_id is None:
        try:
            host_id = getattr(g, 'host_id', 'local')
        except RuntimeError:
            host_id = 'local'
    host_id = host_id or 'local'
    if host_id != 'local':
        return _resolve_client(host_id)
    return _local_thread_client()


# ── Multi-instance host routing ──
MACRO_PROJECT_KEY = 'ADMINTOOLKIT'
MACRO_PROJECT_DEFAULT_NAME = 'Admin Toolkit'


class MacroProjectMissing(Exception):
    """Raised when ADMINTOOLKIT does not exist on the active host.

    Caught by the @app.errorhandler below and turned into a 409 JSON response
    so the frontend can pop the confirm-create modal and retry.
    """
    pass


def _remote_host_config(host_id: str) -> Optional[Dict[str, Any]]:
    """Resolve a remote host preset by id. Returns None if not found.

    Uses dataiku.api_client() directly (not g.client/active client) because
    plugin presets live on the LOCAL DSS install — they describe how to
    reach remote hosts, so reading them must always be a local operation.
    """
    if not host_id or host_id == 'local':
        return None
    try:
        raw = dataiku.api_client().get_plugin('admin-toolkit').get_settings().get_raw()
    except Exception:
        return None
    presets = raw.get('presets') if isinstance(raw, dict) else None
    if not isinstance(presets, list):
        return None
    for preset in presets:
        if not isinstance(preset, dict):
            continue
        # DSS prefixes preset types as `parameter-set-<plugin-id>-<name>`;
        # match on suffix so both raw and prefixed shapes work.
        if not (preset.get('type') or '').endswith('remote-dss-host'):
            continue
        if preset.get('name') != host_id:
            continue
        cfg = preset.get('config') or {}
        return {
            'id': preset.get('name'),
            'label': cfg.get('label') or preset.get('name'),
            'url': (cfg.get('url') or '').rstrip('/'),
            'apiKey': cfg.get('apiKey') or '',
            'verifyTls': bool(cfg.get('verifyTls', True)),
            'backupProjectKey': (cfg.get('backupProjectKey') or '').strip(),
        }
    return None


def _list_remote_hosts() -> List[Dict[str, Any]]:
    """Return all configured remote host presets (without API keys)."""
    try:
        raw = dataiku.api_client().get_plugin('admin-toolkit').get_settings().get_raw()
    except Exception:
        return []
    presets = raw.get('presets') if isinstance(raw, dict) else None
    if not isinstance(presets, list):
        return []
    out: List[Dict[str, Any]] = []
    for preset in presets:
        if not isinstance(preset, dict):
            continue
        if not (preset.get('type') or '').endswith('remote-dss-host'):
            continue
        cfg = preset.get('config') or {}
        out.append({
            'id': preset.get('name'),
            'label': cfg.get('label') or preset.get('name'),
            'url': (cfg.get('url') or '').rstrip('/'),
        })
    return out


def _build_remote_client(cfg: Dict[str, Any]) -> Any:
    """Construct a pooled dataikuapi.DSSClient for a remote preset."""
    from requests.adapters import HTTPAdapter
    client = dataikuapi.DSSClient(cfg['url'], cfg['apiKey'])
    verify = bool(cfg.get('verifyTls', True))
    if not verify:
        app.logger.warning(
            "[host:%s] TLS verification DISABLED — admin API key is sent over an unverified channel",
            cfg.get('id') or '?',
        )
    if hasattr(client, '_session'):
        client._session.verify = verify
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20)
        client._session.mount('http://', adapter)
        client._session.mount('https://', adapter)
    return client


def _safe_request_host_id() -> str:
    """Return the active host id when inside a request, else 'local'."""
    try:
        return getattr(g, 'host_id', 'local') or 'local'
    except RuntimeError:
        return getattr(_THREAD_LOCAL, 'host_id', 'local') or 'local'


def _active_dss_client() -> Any:
    """Return g.client when inside a request, else fall back to the local
    thread-pooled client. Use this from helpers that may be invoked from
    both request handlers and background loaders/threads."""
    try:
        client = getattr(g, 'client', None)
        if client is not None:
            return client
    except RuntimeError:
        pass
    return _thread_client()


def _resolve_client(host_id: Optional[str] = None) -> Any:
    """Return a pooled DSS client for the host this request targets.

    Routing rules:
      - host_id is read from the X-DSS-Host-Id request header (defaults to 'local').
      - 'local' → dataiku.api_client() (internal ticket, pooled).
      - any other id must match a `remote-dss-host` preset name.
      - Per-host clients are cached per thread (same lifetime as _thread_client).
    """
    if host_id is None:
        try:
            host_id = request.headers.get('X-DSS-Host-Id', 'local') or 'local'
        except RuntimeError:
            host_id = getattr(_THREAD_LOCAL, 'host_id', 'local') or 'local'
    cache = getattr(_THREAD_LOCAL, 'clients_by_host', None)
    if cache is None:
        cache = {}
        _THREAD_LOCAL.clients_by_host = cache
    if host_id in cache:
        return cache[host_id]

    if host_id == 'local':
        client = _local_thread_client()
    else:
        cfg = _remote_host_config(host_id)
        if cfg is None:
            abort(400, f"Unknown DSS host id: {host_id}")
        client = _build_remote_client(cfg)

    cache[host_id] = client
    return client


class ThreadPoolExecutor(_BaseThreadPoolExecutor):
    """ThreadPoolExecutor that propagates Admin Toolkit host context.

    Flask's `g.client` exists only in the request thread. Remote scans fan out
    into workers, and older helpers reacquire a client via `_thread_client()`.
    Capturing the active host here keeps those helpers on the selected target.
    """

    def __init__(self, *args, **kwargs):
        self._admin_toolkit_host_id = _safe_request_host_id()
        self._admin_toolkit_bench_record_op = getattr(_THREAD_LOCAL, 'bench_record_op', None)
        super().__init__(*args, **kwargs)

    def submit(self, fn, /, *args, **kwargs):
        host_id = self._admin_toolkit_host_id
        bench_record_op = self._admin_toolkit_bench_record_op

        def _wrapped(*wrapped_args, **wrapped_kwargs):
            previous_host_marker = object()
            previous_bench_marker = object()
            previous_host = getattr(_THREAD_LOCAL, 'host_id', previous_host_marker)
            previous_bench = getattr(_THREAD_LOCAL, 'bench_record_op', previous_bench_marker)
            _THREAD_LOCAL.host_id = host_id
            if bench_record_op is not None:
                _THREAD_LOCAL.bench_record_op = bench_record_op
            try:
                return fn(*wrapped_args, **wrapped_kwargs)
            finally:
                if previous_host is previous_host_marker:
                    try:
                        delattr(_THREAD_LOCAL, 'host_id')
                    except AttributeError:
                        pass
                else:
                    _THREAD_LOCAL.host_id = previous_host
                if previous_bench is previous_bench_marker:
                    try:
                        delattr(_THREAD_LOCAL, 'bench_record_op')
                    except AttributeError:
                        pass
                else:
                    _THREAD_LOCAL.bench_record_op = previous_bench

        return super().submit(_wrapped, *args, **kwargs)


def _resolve_macro_project(client: Any) -> Any:
    """Return the ADMINTOOLKIT project on the active client.

    Forces a server-side check (get_summary) so we fail fast with
    MacroProjectMissing when the project doesn't exist — the @errorhandler
    turns that into a 409 and the frontend pops the bootstrap modal.

    DSS surfaces "project doesn't exist for this caller" as an
    UnauthorizedException with the message "Failed to read project
    permissions" (NOT a 404). We also see real 404s on some routes.
    To keep the bootstrap flow reliable across DSS versions, ANY error
    from the get_summary preflight is treated as "macro project missing".
    If the host is genuinely unreachable, the subsequent create_project
    call in the modal will surface the network error to the user.
    """
    try:
        project = client.get_project(MACRO_PROJECT_KEY)
        project.get_summary()
        return project
    except Exception:
        raise MacroProjectMissing()


def _local_toolkit_client() -> Any:
    """Client for the DSS instance hosting this webapp/plugin."""
    return _local_thread_client()


def _local_toolkit_project() -> Any:
    """Project that hosts this webapp on the local DSS."""
    return _local_toolkit_client().get_project(dataiku.default_project_key())


def _active_support_project(client: Optional[Any] = None) -> Any:
    """Project used for target-host support artifacts.

    Macro execution always uses ADMINTOOLKIT via _resolve_macro_project().
    Backup destinations are different: local scans use the current webapp
    project, and remote scans prefer the target host's running Admin Toolkit
    webapp project so the dropdown matches what an admin sees on that host.
    """
    active_client = client or _active_dss_client()
    host_id = _safe_request_host_id()
    if host_id == 'local':
        return active_client.get_project(dataiku.default_project_key())
    return active_client.get_project(_remote_backup_project_key(active_client, host_id))


def _is_admin_toolkit_webapp(webapp: Dict[str, Any]) -> bool:
    app_type = str(webapp.get('type') or '').lower()
    app_name = str(webapp.get('name') or '').lower()
    return app_type == 'webapp_admin-toolkit_admin-toolkit' or 'admin-toolkit' in app_type or 'admintoolkit' in app_name


def _remote_backup_project_key(client: Any, host_id: str) -> str:
    """Resolve the target-host project that should own backup managed folders."""
    cfg = _remote_host_config(host_id) or {}
    configured_key = (cfg.get('backupProjectKey') or '').strip()
    if configured_key:
        return configured_key

    def discover() -> str:
        candidates: List[Tuple[int, int, str]] = []
        try:
            projects = client.list_projects()
        except Exception:
            return MACRO_PROJECT_KEY
        for project_info in projects or []:
            project_key = (
                project_info.get('projectKey')
                or project_info.get('key')
                or project_info.get('id')
            )
            if not project_key:
                continue
            try:
                project = client.get_project(project_key)
                webapps = project.list_webapps()
            except Exception:
                continue
            if not any(_is_admin_toolkit_webapp(w) for w in webapps or []):
                continue
            running = 1 if any(_is_admin_toolkit_webapp(w) and w.get('backendRunning') for w in webapps or []) else 0
            try:
                folder_count = len(project.list_managed_folders() or [])
            except Exception:
                folder_count = 0
            candidates.append((running, folder_count, project_key))
        if not candidates:
            return MACRO_PROJECT_KEY
        candidates.sort(key=lambda item: (item[0], item[1], item[2] != MACRO_PROJECT_KEY), reverse=True)
        return candidates[0][2]

    return _cache_get(f'remote_backup_project:{host_id}', 300, discover)


@app.before_request
def _attach_client() -> None:
    """Populate g.client / g.host_id for every /api/* request.

    On preset-resolution failure (unknown host_id, bad URL, bad key) we set
    g.client to None and store the error reason on g.host_error. Handlers
    read g.client and the response handler below surfaces the original
    error as a clean 502 instead of letting downstream AttributeError leak.
    """
    if not request.path.startswith('/api/'):
        return
    host_id = request.headers.get('X-DSS-Host-Id', 'local') or 'local'
    g.host_id = host_id
    g.host_error = None
    view = app.view_functions.get(request.endpoint)
    client_host_id = 'local' if view is not None and getattr(view, '_admin_toolkit_local_only', False) else host_id
    try:
        g.client = _resolve_client(client_host_id)
    except Exception as exc:
        g.client = None
        g.host_error = f'{type(exc).__name__}: {str(exc)[:200]}'
        app.logger.warning("[host:%s client:%s] _resolve_client failed: %s", host_id, client_host_id, g.host_error)


def local_only(view_func):
    """Mark a Flask route as local-only: it reads local-DSS-only state and
    must not be 502'd by _check_host_ready when a remote host is unreachable.

    Used for tracking endpoints that read from the local SQL tracking DB and
    have no dependency on the active host's DSS API client. Add between
    @app.route(...) and `def api_*(...)`."""
    view_func._admin_toolkit_local_only = True
    return view_func


# ─────────────────────────────────────────────────────────────────────────
# Advanced-action gate
#
# The public webapp has no auth, so "read-only unless you hold the password"
# must be enforced server-side. An admin stores a secret (a salted PBKDF2 hash)
# in the (admin-only) plugin setting `red_actions_secret` — generated by
# hash.html, which uses the SAME params as the verifier below. A user unlocks
# by POSTing the plaintext to /api/auth/red/unlock; we hash + compare and hand
# back a stateless signed token (key derived from the stored secret, so it
# auto-invalidates on password rotation and validates across gunicorn workers
# with no shared state). Endpoints marked @advanced 403 without a valid
# token. Phrase parsed/emitted in lockstep: pbkdf2_sha256$<iters>$<b64salt>$<b64derived>.
# ─────────────────────────────────────────────────────────────────────────
_RED_TOKEN_TTL_SECONDS = 5 * 365 * 24 * 3600  # 5 years — effectively "remember forever"
_RED_SIGNING_CONTEXT = b'admin-toolkit-red-v1'
# The unlock token rides in an HttpOnly cookie (not JS-readable → XSS can't steal
# it; auto-sent with same-origin requests). SameSite=Lax keeps it off cross-site
# POST/DELETE, so the @advanced routes stay CSRF-safe.
_RED_COOKIE_NAME = 'admin_toolkit_red'


def _red_secret() -> str:
    """The stored secret (PBKDF2 hash) from LOCAL plugin settings (admin-only).

    Read from dataiku.api_client() (local) — the secret is anchored to the
    plugin install where the webapp runs, independent of the active host.
    Empty string means no secret is configured (permanently locked)."""
    try:
        raw = dataiku.api_client().get_plugin('admin-toolkit').get_settings().get_raw()
        config = raw.get('config', {}) if isinstance(raw, dict) else {}
        return (config.get('red_actions_secret') or '').strip()
    except Exception:
        return ''


def _verify_red_password(password: str, stored: str) -> bool:
    """Verify plaintext against a pbkdf2_sha256$iters$b64salt$b64derived string."""
    try:
        algo, iters_s, salt_b64, hash_b64 = stored.split('$')
        if algo != 'pbkdf2_sha256':
            return False
        iters = int(iters_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        derived = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iters, dklen=len(expected))
        return hmac.compare_digest(derived, expected)
    except Exception:
        return False


def _red_signing_key(stored: str) -> bytes:
    return hashlib.sha256(stored.encode('utf-8') + _RED_SIGNING_CONTEXT).digest()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + ('=' * (-len(text) % 4)))


def _make_red_token(stored: str, exp: int) -> str:
    payload = json.dumps({'exp': exp}, separators=(',', ':')).encode('utf-8')
    sig = hmac.new(_red_signing_key(stored), payload, hashlib.sha256).digest()
    return _b64url(payload) + '.' + _b64url(sig)


def _red_token_exp_ms(token: str) -> int:
    """Epoch-ms expiry of a valid unlock token, or 0 if missing/forged/expired.
    Single source of truth for both the gate and /api/auth/red/status."""
    if not token or '.' not in token:
        return 0
    stored = _red_secret()
    if not stored:
        return 0  # no password configured → permanently locked
    try:
        payload_b64, sig_b64 = token.split('.', 1)
        payload = _b64url_decode(payload_b64)
        expected_sig = hmac.new(_red_signing_key(stored), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(_b64url_decode(sig_b64), expected_sig):
            return 0
        exp = int(json.loads(payload.decode('utf-8')).get('exp', 0))
        return exp * 1000 if exp > int(time.time()) else 0
    except Exception:
        return 0


def _verify_red_token(token: str) -> bool:
    return _red_token_exp_ms(token) > 0


def _red_secure_cookie() -> bool:
    """Set the Secure flag when the browser↔edge hop is HTTPS. TLS terminates at
    the load balancer on the deployed hosts, so trust X-Forwarded-Proto."""
    return request.headers.get('X-Forwarded-Proto', '').lower() == 'https' or request.is_secure


def advanced(view_func):
    """Mark a Flask route as advanced: it is 403'd unless the request carries
    a valid unlock cookie. Add between @app.route(...) and `def api_*(...)`."""
    view_func._admin_toolkit_advanced = True
    return view_func


@app.before_request
def _check_red_unlock() -> Optional[Response]:
    """Gate @advanced endpoints behind a valid unlock cookie."""
    if not request.path.startswith('/api/'):
        return None
    view = app.view_functions.get(request.endpoint)
    if not (view is not None and getattr(view, '_admin_toolkit_advanced', False)):
        return None
    if not _verify_red_token(request.cookies.get(_RED_COOKIE_NAME, '')):
        return jsonify({'error': 'advanced-locked'}), 403
    return None


@app.route('/api/auth/red/unlock', methods=['POST'])
@local_only
def api_auth_red_unlock():
    """Verify a plaintext password against the stored secret; return a signed token."""
    stored = _red_secret()
    if not stored:
        return jsonify({
            'error': 'not-configured',
            'message': 'No Advanced Actions secret is configured. An administrator must paste a secret into the plugin settings.',
        }), 400
    body = request.get_json(silent=True) or {}
    password = body.get('password') or ''
    if not _verify_red_password(password, stored):
        return jsonify({'error': 'invalid-password', 'message': 'Incorrect password.'}), 401
    exp = int(time.time()) + _RED_TOKEN_TTL_SECONDS
    resp = jsonify({'unlocked': True, 'expiresAt': exp * 1000})
    resp.set_cookie(
        _RED_COOKIE_NAME, _make_red_token(stored, exp),
        max_age=_RED_TOKEN_TTL_SECONDS, path='/',
        secure=_red_secure_cookie(), httponly=True, samesite='Lax',
    )
    return resp


@app.route('/api/auth/red/status', methods=['GET'])
@local_only
def api_auth_red_status():
    """Report whether this browser holds a valid unlock cookie (UI hydration on
    boot). The token is HttpOnly, so JS can't read it — it asks the server."""
    exp_ms = _red_token_exp_ms(request.cookies.get(_RED_COOKIE_NAME, ''))
    return jsonify({'unlocked': exp_ms > 0, 'expiresAt': exp_ms})


@app.route('/api/auth/red/lock', methods=['POST'])
@local_only
def api_auth_red_lock():
    """Forget the unlock on this device by clearing the cookie."""
    resp = jsonify({'unlocked': False})
    resp.delete_cookie(_RED_COOKIE_NAME, path='/')
    return resp


# Phase 2: macro invocation IDs. The runnables themselves live at
# python-runnables/host-metrics/ and python-runnables/dbhealth-query/.
_HOST_METRICS_MACRO_ID = 'pyrunnable_admin-toolkit_host-metrics'
_PROCESS_METRICS_MACRO_ID = 'pyrunnable_admin-toolkit_process-metrics'
_DBHEALTH_MACRO_ID = 'pyrunnable_admin-toolkit_dbhealth-query'
_IMAGE_CLEANER_MACRO_ID = 'pyrunnable_admin-toolkit_image-cleaner'
_K8S_INSIGHTS_MACRO_ID = 'pyrunnable_admin-toolkit_k8s-insights'


def _host_metrics_macro(client: Any) -> Dict[str, Any]:
    """Invoke host-metrics macro on the active host. Returns the raw JSON
    result dict (see python-runnables/host-metrics/runnable.py for shape).

    Raises MacroProjectMissing if ADMINTOOLKIT doesn't exist on the host —
    the @errorhandler converts that to a 409 the frontend can react to.
    """
    project = _resolve_macro_project(client)
    macro = project.get_macro(_HOST_METRICS_MACRO_ID)
    run_id = macro.run(params={}, wait=True)
    result = macro.get_result(run_id, as_type='json')
    if not isinstance(result, dict):
        return {'error': f'macro returned non-dict: {type(result).__name__}'}
    return result


def _process_metrics_macro(client: Any) -> Dict[str, Any]:
    """Invoke process-metrics macro on the active host. Returns the raw JSON
    result dict (see python-runnables/process-metrics/runnable.py for shape:
    {ok, processes:[{pid,user,cpuPercent,memPercent,rssKb,vszKb,command}], ...}).

    Raises MacroProjectMissing if ADMINTOOLKIT doesn't exist on the host —
    the @errorhandler converts that to a 409 the frontend can react to.
    """
    project = _resolve_macro_project(client)
    macro = project.get_macro(_PROCESS_METRICS_MACRO_ID)
    run_id = macro.run(params={}, wait=True)
    result = macro.get_result(run_id, as_type='json')
    if not isinstance(result, dict):
        return {'ok': False, 'error': f'macro returned non-dict: {type(result).__name__}'}
    return result


def _dbhealth_macro(client: Any, operation: str, **params: Any) -> Dict[str, Any]:
    """Invoke dbhealth-query macro on the active host.

    operation ∈ {test-password, run-query, list-tables}. Extra params:
    sql, connection, password — included only when not None.
    """
    project = _resolve_macro_project(client)
    macro = project.get_macro(_DBHEALTH_MACRO_ID)
    macro_params: Dict[str, Any] = {'operation': operation}
    for k in ('sql', 'connection', 'password'):
        v = params.get(k)
        if v is not None and v != '':
            macro_params[k] = v
    run_id = macro.run(params=macro_params, wait=True)
    result = macro.get_result(run_id, as_type='json')
    if not isinstance(result, dict):
        return {'ok': False, 'error': f'macro returned non-dict: {type(result).__name__}'}
    return result


def _image_cleaner_macro(client: Any, operation: str, **params: Any) -> Dict[str, Any]:
    """Invoke the target-host image-cleaner macro."""
    project = _resolve_macro_project(client)
    macro = project.get_macro(_IMAGE_CLEANER_MACRO_ID)
    macro_params: Dict[str, Any] = {'operation': operation}
    for key, value in params.items():
        if value is not None:
            macro_params[key] = value
    run_id = macro.run(params=macro_params, wait=True)
    result = macro.get_result(run_id, as_type='json')
    if not isinstance(result, dict):
        return {'ok': False, 'error': f'macro returned non-dict: {type(result).__name__}'}
    return result


def _k8s_insights_macro(client: Any, operation: str = 'audit', **params: Any) -> Dict[str, Any]:
    """Invoke the K8S Insights macro on the active host.

    operation = 'audit' | 'list-clusters'. For 'audit', pass cluster_id and
    optional rules_filter via **params.
    """
    project = _resolve_macro_project(client)
    macro = project.get_macro(_K8S_INSIGHTS_MACRO_ID)
    macro_params: Dict[str, Any] = {'operation': operation}
    for key, value in params.items():
        if value is not None and value != '':
            macro_params[key] = value
    run_id = macro.run(params=macro_params, wait=True)
    result = macro.get_result(run_id, as_type='json')
    if not isinstance(result, dict):
        return {'ok': False, 'error': f'macro returned non-dict: {type(result).__name__}'}
    return result


@app.before_request
def _check_host_ready() -> Optional[Response]:
    """Short-circuit /api/* requests when the active host couldn't be resolved.

    Two exemptions: the 3 /api/hosts/* endpoints that exist precisely to
    diagnose / fix a broken host config, and any view marked @local_only
    (it reads local-only state and doesn't need the active host).
    """
    if not request.path.startswith('/api/'):
        return None
    if getattr(g, 'host_error', None) is None:
        return None
    if request.path in ('/api/hosts', '/api/hosts/check', '/api/hosts/macro-project'):
        return None
    view = app.view_functions.get(request.endpoint)
    if view is not None and getattr(view, '_admin_toolkit_local_only', False):
        return None
    return jsonify({
        'error': 'host-unreachable',
        'hostId': getattr(g, 'host_id', 'local'),
        'detail': g.host_error,
    }), 502


@app.errorhandler(MacroProjectMissing)
def _handle_macro_project_missing(_exc: MacroProjectMissing):
    return jsonify({
        'error': 'macro-project-missing',
        'projectKey': MACRO_PROJECT_KEY,
        'defaultName': MACRO_PROJECT_DEFAULT_NAME,
        'hostId': getattr(g, 'host_id', 'local'),
    }), 409


@app.route('/api/hosts')
def api_hosts():
    """List local + remote-preset hosts. API keys are never returned."""
    hosts = [{'id': 'local', 'label': 'Local DSS', 'url': ''}]
    hosts.extend(_list_remote_hosts())
    return jsonify(hosts)


@app.route('/api/hosts/check', methods=['POST'])
def api_hosts_check():
    """Probe a host: reachable? plugin installed? ADMINTOOLKIT exists?"""
    payload = request.get_json(silent=True) or {}
    host_id = (payload.get('hostId') or 'local').strip()
    try:
        client = _resolve_client(host_id)
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:200]}'})
    result: Dict[str, Any] = {
        'ok': True,
        'pluginInstalled': False,
        'pluginVersion': None,
        'adminToolkitProjectExists': False,
    }
    try:
        plugins = client.list_plugins() or []
        for plug in plugins:
            if isinstance(plug, dict) and plug.get('id') == 'admin-toolkit':
                result['pluginInstalled'] = True
                result['pluginVersion'] = plug.get('version')
                break
    except Exception as exc:
        result['ok'] = False
        result['error'] = f'list_plugins failed: {str(exc)[:200]}'
        return jsonify(result)
    try:
        project = client.get_project(MACRO_PROJECT_KEY)
        project.get_summary()
        result['adminToolkitProjectExists'] = True
    except Exception:
        result['adminToolkitProjectExists'] = False
    return jsonify(result)


@app.route('/api/hosts/macro-project', methods=['POST'])
def api_hosts_macro_project():
    """Create the ADMINTOOLKIT project on the active host."""
    payload = request.get_json(silent=True) or {}
    host_id = (payload.get('hostId') or 'local').strip()
    name = (payload.get('name') or MACRO_PROJECT_DEFAULT_NAME).strip() or MACRO_PROJECT_DEFAULT_NAME
    try:
        client = _resolve_client(host_id)
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:200]}'}), 400
    try:
        client.create_project(MACRO_PROJECT_KEY, name, owner='admin')
        return jsonify({'ok': True, 'projectKey': MACRO_PROJECT_KEY, 'name': name})
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:300]}'}), 500


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return str(value)


def _client_perform_json(client: Any, method: str, path: str) -> Optional[Any]:
    if not hasattr(client, '_perform_json'):
        return None

    # Different DSS client variants expose different signatures.
    for attempt in (
        lambda: client._perform_json(method, path),
        lambda: client._perform_json(path),
    ):
        try:
            response = attempt()
            if isinstance(response, (dict, list)):
                return response
        except Exception:
            continue
    return None


def _parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'y', 'on')


def _unwrap_footprint_payload(value: Any) -> Any:
    current = value
    seen = 0
    while isinstance(current, dict) and seen < 8:
        seen += 1
        nested = current.get('result')
        if not isinstance(nested, dict):
            break
        current = nested
    return current


def _wrap_project_footprint_payload(payload: Any, project_key: Optional[str]) -> Any:
    if not isinstance(payload, dict):
        return payload
    projects = payload.get('projects')
    if not isinstance(projects, dict):
        return payload
    items = projects.get('items')
    if not isinstance(items, list):
        return payload
    if project_key:
        for item in items:
            if isinstance(item, dict) and item.get('projectKey') == project_key:
                return item
    if items:
        first = items[0]
        if isinstance(first, dict):
            return first
    return payload


def _new_footprint_state() -> Dict[str, Any]:
    return {
        'unavailable': False,
        'reason': None,
        'failures': 0,
        'latched_at': 0.0,
    }


_FOOTPRINT_STATES: Dict[str, Dict[str, Any]] = {'local': _new_footprint_state()}
_FOOTPRINT_LOCK = threading.Lock()
_FOOTPRINT_FAIL_THRESHOLD = 2
_FOOTPRINT_COOLDOWN_SECS = 600


def _footprint_state_locked() -> Dict[str, Any]:
    host_id = _cache_host_id()
    return _FOOTPRINT_STATES.setdefault(host_id, _new_footprint_state())


def _footprint_available() -> bool:
    with _FOOTPRINT_LOCK:
        state = _footprint_state_locked()
        if not state.get('unavailable'):
            return True
        latched_at = float(state.get('latched_at') or 0.0)
        if latched_at and (time.time() - latched_at) > _FOOTPRINT_COOLDOWN_SECS:
            app.logger.info("[footprint] attempting after cooldown — %.0fs since latch",
                            time.time() - latched_at)
            state['unavailable'] = False
            state['reason'] = None
            state['failures'] = 0
            state['latched_at'] = 0.0
            return True
        return False


def _footprint_unavailable_reason() -> Optional[str]:
    with _FOOTPRINT_LOCK:
        state = _footprint_state_locked()
        return state.get('reason') if state.get('unavailable') else None


def _footprint_reset_negative_cache() -> None:
    with _FOOTPRINT_LOCK:
        state = _footprint_state_locked()
        state['unavailable'] = False
        state['reason'] = None
        state['failures'] = 0
        state['latched_at'] = 0.0


def _footprint_record_failure(reason: str) -> None:
    with _FOOTPRINT_LOCK:
        state = _footprint_state_locked()
        if state.get('unavailable'):
            return
        state['failures'] = int(state.get('failures') or 0) + 1
        if state['failures'] >= _FOOTPRINT_FAIL_THRESHOLD:
            state['unavailable'] = True
            state['reason'] = reason
            state['latched_at'] = time.time()
            app.logger.warning("[footprint] latched unavailable after %d failures: %s",
                               state['failures'], reason)


def _footprint_record_success() -> None:
    with _FOOTPRINT_LOCK:
        _footprint_state_locked()['failures'] = 0


def _compute_footprint_payload(
    client: Any,
    scope: str,
    project_key: Optional[str],
) -> Optional[Any]:
    if not _footprint_available():
        return None

    op_name = 'compute_all_dss_footprint'
    if scope == 'global':
        op_name = 'compute_global_footprint'
    elif scope == 'project' and project_key:
        op_name = 'compute_project_footprint'

    if hasattr(client, 'get_data_directories_footprint'):
        try:
            footprint_api = _bench_call('get_data_directories_footprint', client.get_data_directories_footprint)
            if scope == 'global':
                return _sdk_fetch(
                    'global_footprint',
                    _BACKEND_SETTINGS['cache_ttl_projects'],
                    lambda: _bench_call(op_name, lambda: _unwrap_footprint_payload(footprint_api.compute_global_only_footprint(wait=True))),
                )
            if scope == 'project' and project_key:
                return _sdk_fetch(
                    f'project_footprint:{project_key}',
                    _BACKEND_SETTINGS['cache_ttl_projects'],
                    lambda: _bench_call(op_name, lambda: _unwrap_footprint_payload(footprint_api.compute_project_footprint(project_key, wait=True))),
                )
            return _bench_call(op_name, lambda: _unwrap_footprint_payload(footprint_api.compute_all_dss_footprint(wait=True)))
        except Exception as exc:
            # On some DSS versions / under load the SDK path fails; fall back to REST.
            app.logger.debug(
                "[footprint] sdk %s scope=%s project=%s failed, falling back to REST: %s: %s",
                op_name, scope, project_key, type(exc).__name__, str(exc)[:200],
            )

    rest_path = '/directories-footprint/all-dss?summaryOnly=false'
    if scope == 'global':
        rest_path = '/directories-footprint/global?summaryOnly=false'
    elif scope == 'project' and project_key:
        rest_path = f'/directories-footprint/projects/{project_key}?summaryOnly=false'

    try:
        response = _bench_call(op_name, _client_perform_json, client, 'GET', rest_path)
    except Exception as exc:
        _footprint_record_failure(f"REST {rest_path}: {type(exc).__name__}: {str(exc)[:200]}")
        app.logger.debug("[footprint] REST %s failed: %s", rest_path, exc)
        return None

    if not isinstance(response, dict):
        _footprint_record_failure(f"REST {rest_path} returned non-dict: {type(response).__name__}")
        app.logger.debug(
            "[footprint] REST %s scope=%s project=%s returned non-dict: type=%s",
            rest_path, scope, project_key, type(response).__name__,
        )
        return None

    _footprint_record_success()
    unwrapped = _unwrap_footprint_payload(response)
    if scope == 'project':
        return _wrap_project_footprint_payload(unwrapped, project_key)
    return unwrapped



def _scope_root(scope: str, project_key: Optional[str]) -> Dict[str, str]:
    if scope == 'all':
        return {'name': '/', 'path': '/'}
    if scope == 'global':
        return {'name': 'global', 'path': '/dss-data/global'}
    if scope == 'project' and project_key:
        return {'name': project_key, 'path': f'/dss-data/projects/{project_key}'}
    return {'name': 'dss_data', 'path': '/dss-data'}





_FOOTPRINT_SCALAR_KEYS = frozenset({
    'size', 'nbFiles', 'nbFolders', 'nbErrors',
    'projectKey', 'name', 'language', 'type',
    'result',
})


def _footprint_details_map(footprint: Any) -> Dict[str, Any]:
    # Footprint payloads may be missing entirely (per-project fetch failed, stale cache, etc.)
    # so every accessor in this family must tolerate a non-dict input and return an empty map.
    if not isinstance(footprint, dict):
        return {}
    details = footprint.get('details')
    if isinstance(details, dict):
        return details
    # Sections with 'items' array -> expand items as named children
    items = footprint.get('items')
    if isinstance(items, list):
        result: Dict[str, Any] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            item_name = str(item.get('projectKey') or item.get('name') or '').strip()
            if item_name:
                result[item_name] = item
        return result
    # Otherwise children are dict-valued keys (excluding metadata scalars)
    result = {}
    for key, val in footprint.items():
        if key in _FOOTPRINT_SCALAR_KEYS:
            continue
        if isinstance(val, dict):
            result[key] = val
    return result


def _footprint_size(footprint: Any) -> int:
    if not isinstance(footprint, dict):
        return 0
    size = _coerce_int(footprint.get('size'), 0)
    if size > 0:
        return size
    details = _footprint_details_map(footprint)
    if not details:
        return 0
    return sum(_footprint_size(child) for child in details.values())


def _normalize_bucket_name(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(name or '').lower())


def _collect_bucket_size_by_name(footprint: Any, matcher) -> int:
    details = _footprint_details_map(footprint)
    if not details:
        return 0
    total = 0
    for name, child in details.items():
        normalized = _normalize_bucket_name(name)
        if matcher(normalized):
            total += _footprint_size(child)
            continue
        total += _collect_bucket_size_by_name(child, matcher)
    return total


def _collect_bucket_file_count_by_name(footprint: Any, matcher) -> int:
    details = _footprint_details_map(footprint)
    if not details:
        return 0
    total = 0
    for name, child in details.items():
        normalized = _normalize_bucket_name(name)
        if matcher(normalized):
            total += _coerce_int(child.get('nbFiles'), 0)
            continue
        total += _collect_bucket_file_count_by_name(child, matcher)
    return total


_FOOTPRINT_BUCKET_LABELS = {
    'managedDatasets': 'Managed datasets', 'managedFolders': 'Managed folders',
    'preparedBundles': 'Bundles', 'savedModels': 'Saved models',
    'analysis': 'Visual analyses', 'notebookResults': 'Notebook results',
    'uploadedDatasets': 'Uploaded datasets', 'shakerSamples': 'Prepare samples',
    'codeStudioResources': 'Code Studio resources', 'scenarios': 'Scenario logs',
    'webApps': 'Web app runs', 'dkuWorkdirs': 'Jupyter work dirs',
    'thumbnails': 'Thumbnails', 'projectStandards': 'Project standards',
    'docportal': 'Doc portal', 'libResources': 'Library resources',
    'wikiAttachments': 'Wiki attachments', 'config': 'Project config',
    'git': 'Git history',
}


def _footprint_bucket_breakdown(footprint, top_n=5):
    """Top-level footprint folders sorted by size desc, each {name,label,bytes,location}.
    Returns {'buckets': [top_n...], 'otherCount': n, 'otherBytes': sum_of_remainder}."""
    details = _footprint_details_map(footprint)
    items = []
    for key, child in details.items():
        bytes_ = _footprint_size(child)
        if bytes_ <= 0:
            continue
        loc = ''
        if isinstance(child, dict):
            locs = child.get('locations')
            if isinstance(locs, list) and locs:
                loc = str(locs[0])
        items.append({'name': key,
                      'label': _FOOTPRINT_BUCKET_LABELS.get(key, key),
                      'bytes': bytes_, 'location': loc})
    items.sort(key=lambda d: d['bytes'], reverse=True)
    top = items[:top_n]
    rest = items[top_n:]
    return {'buckets': top,
            'otherCount': len(rest),
            'otherBytes': sum(d['bytes'] for d in rest)}


def _project_size_index(total_gb: float, avg_gb: float) -> float:
    safe_total = max(0.0, total_gb)
    if safe_total >= 40.0:
        return 1.0
    abs_norm = math.log1p(min(safe_total, 40.0)) / math.log1p(40.0)
    ratio = safe_total / max(avg_gb, 0.1)
    rel_norm = math.log1p(min(max(ratio, 0.0), 4.0)) / math.log1p(4.0)
    return max(0.0, min(1.0, (0.6 * abs_norm) + (0.4 * rel_norm)))


def _project_size_health(total_gb: float, size_index: float) -> str:
    if total_gb >= 40.0:
        return 'angry-red'
    if size_index >= 0.85:
        return 'angry-red'
    if size_index >= 0.60:
        return 'red'
    if size_index >= 0.35:
        return 'orange'
    return 'green'


def _code_env_health(code_env_count: int) -> str:
    if code_env_count >= 5:
        return 'angry-red'
    if code_env_count == 4:
        return 'red'
    if code_env_count == 3:
        return 'orange'
    if code_env_count == 2:
        return 'yellow'
    return 'green'


def _code_env_risk(code_env_count: int) -> float:
    if code_env_count <= 1:
        return 0.0
    if code_env_count == 2:
        return 0.45
    if code_env_count == 3:
        return 0.75
    return 1.0


def _usage_to_dict(usage: Any) -> Dict[str, Any]:
    if isinstance(usage, dict):
        return usage
    if hasattr(usage, 'to_dict'):
        try:
            raw = usage.to_dict()
            if isinstance(raw, dict):
                return raw
        except Exception:
            pass
    if hasattr(usage, 'get_raw'):
        try:
            raw = usage.get_raw()
            if isinstance(raw, dict):
                return raw
        except Exception:
            pass
    out: Dict[str, Any] = {}
    for attr in (
        'projectKey',
        'project',
        'projectId',
        'projectSummary',
        'usageType',
        'type',
        'objectType',
        'objectId',
        'objectSmartId',
        'envName',
        'envLang',
    ):
        if hasattr(usage, attr):
            out[attr] = getattr(usage, attr)
    return out


def _extract_usage_project_key(usage: Dict[str, Any]) -> Optional[str]:
    for key in ('projectKey', 'projectId', 'project_key'):
        value = usage.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    nested_project = usage.get('project')
    if isinstance(nested_project, dict):
        for key in ('projectKey', 'key', 'id'):
            value = nested_project.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    elif isinstance(nested_project, str) and nested_project.strip():
        return nested_project.strip()

    summary = usage.get('projectSummary')
    if isinstance(summary, dict):
        for key in ('projectKey', 'key', 'id'):
            value = summary.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _extract_usage_type(usage: Dict[str, Any]) -> str:
    for key in ('usageType', 'envUsage', 'type', 'objectType'):
        value = usage.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return 'UNKNOWN'


def _normalize_language(lang_raw: Any) -> str:
    if isinstance(lang_raw, str) and lang_raw.strip().lower().startswith('r'):
        return 'r'
    return 'python'


def _safe_get_raw(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if hasattr(obj, 'get_raw'):
        try:
            raw = obj.get_raw()
            if isinstance(raw, dict):
                return raw
        except Exception:
            pass
    return {}


_SENTINEL = object()


def _resolve_nested_path(payload: dict, path: str) -> Any:
    current: Any = payload
    for part in path.split('.'):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return _SENTINEL
    return current


def _extract_nested_text(payload: Any, *paths: str) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    for path in paths:
        value = _resolve_nested_path(payload, path)
        if value is _SENTINEL:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_nested_int(payload: Any, *paths: str) -> Optional[int]:
    if not isinstance(payload, dict):
        return None
    for path in paths:
        value = _resolve_nested_path(payload, path)
        if value is _SENTINEL:
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            text = value.strip()
            if text.isdigit():
                return int(text)
    return None


def _normalize_project_permissions(perms_raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(perms_raw, dict):
        return []

    entries = perms_raw.get('permissions')
    if not isinstance(entries, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for perm in entries:
        if not isinstance(perm, dict):
            continue
        name = perm.get('group') or perm.get('user') or 'Unknown'
        entry = {
            'type': 'Group' if perm.get('group') else 'User',
            'name': name,
            'permissions': {},
        }
        for perm_key, perm_val in perm.items():
            if perm_key in ('group', 'user'):
                continue
            entry['permissions'][perm_key] = perm_val
        normalized.append(entry)
    return normalized


def _extract_project_version_number(listing: Dict[str, Any], summary: Dict[str, Any], settings: Dict[str, Any]) -> int:
    value = summary.get('versionTag', {}).get('versionNumber')
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _extract_code_env_owner(env_listing: Dict[str, Any], settings_raw: Optional[Dict[str, Any]]) -> str:
    if settings_raw:
        owner = settings_raw.get('owner')
        if isinstance(owner, str) and owner.strip():
            return owner.strip()

    owner = env_listing.get('owner')
    if isinstance(owner, str) and owner.strip():
        return owner.strip()
    return 'Unknown'


def _extract_usage_object_type(usage: Dict[str, Any]) -> str:
    value = _extract_nested_text(
        usage,
        'objectType',
        'targetType',
        'projectObjectType',
        'object.type',
    )
    if value:
        return value.upper()
    return _extract_usage_type(usage)


def _extract_usage_object_id(usage: Dict[str, Any]) -> str:
    value = _extract_nested_text(
        usage,
        'objectId',
        'targetId',
        'id',
        'object.id',
        'objectSmartId',
    )
    if value:
        return value
    return ''


def _extract_usage_object_name(usage: Dict[str, Any]) -> str:
    value = _extract_nested_text(
        usage,
        'objectName',
        'targetName',
        'name',
        'displayName',
        'object.name',
        'object.displayName',
    )
    if value:
        return value
    fallback = _extract_usage_object_id(usage)
    if fallback:
        return fallback
    return _extract_usage_object_type(usage)


def _normalize_usage_entry(
    usage: Dict[str, Any],
    project_names: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:
    project_key = _extract_usage_project_key(usage) or ''
    project_meta = project_names.get(project_key) or {}
    project_name = (
        _extract_nested_text(usage, 'projectSummary.name', 'project.name', 'projectName')
        or project_meta.get('name')
        or project_key
    )

    object_type = _extract_usage_object_type(usage)
    object_id = _extract_usage_object_id(usage)
    object_name = _extract_usage_object_name(usage)

    return {
        'projectKey': project_key,
        'projectName': project_name,
        'usageType': _extract_usage_type(usage),
        'objectType': object_type,
        'objectId': object_id,
        'objectName': object_name,
    }


def _usage_signature(usage: Dict[str, Any]) -> str:
    return '|'.join(
        [
            str(usage.get('projectKey') or ''),
            str(usage.get('usageType') or ''),
            str(usage.get('objectType') or ''),
            str(usage.get('objectId') or ''),
            str(usage.get('objectName') or ''),
            str(usage.get('codeEnvKey') or ''),
        ]
    )


def _dedupe_usage_entries(usages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for usage in usages:
        sig = _usage_signature(usage)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(usage)
    return out


def _usage_to_email_line(usage: Dict[str, Any]) -> str:
    object_type = usage.get('objectType') or usage.get('usageType') or 'OBJECT'
    object_name = usage.get('objectName') or usage.get('objectId') or 'unknown'
    project_key = usage.get('projectKey') or '?'
    code_env_name = usage.get('codeEnvName') or '?'
    return f"- [{object_type}] {object_name} (project={project_key}, code env={code_env_name})"


def _email_object_type_label(object_type: Any, usage_type: Any) -> str:
    raw = str(object_type or usage_type or 'OBJECT').strip().upper()
    if raw.startswith('RECIPE'):
        return 'Recipe'
    if raw.startswith('NOTEBOOK'):
        return 'Notebook'
    if raw.startswith('WEBAPP'):
        return 'Webapp Backend'
    if raw.startswith('SCENARIO_STEP'):
        return 'Scenario Step'
    if raw.startswith('SCENARIO'):
        return 'Scenario'
    if raw.startswith('CODE_STUDIO'):
        return 'Code Studio'
    if raw.startswith('PROJECT'):
        return 'Project'
    return raw.replace('_', ' ').title()


def _usage_lines_grouped_by_code_env(usages: List[Dict[str, Any]]) -> List[str]:
    grouped: Dict[str, List[str]] = {}
    seen = set()

    for usage in usages:
        if not isinstance(usage, dict):
            continue
        usage_type = str(usage.get('usageType') or '').strip().upper()
        if usage_type == 'PROJECT':
            # Project-level defaults are too generic for outreach emails.
            continue

        code_env = str(usage.get('codeEnvName') or usage.get('codeEnvKey') or 'Unknown').strip() or 'Unknown'
        project_key = str(usage.get('projectKey') or '?').strip() or '?'
        object_label = _email_object_type_label(usage.get('objectType'), usage_type)
        object_name = str(usage.get('objectName') or usage.get('objectId') or 'unknown').strip() or 'unknown'

        signature = (
            code_env.lower(),
            project_key,
            object_label.lower(),
            object_name,
        )
        if signature in seen:
            continue
        seen.add(signature)

        grouped.setdefault(code_env, []).append(
            f"- {object_label}: {object_name} (project={project_key})"
        )

    if not grouped:
        return ['- No concrete object usage details found']

    out: List[str] = []
    env_names = sorted(grouped.keys(), key=lambda name: name.lower())
    for idx, env_name in enumerate(env_names):
        out.append(f"Code Environment: {env_name}")
        env_lines = sorted(grouped[env_name], key=lambda line: line.lower())
        out.extend([f"  {line}" for line in env_lines])
        if idx < len(env_names) - 1:
            out.append('')
    return out


def _usage_lines_grouped_by_project(usages: List[Dict[str, Any]]) -> List[str]:
    grouped: Dict[str, Dict[str, List[str]]] = {}
    seen = set()

    for usage in usages:
        if not isinstance(usage, dict):
            continue
        usage_type = str(usage.get('usageType') or '').strip().upper()
        if usage_type == 'PROJECT':
            continue

        code_env = str(usage.get('codeEnvName') or usage.get('codeEnvKey') or 'Unknown').strip() or 'Unknown'
        project_key = str(usage.get('projectKey') or '?').strip() or '?'
        object_label = _email_object_type_label(usage.get('objectType'), usage_type)
        object_name = str(usage.get('objectName') or usage.get('objectId') or 'unknown').strip() or 'unknown'

        signature = (project_key, code_env.lower(), object_label.lower(), object_name)
        if signature in seen:
            continue
        seen.add(signature)

        grouped.setdefault(project_key, {}).setdefault(code_env, []).append(
            f"    - {object_label}: {object_name}"
        )

    if not grouped:
        return ['- No concrete object usage details found']

    out: List[str] = []
    project_keys = sorted(grouped.keys(), key=lambda k: k.lower())
    for idx, pkey in enumerate(project_keys):
        out.append(f"Project: {pkey}")
        envs = sorted(grouped[pkey].keys(), key=lambda e: e.lower())
        for env_name in envs:
            out.append(f"  - Code Env: {env_name}")
            obj_lines = sorted(grouped[pkey][env_name], key=lambda l: l.lower())
            out.extend(obj_lines)
        if idx < len(project_keys) - 1:
            out.append('')
    return out


def _wrap_html_email(body_html: str) -> str:
    year = __import__('datetime').datetime.now().year
    return (
        '<!-- html:true -->\n'
        '<html lang="en">\n'
        '<head>\n'
        '    <meta charset="utf-8">\n'
        '    <meta name="viewport" content="width=device-width">\n'
        '    <meta http-equiv="X-UA-Compatible" content="IE=edge">\n'
        '    <title>DSS Health</title>\n'
        '    <style>\n'
        "        @import url('https://fonts.googleapis.com/css2?family=Source+Sans+3:ital,wght@0,200..900;1,200..900&display=swap');\n"
        '    </style>\n'
        '    <style type="text/css">\n'
        '        body, #bodyTable {\n'
        '            height: 100% !important; width: 100% !important;\n'
        '            margin: 0; padding: 0;\n'
        '            font-family: "Source Sans 3", -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;\n'
        '            background-color: #f4f5f7;\n'
        '        }\n'
        '        body, table, td, p, a, li, blockquote {\n'
        '            -ms-text-size-adjust: 100%; -webkit-text-size-adjust: 100%;\n'
        '        }\n'
        '        table { border-spacing: 0; }\n'
        '        table, td { border-collapse: collapse; mso-table-lspace: 0pt; mso-table-rspace: 0pt; }\n'
        '        img { -ms-interpolation-mode: bicubic; }\n'
        '        img, a img { border: 0; outline: none; text-decoration: none; }\n'
        '        .yshortcuts a { border-bottom: none !important; }\n'
        '        @media only screen and (min-width: 900px) {\n'
        '            .email-container { width: 880px !important; }\n'
        '        }\n'
        '        a { color: #00897b; }\n'
        '        .logo-header { text-align: left; margin-bottom: 16px; }\n'
        '        .logo { max-width: 120px; margin-bottom: 4px; }\n'
        '        .banner { width: 100%; max-width: 580px; margin: 4px auto 8px auto; display: block; }\n'
        '        .container {\n'
        '            background-color: #ffffff;\n'
        '            padding: 28px 36px 32px 36px;\n'
        '            border: 1px solid #e5eaf0;\n'
        '            border-radius: 12px;\n'
        '        }\n'
        '        .content {\n'
        '            color: #3a3f47;\n'
        '            font-size: 15px;\n'
        '            line-height: 1.6;\n'
        '        }\n'
        '        .content p { margin: 10px 0; color: #3a3f47; }\n'
        '        .content h3 { color: #1a1a2e; font-size: 16px; font-weight: 600; margin: 20px 0 8px 0; }\n'
        '        .content ul { padding-left: 20px; margin: 6px 0; line-height: 1.7; }\n'
        '        .content li { margin: 4px 0; color: #4a5568; }\n'
        '        .button {\n'
        '            display: inline-block; margin-top: 4px; margin-bottom: 12px;\n'
        '            padding: 12px 20px; text-decoration: none;\n'
        '            border-radius: 32px; font-weight: 500;\n'
        '        }\n'
        '        .btn-primary { background-color: #00897b; color: #ffffff; }\n'
        '        .btn-secondary { background-color: #ffffff; color: #00897b; border: 1px solid #00897b; }\n'
        '        .footer { text-align: center; color: #8895a7; font-size: 12px; padding: 32px 0; }\n'
        '    </style>\n'
        '</head>\n'
        '<table id="bodyTable" border="0" cellpadding="0" cellspacing="0" width="100%">\n'
        '    <tr>\n'
        '        <td align="center" valign="top">\n'
        '            <table align="center" border="0" cellpadding="0" cellspacing="0" class="email-container"\n'
        '                   style="max-width: 720px;">\n'
        '                <tr>\n'
        '                    <td height="20" style="font-size: 0; line-height: 0;">&nbsp;</td>\n'
        '                </tr>\n'
        '                <tr>\n'
        '                    <td>\n'
        '                        <div class="logo-header">\n'
        '                            <a href="https://www.dataiku.com">\n'
        '                                <img src="https://dku-assets.s3.amazonaws.com/img/emailing/DataikuLogoTeal_2025.png" alt="Dataiku Logo" class="logo">\n'
        '                            </a>\n'
        '                        </div>\n'
        '                    </td>\n'
        '                </tr>\n'
        '                <tr>\n'
        '                    <td>\n'
        '                        <div class="container">\n'
        '                            <div class="content">\n'
        '                                <img src="https://dku-assets.s3.amazonaws.com/img/emailing/EmailBanner.png" class="banner" alt="Banner">\n'
        + body_html +
        '\n                            </div>\n'
        '                        </div>\n'
        '                    </td>\n'
        '                </tr>\n'
        '                <tr>\n'
        '                    <td class="footer">\n'
        f'                        &copy; {year} Dataiku | All rights reserved.<br>\n'
        '                        <br>\n'
        '                        <a href="mailto:{{admin_email}}" class="button btn-primary" style="color:#ffffff;font-size:13px;padding:8px 18px;background-color:#00897b;text-decoration:none;border-radius:32px;display:inline-block;">Contact your DSS Admin</a>\n'
        '                        &nbsp;\n'
        '                        <a href="{{chat_channel_url}}" class="button btn-secondary" style="color:#00897b;font-size:13px;padding:8px 18px;background-color:#ffffff;text-decoration:none;border:1px solid #00897b;border-radius:32px;display:inline-block;">Join the DSS Channel</a>\n'
        '                    </td>\n'
        '                </tr>\n'
        '            </table>\n'
        '        </td>\n'
        '    </tr>\n'
        '</table>\n'
        '</html>\n'
    )


def _text_body_to_html(rendered_text: str) -> str:
    import html as _html
    lines = rendered_text.split('\n')
    fragments: List[str] = []
    in_list = False
    in_sub_list = False

    _p_style = 'style="margin:10px 0;color:#3a3f47;font-size:15px;line-height:1.6;"'
    _h3_style = 'style="color:#1a1a2e;font-size:15px;font-weight:600;margin:20px 0 6px 0;padding:0;"'
    _ul_style = 'style="padding-left:20px;margin:6px 0;"'
    _li_style = 'style="margin:4px 0;color:#3a3f47;font-size:14px;line-height:1.5;"'
    _li_sub_style = 'style="margin:3px 0;color:#4a5568;font-size:13px;line-height:1.5;"'

    def _close_sub_list():
        nonlocal in_sub_list
        if in_sub_list:
            fragments.append('</ul></li>')
            in_sub_list = False

    def _close_list():
        nonlocal in_list
        _close_sub_list()
        if in_list:
            fragments.append('</ul>')
            in_list = False

    for line in lines:
        stripped = line.rstrip()

        # Section headers
        if stripped.startswith('Project:') or stripped.startswith('Code Environment:'):
            _close_list()
            fragments.append(f'<h3 {_h3_style}>' + _html.escape(stripped) + '</h3>')
            continue

        # Deeply indented list item (4+ spaces then "- ")
        if stripped.startswith('    - ') or stripped.startswith('\t\t- '):
            content = stripped.lstrip().lstrip('- ').strip()
            if not in_list:
                fragments.append(f'<ul {_ul_style}>')
                in_list = True
            if not in_sub_list:
                fragments.append(f'<li {_li_style}><ul {_ul_style}>')
                in_sub_list = True
            fragments.append(f'<li {_li_sub_style}>' + _html.escape(content) + '</li>')
            continue

        # Indented list item (2 spaces then "- ")
        if stripped.startswith('  - ') or stripped.startswith('\t- '):
            _close_sub_list()
            content = stripped.lstrip().lstrip('- ').strip()
            if not in_list:
                fragments.append(f'<ul {_ul_style}>')
                in_list = True
            fragments.append(f'<li {_li_style}>' + _html.escape(content) + '</li>')
            continue

        # Top-level list item ("- ")
        if stripped.startswith('- '):
            _close_sub_list()
            content = stripped[2:].strip()
            if not in_list:
                fragments.append(f'<ul {_ul_style}>')
                in_list = True
            fragments.append(f'<li {_li_style}>' + _html.escape(content) + '</li>')
            continue

        # Empty line = paragraph break
        if not stripped:
            _close_list()
            continue

        # Regular text line
        _close_list()
        fragments.append(f'<p {_p_style}>' + _html.escape(stripped) + '</p>')

    _close_list()
    return _wrap_html_email('\n'.join(fragments))


_PROJECT_ENV_MARKER = '__PEL_HTML__'


def _build_project_env_html(projects_data: list, _pel_grouped: dict) -> str:
    """Build rich HTML cards for the project -> code env -> objects hierarchy."""
    import html as _html
    cards: List[str] = []

    for proj in projects_data:
        if not isinstance(proj, dict):
            continue
        pkey = str(proj.get('projectKey') or '')
        pname = str(proj.get('name') or pkey)
        ce_count = _coerce_int(proj.get('codeEnvCount'), 0)

        parts: List[str] = []
        parts.append(
            '<table cellpadding="0" cellspacing="0" width="100%" style="'
            'background:#fafbfc;border:1px solid #e5eaf0;border-radius:8px;'
            'margin:14px 0;font-family:inherit;">'
        )

        # ── Header row ──
        name_html = _html.escape(pname)
        if pname != pkey and pkey:
            name_html += (
                f' <span style="color:#8895a7;font-weight:400;font-size:13px;">'
                f'({_html.escape(pkey)})</span>'
            )
        badge = ''
        if ce_count:
            badge = (
                f' <span style="display:inline-block;background:#e0f2f1;color:#00897b;'
                f'font-size:11px;font-weight:600;padding:2px 10px;border-radius:10px;'
                f'margin-left:6px;vertical-align:middle;letter-spacing:0.3px;">'
                f'{ce_count} code env{"s" if ce_count != 1 else ""}</span>'
            )
        parts.append(
            f'<tr><td style="padding:14px 20px 10px 20px;font-weight:600;font-size:15px;'
            f'color:#1a1a2e;border-bottom:1px solid #eef0f4;">'
            f'{name_html}{badge}</td></tr>'
        )

        # ── Code env entries ──
        env_data = _pel_grouped.get(pkey, {})
        env_names = sorted(env_data.keys(), key=lambda e: e.lower()) if env_data else []
        if not env_names:
            env_names = sorted(set(
                str(n) for n in (proj.get('codeEnvNames') or []) if str(n).strip()
            ))

        for idx, env_name in enumerate(env_names):
            obj_lines = env_data.get(env_name, []) if env_data else []
            is_last = idx == len(env_names) - 1

            inner = (
                f'<div style="margin:0 0 2px 0;">'
                f'<span style="display:inline-block;color:#00897b;font-weight:600;'
                f'font-size:13px;">&#9679;&nbsp; {_html.escape(env_name)}</span></div>'
            )

            if obj_lines:
                tags = []
                for obj_line in sorted(obj_lines, key=lambda l: l.lower()):
                    obj_stripped = obj_line.strip()
                    if ':' in obj_stripped:
                        obj_type, obj_name = obj_stripped.split(':', 1)
                        tags.append(
                            f'<span style="display:inline-block;background:#eef0f5;color:#4a5568;'
                            f'font-size:12px;padding:3px 10px;border-radius:4px;margin:2px 3px 2px 0;'
                            f'line-height:1.4;">'
                            f'<span style="color:#8895a7;font-weight:500;">'
                            f'{_html.escape(obj_type.strip())}</span>'
                            f' {_html.escape(obj_name.strip())}</span>'
                        )
                    else:
                        tags.append(
                            f'<span style="display:inline-block;background:#eef0f5;color:#4a5568;'
                            f'font-size:12px;padding:3px 10px;border-radius:4px;margin:2px 3px 2px 0;'
                            f'line-height:1.4;">{_html.escape(obj_stripped)}</span>'
                        )
                inner += f'<div style="margin:4px 0 0 18px;">{"".join(tags)}</div>'

            bottom_pad = '12px' if is_last else '6px'
            sep = '' if is_last else 'border-bottom:1px solid #f2f4f6;'
            parts.append(
                f'<tr><td style="padding:10px 20px {bottom_pad} 20px;{sep}">'
                f'{inner}</td></tr>'
            )

        parts.append('</table>')
        cards.append('\n'.join(parts))

    if not cards:
        return (
            '<p style="color:#8895a7;font-size:14px;font-style:italic;">'
            'No code environment details available.</p>'
        )
    return '\n'.join(cards)


# ── Markers for rich-HTML injection (all email list variables) ──
_PROJECT_LIST_MARKER = '__PLIST_HTML__'
_CODE_ENV_LIST_MARKER = '__CELIST_HTML__'
_OBJECTS_LIST_MARKER = '__OLIST_HTML__'
_CODE_STUDIO_LIST_MARKER = '__CSLIST_HTML__'
_SCENARIO_LIST_MARKER = '__SCLIST_HTML__'
_INACTIVE_LIST_MARKER = '__IPLIST_HTML__'


def _build_items_html(items: List[str], accent: str = '#3a3f47') -> str:
    """Render a flat list of items as styled inline tags."""
    import html as _html
    if not items:
        return '<span style="color:#8895a7;font-size:13px;font-style:italic;">none</span>'
    tags = []
    for item in items:
        tags.append(
            f'<span style="display:inline-block;background:#f0f2f5;color:{accent};'
            f'font-size:13px;font-weight:500;padding:5px 14px;border-radius:6px;'
            f'margin:3px 4px 3px 0;line-height:1.4;">{_html.escape(item)}</span>'
        )
    return f'<div style="margin:8px 0 4px 0;">{"".join(tags)}</div>'


def _build_code_studio_html(projects_data: list) -> str:
    """Render code studio counts per project as a styled card."""
    import html as _html
    rows: List[str] = []
    valid = [p for p in projects_data if isinstance(p, dict)]
    for idx, proj in enumerate(valid):
        pkey = str(proj.get('projectKey') or '')
        pname = str(proj.get('name') or pkey)
        cs_count = _coerce_int(proj.get('codeStudioCount'), 0)
        is_last = idx == len(valid) - 1
        sep = '' if is_last else 'border-bottom:1px solid #f2f4f6;'

        name_html = _html.escape(pname)
        if pname != pkey and pkey:
            name_html += (
                f' <span style="color:#8895a7;font-weight:400;font-size:13px;">'
                f'({_html.escape(pkey)})</span>'
            )
        badge = (
            f' <span style="display:inline-block;background:#fff3e0;color:#e65100;'
            f'font-size:11px;font-weight:600;padding:2px 10px;border-radius:10px;'
            f'margin-left:6px;vertical-align:middle;">'
            f'{cs_count} code studio{"s" if cs_count != 1 else ""}</span>'
        )
        rows.append(
            f'<tr><td style="padding:12px 20px;{sep}font-weight:600;font-size:14px;color:#1a1a2e;">'
            f'{name_html}{badge}</td></tr>'
        )
    if not rows:
        return '<span style="color:#8895a7;font-size:13px;font-style:italic;">none</span>'
    return (
        '<table cellpadding="0" cellspacing="0" width="100%" style="'
        'background:#fafbfc;border:1px solid #e5eaf0;border-radius:8px;'
        'margin:14px 0;font-family:inherit;">'
        + ''.join(rows) + '</table>'
    )


def _build_scenario_html(projects_data: list) -> str:
    """Render scenario details per project as styled cards."""
    import html as _html
    cards: List[str] = []
    for proj in projects_data:
        if not isinstance(proj, dict):
            continue
        auto_scenarios = proj.get('autoScenarios') or []
        if not auto_scenarios:
            continue
        pkey = str(proj.get('projectKey') or '')
        pname = str(proj.get('name') or pkey)

        parts: List[str] = []
        parts.append(
            '<table cellpadding="0" cellspacing="0" width="100%" style="'
            'background:#fafbfc;border:1px solid #e5eaf0;border-radius:8px;'
            'margin:14px 0;font-family:inherit;">'
        )

        # Header
        name_html = _html.escape(pname)
        if pname != pkey and pkey:
            name_html += (
                f' <span style="color:#8895a7;font-weight:400;font-size:13px;">'
                f'({_html.escape(pkey)})</span>'
            )
        valid_sc = [s for s in auto_scenarios if isinstance(s, dict)]
        badge = (
            f' <span style="display:inline-block;background:#e8eaf6;color:#3949ab;'
            f'font-size:11px;font-weight:600;padding:2px 10px;border-radius:10px;'
            f'margin-left:6px;vertical-align:middle;">'
            f'{len(valid_sc)} scenario{"s" if len(valid_sc) != 1 else ""}</span>'
        )
        parts.append(
            f'<tr><td style="padding:14px 20px 10px 20px;font-weight:600;font-size:15px;'
            f'color:#1a1a2e;border-bottom:1px solid #eef0f4;">'
            f'{name_html}{badge}</td></tr>'
        )

        # Scenario rows
        for sidx, sc in enumerate(valid_sc):
            sc_name = str(sc.get('name') or sc.get('id') or 'Unknown')
            sc_type = str(sc.get('type') or 'unknown')
            trigger_count = _coerce_int(sc.get('triggerCount'), 0)
            is_last = sidx == len(valid_sc) - 1

            inner = (
                f'<div style="margin:0 0 2px 0;">'
                f'<span style="display:inline-block;color:#3949ab;font-weight:600;'
                f'font-size:13px;">&#9679;&nbsp; {_html.escape(sc_name)}</span></div>'
            )
            meta_tags = (
                f'<span style="display:inline-block;background:#eef0f5;color:#4a5568;'
                f'font-size:12px;padding:3px 10px;border-radius:4px;margin:2px 3px 2px 0;'
                f'line-height:1.4;">'
                f'<span style="color:#8895a7;font-weight:500;">type</span>'
                f' {_html.escape(sc_type)}</span>'
                f'<span style="display:inline-block;background:#eef0f5;color:#4a5568;'
                f'font-size:12px;padding:3px 10px;border-radius:4px;margin:2px 3px 2px 0;'
                f'line-height:1.4;">'
                f'<span style="color:#8895a7;font-weight:500;">triggers</span>'
                f' {trigger_count}</span>'
            )
            inner += f'<div style="margin:4px 0 0 18px;">{meta_tags}</div>'

            bottom_pad = '12px' if is_last else '6px'
            sep = '' if is_last else 'border-bottom:1px solid #f2f4f6;'
            parts.append(
                f'<tr><td style="padding:10px 20px {bottom_pad} 20px;{sep}">'
                f'{inner}</td></tr>'
            )

        parts.append('</table>')
        cards.append('\n'.join(parts))
    if not cards:
        return '<span style="color:#8895a7;font-size:13px;font-style:italic;">none</span>'
    return '\n'.join(cards)


def _build_inactive_projects_html(projects_data: list) -> str:
    """Render inactive projects as a styled card with duration badges."""
    import html as _html
    rows: List[str] = []
    valid = [p for p in projects_data if isinstance(p, dict)]
    for idx, proj in enumerate(valid):
        pkey = str(proj.get('projectKey') or '')
        pname = str(proj.get('name') or pkey)
        days_inactive = _coerce_int(proj.get('daysInactive'), 0)
        is_last = idx == len(valid) - 1
        sep = '' if is_last else 'border-bottom:1px solid #f2f4f6;'

        name_html = _html.escape(pname)
        if pname != pkey and pkey:
            name_html += (
                f' <span style="color:#8895a7;font-weight:400;font-size:13px;">'
                f'({_html.escape(pkey)})</span>'
            )
        badge = ''
        if days_inactive > 0:
            badge = (
                f' <span style="display:inline-block;background:#fff3e0;color:#e65100;'
                f'font-size:11px;font-weight:600;padding:2px 10px;border-radius:10px;'
                f'margin-left:6px;vertical-align:middle;">'
                f'inactive {days_inactive} days</span>'
            )
        rows.append(
            f'<tr><td style="padding:12px 20px;{sep}font-weight:600;font-size:14px;color:#1a1a2e;">'
            f'{name_html}{badge}</td></tr>'
        )
    if not rows:
        return '<span style="color:#8895a7;font-size:13px;font-style:italic;">none</span>'
    return (
        '<table cellpadding="0" cellspacing="0" width="100%" style="'
        'background:#fafbfc;border:1px solid #e5eaf0;border-radius:8px;'
        'margin:14px 0;font-family:inherit;">'
        + ''.join(rows) + '</table>'
    )


def _build_objects_html(usage_details: list, group_by_project: bool = False) -> str:
    """Render usage objects as styled cards, grouped by code env or project."""
    import html as _html

    if group_by_project:
        # Group by project → code env → objects
        grouped: Dict[str, Dict[str, List[tuple]]] = {}
        seen: set = set()
        for u in usage_details:
            if not isinstance(u, dict):
                continue
            usage_type = str(u.get('usageType') or '').strip().upper()
            if usage_type == 'PROJECT':
                continue
            ce = str(u.get('codeEnvName') or u.get('codeEnvKey') or 'Unknown').strip() or 'Unknown'
            pk = str(u.get('projectKey') or '?').strip() or '?'
            obj_label = _email_object_type_label(u.get('objectType'), usage_type)
            obj_name = str(u.get('objectName') or u.get('objectId') or 'unknown').strip() or 'unknown'
            sig = (pk, ce.lower(), obj_label.lower(), obj_name)
            if sig in seen:
                continue
            seen.add(sig)
            grouped.setdefault(pk, {}).setdefault(ce, []).append((obj_label, obj_name))

        if not grouped:
            return '<span style="color:#8895a7;font-size:13px;font-style:italic;">No object usage details found</span>'

        cards: List[str] = []
        for pkey in sorted(grouped.keys(), key=lambda k: k.lower()):
            parts: List[str] = []
            parts.append(
                '<table cellpadding="0" cellspacing="0" width="100%" style="'
                'background:#fafbfc;border:1px solid #e5eaf0;border-radius:8px;'
                'margin:14px 0;font-family:inherit;">'
            )
            parts.append(
                f'<tr><td style="padding:14px 20px 10px 20px;font-weight:600;font-size:15px;'
                f'color:#1a1a2e;border-bottom:1px solid #eef0f4;">'
                f'{_html.escape(pkey)}</td></tr>'
            )
            envs = sorted(grouped[pkey].keys(), key=lambda e: e.lower())
            for eidx, env_name in enumerate(envs):
                objs = grouped[pkey][env_name]
                is_last = eidx == len(envs) - 1
                inner = (
                    f'<div style="margin:0 0 2px 0;">'
                    f'<span style="display:inline-block;color:#00897b;font-weight:600;'
                    f'font-size:13px;">&#9679;&nbsp; {_html.escape(env_name)}</span></div>'
                )
                if objs:
                    tags = []
                    for obj_label, obj_name in sorted(objs, key=lambda x: x[1].lower()):
                        tags.append(
                            f'<span style="display:inline-block;background:#eef0f5;color:#4a5568;'
                            f'font-size:12px;padding:3px 10px;border-radius:4px;margin:2px 3px 2px 0;'
                            f'line-height:1.4;">'
                            f'<span style="color:#8895a7;font-weight:500;">'
                            f'{_html.escape(obj_label)}</span>'
                            f' {_html.escape(obj_name)}</span>'
                        )
                    inner += f'<div style="margin:4px 0 0 18px;">{"".join(tags)}</div>'
                bottom_pad = '12px' if is_last else '6px'
                sep = '' if is_last else 'border-bottom:1px solid #f2f4f6;'
                parts.append(
                    f'<tr><td style="padding:10px 20px {bottom_pad} 20px;{sep}">'
                    f'{inner}</td></tr>'
                )
            parts.append('</table>')
            cards.append('\n'.join(parts))
        return '\n'.join(cards)

    # Group by code env → objects (with project context)
    grouped_by_env: Dict[str, List[tuple]] = {}
    seen2: set = set()
    for u in usage_details:
        if not isinstance(u, dict):
            continue
        usage_type = str(u.get('usageType') or '').strip().upper()
        if usage_type == 'PROJECT':
            continue
        ce = str(u.get('codeEnvName') or u.get('codeEnvKey') or 'Unknown').strip() or 'Unknown'
        pk = str(u.get('projectKey') or '?').strip() or '?'
        obj_label = _email_object_type_label(u.get('objectType'), usage_type)
        obj_name = str(u.get('objectName') or u.get('objectId') or 'unknown').strip() or 'unknown'
        sig = (ce.lower(), pk, obj_label.lower(), obj_name)
        if sig in seen2:
            continue
        seen2.add(sig)
        grouped_by_env.setdefault(ce, []).append((obj_label, obj_name, pk))

    if not grouped_by_env:
        return '<span style="color:#8895a7;font-size:13px;font-style:italic;">No object usage details found</span>'

    cards2: List[str] = []
    for env_name in sorted(grouped_by_env.keys(), key=lambda n: n.lower()):
        objs = grouped_by_env[env_name]
        parts2: List[str] = []
        parts2.append(
            '<table cellpadding="0" cellspacing="0" width="100%" style="'
            'background:#fafbfc;border:1px solid #e5eaf0;border-radius:8px;'
            'margin:14px 0;font-family:inherit;">'
        )
        parts2.append(
            f'<tr><td style="padding:14px 20px 10px 20px;font-weight:600;font-size:15px;'
            f'color:#00897b;border-bottom:1px solid #eef0f4;">'
            f'&#9679;&nbsp; {_html.escape(env_name)}</td></tr>'
        )
        tags = []
        for obj_label, obj_name, pk in sorted(objs, key=lambda x: (x[2].lower(), x[1].lower())):
            tags.append(
                f'<span style="display:inline-block;background:#eef0f5;color:#4a5568;'
                f'font-size:12px;padding:3px 10px;border-radius:4px;margin:2px 3px 2px 0;'
                f'line-height:1.4;">'
                f'<span style="color:#8895a7;font-weight:500;">'
                f'{_html.escape(obj_label)}</span>'
                f' {_html.escape(obj_name)}'
                f' <span style="color:#b0b8c4;font-size:11px;">({_html.escape(pk)})</span>'
                f'</span>'
            )
        parts2.append(
            f'<tr><td style="padding:10px 20px 12px 20px;">'
            f'<div style="margin:4px 0 0 0;">{"".join(tags)}</div>'
            f'</td></tr>'
        )
        parts2.append('</table>')
        cards2.append('\n'.join(parts2))
    return '\n'.join(cards2)


def _default_email_template(campaign: str) -> Dict[str, str]:
    if campaign == 'code_env':
        return {
            'subject': '[DSS Health] Code environment ownership mismatch in your projects',
            'body': (
                "Hi {{owner}},\n\n"
                "DSS health checks flagged code environments in your projects that are owned by other users.\n"
                "Project owners should own their project code environments (ideally one per project) so changes do not break other projects.\n\n"
                "Impacted projects:\n{{project_list}}\n\n"
                "Code environments not owned by you:\n{{code_env_list}}\n\n"
                "Detected objects:\n{{objects_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'code_studio':
        return {
            'subject': '[DSS Health] Too many Code Studios in your projects',
            'body': (
                "Hi {{owner}},\n\n"
                "DSS health checks flagged that some of your projects have too many Code Studios.\n"
                "Please consolidate or remove unused Code Studios to reduce resource consumption.\n\n"
                "Projects with excessive Code Studios:\n{{code_studio_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'auto_scenario':
        return {
            'subject': '[DSS Health] Review auto-start scenarios in your projects',
            'body': (
                "Hi {{owner}},\n\n"
                "DSS health checks found scenarios set to automatically start in your projects.\n"
                "Please review these scenarios to ensure they are still needed and properly configured.\n\n"
                "Projects and auto-start scenarios:\n{{scenario_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'disabled_user':
        return {
            'subject': '[DSS Health] Projects owned by disabled users need reassignment',
            'body': (
                "Hi admin,\n\n"
                "The following projects are owned by disabled user accounts.\n"
                "Please reassign ownership to active users.\n\n"
                "Projects owned by disabled users:\n{{project_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'deprecated_code_env':
        return {
            'subject': '[DSS Health] Deprecated Python versions in your code environments',
            'body': (
                "Hi {{owner}},\n\n"
                "Some of your code environments use deprecated Python versions (2.x, 3.6, or 3.7).\n"
                "Please upgrade to a supported Python version.\n\n"
                "Code environments:\n{{code_env_list}}\n\n"
                "Impacted projects:\n{{project_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'default_code_env':
        return {
            'subject': '[DSS Health] Projects missing default code environment',
            'body': (
                "Hi {{owner}},\n\n"
                "Some of your projects use code environments but have no default Python code environment configured.\n"
                "Setting a default code environment prevents unexpected version conflicts.\n\n"
                "Projects:\n{{project_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'overshared_project':
        return {
            'subject': '[DSS Health] Projects with excessive permissions',
            'body': (
                "Hi {{owner}},\n\n"
                "Some of your projects have a large number of permission entries.\n"
                "Please review and consolidate permissions using groups where possible.\n\n"
                "Projects:\n{{project_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'scenario_frequency':
        return {
            'subject': '[DSS Health] High-frequency scenarios in your projects',
            'body': (
                "Hi {{owner}},\n\n"
                "Some scenarios in your projects run very frequently (under 30 minutes).\n"
                "Please review whether this frequency is necessary.\n\n"
                "Projects and scenarios:\n{{scenario_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'empty_project':
        return {
            'subject': '[DSS Health] Empty projects that may need cleanup',
            'body': (
                "Hi {{owner}},\n\n"
                "Some of your projects appear to be empty or unused.\n"
                "Please archive or delete projects that are no longer needed.\n\n"
                "Projects:\n{{project_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'large_flow':
        return {
            'subject': '[DSS Health] Projects with large flows',
            'body': (
                "Hi {{owner}},\n\n"
                "Some of your projects have very large flows with many objects.\n"
                "Consider splitting large flows into smaller, focused projects.\n\n"
                "Projects:\n{{project_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'orphan_notebooks':
        return {
            'subject': '[DSS Health] Projects with many notebooks but few recipes',
            'body': (
                "Hi {{owner}},\n\n"
                "Some of your projects have many notebooks but few recipes.\n"
                "Consider converting mature notebooks into recipes for production use.\n\n"
                "Projects:\n{{project_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'scenario_failing':
        return {
            'subject': '[DSS Health] Failing scenarios in your projects',
            'body': (
                "Hi {{owner}},\n\n"
                "Some scenarios in your projects have failed in their last run.\n"
                "Please investigate and fix the failing scenarios.\n\n"
                "Projects and failing scenarios:\n{{scenario_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'inactive_project':
        return {
            'subject': '[DSS Health] Inactive projects that may need cleanup',
            'body': (
                "Hi {{owner}},\n\n"
                "Some of your projects have been inactive for a long time.\n"
                "A project is considered inactive when it has no recent modifications, "
                "no active scenarios, and no deployed bundles.\n\n"
                "Please delete or archive projects that are no longer needed to keep the instance clean.\n\n"
                "Inactive projects:\n{{inactive_project_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'unused_code_env':
        return {
            'subject': '[DSS Health] Unused code environments you own',
            'body': (
                "Hi {{owner}},\n\n"
                "Some code environments you own have zero usages across all projects.\n"
                "Please delete code environments that are no longer needed to free up resources.\n\n"
                "Unused code environments:\n{{code_env_list}}\n\n"
                "Thanks."
            ),
        }
    return {
        'subject': '[DSS Health] Please reduce code environments in your projects',
        'body': (
            "Hi {{owner}},\n\n"
            "DSS health checks flagged that some of your projects use too many code environments.\n"
            "Please keep one code environment per project unless absolutely necessary.\n\n"
            "{{project_env_list}}\n\n"
            "Thanks."
        ),
    }


def _render_template_text(template: str, variables: Dict[str, str]) -> str:
    out = template or ''
    for key, value in variables.items():
        out = out.replace(f'{{{{{key}}}}}', value)
    return out


def _get_configured_mail_channel() -> str:
    """Read the outreach_mail_channel plugin param (empty string if unset)."""
    try:
        raw = _active_dss_client().get_plugin('admin-toolkit').get_settings().get_raw()
        config = raw.get('config', {}) if isinstance(raw, dict) else {}
        return (config.get('outreach_mail_channel') or '').strip()
    except Exception:
        return ''


def _list_mail_channels(client: Any, diagnostics: Optional[List[str]] = None) -> List[Dict[str, str]]:
    diag = diagnostics if diagnostics is not None else []
    channels: List[Dict[str, str]] = []

    raw_items = client.list_messaging_channels(channel_family='mail')
    diag.append(f"raw_items={len(raw_items) if isinstance(raw_items, list) else '?'}")

    for item in raw_items:
        raw = item.get_raw()
        channel_id = raw.get('id')
        family = str(raw.get('family') or '').lower()
        channel_type = str(raw.get('type') or '').lower()
        label = raw.get('label') or channel_id

        if family and family != 'mail':
            continue
        if not family and channel_type and channel_type not in ('smtp', 'mail'):
            continue

        if not channel_id:
            continue
        channels.append({
            'id': str(channel_id),
            'label': str(label or channel_id),
        })

    unique: Dict[str, Dict[str, str]] = {}
    for channel in channels:
        unique[channel['id']] = channel

    result = list(unique.values())
    diag.append(f"filtered={len(channels)} deduped={len(result)}")
    if not result:
        app.logger.warning(
            "[tools] _list_mail_channels: no mail channels found — diag: %s",
            "; ".join(diag),
        )
    return result


def _get_mail_channel(client: Any, requested_id: Optional[str]) -> Any:
    channels = _list_mail_channels(client)
    if not channels:
        return None

    selected = channels[0]
    if requested_id:
        for channel in channels:
            if channel['id'] == requested_id:
                selected = channel
                break

    channel_id = selected['id']
    if not hasattr(client, 'get_messaging_channel'):
        channel = None
    else:
        try:
            channel = client.get_messaging_channel(channel_id)
            if channel is not None:
                return channel
        except Exception:
            channel = None

    if hasattr(client, 'list_messaging_channels'):
        for attempt in (
            lambda: client.list_messaging_channels(as_type='objects', channel_family='mail'),
            lambda: client.list_messaging_channels(as_type='objects'),
        ):
            try:
                items = attempt()
            except Exception:
                continue
            if not isinstance(items, list):
                continue
            for item in items:
                item_id = None
                if hasattr(item, 'id'):
                    try:
                        item_id = str(getattr(item, 'id'))
                    except Exception:
                        item_id = None
                if not item_id and hasattr(item, 'get_id'):
                    try:
                        item_id = str(item.get_id())
                    except Exception:
                        item_id = None
                if item_id and item_id == channel_id:
                    return item
    return None


_PYTHON_WEBAPP_TYPES = {'DASH', 'STANDARD', 'BOKEH'}


def _list_projects_catalog_cheap(client: Any) -> List[Dict[str, str]]:
    """Cheap catalog: list_projects only, no git-log enrichment.

    Use this when callers only need {key, name, owner}. On large instances
    this avoids the ~130s per-project git-log walk.
    """
    t_total = time.time()
    projects = _sdk_fetch(
        'list_projects',
        _BACKEND_SETTINGS['cache_ttl_projects'],
        lambda: _bench_call('list_projects', client.list_projects) or [],
    )
    out: List[Dict[str, str]] = []
    for project in projects:
        if not isinstance(project, dict):
            continue
        key = str(project.get('projectKey') or project.get('key') or project.get('id') or '').strip()
        if not key:
            continue
        out.append({
            'key': key,
            'name': str(project.get('name') or key),
            'owner': str(project.get('ownerLogin') or project.get('owner') or project.get('ownerName') or 'Unknown'),
        })
    out.sort(key=lambda item: item.get('key') or '')
    app.logger.debug("[perf:catalog_cheap] elapsed=%.0fms count=%d", (time.time() - t_total) * 1000, len(out))
    return out


def _list_projects_catalog(client: Any) -> List[Dict[str, str]]:
    t_total = time.time()
    projects = _sdk_fetch(
        'list_projects',
        _BACKEND_SETTINGS['cache_ttl_projects'],
        lambda: _bench_call('list_projects', client.list_projects) or [],
    )
    app.logger.debug("[perf:catalog] list_projects elapsed=%.0fms count=%d", (time.time() - t_total) * 1000, len(projects))
    out: List[Dict[str, str]] = []
    keys: List[str] = []
    for project in projects:
        if not isinstance(project, dict):
            continue
        key = str(project.get('projectKey') or project.get('key') or project.get('id') or '').strip()
        if not key:
            continue
        entry: Dict[str, Any] = {
            'key': key,
            'name': str(project.get('name') or key),
            'owner': str(project.get('ownerLogin') or project.get('owner') or project.get('ownerName') or 'Unknown'),
        }
        out.append(entry)
        keys.append(key)

    # Fetch last-modified timestamps from git log in parallel (replaces versionTag
    # which does not reflect webapp edits).
    # Uses batch cache reads + batch writes to minimize SQL round-trips.
    if keys:
        cache = _get_sdk_cache()
        iid = _sdk_cache_instance_id()
        ttl = _BACKEND_SETTINGS['cache_ttl_projects']

        # Phase A: L1-only cache check (no SQL — avoids 418 SQL SELECTs on cold cache)
        cached_logs: Dict[str, Any] = {}
        uncached_keys: List[str] = []
        _get_mem = cache.get_mem if hasattr(cache, 'get_mem') else cache.get
        for key in keys:
            cached = _get_mem(iid, f'project_git_log:{key}', ttl)
            if cached is not None:
                cached_logs[key] = cached
            else:
                uncached_keys.append(key)
        app.logger.debug("[perf:catalog] git_log cache hit=%d miss=%d", len(cached_logs), len(uncached_keys))

        # Phase B: fetch uncached git logs from API in parallel
        fetched_logs: Dict[str, Any] = {}
        if uncached_keys:
            def _fetch_git_log(project_key: str) -> Tuple[str, Optional[Any]]:
                try:
                    local_client = _thread_client()
                    return (project_key, local_client.get_project(project_key).get_project_git().log())
                except Exception:
                    return (project_key, None)

            workers = min(8, len(uncached_keys))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_fetch_git_log, k): k for k in uncached_keys}
                for future in as_completed(futures):
                    pk, log = future.result()
                    if log is not None:
                        fetched_logs[pk] = log

            # Phase C: batch-write to cache
            if fetched_logs:
                cache.set_many(iid, {f'project_git_log:{k}': v for k, v in fetched_logs.items()}, ttl)

        # Extract timestamps from all logs
        all_logs = {**cached_logs, **fetched_logs}
        ts_map: Dict[str, Optional[int]] = {}
        for key, log in all_logs.items():
            try:
                ts_str = log['entries'][0]['timestamp']
                ts_map[key] = int(dtparser.isoparse(ts_str).timestamp() * 1000)
            except Exception:
                ts_map[key] = None
        for entry in out:
            ts = ts_map.get(entry['key'])
            if ts is not None:
                entry['lastModifiedOn'] = ts
        app.logger.debug("[perf:catalog] git_log_batch elapsed=%.0fms projects=%d workers=%d cached=%d fetched=%d", (time.time() - t_total) * 1000, len(keys), min(8, len(uncached_keys)) if uncached_keys else 0, len(cached_logs), len(fetched_logs))

    app.logger.debug("[perf:catalog] total elapsed=%.0fms", (time.time() - t_total) * 1000)
    out.sort(key=lambda item: item.get('key') or '')
    return out


def _build_project_info(client: Any, limit: int, include_settings: bool = True) -> Dict[str, Dict[str, str]]:
    project_info: Dict[str, Dict[str, str]] = {}
    projects = _list_projects_catalog(client)
    if limit > 0:
        projects = projects[:limit]

    # Pre-populate from catalog data (no API calls)
    catalog_by_key: Dict[str, Dict[str, str]] = {}
    project_keys: List[str] = []
    for project in projects:
        key = project.get('key')
        if not key:
            continue
        cat_entry: Dict[str, Any] = {
            'owner': str(project.get('owner') or 'Unknown'),
            'name': str(project.get('name') or key),
        }
        if project.get('lastModifiedOn') is not None:
            cat_entry['lastModifiedOn'] = project['lastModifiedOn']
        catalog_by_key[key] = cat_entry
        project_keys.append(key)

    if not include_settings:
        for key in project_keys:
            entry: Dict[str, Any] = {
                'name': catalog_by_key[key]['name'],
                'owner': catalog_by_key[key]['owner'],
            }
            if catalog_by_key[key].get('lastModifiedOn') is not None:
                entry['lastModifiedOn'] = catalog_by_key[key]['lastModifiedOn']
            project_info[key] = entry
        return project_info

    def _fetch_project_settings(key: str) -> Tuple[str, Dict[str, str]]:
        local_client = _thread_client()
        info: Dict[str, Any] = {
            'name': catalog_by_key[key]['name'],
            'owner': catalog_by_key[key]['owner'],
        }
        if catalog_by_key[key].get('lastModifiedOn') is not None:
            info['lastModifiedOn'] = catalog_by_key[key]['lastModifiedOn']
        try:
            project_obj = _bench_call('get_project', local_client.get_project, key)
            settings = project_obj.get_settings().get_raw()
            if isinstance(settings, dict):
                if settings.get('owner'):
                    info['owner'] = str(settings.get('owner'))
                if settings.get('name'):
                    info['name'] = str(settings.get('name'))
                default_python_env = _extract_nested_text(
                    settings,
                    'settings.codeEnvs.python.envName',
                    'codeEnvs.python.envName',
                )
                if default_python_env:
                    info['defaultPythonEnv'] = default_python_env
        except Exception:
            pass
        return (key, info)

    workers = min(_parallel_workers(8), len(project_keys))
    if workers <= 1:
        for key in project_keys:
            _, info = _fetch_project_settings(key)
            project_info[key] = info
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch_project_settings, key): key for key in project_keys}
            for future in as_completed(futures):
                try:
                    key, info = future.result()
                    project_info[key] = info
                except Exception:
                    fkey = futures[future]
                    project_info[fkey] = {
                        'name': catalog_by_key[fkey]['name'],
                        'owner': catalog_by_key[fkey]['owner'],
                    }

    return project_info


def _get_code_env_size_map(client: Any) -> Dict[str, int]:
    size_by_env: Dict[str, int] = {}
    global_footprint = _compute_footprint_payload(client, 'global', None)
    if isinstance(global_footprint, dict):
        code_envs_section = global_footprint.get('codeEnvs')
        if isinstance(code_envs_section, dict):
            items = code_envs_section.get('items')
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    name = item.get('name')
                    language = str(item.get('language') or '').strip().lower()
                    if not name or not language:
                        continue
                    size_by_env[f"{language}:{name}"] = _coerce_int(item.get('size'), 0)
    return size_by_env


def _extract_project_footprint_map_from_all_dss(payload: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(payload, dict):
        return out
    projects = payload.get('projects')
    if not isinstance(projects, dict):
        return out
    items = projects.get('items')
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get('projectKey') or '').strip()
        if not key:
            continue
        out[key] = item
    return out


def _fetch_project_footprint(project_key: str) -> Dict[str, Any]:
    project_key = str(project_key or '').strip()
    if not project_key:
        return {'projectKey': '', 'payload': None}
    client = _thread_client()
    payload = _compute_footprint_payload(client, 'project', project_key)
    return {'projectKey': project_key, 'payload': payload}


def _build_project_footprint_map(client: Any, project_keys: List[str]) -> Dict[str, Any]:
    return _build_project_footprint_map_with_deadline(client, project_keys, None, None)


def _build_project_footprint_map_with_deadline(
    client: Any,
    project_keys: List[str],
    deadline_ts: Optional[float] = None,
    progress_cb: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    wanted_keys = [str(key) for key in project_keys if str(key).strip()]
    footprint_map: Dict[str, Any] = {}

    started = time.time()
    if not wanted_keys:
        return footprint_map

    # Run direct per-project footprint calls with a fixed parallelism budget.
    max_workers = min(8, len(wanted_keys))
    app.logger.info("[footprint-map] mode=per-project wanted=%s workers=%s", len(wanted_keys), max_workers)
    _notify_progress(
        progress_cb,
        'project_footprint_fetch_pool_start',
        f"project footprint fetch started projects={len(wanted_keys)} workers={max_workers}",
    )
    if max_workers <= 1:
        for key in wanted_keys:
            if deadline_ts is not None and time.time() >= deadline_ts:
                _notify_progress(progress_cb, 'project_footprint_fetch_timeout', 'deadline reached before serial fetch', 'warn', key)
                break
            fetch_started = time.time()
            _notify_progress(progress_cb, 'project_footprint_fetch_start', 'fetch project footprint', 'info', key)
            result = _fetch_project_footprint(key)
            payload = result.get('payload')
            if payload is not None:
                footprint_map[key] = payload
                _notify_progress(
                    progress_cb,
                    'project_footprint_fetch_ok',
                    'project footprint loaded',
                    'info',
                    key,
                    elapsed_ms=(time.time() - fetch_started) * 1000.0,
                )
            else:
                _notify_progress(
                    progress_cb,
                    'project_footprint_fetch_error',
                    'project footprint payload missing',
                    'warn',
                    key,
                    elapsed_ms=(time.time() - fetch_started) * 1000.0,
                )
        app.logger.info("[footprint-map] serial rows=%s elapsed=%.2fs", len(footprint_map), time.time() - started)
        _notify_progress(
            progress_cb,
            'project_footprint_fetch_pool_done',
            f"project footprint fetch completed rows={len(footprint_map)}",
            'info',
            elapsed_ms=(time.time() - started) * 1000.0,
        )
        return footprint_map

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_key: Dict[Any, str] = {}
        future_started_at: Dict[str, float] = {}
        for key in wanted_keys:
            if deadline_ts is not None and time.time() >= deadline_ts:
                _notify_progress(progress_cb, 'project_footprint_fetch_timeout', 'deadline reached while submitting fetch jobs', 'warn', key)
                break
            _notify_progress(progress_cb, 'project_footprint_fetch_start', 'fetch project footprint', 'info', key)
            future_started_at[key] = time.time()
            future = pool.submit(_fetch_project_footprint, key)
            future_to_key[future] = key

        timed_out = False
        if future_to_key:
            timeout_seconds: Optional[float] = None
            if deadline_ts is not None:
                timeout_seconds = max(0.0, deadline_ts - time.time())
            try:
                future_iter = as_completed(list(future_to_key.keys()), timeout=timeout_seconds)
                for future in future_iter:
                    key = future_to_key.get(future, '')
                    if deadline_ts is not None and time.time() >= deadline_ts:
                        timed_out = True
                        _notify_progress(progress_cb, 'project_footprint_fetch_timeout', 'deadline reached while collecting results', 'warn', key or None)
                        break
                    try:
                        result = future.result()
                    except Exception as exc:
                        _notify_progress(progress_cb, 'project_footprint_fetch_error', f"fetch error: {exc}", 'warn', key or None)
                        continue
                    key = str(result.get('projectKey') or key or '')
                    payload = result.get('payload')
                    if key and payload is not None:
                        footprint_map[key] = payload
                        started_at = future_started_at.get(key, started)
                        _notify_progress(
                            progress_cb,
                            'project_footprint_fetch_ok',
                            'project footprint loaded',
                            'info',
                            key,
                            elapsed_ms=(time.time() - started_at) * 1000.0,
                        )
                    elif key:
                        started_at = future_started_at.get(key, started)
                        _notify_progress(
                            progress_cb,
                            'project_footprint_fetch_error',
                            'project footprint payload missing',
                            'warn',
                            key,
                            elapsed_ms=(time.time() - started_at) * 1000.0,
                        )
            except FuturesTimeoutError:
                timed_out = True
                _notify_progress(progress_cb, 'project_footprint_fetch_timeout', 'deadline reached while waiting for project footprint futures', 'warn')

        if timed_out or (deadline_ts is not None and time.time() >= deadline_ts):
            for future, key in future_to_key.items():
                if future.done():
                    continue
                future.cancel()
                started_at = future_started_at.get(key, started)
                _notify_progress(
                    progress_cb,
                    'project_footprint_fetch_timeout',
                    'project footprint fetch cancelled on deadline',
                    'warn',
                    key,
                    elapsed_ms=(time.time() - started_at) * 1000.0,
                )

    missing = max(0, len(wanted_keys) - len(footprint_map))
    if missing > 0:
        # Per-project fetch exceptions are logged at DEBUG in _compute_footprint_payload; surface
        # the aggregate here so customer logs reveal systemic failures (e.g., DSS 14.2 endpoint gone).
        app.logger.warning(
            "[footprint-map] final rows=%s wanted=%s missing=%s elapsed=%.2fs — run with DEBUG on 'webapps.backend' for per-project reasons",
            len(footprint_map), len(wanted_keys), missing, time.time() - started,
        )
    else:
        app.logger.info("[footprint-map] final rows=%s elapsed=%.2fs", len(footprint_map), time.time() - started)
    _notify_progress(
        progress_cb,
        'project_footprint_fetch_pool_done',
        f"project footprint fetch completed rows={len(footprint_map)}",
        'info',
        elapsed_ms=(time.time() - started) * 1000.0,
    )
    return footprint_map


def _check_env_usages(
    env_listing: Dict[str, Any],
    project_info: Dict[str, Dict[str, str]],
    size_by_env: Dict[str, int],
    usages_by_env: Dict[Tuple[str, str], List[Dict]],
) -> Optional[Dict[str, Any]]:
    """Look up usages for a single code env from the pre-fetched bulk dict.

    Returns a dict with env metadata and normalized usages, or None if the env
    should be skipped (e.g. plugin-managed or missing name).
    """
    if not isinstance(env_listing, dict):
        return None
    env_name = env_listing.get('envName') or env_listing.get('name') or env_listing.get('id')
    env_lang_raw = env_listing.get('envLang') or env_listing.get('language') or env_listing.get('type') or 'PYTHON'
    if not env_name:
        return None

    normalized_lang = _normalize_language(env_lang_raw)
    env_key = f"{normalized_lang}:{env_name}"
    deployment_mode = str(env_listing.get('deploymentMode') or '').upper()
    if deployment_mode in {'PLUGIN_MANAGED', 'DSS_INTERNAL'}:
        return None

    owner = _extract_code_env_owner(env_listing, {})
    env_key_tuple = (normalized_lang.upper(), env_name)
    usages: List[Any] = usages_by_env.get(env_key_tuple, [])

    normalized_usages: List[Dict[str, Any]] = []
    for raw_usage in usages:
        usage = _usage_to_dict(raw_usage)
        project_key = _extract_usage_project_key(usage)
        if not project_key:
            continue
        normalized = _normalize_usage_entry(usage, project_info)
        normalized_usages.append({
            'projectKey': project_key,
            'projectName': str(normalized.get('projectName') or project_key),
            'usageType': str(normalized.get('usageType') or 'UNKNOWN'),
            'objectType': str(normalized.get('objectType') or normalized.get('usageType') or 'UNKNOWN'),
            'objectId': str(normalized.get('objectId') or ''),
            'objectName': str(normalized.get('objectName') or normalized.get('objectId') or ''),
            'codeEnvKey': env_key,
            'codeEnvName': str(env_name),
            'codeEnvLanguage': normalized_lang,
            'codeEnvOwner': owner,
        })

    return {
        'envKey': env_key,
        'name': str(env_name),
        'language': normalized_lang,
        'owner': owner,
        'sizeBytes': _coerce_int(size_by_env.get(env_key), 0),
        'pythonVersion': str(env_listing.get('pythonVersion') or env_listing.get('pythonInterpreter') or ''),
        'usages': normalized_usages,
    }


def _fetch_code_env_details(
    client: Any, lang_upper: str, env_name: str,
    fetch_settings: bool = True,
) -> Tuple[Dict[str, Any], List[Any]]:
    """Fetch code env settings. Returns (settings_raw, [])."""
    settings_raw: Dict[str, Any] = {}
    if fetch_settings and hasattr(client, 'get_code_env'):
        try:
            settings_raw = _sdk_fetch(
                f'code_env_settings:{lang_upper}:{env_name}',
                _BACKEND_SETTINGS['cache_ttl_code_envs'],
                lambda: _safe_get_raw(_bench_call('get_code_env', client.get_code_env, lang_upper, env_name).get_settings()),
            )
        except Exception:
            settings_raw = {}
    return settings_raw, []


def _load_code_env_full_details(
    env_listing: Dict[str, Any],
    project_info: Dict[str, Dict[str, str]],
    size_by_env: Dict[str, int],
    include_usages: bool = True,
    usages_by_env: Optional[Dict[Tuple[str, str], List[Dict]]] = None,
    user_email_by_login: Optional[Dict[str, str]] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(env_listing, dict):
        return None

    name = env_listing.get('envName') or env_listing.get('name') or env_listing.get('id')
    lang = env_listing.get('envLang') or env_listing.get('language') or env_listing.get('type')
    version = env_listing.get('pythonVersion') or env_listing.get('rVersion') or env_listing.get('version')
    if not name:
        return None

    language = _normalize_language(lang)

    size_key = f"{language}:{name}"
    size_bytes = _coerce_int(size_by_env.get(size_key), 0)
    owner = _extract_code_env_owner(env_listing, {})

    # Fast path for large instances: avoid fetching settings unless needed.
    should_fetch = include_usages or (not version) or owner == 'Unknown'
    client = _thread_client()
    settings_raw, _ = _fetch_code_env_details(
        client, language.upper(), name,
        fetch_settings=should_fetch,
    )
    usages: List[Any] = []
    if include_usages and usages_by_env is not None:
        usages = usages_by_env.get((language.upper(), name), [])
    if settings_raw:
        owner = _extract_code_env_owner(env_listing, settings_raw)
    owner_email = (user_email_by_login or {}).get(owner, '') if owner and owner != 'Unknown' else ''
    normalized_usages: List[Dict[str, Any]] = []
    usage_counts: Dict[str, int] = {}
    project_keys: set = set()
    for raw_usage in usages:
        usage = _usage_to_dict(raw_usage)
        normalized = _normalize_usage_entry(usage, project_info)
        normalized.update({
            'codeEnvName': name,
            'codeEnvLanguage': language,
            'codeEnvOwner': owner,
            'codeEnvKey': size_key,
        })
        usage_type = str(normalized.get('usageType') or 'UNKNOWN')
        usage_counts[usage_type] = usage_counts.get(usage_type, 0) + 1
        project_key = str(normalized.get('projectKey') or '')
        if project_key:
            project_keys.add(project_key)
        normalized_usages.append(normalized)

    if language == 'r':
        version_label = str(version or 'R')
    else:
        detail_version = (
            _extract_nested_text(
                settings_raw,
                'desc.pythonInterpreter',
                'pythonInterpreter',
                'spec.pythonInterpreter',
            )
            or env_listing.get('pythonInterpreter')
            or version
        )
        if not detail_version and include_usages:
            detail = _bench_call('code_env_detail_lookup', _client_perform_json, client, 'GET', f"/admin/code-envs/PYTHON/{name}")
            if isinstance(detail, dict):
                detail_version = _extract_nested_text(detail, 'desc.pythonInterpreter', 'pythonInterpreter')

        raw_version_text = str(detail_version or 'Unknown')
        match = re.search(r'PYTHON(\d)(\d+)', raw_version_text, flags=re.IGNORECASE)
        if match:
            version_label = f"{int(match.group(1))}.{int(match.group(2))}"
        else:
            dotted = re.search(r'(\d+)\.(\d+)', raw_version_text)
            version_label = f"{dotted.group(1)}.{dotted.group(2)}" if dotted else raw_version_text

    return {
        'language': language,
        'versionLabel': version_label,
        'row': {
            'name': name,
            'version': version_label,
            'language': language,
            'sizeBytes': size_bytes,
            'owner': owner,
            'ownerEmail': owner_email,
            'usageCount': len(normalized_usages),
            'usageSummary': usage_counts,
            'projectCount': len(project_keys),
            'projectKeys': sorted(project_keys),
            'usageDetails': _dedupe_usage_entries(normalized_usages),
        },
    }


def _collect_project_code_env_usage(
    client: Any,
    project_info: Dict[str, Dict[str, str]],
    size_by_env: Dict[str, int],
    include_project_object_scan: bool = True,
    include_code_env_usage_api: bool = True,
    deadline_ts: Optional[float] = None,
    progress_cb: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    """Collect code env usage by querying the Dataiku API for each env's usages."""
    _notify_progress(
        progress_cb,
        'collect_project_code_env_usage_start',
        f"start projects={len(project_info)}",
    )

    envs = [env for env in (_sdk_fetch(
        'list_code_envs',
        _BACKEND_SETTINGS['cache_ttl_code_envs'],
        lambda: client.list_code_envs() or [],
    ) or []) if isinstance(env, dict)]
    total = len(envs)

    bulk_usages_raw = _sdk_fetch(
        'list_code_env_usages',
        _BACKEND_SETTINGS['cache_ttl_code_envs'],
        lambda: client.list_code_env_usages() or [],
    )
    usages_by_env: Dict[Tuple[str, str], List[Dict]] = {}
    for u in bulk_usages_raw:
        k = (str(u.get('envLang', '')).upper(), str(u.get('envName', '')))
        usages_by_env.setdefault(k, []).append(u)

    _notify_progress(
        progress_cb,
        'code_env_usage_scan_start',
        f"checking {total} code envs",
    )

    env_payloads: List[Dict[str, Any]] = []
    checked = [0]

    def _check_and_report(env: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        payload = _check_env_usages(env, project_info, size_by_env, usages_by_env)
        env_name = env.get('envName') or env.get('name') or '?'
        checked[0] += 1
        idx = checked[0]
        if payload is None:
            _notify_progress(progress_cb, 'code_env_usage_check', f"[{idx}/{total}] {env_name} — skipped (plugin/internal)")
        else:
            usage_count = len(payload.get('usages') or [])
            status = f"{usage_count} usage(s)" if usage_count > 0 else "UNUSED"
            _notify_progress(progress_cb, 'code_env_usage_check', f"[{idx}/{total}] {env_name} — {status}")
        return payload

    for env in envs:
        payload = _check_and_report(env)
        if payload:
            env_payloads.append(payload)

    envs_by_project: Dict[str, set] = {k: set() for k in project_info.keys()}
    usage_breakdown_by_project: Dict[str, Dict[str, int]] = {k: {} for k in project_info.keys()}
    usage_details_by_project: Dict[str, List[Dict[str, Any]]] = {k: [] for k in project_info.keys()}
    env_meta_by_key: Dict[str, Dict[str, Any]] = {}

    for payload in env_payloads:
        env_key = str(payload.get('envKey') or '')
        env_name = str(payload.get('name') or '')
        if not env_key or not env_name:
            continue

        env_meta_by_key[env_key] = {
            'key': env_key,
            'name': env_name,
            'language': str(payload.get('language') or 'python'),
            'owner': str(payload.get('owner') or 'Unknown'),
            'sizeBytes': _coerce_int(payload.get('sizeBytes'), 0),
            'pythonVersion': str(payload.get('pythonVersion') or ''),
            'deploymentMode': '',
            'usageSummary': {},
            'usageDetails': [],
            'projectKeys': set(),
        }

        for usage in payload.get('usages') or []:
            project_key = str(usage.get('projectKey') or '')
            if not project_key:
                continue
            usage_type = str(usage.get('usageType') or 'UNKNOWN').upper()

            # Track usage in per-project maps only for known projects
            if project_key in envs_by_project:
                envs_by_project[project_key].add(env_key)
                counts = usage_breakdown_by_project[project_key]
                counts[usage_type] = counts.get(usage_type, 0) + 1
                usage_details_by_project[project_key].append(usage)

            # Always track in env metadata (determines unused status)
            env_meta = env_meta_by_key[env_key]
            env_meta['usageSummary'][usage_type] = env_meta['usageSummary'].get(usage_type, 0) + 1
            env_meta['usageDetails'].append(usage)
            env_meta['projectKeys'].add(project_key)

    for env_key, env_meta in env_meta_by_key.items():
        deduped = _dedupe_usage_entries(env_meta.get('usageDetails') or [])
        env_meta['usageDetails'] = deduped
        env_meta['usageCount'] = len(deduped)
        env_meta['projectKeys'] = sorted(set(env_meta.get('projectKeys') or []))
        env_meta['projectCount'] = len(env_meta['projectKeys'])
        env_meta['usageSummary'] = dict(env_meta.get('usageSummary') or {})

    for project_key, usages in usage_details_by_project.items():
        usage_details_by_project[project_key] = _dedupe_usage_entries(usages)

    unused_count = sum(1 for m in env_meta_by_key.values() if not m.get('usageDetails'))
    in_use_count = len(env_meta_by_key) - unused_count
    _notify_progress(
        progress_cb,
        'collect_project_code_env_usage_done',
        f"done — {len(env_meta_by_key)} checked, {in_use_count} in use, {unused_count} unused",
    )
    return {
        'envsByProject': envs_by_project,
        'usageBreakdownByProject': usage_breakdown_by_project,
        'usageDetailsByProject': usage_details_by_project,
        'envMetaByKey': env_meta_by_key,
        'codeStudiosByProject': _list_code_studios_by_project(client, project_info, progress_cb),
    }


def _list_code_studios_by_project(
    client: Any,
    project_info: Dict[str, Dict[str, str]],
    progress_cb: Optional[Callable[..., None]] = None,
) -> Dict[str, List[Dict[str, str]]]:
    """Return {project_key: [{id, name}, ...]} for all known projects."""
    studios_by_project: Dict[str, List[Dict[str, str]]] = {}
    for pk in project_info:
        entries: List[Dict[str, str]] = []
        try:
            items = client.get_project(pk).list_code_studios(as_type='listitems')
            for item in items:
                raw = getattr(item, '_data', {}) or {}
                cs_id = str(raw.get('id') or '')
                cs_name = str(raw.get('name') or cs_id)
                if cs_id:
                    entries.append({'id': cs_id, 'name': cs_name})
        except Exception as exc:
            app.logger.debug("[footprint-map] code studio list failed project=%s: %s", pk, exc)
            _notify_progress(progress_cb, 'project_code_studios_error', f"code studio list failed: {exc}", 'warn', pk)
            entries = []
        studios_by_project[pk] = entries
    return studios_by_project


def _count_permissions_by_project(
    client: Any,
    project_info: Dict[str, Dict[str, str]],
    progress_cb: Optional[Callable[..., None]] = None,
) -> Dict[str, int]:
    """Return {project_key: permission_entry_count} for all known projects."""
    counts: Dict[str, int] = {}
    for pk in project_info:
        try:
            raw = client.get_project(pk).get_settings().get_raw()
            counts[pk] = len(raw.get('permissions') or [])
        except Exception as exc:
            app.logger.debug("[footprint-map] permission count failed project=%s: %s", pk, exc)
            _notify_progress(progress_cb, 'project_permissions_error', f"permission count failed: {exc}", 'warn', pk)
            counts[pk] = 0
    return counts


def _get_shared_project_code_env_usage(
    client: Any,
    project_info: Dict[str, Dict[str, str]],
    size_by_env: Dict[str, int],
    include_project_object_scan: bool = True,
    include_code_env_usage_api: bool = True,
    deadline_ts: Optional[float] = None,
    progress_cb: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    if not project_info:
        return {}

    cache_key = _shared_project_code_env_usage_key(project_info)
    ttl_sec = max(1, int(_BACKEND_SETTINGS.get('cache_ttl_usage_full') or 5))
    now_ts = time.time()
    wait_event: Optional[threading.Event] = None
    is_owner = False

    with _SHARED_USAGE_SCANS_LOCK:
        stale_keys = [
            key
            for key, entry in _SHARED_USAGE_SCANS.items()
            if isinstance(entry, dict)
            and str(entry.get('status') or '') != 'running'
            and (now_ts - float(entry.get('ts') or now_ts)) > ttl_sec
        ]
        for key in stale_keys:
            _SHARED_USAGE_SCANS.pop(key, None)

        entry = _SHARED_USAGE_SCANS.get(cache_key)
        if isinstance(entry, dict):
            entry_status = str(entry.get('status') or '')
            entry_ts = float(entry.get('ts') or 0.0)
            entry_result = entry.get('result')
            if entry_status == 'done' and (now_ts - entry_ts) <= ttl_sec and isinstance(entry_result, dict):
                _notify_progress(
                    progress_cb,
                    'collect_project_code_env_usage_cache_hit',
                    f"reusing cached code env usage scan envs={len((entry_result.get('envMetaByKey') or {}))}",
                )
                return entry_result
            if entry_status == 'running':
                ready = entry.get('ready')
                if isinstance(ready, threading.Event):
                    wait_event = ready

        if wait_event is None:
            wait_event = threading.Event()
            _SHARED_USAGE_SCANS[cache_key] = {
                'status': 'running',
                'ts': now_ts,
                'ready': wait_event,
                'result': None,
                'error': None,
            }
            is_owner = True

    if is_owner:
        try:
            result = _collect_project_code_env_usage(
                client,
                project_info,
                size_by_env,
                include_project_object_scan=include_project_object_scan,
                include_code_env_usage_api=include_code_env_usage_api,
                deadline_ts=deadline_ts,
                progress_cb=progress_cb,
            )
        except Exception as exc:
            with _SHARED_USAGE_SCANS_LOCK:
                entry = _SHARED_USAGE_SCANS.get(cache_key)
                if isinstance(entry, dict) and entry.get('ready') is wait_event:
                    entry['status'] = 'error'
                    entry['ts'] = time.time()
                    entry['error'] = str(exc)
                    wait_event.set()
            raise

        with _SHARED_USAGE_SCANS_LOCK:
            entry = _SHARED_USAGE_SCANS.get(cache_key)
            if isinstance(entry, dict) and entry.get('ready') is wait_event:
                entry['status'] = 'done'
                entry['ts'] = time.time()
                entry['result'] = result
                entry['error'] = None
                wait_event.set()
        return result

    _notify_progress(
        progress_cb,
        'collect_project_code_env_usage_wait',
        'waiting for shared code env usage scan',
    )
    timeout_seconds = None if deadline_ts is None else max(0.0, deadline_ts - time.time())
    finished = wait_event.wait(timeout_seconds)

    with _SHARED_USAGE_SCANS_LOCK:
        entry = _SHARED_USAGE_SCANS.get(cache_key)
        entry_status = str(entry.get('status') or '') if isinstance(entry, dict) else ''
        entry_result = entry.get('result') if isinstance(entry, dict) else None
        entry_error = str(entry.get('error') or '') if isinstance(entry, dict) else ''

    if finished and entry_status == 'done' and isinstance(entry_result, dict):
        _notify_progress(
            progress_cb,
            'collect_project_code_env_usage_wait_done',
            f"shared code env usage scan ready envs={len((entry_result.get('envMetaByKey') or {}))}",
        )
        return entry_result

    if entry_status == 'error':
        _notify_progress(
            progress_cb,
            'collect_project_code_env_usage_wait_retry',
            f"shared code env usage scan failed ({entry_error or 'unknown error'}); retrying locally",
            'warn',
        )
    else:
        _notify_progress(
            progress_cb,
            'collect_project_code_env_usage_wait_timeout',
            'shared code env usage scan wait timed out; retrying locally',
            'warn',
        )

    return _collect_project_code_env_usage(
        client,
        project_info,
        size_by_env,
        include_project_object_scan=include_project_object_scan,
        include_code_env_usage_api=include_code_env_usage_api,
        deadline_ts=deadline_ts,
        progress_cb=progress_cb,
    )


def _build_footprint_node(name: str, path: str, footprint: Any, depth: int, max_depth: int,
                          bonus_depth: int = 0) -> Dict[str, Any]:
    details = _footprint_details_map(footprint)
    children: List[Dict[str, Any]] = []
    has_hidden = False
    effective_max = max_depth + bonus_depth
    if depth < effective_max:
        # Pre-sort children by size to identify top-N for adaptive depth
        child_items = []
        for child_name, child_footprint in details.items():
            child_size = _coerce_int(child_footprint.get('size'), 0)
            child_items.append((child_name, child_footprint, child_size))
        child_items.sort(key=lambda x: x[2], reverse=True)

        top_n = 5
        for idx, (child_name, child_footprint, _child_size) in enumerate(child_items):
            clean_name = str(child_name).strip('/') or str(child_name)
            child_path = f"{path.rstrip('/')}/{clean_name}" if path != '/' else f"/{clean_name}"
            child_bonus = 2 if (idx < top_n and bonus_depth == 0 and depth == 0) else bonus_depth
            child = _build_footprint_node(clean_name, child_path, child_footprint, depth + 1, max_depth,
                                          bonus_depth=child_bonus)
            children.append(child)
    elif details:
        has_hidden = True

    children.sort(key=lambda c: c.get('size', 0), reverse=True)

    size = _coerce_int(footprint.get('size'), 0)
    file_count = _coerce_int(footprint.get('nbFiles'), 0)

    if size <= 0 and children:
        size = sum(child['size'] for child in children)
    if file_count <= 0 and children:
        file_count = sum(child['fileCount'] for child in children)

    own_size = max(0, size - sum(child['size'] for child in children))
    locations_raw = footprint.get('locations')
    locations: List[str] = []
    if isinstance(locations_raw, list):
        locations = [str(loc) for loc in locations_raw if loc is not None and str(loc).strip()]
    elif isinstance(locations_raw, str) and locations_raw.strip():
        locations = [locations_raw.strip()]

    if not children and not details:
        file_count = max(file_count, 1)

    return {
        'name': name,
        'path': path,
        'size': size,
        'ownSize': own_size,
        'isDirectory': True,
        'children': children,
        'fileCount': file_count,
        'depth': depth,
        'hasHiddenChildren': has_hidden,
        'locations': locations,
    }


def _find_footprint_subtree(
    root_footprint: Any,
    root_path: str,
    target_path: str,
) -> Optional[Tuple[str, str, Any]]:
    """Locate target subtree using only Dataiku footprint details."""
    abs_root = os.path.abspath(str(root_path or '/'))
    abs_target = os.path.abspath(str(target_path or abs_root))
    if abs_target == abs_root:
        return (str(os.path.basename(abs_root) or abs_root or '/'), abs_root, root_footprint)
    root_prefix = abs_root.rstrip('/') + '/'
    if not abs_target.startswith(root_prefix):
        return None

    rel = abs_target[len(root_prefix):]
    parts = [part for part in rel.split('/') if part]
    current = root_footprint
    current_path = abs_root
    current_name = str(os.path.basename(abs_root) or abs_root or '/')

    for part in parts:
        details = _footprint_details_map(current)
        if not details:
            return None
        next_footprint = details.get(part)
        if next_footprint is None:
            # Be tolerant to slash formatting differences.
            for key, value in details.items():
                if str(key).strip('/') == part:
                    next_footprint = value
                    break
        if next_footprint is None:
            return None
        current = next_footprint
        current_name = part
        current_path = f"{current_path.rstrip('/')}/{part}" if current_path != '/' else f"/{part}"

    return (current_name, current_path, current)


def _ensure_license_fallback(payload: Dict[str, Any], dip_home: str) -> Dict[str, Any]:
    properties = payload.get('licenseProperties') or {}
    if properties:
        return payload

    fallback = {
        'License Source': 'Unavailable from webapp context',
        'DIP_HOME': dip_home,
        'Resolution': 'Use ZIP diagnostics or grant webapp backend read access to config/license.json',
    }
    payload['licenseProperties'] = fallback
    payload['hasLicenseUsage'] = False
    return payload


def _build_dir_tree_from_footprint(
    client: Any,
    dip_home: str,
    max_depth: int,
    target_path: Optional[str] = None,
    scope: str = 'dss',
    project_key: Optional[str] = None,
    footprint_payload: Optional[Any] = None,
) -> Dict[str, Any]:
    scope = scope if scope in ('dss', 'project') else 'dss'
    if footprint_payload is not None:
        root_footprint = footprint_payload
    else:
        footprint_scope = 'all-dss' if scope == 'dss' else scope
        root_footprint = _compute_footprint_payload(client, footprint_scope, project_key)
    root_meta = _scope_root(scope, project_key)
    root_path = root_meta['path']

    if not root_footprint:
        app.logger.warning("[dir-tree] footprint payload unavailable scope=%s project=%s", scope, project_key)
        if target_path:
            return {'node': None}
        return {
            'root': None,
            'totalSize': 0,
            'totalFiles': 0,
            'rootPath': root_path,
            'scope': scope,
            'projectKey': project_key,
        }

    if target_path:
        subtree = _find_footprint_subtree(root_footprint, root_path, target_path)
        if subtree is None:
            return {'node': None}
        node_name, node_path, node_footprint = subtree
        node = _build_footprint_node(node_name, node_path, node_footprint, 0, max_depth)
        return {'node': node}

    root_node = _build_footprint_node(root_meta['name'], root_path, root_footprint, 0, max_depth)
    return {
        'root': root_node,
        'totalSize': root_node['size'],
        'totalFiles': root_node['fileCount'],
        'rootPath': root_node['path'],
        'scope': scope,
        'projectKey': project_key,
    }


@app.route('/api/mode')
def api_mode():
    return jsonify({'mode': 'live'})


@app.route('/api/settings/raw')
def api_settings_raw():
    client = g.client
    settings = client.get_general_settings().get_raw()
    return jsonify(settings)


@app.route('/api/project-standards/raw')
def api_project_standards_raw():
    client = g.client
    try:
        standards = client.get_project_standards().get_raw()
    except Exception:
        standards = {}
    return jsonify(standards)


def _instance_info_from_install_map(install: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(install, dict):
        return {}
    normalized = {str(k).strip().lower(): v for k, v in install.items()}

    def pick(*keys: str) -> Any:
        for key in keys:
            value = normalized.get(key.lower())
            if value not in (None, ''):
                return value
        return None

    info: Dict[str, Any] = {}
    node_id = pick('general.nodeid', 'nodeid', 'general.nodeId')
    install_id = pick('general.installid', 'installid', 'general.installId')
    instance_url = pick('general.instanceurl', 'instanceurl', 'general.instanceUrl')
    ssl = pick('server.ssl', 'ssl')
    port = pick('server.port', 'port')
    if node_id:
        info['nodeId'] = node_id
    if install_id:
        info['installId'] = install_id
    if instance_url:
        info['instanceUrl'] = instance_url
    if ssl is not None:
        info['https'] = str(ssl).lower() in ('true', '1', 'yes')
    if port:
        info['port'] = port
    return info


def _parse_install_ini_map(text: Optional[str]) -> Dict[str, str]:
    if not text:
        return {}
    out: Dict[str, str] = {}
    current_section = None
    for raw in text.split('\n'):
        line = raw.strip()
        if not line or line.startswith('#') or line.startswith(';'):
            continue
        if line.startswith('[') and line.endswith(']'):
            current_section = line[1:-1].strip().lower()
            continue
        if '=' not in line:
            continue
        key, value = [part.strip() for part in line.split('=', 1)]
        key_l = key.lower()
        out[key_l] = value
        if current_section:
            out[f'{current_section}.{key_l}'] = value
    return out


@app.route('/api/overview')
def api_overview():
    client = g.client
    dip_home = _dip_home()
    host_id = _safe_request_host_id()

    def loader_remote():
        m = _host_metrics_macro(client)
        install = m.get('install') or {}
        version = m.get('version') or {}
        cpu = m.get('cpu') or {}
        os_info = m.get('os') or {}
        physical_cores = _coerce_int(cpu.get('physicalCores'), 0)
        logical_cores = _coerce_int(cpu.get('logicalCores'), 0)
        if physical_cores > 0 and logical_cores > physical_cores:
            cpu_label = f"{physical_cores} Cores / {logical_cores} Threads"
        else:
            cpu_label = str(physical_cores or logical_cores or '')
        settings = None
        try:
            settings = client.get_general_settings().get_raw()
        except Exception:
            settings = None
        return {
            'cpuCores': cpu_label,
            'osInfo': os_info.get('PRETTY_NAME') or os_info.get('NAME') or '',
            'memoryInfo': _parse_memory_info(m.get('freeOutput')),
            'systemLimits': _parse_system_limits(m.get('ulimitOutput')),
            'filesystemInfo': _parse_filesystem_info(m.get('dfOutput')),
            'pythonVersion': m.get('pythonVersion') or '',
            'sparkVersion': _find_spark_version(settings) or '',
            'lastRestartTime': _parse_supervisord_restart(m.get('supervisordLog')) or '',
            'dssVersion': version.get('product_version') or version.get('version'),
            'instanceInfo': _instance_info_from_install_map(install),
            'javaMemRaw': m.get('javaMemRaw'),
        }

    def loader():
        if host_id != 'local':
            return loader_remote()
        free_output = _run_command(['free', '-m'])
        ulimit_output = _run_command(['bash', '-lc', 'ulimit -a'])
        df_output = _run_command(['df', '-h'])

        version_info = (
            _safe_read_json(os.path.join(dip_home, 'dss-version.json'))
            or _safe_read_json(os.path.join(dip_home, 'config', 'dss-version.json'))
            or {}
        )
        install_ini = _safe_read_text(os.path.join(dip_home, 'install.ini'))
        instance_info = _instance_info_from_install_map(_parse_install_ini_map(install_ini))

        supervisord_log = None
        try:
            supervisord_log = client.get_log('supervisord.log')
        except Exception:
            supervisord_log = _safe_read_text(os.path.join(dip_home, 'run', 'supervisord.log'))

        settings = None
        try:
            settings = client.get_general_settings().get_raw()
        except Exception:
            settings = None

        spark_version = _find_spark_version(settings)
        local_metrics = None
        if not instance_info or not (version_info.get('version') or version_info.get('dssVersion') or version_info.get('product_version')):
            try:
                local_metrics = _host_metrics_macro(client)
            except Exception:
                local_metrics = None
        if isinstance(local_metrics, dict):
            metric_instance_info = _instance_info_from_install_map(local_metrics.get('install') or {})
            for key, value in metric_instance_info.items():
                if value not in (None, '') and not instance_info.get(key):
                    instance_info[key] = value
            metric_version = local_metrics.get('version')
            if isinstance(metric_version, dict):
                for key, value in metric_version.items():
                    if value not in (None, '') and not version_info.get(key):
                        version_info[key] = value

        return {
            'cpuCores': _get_cpu_cores(),
            'osInfo': _get_os_info(),
            'memoryInfo': _parse_memory_info(free_output),
            'systemLimits': _parse_system_limits(ulimit_output),
            'filesystemInfo': _parse_filesystem_info(df_output),
            'pythonVersion': platform.python_version(),
            'sparkVersion': spark_version,
            'lastRestartTime': _parse_supervisord_restart(supervisord_log),
            'dssVersion': version_info.get('version') or version_info.get('dssVersion') or version_info.get('product_version'),
            'instanceInfo': instance_info,
        }

    data = _cache_get('overview', _BACKEND_SETTINGS['cache_ttl_overview'], loader)
    return jsonify(data)


@app.route('/api/host/process-metrics')
def api_process_metrics():
    """Per-process CPU + memory snapshot from the active host (via macro).

    Host-bound (`ps`/subprocess) so it goes through the process-metrics macro,
    which runs as `dataiku`. Short-cached to keep repeated page loads cheap.
    """
    data = _cache_get(
        'process_metrics',
        _BACKEND_SETTINGS['cache_ttl_overview'],
        lambda: _process_metrics_macro(g.client),
    )
    return jsonify(data)


@app.route('/api/connections')
def api_connections():
    client = g.client

    def loader():
        connections = _sdk_fetch(
            'list_connections',
            _BACKEND_SETTINGS['cache_ttl_overview'],
            lambda: client.list_connections(),
        )
        connection_counts: Dict[str, int] = {}
        details: List[Dict[str, Any]] = []

        if isinstance(connections, dict):
            items = connections.items()
        else:
            items = [(c.get('name'), c) for c in connections]

        for name, config in items:
            if not isinstance(config, dict):
                continue
            conn_type = config.get('type')
            if conn_type == 'EC2':
                conn_type = 'S3'
            if not conn_type:
                continue
            driver = None
            params = config.get('params') or {}
            if isinstance(params, dict):
                driver = params.get('driverClassName')

            display_type = conn_type
            if conn_type == 'JDBC' and driver:
                short_driver = driver if len(driver) <= 50 else driver[:47] + '...'
                display_type = f"JDBC ({short_driver})"

            details.append({
                'name': name or 'unknown',
                'type': conn_type,
                'driverClassName': driver,
            })

            connection_counts[display_type] = connection_counts.get(display_type, 0) + 1

        return {'connections': connection_counts, 'connectionDetails': details}

    data = _cache_get('connections', _BACKEND_SETTINGS['cache_ttl_connections'], loader)
    return jsonify(data)


_CLOUD_HDFS_INTERFACES = {
    'S3': ('S3A', 'EMRFS'),
    'EC2': ('S3A', 'EMRFS'),
    'Azure': ('ABFS', 'WASB', 'WASBS'),
    'GCS': ('GS',),
}


def _audit_details_readable(config: dict) -> bool:
    """True if connection details are readable by at least one group (ALL or ALLOWED with groups)."""
    dr = config.get('detailsReadability') or {}
    mode = dr.get('readableBy')
    if mode == 'ALL':
        return True
    if mode == 'ALLOWED' and dr.get('allowedGroups'):
        return True
    return False


def _audit_connection(name: str, config: dict) -> dict:
    """Inspect one connection and return {name,type,configIssues,severity}."""
    conn_type = config.get('type') or 'Unknown'
    params = config.get('params') if isinstance(config.get('params'), dict) else {}
    issues: List[str] = []
    severity = 'info'

    if conn_type == 'Filesystem' and name == 'filesystem_root':
        issues.append('Default filesystem_root connection should be removed')
        severity = 'critical'

    elif conn_type in ('S3', 'EC2', 'Azure', 'GCS'):
        if not _audit_details_readable(config):
            issues.append('Connection details not readable by any group (detailsReadability)')
        allowed_interfaces = _CLOUD_HDFS_INTERFACES.get(conn_type, ())
        hdfs_interface = params.get('hdfsInterface') or ''
        if not hdfs_interface:
            issues.append('HDFS interface not configured')
        elif allowed_interfaces and hdfs_interface not in allowed_interfaces:
            issues.append('HDFS interface %s not in recommended %s' % (hdfs_interface, '/'.join(allowed_interfaces)))
        if issues:
            severity = 'warning'

    elif conn_type == 'Snowflake':
        if not params.get('useSparkNative'):
            issues.append('Spark native integration not enabled (useSparkNative)')
        if not params.get('useUDF'):
            issues.append('UDF support not enabled (useUDF)')
        if not params.get('autoFastWriteConnection'):
            issues.append('Fast-write connection not configured (autoFastWriteConnection)')
        if not _audit_details_readable(config):
            issues.append('Connection details not readable by any group (detailsReadability)')
        if issues:
            severity = 'warning'

    elif conn_type == 'Databricks':
        if not params.get('autoFastWriteConnection'):
            issues.append('Fast-write connection not configured (autoFastWriteConnection)')
        if not _audit_details_readable(config):
            issues.append('Connection details not readable by any group (detailsReadability)')
        if issues:
            severity = 'warning'

    elif conn_type in ('Redshift', 'BigQuery', 'Synapse'):
        if not params.get('autoFastWriteConnection'):
            issues.append('Fast-write connection not configured (autoFastWriteConnection)')
        if issues:
            severity = 'warning'

    return {
        'name': name,
        'type': conn_type,
        'configIssues': issues,
        'severity': severity,
    }


@app.route('/api/connections/audit')
def api_connections_audit():
    """Audit connection configuration (fast-write, details readability, HDFS interface, filesystem_root)."""
    app.logger.info("[connections-audit] endpoint hit")
    client = g.client

    def loader():
        connections = _sdk_fetch(
            'list_connections',
            _BACKEND_SETTINGS['cache_ttl_connections'],
            lambda: client.list_connections(),
        )
        if isinstance(connections, dict):
            items = connections.items()
        else:
            items = [(c.get('name'), c) for c in connections]

        results: List[Dict[str, Any]] = []
        summary = {'critical': 0, 'warning': 0, 'info': 0, 'total': 0}
        for name, config in items:
            if not name or not isinstance(config, dict):
                continue
            summary['total'] += 1
            audit = _audit_connection(name, config)
            if audit['configIssues']:
                results.append(audit)
                sev = audit['severity']
                if sev in summary:
                    summary[sev] += 1
        return {'connections': results, 'summary': summary}

    data = _cache_get('connections_audit', _BACKEND_SETTINGS['cache_ttl_connections'], loader)
    return jsonify(data)


_CONN_HEALTH_MEMO: Dict[Tuple[str, int, str], Dict[str, Any]] = {}
_CONN_HEALTH_MEMO_LOCK = threading.Lock()


@app.route('/api/connections/health')
def api_connection_health():
    """Stream connection health-test results via SSE.

    Memoized by (session_epoch, connection_set_hash). If the same set was
    tested earlier in this epoch, replay the cached events and skip the
    141×850ms work. Global Refresh bumps the epoch and invalidates the memo.
    """
    import re
    _SANITIZE_RE = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b|/[\w/.-]{4,}')

    def _test_one(name, conn_type):
        try:
            client = _thread_client()
            resp = client.get_connection(name).test()
            ok = resp.get('connectionOK', False) if isinstance(resp, dict) else False
            if ok:
                return {'name': name, 'type': conn_type, 'status': 'ok'}
            error_msg = ''
            if isinstance(resp, dict):
                error_msg = resp.get('connectionErrorMsg') or resp.get('message') or ''
            sanitized = _SANITIZE_RE.sub('***', error_msg)[:200] if error_msg else 'Connection test failed'
            return {'name': name, 'type': conn_type, 'status': 'fail', 'error': sanitized}
        except Exception as exc:
            msg = str(exc)
            if 'NotImplementedException' in msg or 'not implemented' in msg.lower():
                return {'name': name, 'type': conn_type, 'status': 'skipped'}
            sanitized = _SANITIZE_RE.sub('***', msg)[:200]
            return {'name': name, 'type': conn_type, 'status': 'fail', 'error': sanitized}

    def generate():
        t0 = time.time()
        try:
            connections = _sdk_fetch(
                'list_connections',
                _BACKEND_SETTINGS['cache_ttl_connections'],
                lambda: g.client.list_connections(),
            )
            if isinstance(connections, dict):
                items = list(connections.items())
            else:
                items = [(c.get('name'), c) for c in connections]
        except Exception as e:
            yield "event: error\ndata: %s\n\n" % json.dumps({'error': str(e)[:200]})
            return

        epoch = _get_session_epoch()
        item_names = sorted([str(n) for n, _ in items if n])
        item_hash = hashlib.sha1('\n'.join(item_names).encode('utf-8')).hexdigest()
        memo_key = (_safe_request_host_id(), epoch, item_hash)
        with _CONN_HEALTH_MEMO_LOCK:
            cached = _CONN_HEALTH_MEMO.get(memo_key)
        if cached is not None:
            yield "event: init\ndata: %s\n\n" % json.dumps({'total': len(items), 'cached': True})
            for result in cached.get('results', []):
                yield "event: conn\ndata: %s\n\n" % json.dumps(result)
            yield "event: done\ndata: %s\n\n" % json.dumps(cached.get('done') or {})
            return

        yield "event: init\ndata: %s\n\n" % json.dumps({'total': len(items)})

        ok_count = 0
        fail_count = 0
        skipped_count = 0
        collected_results: List[Dict[str, Any]] = []
        workers = min(8, max(1, len(items)))
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            futures = {
                pool.submit(_test_one, name, (config.get('type', 'unknown') if isinstance(config, dict) else 'unknown')): name
                for name, config in items if name
            }
            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception as exc:
                    result = {'name': futures[future], 'type': 'unknown', 'status': 'fail',
                              'error': str(exc)[:200]}
                st = result.get('status')
                if st == 'ok':
                    ok_count += 1
                elif st == 'fail':
                    fail_count += 1
                else:
                    skipped_count += 1
                collected_results.append(result)
                yield "event: conn\ndata: %s\n\n" % json.dumps(result)
        except GeneratorExit:
            pool.shutdown(wait=False, cancel_futures=True)
            return
        finally:
            pool.shutdown(wait=False)

        testable = ok_count + fail_count
        pct = round((ok_count / testable) * 100) if testable > 0 else 100

        total_ms = int((time.time() - t0) * 1000)
        done_payload = {
            'total_ms': total_ms,
            'summary': {
                'total': len(items),
                'ok': ok_count,
                'fail': fail_count,
                'skipped': skipped_count,
                'healthPct': pct,
            },
        }
        with _CONN_HEALTH_MEMO_LOCK:
            # Drop other epochs' entries (keep only current).
            stale = [k for k in _CONN_HEALTH_MEMO if len(k) < 2 or k[1] != epoch]
            for k in stale:
                _CONN_HEALTH_MEMO.pop(k, None)
            _CONN_HEALTH_MEMO[memo_key] = {'results': collected_results, 'done': done_payload}
        yield "event: done\ndata: %s\n\n" % json.dumps(done_payload)

    return Response(stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route('/api/connections/usages')
def api_connection_usages():
    """Stream connection-project usage mapping via SSE.

    Scans all projects to find:
    - Dataset connections (params.connection)
    - LLM recipe connections (llmId field in recipe payload)
    """

    _LLM_RECIPE_PREFIXES = ('prompt', 'nlp_llm_')

    def _parse_conn_params(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                import ast
                parsed = ast.literal_eval(value)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                try:
                    parsed = json.loads(value)
                    return parsed if isinstance(parsed, dict) else {}
                except Exception:
                    return {}
        return {}

    def _is_local_filesystem_connection(conn_name: Any, conn_types: Dict[str, str]) -> bool:
        name = str(conn_name or '').strip()
        if not name:
            return False
        if name == 'filesystem_root':
            return True
        typ = str(conn_types.get(name) or '').strip().lower()
        if not typ:
            return False
        if any(token in typ for token in ('s3', 'snowflake', 'jdbc', 'sql', 'hdfs', 'azure', 'gcs', 'google', 'adls')):
            return False
        return 'filesystem' in typ or typ in {'fs', 'localfs', 'local-filesystem', 'local_filesystem'}

    def _folder_connection_from_raw(raw: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        params = _parse_conn_params(raw.get('params') or raw.get('folderParams') or raw.get('folderParamsMap'))
        conn_name = (
            params.get('connection')
            or params.get('connectionName')
            or raw.get('connection')
            or raw.get('connectionName')
        )
        path = (
            params.get('path')
            or params.get('root')
            or params.get('folderPath')
            or raw.get('path')
        )
        return (str(conn_name) if conn_name else None, str(path) if path else None)

    def _format_dataset_path(params: Dict[str, Any]) -> str:
        path = params.get('path')
        if isinstance(path, str) and path:
            return path
        rules = params.get('filesSelectionRules')
        if isinstance(rules, dict):
            explicit = rules.get('explicitFiles')
            if isinstance(explicit, list) and explicit:
                files = [str(f) for f in explicit if f]
                return ', '.join(files)
            mode = rules.get('mode')
            return f'({mode})' if mode else ''
        return str(path) if path else ''

    def _scan_project(project_key, conn_types: Dict[str, str]):
        """Scan one project for dataset connections and LLM connections."""
        client = _thread_client()
        proj = client.get_project(project_key)
        dataset_conns = []
        llm_conns = []
        local_fs_objects = []
        errors = []

        # 1. Dataset connections
        try:
            for ds in proj.list_datasets():
                params = _parse_conn_params(ds.get('params', {}))
                conn_name = params.get('connection') if isinstance(params, dict) else None
                dataset_name = ds.get('name', '')
                dataset_type = ds.get('type', '')
                if conn_name:
                    dataset_conns.append({
                        'datasetName': dataset_name,
                        'datasetType': dataset_type,
                        'connection': conn_name,
                    })
                    if _is_local_filesystem_connection(conn_name, conn_types):
                        local_fs_objects.append({
                            'objectType': 'dataset',
                            'objectId': dataset_name,
                            'objectName': dataset_name,
                            'objectSubtype': dataset_type,
                            'connection': conn_name,
                            'path': _format_dataset_path(params),
                        })
        except Exception as e:
            app.logger.debug("[conn_usage] list_datasets failed for %s: %s", project_key, e)
            errors.append({'projectKey': project_key, 'area': 'datasets', 'error': str(e)[:240]})

        # 2. Managed folders using local filesystem connections
        try:
            for folder in proj.list_managed_folders():
                raw = _cex_item_raw(folder)
                folder_id = str(raw.get('id') or raw.get('odbId') or raw.get('name') or '').strip()
                folder_name = str(raw.get('name') or folder_id or '').strip()
                conn_name, folder_path = _folder_connection_from_raw(raw)
                if not conn_name and folder_id:
                    try:
                        folder_obj = proj.get_managed_folder(folder_id)
                        settings = folder_obj.get_settings() if hasattr(folder_obj, 'get_settings') else None
                        folder_raw = settings.get_raw() if settings is not None and hasattr(settings, 'get_raw') else {}
                        if isinstance(folder_raw, dict):
                            conn_name, folder_path = _folder_connection_from_raw(folder_raw)
                            folder_name = str(folder_raw.get('name') or folder_name or folder_id)
                    except Exception as exc:
                        app.logger.debug("[conn_usage] managed folder settings failed for %s/%s: %s", project_key, folder_id, exc)
                if conn_name and _is_local_filesystem_connection(conn_name, conn_types):
                    local_fs_objects.append({
                        'objectType': 'folder',
                        'objectId': folder_id,
                        'objectName': folder_name or folder_id,
                        'objectSubtype': str(raw.get('type') or 'managed folder'),
                        'connection': conn_name,
                        'path': folder_path or '',
                    })
        except Exception as e:
            app.logger.debug("[conn_usage] list_managed_folders failed for %s: %s", project_key, e)
            errors.append({'projectKey': project_key, 'area': 'folders', 'error': str(e)[:240]})

        # 3. LLM recipe connections
        try:
            recipes = proj.list_recipes()
            llm_recipes = [r for r in recipes
                           if r.get('type', '').startswith(_LLM_RECIPE_PREFIXES)
                           or 'llm' in r.get('type', '').lower()]
            for r in llm_recipes:
                try:
                    recipe = proj.get_recipe(r['name'])
                    settings = recipe.get_settings()
                    payload = settings.get_json_payload() if hasattr(settings, 'get_json_payload') else None
                    if not payload:
                        raw_str = settings.get_payload() if hasattr(settings, 'get_payload') else ''
                        try:
                            payload = json.loads(raw_str) if raw_str else {}
                        except Exception:
                            payload = {}
                    if not payload:
                        continue
                    for llm_id in _find_llm_ids(payload):
                        parts = llm_id.split(':')
                        if len(parts) >= 3:
                            conn_name = parts[1]
                            llm_conns.append({
                                'recipeName': r.get('name', ''),
                                'recipeType': r.get('type', ''),
                                'llmId': llm_id,
                                'connection': conn_name,
                            })
                except Exception as e:
                    app.logger.debug("[conn_usage] recipe %s/%s failed: %s", project_key, r.get('name'), e)
                    errors.append({'projectKey': project_key, 'area': 'recipes', 'error': str(e)[:240]})
        except Exception as e:
            app.logger.debug("[conn_usage] list_recipes failed for %s: %s", project_key, e)
            errors.append({'projectKey': project_key, 'area': 'recipes', 'error': str(e)[:240]})

        return {
            'projectKey': project_key,
            'datasetConns': dataset_conns,
            'llmConns': llm_conns,
            'localFilesystemObjects': local_fs_objects,
            'errors': errors,
        }

    def generate():
        t0 = time.time()
        try:
            client = g.client
            projects = _list_projects_catalog_cheap(client)
            project_names = {p['key']: p.get('name', p['key']) for p in projects}
            project_owner_by_key = {p['key']: p.get('owner', 'Unknown') for p in projects}
            project_keys = list(project_names.keys())
            users = client.list_users() if hasattr(client, 'list_users') else []
            user_email_by_login: Dict[str, str] = {}
            for user in users:
                if isinstance(user, dict) and user.get('login'):
                    user_email_by_login[str(user.get('login'))] = str(user.get('email') or user.get('login'))

            connections = _sdk_fetch(
                'list_connections',
                _BACKEND_SETTINGS['cache_ttl_connections'],
                lambda: client.list_connections(),
            )
            conn_types: Dict[str, str] = {}
            if isinstance(connections, dict):
                for name, config in connections.items():
                    if isinstance(config, dict):
                        conn_types[name] = config.get('type', 'unknown')
            else:
                for c in connections:
                    conn_types[c.get('name', '')] = c.get('type', 'unknown')
        except Exception as e:
            yield "event: error\ndata: %s\n\n" % json.dumps({'error': str(e)[:200]})
            return

        yield "event: init\ndata: %s\n\n" % json.dumps({'total': len(project_keys)})

        dataset_map: Dict[str, List[Dict]] = {}   # conn -> [{projectKey, projectName, datasetName, datasetType}]
        llm_map: Dict[str, List[Dict]] = {}       # conn -> [{projectKey, projectName, recipeName, recipeType, llmId}]
        local_fs_usages: List[Dict[str, Any]] = []
        scanned = 0
        scan_errors = []

        workers = min(8, max(1, len(project_keys)))
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            futures = {pool.submit(_scan_project, pk, conn_types): pk for pk in project_keys}
            for future in as_completed(futures):
                pk = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {'projectKey': pk, 'datasetConns': [], 'llmConns': []}
                    scan_errors.append({'projectKey': pk, 'area': 'scan', 'error': str(exc)[:240]})
                scan_errors.extend(result.get('errors', []) or [])

                pname = project_names.get(pk, pk)
                owner = str(project_owner_by_key.get(pk) or 'Unknown')
                owner_email = user_email_by_login.get(owner, owner)
                for u in result.get('datasetConns', []):
                    conn = u['connection']
                    dataset_map.setdefault(conn, []).append({
                        'projectKey': pk,
                        'projectName': pname,
                        'datasetName': u['datasetName'],
                        'datasetType': u['datasetType'],
                    })
                for u in result.get('llmConns', []):
                    conn = u['connection']
                    llm_map.setdefault(conn, []).append({
                        'projectKey': pk,
                        'projectName': pname,
                        'recipeName': u['recipeName'],
                        'recipeType': u['recipeType'],
                        'llmId': u['llmId'],
                    })
                for u in result.get('localFilesystemObjects', []):
                    if not isinstance(u, dict):
                        continue
                    local_fs_usages.append({
                        'owner': owner,
                        'ownerEmail': owner_email,
                        'projectKey': pk,
                        'projectName': pname,
                        'objectType': u.get('objectType') or 'object',
                        'objectId': u.get('objectId') or '',
                        'objectName': u.get('objectName') or u.get('objectId') or '',
                        'objectSubtype': u.get('objectSubtype') or '',
                        'connection': u.get('connection') or '',
                        'path': u.get('path') or '',
                    })

                scanned += 1
                if scanned % 20 == 0 or scanned == len(project_keys):
                    yield "event: progress\ndata: %s\n\n" % json.dumps({'scanned': scanned})
        except GeneratorExit:
            pool.shutdown(wait=False, cancel_futures=True)
            return
        finally:
            pool.shutdown(wait=False)

        # Build final payloads
        dataset_usages = []
        for conn_name in sorted(dataset_map.keys()):
            usages = dataset_map[conn_name]
            dataset_usages.append({
                'name': conn_name,
                'type': conn_types.get(conn_name, 'unknown'),
                'projects': usages,
                'projectCount': len(set(u['projectKey'] for u in usages)),
                'datasetCount': len(usages),
            })

        llm_usages = []
        for conn_name in sorted(llm_map.keys()):
            usages = llm_map[conn_name]
            llm_usages.append({
                'name': conn_name,
                'type': conn_types.get(conn_name, 'unknown'),
                'projects': usages,
                'projectCount': len(set(u['projectKey'] for u in usages)),
                'recipeCount': len(usages),
            })

        total_ms = int((time.time() - t0) * 1000)
        yield "event: done\ndata: %s\n\n" % json.dumps({
            'total_ms': total_ms,
            'scanErrors': scan_errors,
            'failedProjectCount': len({e['projectKey'] for e in scan_errors}),
            'scannedProjectCount': len(project_keys),
            'datasetUsages': dataset_usages,
            'llmUsages': llm_usages,
            'localFilesystemUsages': sorted(
                local_fs_usages,
                key=lambda item: (
                    str(item.get('owner') or '').lower(),
                    str(item.get('projectKey') or '').lower(),
                    str(item.get('objectName') or '').lower(),
                ),
            ),
        })

    return Response(stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


_SQL_PUSHDOWN_CODE_RECIPE_TYPES = frozenset({
    'python', 'r', 'pyspark', 'spark_scala', 'scala', 'sql_query', 'sql_script',
})

# Visual recipes that have no SQL engine option at all — always skip.
_SQL_PUSHDOWN_NO_SQL_ENGINE_TYPES = frozenset({
    'clustering_cluster',   # ML clustering scoring — no SQL engine
    'clustering_scoring',   # alt name used by some DSS versions
    'fuzzyjoin',            # docs: "Only DSS engine is supported"
    'download',             # downloads files, never in-database
})

# Sampling-recipe methods that CAN be pushed to Snowflake SQL.
# FULL = no sampling (all data); HEAD_SEQUENTIAL = first N rows → LIMIT N;
# RANDOM_FIXED_NB = fixed random N → SAMPLE (only without seed).
# Everything else (class rebalance, column subset, stratified, sorted,
# random-approx, last records, etc.) requires a 2-pass / full-sort that
# does not translate to a Snowflake SELECT — leave it off the allowlist.
_SAMPLING_METHODS_PUSHDOWNABLE = frozenset({
    'FULL',
    'HEAD_SEQUENTIAL',
    'RANDOM_FIXED_NB',
})


@app.route('/api/projects/sql_pushdown_audit')
def api_sql_pushdown_audit():
    """Stream visual recipes running on DSS engine that qualify for SQL pushdown.

    A recipe is reported when: (1) it is a visual (non-code) recipe, (2) all inputs
    and outputs are SQL-type datasets sharing the same connection, and (3) the
    selected engine is DSS (i.e., not already SQL). Grouped by project owner.
    """
    app.logger.info("[sql_pushdown] endpoint hit")

    def _dataset_map_for(proj) -> Dict[str, Dict[str, str]]:
        out: Dict[str, Dict[str, str]] = {}
        try:
            datasets = proj.list_datasets()
        except Exception as e:
            app.logger.debug("[sql_pushdown] list_datasets failed: %s", e)
            return out
        for ds in datasets:
            name = ds.get('name') or ''
            if not name:
                continue
            params = ds.get('params', {})
            if isinstance(params, str):
                try:
                    import ast
                    params = ast.literal_eval(params)
                except Exception:
                    params = {}
            conn = params.get('connection') if isinstance(params, dict) else None
            out[name] = {
                'type': ds.get('type', '') or '',
                'connection': conn or '',
            }
        return out

    def _strip_project_prefix(ref: str, project_key: str) -> Tuple[str, bool]:
        """Return (localName, isForeign). Foreign refs are disqualifiers."""
        if '.' in ref:
            prefix, name = ref.split('.', 1)
            if prefix == project_key:
                return name, False
            return ref, True
        return ref, False

    def _scan_project_sql_pushdown(project_key: str) -> Dict[str, Any]:
        client = _thread_client()
        proj = client.get_project(project_key)
        findings: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []

        ds_map = _dataset_map_for(proj)

        try:
            recipes = proj.list_recipes() or []
        except Exception as e:
            app.logger.debug("[sql_pushdown] list_recipes failed for %s: %s", project_key, e)
            errors.append({'projectKey': project_key, 'area': 'recipes', 'error': str(e)[:240]})
            return {'projectKey': project_key, 'findings': findings, 'errors': errors}

        for r in recipes:
            if not isinstance(r, dict):
                continue
            rtype = r.get('type', '') or ''
            if not rtype or rtype in _SQL_PUSHDOWN_CODE_RECIPE_TYPES:
                continue
            if rtype in _SQL_PUSHDOWN_NO_SQL_ENGINE_TYPES:
                continue
            rname = r.get('name') or ''
            if not rname:
                continue
            try:
                recipe = proj.get_recipe(rname)
                settings = recipe.get_settings()
                if rtype == 'sampling':
                    payload = settings.get_json_payload() if hasattr(settings, 'get_json_payload') else None
                    if not payload:
                        raw_str = settings.get_payload() if hasattr(settings, 'get_payload') else ''
                        try:
                            payload = json.loads(raw_str) if raw_str else {}
                        except Exception:
                            payload = {}
                    sel = (payload or {}).get('selection') or {}
                    sp = sel.get('samplingMethod') or sel.get('samplingMethodObj') or ''
                    # If samplingMethod is absent, recipe is filter-only (pushdownable as WHERE).
                    # If present, only the allowlist translates to Snowflake SQL.
                    if sp and sp not in _SAMPLING_METHODS_PUSHDOWNABLE:
                        continue
                    # RANDOM_FIXED_NB only translates if no random seed is set
                    if sp == 'RANDOM_FIXED_NB' and bool(sel.get('useRandomSeed')):
                        continue
                inputs = list(settings.get_flat_input_refs() or [])
                outputs = list(settings.get_flat_output_refs() or [])
                if not inputs or not outputs:
                    continue

                def _resolve(refs):
                    resolved: List[Tuple[str, Dict[str, str]]] = []
                    for ref in refs:
                        local, foreign = _strip_project_prefix(ref, project_key)
                        if foreign:
                            return None
                        info = ds_map.get(local)
                        if info is None:
                            return None
                        resolved.append((local, info))
                    return resolved

                in_resolved = _resolve(inputs)
                if in_resolved is None:
                    continue
                out_resolved = _resolve(outputs)
                if out_resolved is None:
                    continue

                all_infos = [info for _, info in in_resolved + out_resolved]
                if not all(info.get('type') in _SQL_CONNECTION_TYPES for info in all_infos):
                    continue
                connections = {info.get('connection') for info in all_infos}
                if len(connections) != 1:
                    continue
                connection = next(iter(connections))
                if not connection:
                    continue

                status = recipe.get_status()
                engine_details = status.get_selected_engine_details() if status else None
                if not isinstance(engine_details, dict):
                    continue
                if engine_details.get('type') != 'DSS':
                    continue

                # Ask the recipe whether it CAN run in-database. DSS exposes one
                # dict per candidate engine; the SQL (in-database) engine reports
                # isSelectable=False with an explanatory statusMessage when the
                # recipe can't be pushed down — e.g. a prepare step like the
                # UpDownFiller processor ("Not translatable to SQL"). Without this
                # check we false-positive those recipes as "should run in SQL".
                engines = status.get_engines_details() if status else None
                if not isinstance(engines, list):
                    continue
                sql_engine = next(
                    (e for e in engines if isinstance(e, dict) and e.get('type') == 'SQL'),
                    None,
                )
                if not sql_engine or not sql_engine.get('isSelectable'):
                    continue

                findings.append({
                    'recipeName': rname,
                    'recipeType': rtype,
                    'connection': connection,
                    'inputs': [local for local, _ in in_resolved],
                    'outputs': [local for local, _ in out_resolved],
                })
            except Exception as e:
                app.logger.debug("[sql_pushdown] recipe %s/%s failed: %s", project_key, rname, e)
                errors.append({'projectKey': project_key, 'area': 'recipe', 'error': str(e)[:240]})

        return {'projectKey': project_key, 'findings': findings, 'errors': errors}

    def generate():
        t0 = time.time()
        try:
            client = g.client
            projects_catalog = _list_projects_catalog_cheap(client)
            project_names = {p['key']: p.get('name', p['key']) for p in projects_catalog}
            project_owners = {p['key']: p.get('owner') or '' for p in projects_catalog}
            project_keys = list(project_names.keys())

            users = _sdk_fetch(
                'list_users',
                _BACKEND_SETTINGS['cache_ttl_users'],
                lambda: client.list_users(),
            ) or []
            user_map: Dict[str, Dict[str, Any]] = {}
            for u in users:
                login = u.get('login') or ''
                if not login:
                    continue
                user_map[login] = {
                    'displayName': u.get('displayName') or login,
                    'email': u.get('email') or None,
                }
        except Exception as e:
            app.logger.exception("[sql_pushdown] setup failed exc_type=%s", type(e).__name__)
            yield "event: error\ndata: %s\n\n" % json.dumps({'error': f"{type(e).__name__}: {str(e)[:200]}"})
            return

        app.logger.info("[sql_pushdown] scan start projects=%d users=%d", len(project_keys), len(user_map))
        yield "event: init\ndata: %s\n\n" % json.dumps({'total': len(project_keys)})

        per_project: Dict[str, List[Dict[str, Any]]] = {}
        scan_errors = []
        scanned = 0

        workers = min(8, max(1, len(project_keys)))
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            futures = {pool.submit(_scan_project_sql_pushdown, pk): pk for pk in project_keys}
            for future in as_completed(futures):
                pk = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {'projectKey': pk, 'findings': []}
                    scan_errors.append({'projectKey': pk, 'area': 'scan', 'error': str(exc)[:240]})
                if result.get('findings'):
                    per_project[pk] = result['findings']
                scan_errors.extend(result.get('errors', []) or [])
                scanned += 1
                if scanned % 20 == 0 or scanned == len(project_keys):
                    yield "event: progress\ndata: %s\n\n" % json.dumps({'scanned': scanned})
        except GeneratorExit:
            pool.shutdown(wait=False, cancel_futures=True)
            return
        finally:
            pool.shutdown(wait=False)

        # Group by owner
        owner_buckets: Dict[str, Dict[str, Any]] = {}
        for pk, findings in per_project.items():
            owner_login = project_owners.get(pk) or 'Unknown'
            info = user_map.get(owner_login, {})
            bucket = owner_buckets.setdefault(owner_login, {
                'ownerLogin': owner_login,
                'ownerDisplayName': info.get('displayName') or owner_login,
                'ownerEmail': info.get('email'),
                'projects': [],
                'totalRecipes': 0,
            })
            sorted_findings = sorted(findings, key=lambda f: (f.get('recipeName') or '').lower())
            bucket['projects'].append({
                'projectKey': pk,
                'projectName': project_names.get(pk, pk),
                'recipes': sorted_findings,
            })
            bucket['totalRecipes'] += len(sorted_findings)

        # Sort projects within each owner by recipe count desc, then name asc
        for bucket in owner_buckets.values():
            bucket['projects'].sort(
                key=lambda p: (-len(p['recipes']), (p.get('projectName') or '').lower()),
            )

        # Sort owners by totalRecipes desc, then displayName asc
        owner_groups = sorted(
            owner_buckets.values(),
            key=lambda b: (-b['totalRecipes'], (b.get('ownerDisplayName') or '').lower()),
        )

        total_ms = int((time.time() - t0) * 1000)
        yield "event: done\ndata: %s\n\n" % json.dumps({
            'total_ms': total_ms,
            'ownerGroups': owner_groups,
            'scanErrors': scan_errors,
            'failedProjectCount': len({e['projectKey'] for e in scan_errors}),
            'scannedProjectCount': len(project_keys),
        })

    return Response(stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route('/api/users')
def api_users():
    client = g.client

    def loader():
        users = _sdk_fetch(
            'list_users',
            _BACKEND_SETTINGS['cache_ttl_users'],
            lambda: client.list_users(),
        )
        groups = _sdk_fetch(
            'list_groups',
            _BACKEND_SETTINGS['cache_ttl_users'],
            lambda: client.list_groups(),
        )

        enabled_users = [u for u in users if u.get('enabled') is True]
        user_stats: Dict[str, Any] = {
            'Total Users': len(users),
            'Enabled Users': len(enabled_users),
        }

        profile_counts: Dict[str, int] = {}
        for user in enabled_users:
            profile = user.get('userProfile')
            if profile:
                profile_counts[profile] = profile_counts.get(profile, 0) + 1
        user_stats.update(profile_counts)

        if groups:
            user_stats['Total Groups'] = len(groups)

        return {
            'userStats': user_stats,
            'users': [
                {
                    'login': u.get('login') or '',
                    'email': u.get('email'),
                    'enabled': u.get('enabled'),
                    'userProfile': u.get('userProfile'),
                }
                for u in users
            ],
        }

    data = _cache_get('users', _BACKEND_SETTINGS['cache_ttl_users'], loader)
    return jsonify(data)


@app.route('/api/license')
def api_license():
    client = g.client
    dip_home = _dip_home()

    def loader():
        def _fetch_raw():
            status = client.get_licensing_status()
            return status if isinstance(status, dict) else status.get_raw()
        raw = _sdk_fetch(
            'licensing_status',
            _BACKEND_SETTINGS['cache_ttl_license'],
            _fetch_raw,
        )
        license_content = raw.get('base', {}).get('licenseContent', {})
        parsed = _parse_license(license_content)
        parsed['licenseSource'] = 'api'
        parsed['licensingStatus'] = raw
        return _ensure_license_fallback(parsed, dip_home)

    data = _cache_get('license', _BACKEND_SETTINGS['cache_ttl_license'], loader)
    return jsonify(data)


@app.route('/api/java-memory')
def api_java_memory():
    dip_home = _dip_home()
    content = _safe_read_text(os.path.join(dip_home, 'bin', 'env-default.sh')) or ''
    return content


@app.route('/api/projects')
def api_projects():
    client = g.client

    def loader():
        started = time.time()
        projects = []
        raw_projects = _sdk_fetch(
            'list_projects',
            _BACKEND_SETTINGS['cache_ttl_projects'],
            lambda: client.list_projects() or [],
        )
        total = len(raw_projects)
        app.logger.info("[projects] start total=%s", total)
        for idx, project in enumerate(raw_projects, 1):
            key = project.get('projectKey') or project.get('key') or project.get('id')
            name = project.get('name') or key
            owner = project.get('ownerLogin') or project.get('owner') or project.get('ownerName') or 'Unknown'

            perms_raw: Any = None

            try:
                project_obj = client.get_project(key)
            except Exception:
                project_obj = None

            if project_obj is not None:
                try:
                    perms_raw = project_obj.get_permissions()
                except Exception as exc:
                    app.logger.warning("[projects] %s permissions fetch failed: %s", key, exc)

            listing = project if isinstance(project, dict) else {}
            version_number = _extract_project_version_number(listing, listing, {})
            permissions = _normalize_project_permissions(perms_raw)

            if key == 'PYTHONAUDIT_TEST' or (version_number == 0 and len(permissions) == 0):
                perms_raw_type = type(perms_raw).__name__ if perms_raw is not None else 'NoneType'
                perms_raw_keys = []
                if isinstance(perms_raw, dict):
                    perms_raw_keys = sorted(list(perms_raw.keys()))
                app.logger.info(
                    "[projects] %s version=%s perms=%s listingVersion=%s permsRawType=%s permsRawKeys=%s",
                    key,
                    version_number,
                    len(permissions),
                    _extract_nested_int(listing, 'versionTag.versionNumber'),
                    perms_raw_type,
                    perms_raw_keys,
                )

            projects.append({
                'key': key,
                'name': name.replace('_', ' ') if isinstance(name, str) else key,
                'owner': owner,
                'permissions': permissions,
                'versionNumber': version_number,
            })
            if idx % 50 == 0:
                app.logger.info(
                    "[projects] progress=%s/%s elapsed=%.2fs",
                    idx,
                    total,
                    time.time() - started,
                )

        app.logger.info("[projects] done count=%s elapsed=%.2fs", len(projects), time.time() - started)
        return {'projects': projects}

    data = _cache_get('projects', _BACKEND_SETTINGS['cache_ttl_projects'], loader)
    return jsonify(data)


# ── Scan pipeline helpers: /api/code-envs ─────────────────────────────────────

def _env_key_from_listing(env: Dict[str, Any]) -> str:
    env_name = env.get('envName') or env.get('name') or env.get('id')
    env_lang_raw = env.get('envLang') or env.get('language') or env.get('type') or 'PYTHON'
    language = _normalize_language(env_lang_raw)
    return f"{language}:{env_name}" if env_name else 'unknown'


def _task_ce_catalog(
    client: Any,
    add_event: Callable,
    limit_label: str,
    project_limit: int,
) -> Dict[str, Any]:
    add_event('load_project_catalog', 'loading project catalog')
    project_catalog = _list_projects_catalog_cheap(client)
    selected_catalog: List[Dict[str, str]] = project_catalog[:] if project_limit <= 0 else project_catalog[:project_limit]
    add_event('select_projects_by_key', f"selecting projects by key limit={limit_label}")
    project_info: Dict[str, Dict[str, str]] = {}
    for project in selected_catalog:
        key = str(project.get('key') or '').strip()
        if not key:
            continue
        project_info[key] = {
            'name': str(project.get('name') or key),
            'owner': str(project.get('owner') or 'Unknown'),
        }
    add_event(
        'project_scope_ready',
        f"project scope ready selected={len(project_info)} total={len(project_catalog)} limit={limit_label}",
    )
    return {
        'project_catalog': project_catalog,
        'selected_catalog': selected_catalog,
        'project_info': project_info,
        'selected_count': len(project_info),
    }


def _task_ce_size_map(add_event: Callable) -> Dict[str, int]:
    """Runs in a background thread; acquires its own client via _thread_client()."""
    client = _thread_client()
    global_footprint = _compute_footprint_payload(client, 'global', None)
    size_by_env: Dict[str, int] = {}
    if isinstance(global_footprint, dict):
        code_envs_section = global_footprint.get('codeEnvs')
        if isinstance(code_envs_section, dict):
            code_env_items = code_envs_section.get('items')
            if isinstance(code_env_items, list):
                for item in code_env_items:
                    if not isinstance(item, dict):
                        continue
                    item_name = item.get('name')
                    item_lang = str(item.get('language') or '').strip().lower()
                    if not item_name or not item_lang:
                        continue
                    size_key = f"{item_lang}:{item_name}"
                    size_by_env[size_key] = _coerce_int(item.get('size'), 0)
    return size_by_env


def _task_ce_usage_scan(
    client: Any,
    project_info: Dict[str, Dict[str, str]],
    deadline_ts: float,
    add_event: Callable,
    progress_cb: Callable,
) -> Dict[str, Any]:
    if not project_info:
        return {}
    add_event('collect_project_code_env_usage', f"collecting usage for projects={len(project_info)}")
    return _get_shared_project_code_env_usage(
        client,
        project_info,
        {},
        include_project_object_scan=True,
        include_code_env_usage_api=False,
        deadline_ts=deadline_ts,
        progress_cb=progress_cb,
    )


def _task_ce_env_details(
    client: Any,
    envs: List[Dict[str, Any]],
    project_info: Dict[str, Dict[str, str]],
    size_by_env: Dict[str, int],
    progress_meta: Dict[str, Any],
    deadline_ts: float,
    add_event: Callable,
    append_partial_row: Callable,
    usages_by_env: Optional[Dict[Tuple[str, str], List[Dict]]] = None,
    user_email_by_login: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    env_details: List[Dict[str, Any]] = []
    max_workers = min(_parallel_workers(_BACKEND_SETTINGS['code_env_detail_workers']), max(1, len(envs)))
    progress_meta['envDetailsTotal'] = len(envs)
    progress_meta['envDetailsDone'] = 0
    add_event('load_code_env_details', f"loading env details envs={len(envs)} workers={max_workers}")
    if max_workers <= 1:
        for env in envs:
            if time.time() > deadline_ts:
                add_event('load_code_env_details', 'deadline reached at step=load_code_env_details', 'warn')
                break
            env_key = _env_key_from_listing(env)
            env_started = time.time()
            add_event('code_env_detail_start', 'loading code env detail', 'info', env_key)
            detail = _load_code_env_full_details(env, project_info, size_by_env, include_usages=True, usages_by_env=usages_by_env, user_email_by_login=user_email_by_login)
            if detail:
                env_details.append(detail)
                row = detail.get('row')
                if isinstance(row, dict):
                    append_partial_row(row)
                add_event('code_env_detail_ok', 'code env detail loaded', 'info', env_key, (time.time() - env_started) * 1000.0)
            else:
                add_event('code_env_detail_error', 'code env detail missing', 'warn', env_key, (time.time() - env_started) * 1000.0)
    else:
        future_to_env: Dict[Any, Dict[str, Any]] = {}
        env_started_at: Dict[str, float] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for env in envs:
                if time.time() > deadline_ts:
                    break
                env_key = _env_key_from_listing(env)
                add_event('code_env_detail_start', 'loading code env detail', 'info', env_key)
                env_started_at[env_key] = time.time()
                future = pool.submit(_load_code_env_full_details, env, project_info, size_by_env, True, usages_by_env, user_email_by_login)
                future_to_env[future] = env

            timed_out_futures = False
            remaining = max(0.0, deadline_ts - time.time())
            try:
                for future in as_completed(list(future_to_env.keys()), timeout=remaining):
                    if time.time() > deadline_ts:
                        timed_out_futures = True
                        break
                    env = future_to_env.get(future) or {}
                    env_key = _env_key_from_listing(env)
                    started_at = env_started_at.get(env_key, time.time())
                    try:
                        detail = future.result()
                    except Exception as exc:
                        add_event('code_env_detail_error', f"code env detail failed: {exc}", 'warn', env_key, (time.time() - started_at) * 1000.0)
                        continue
                    if detail:
                        env_details.append(detail)
                        row = detail.get('row')
                        if isinstance(row, dict):
                            append_partial_row(row)
                        add_event('code_env_detail_ok', 'code env detail loaded', 'info', env_key, (time.time() - started_at) * 1000.0)
                    else:
                        add_event('code_env_detail_error', 'code env detail missing', 'warn', env_key, (time.time() - started_at) * 1000.0)
            except FuturesTimeoutError:
                timed_out_futures = True
                add_event('load_code_env_details', 'timeout while waiting for env detail futures', 'warn')

            if timed_out_futures or time.time() > deadline_ts:
                for future, env in future_to_env.items():
                    if future.done():
                        continue
                    future.cancel()
                    env_key = _env_key_from_listing(env)
                    started_at = env_started_at.get(env_key, time.time())
                    add_event('code_env_detail_timeout', 'cancelled env detail future on deadline', 'warn', env_key, (time.time() - started_at) * 1000.0)
    return env_details


@app.route('/api/code-envs')
def api_code_envs():
    client = g.client

    def loader():
        timeout_ms = _BACKEND_SETTINGS['code_env_timeout_ms']
        started = time.time()
        deadline = started + (timeout_ms / 1000.0)
        project_limit = 0
        project_selection = 'all_by_project_key'
        limit_label = 'all' if project_limit <= 0 else str(project_limit)
        code_envs = []
        python_counts: Dict[str, int] = {}
        r_counts: Dict[str, int] = {}
        steps: List[Dict[str, Any]] = []
        op_stats: Dict[str, Dict[str, Any]] = {}
        events: List[Dict[str, Any]] = []
        timed_out = False
        timeout_at_step: Optional[str] = None
        deadline_pressure_steps: set = set()
        timeout_event_steps: set = set()
        timed_out_or_error = False
        progress_run_id = _start_progress('code_envs')
        catalog: Optional[Dict[str, Any]] = None
        progress_meta: Dict[str, Any] = {
            'selectedProjects': 0,
            'projectUsageDone': 0,
            'envDetailsTotal': 0,
            'envDetailsDone': 0,
            'catalogDone': False,
            'sizeMapDone': False,
        }

        def elapsed_ms() -> float:
            return (time.time() - started) * 1000.0

        def remaining_ms() -> int:
            return max(0, int((deadline - time.time()) * 1000.0))

        def remaining_seconds() -> float:
            return max(0.0, deadline - time.time())

        def _compute_progress_pct(force_done: bool = False) -> int:
            if force_done:
                return 100
            usage_total = max(0, int(progress_meta['selectedProjects']))
            usage_ratio = min(1.0, float(progress_meta['projectUsageDone']) / float(usage_total)) if usage_total > 0 else 1.0
            detail_total = max(0, int(progress_meta['envDetailsTotal']))
            detail_ratio = min(1.0, float(progress_meta['envDetailsDone']) / float(detail_total)) if detail_total > 0 else 0.0
            pct = 0.0
            pct += 10.0 if progress_meta['catalogDone'] else 0.0
            pct += 15.0 if progress_meta['sizeMapDone'] else 0.0
            pct += 50.0 * usage_ratio
            pct += 25.0 * detail_ratio
            if timed_out_or_error:
                return int(max(0.0, min(100.0, pct)))
            return int(max(0.0, min(99.0, pct)))

        def _infer_phase() -> str:
            if not progress_meta['catalogDone']:
                return 'catalog'
            if not progress_meta['sizeMapDone']:
                return 'size_map'
            if progress_meta['selectedProjects'] > 0 and progress_meta['projectUsageDone'] < progress_meta['selectedProjects']:
                return 'usage_scan'
            if progress_meta['envDetailsTotal'] > 0 and progress_meta['envDetailsDone'] < progress_meta['envDetailsTotal']:
                return 'env_details'
            return 'finalizing'

        def _update_progress_summary(force_done: bool = False) -> None:
            _set_progress_summary(
                'code_envs',
                progress_run_id,
                {
                    'progressPct': _compute_progress_pct(force_done),
                    'phase': _infer_phase() if not force_done else 'done',
                    'selectedProjects': int(progress_meta['selectedProjects']),
                    'projectUsageDone': int(progress_meta['projectUsageDone']),
                    'envDetailsTotal': int(progress_meta['envDetailsTotal']),
                    'envDetailsDone': int(progress_meta['envDetailsDone']),
                    'timedOut': bool(timed_out),
                    'timeoutAtStep': timeout_at_step,
                    'totalElapsedMs': round(elapsed_ms(), 2),
                    'remainingMs': remaining_ms(),
                },
            )

        def add_event(
            step: str,
            message: str,
            level: str = 'info',
            project_key: Optional[str] = None,
            event_elapsed_ms: Optional[float] = None,
        ) -> None:
            event: Dict[str, Any] = {
                'tMs': round(elapsed_ms(), 2),
                'level': level,
                'step': step,
                'message': message,
            }
            if project_key:
                event['projectKey'] = project_key
            if event_elapsed_ms is not None:
                event['elapsedMs'] = round(max(0.0, float(event_elapsed_ms)), 2)
            events.append(event)
            _append_progress_event('code_envs', progress_run_id, event)
            if step == 'project_env_refs_resolved' and project_key:
                progress_meta['projectUsageDone'] += 1
            if step in ('code_env_detail_ok', 'code_env_detail_error', 'code_env_detail_timeout'):
                progress_meta['envDetailsDone'] += 1
            _update_progress_summary(False)

        def progress_event(**kwargs) -> None:
            add_event(
                step=str(kwargs.get('step') or 'event'),
                message=str(kwargs.get('message') or ''),
                level=str(kwargs.get('level') or 'info'),
                project_key=kwargs.get('project_key'),
                event_elapsed_ms=kwargs.get('elapsed_ms'),
            )

        def deadline_reached(step_name: str) -> bool:
            nonlocal timed_out, timeout_at_step, timed_out_or_error
            now = time.time()
            if now < deadline:
                if step_name not in deadline_pressure_steps and (deadline - now) <= 10.0:
                    deadline_pressure_steps.add(step_name)
                    add_event(step_name, f"deadline pressure: only {remaining_ms()}ms remaining", 'warn')
                return False
            timed_out = True
            timed_out_or_error = True
            if timeout_at_step is None:
                timeout_at_step = step_name
            if step_name not in timeout_event_steps:
                timeout_event_steps.add(step_name)
                add_event(step_name, f"deadline reached at step={step_name}", 'warn')
            return True

        def record_step(name: str, step_start: float, calls: int = 0) -> None:
            elapsed = max(0.0, (time.time() - step_start) * 1000.0)
            avg_ms = (elapsed / calls) if calls > 0 else 0.0
            qps = (calls / (elapsed / 1000.0)) if calls > 0 and elapsed > 0 else 0.0
            steps.append({
                'name': name,
                'calls': int(calls),
                'elapsedMs': round(elapsed, 2),
                'avgMs': round(avg_ms, 2),
                'qps': round(qps, 2),
            })
            add_event(name, f"{name} done calls={calls}", 'info', event_elapsed_ms=elapsed)

        def record_op(name: str, elapsed_ms_value: float, calls: int = 1) -> None:
            entry = op_stats.setdefault(name, {'operation': name, 'calls': 0, 'elapsedMs': 0.0})
            entry['calls'] = int(entry.get('calls') or 0) + int(max(0, calls))
            entry['elapsedMs'] = float(entry.get('elapsedMs') or 0.0) + max(0.0, float(elapsed_ms_value))

        previous_recorder = getattr(_THREAD_LOCAL, 'bench_record_op', None)
        setattr(_THREAD_LOCAL, 'bench_record_op', record_op)
        add_event('code_envs_start', f"code env analysis started timeoutMs={timeout_ms} limit={limit_label}")

        try:
            # User-email lookup for ownerEmail enrichment.
            users = _sdk_fetch(
                'list_users',
                _BACKEND_SETTINGS['cache_ttl_users'],
                lambda: client.list_users() if hasattr(client, 'list_users') else [],
            ) or []
            user_email_by_login: Dict[str, str] = {}
            for user in users:
                if isinstance(user, dict) and user.get('login'):
                    user_email_by_login[str(user.get('login'))] = str(user.get('email') or user.get('login'))

            # Phase 1: catalog
            step_started = time.time()
            catalog = _task_ce_catalog(client, add_event, limit_label, project_limit)
            record_step('load_project_catalog', step_started, calls=catalog['selected_count'])
            progress_meta['selectedProjects'] = catalog['selected_count']
            progress_meta['catalogDone'] = True
            _update_progress_summary(False)
            app.logger.info(
                "[code-envs] projectInfo selected=%s total=%s limit=%s elapsed=%.2fs",
                catalog['selected_count'],
                len(catalog['project_catalog']),
                limit_label,
                time.time() - started,
            )
            app.logger.info("[perf:ce] phase1_catalog elapsed=%.0fms projects=%d", elapsed_ms(), catalog['selected_count'])

            # Phase 2: usage_scan and size_map deferred.
            # Per-env usages come from list_code_env_usages() bulk call below; per-project
            # walk is only needed by /api/project-footprint.
            size_by_env: Dict[str, int] = {}
            progress_meta['sizeMapDone'] = True
            _update_progress_summary(False)
            app.logger.info("[perf:ce] usage+size deferred, elapsed=%.0fms", elapsed_ms())

            envs: List[Dict[str, Any]] = []
            if not deadline_reached('list_code_envs'):
                step_started = time.time()
                add_event('list_code_envs', 'listing code envs')
                envs = [env for env in (_sdk_fetch(
                    'list_code_envs',
                    _BACKEND_SETTINGS['cache_ttl_code_envs'],
                    lambda: client.list_code_envs() or [],
                ) or []) if isinstance(env, dict)]
                record_step('list_code_envs', step_started, calls=1)
                app.logger.info("[perf:ce] list_code_envs elapsed=%.0fms count=%d", elapsed_ms(), len(envs))

            _SKIP_DEPLOYMENT_MODES = {'PLUGIN_MANAGED', 'DSS_INTERNAL'}
            total_env_count = len(envs)
            skipped_env_count = 0
            if not deadline_reached('filter_selected_envs'):
                step_started = time.time()
                before_count = len(envs)
                envs = [
                    env for env in envs
                    if str(env.get('deploymentMode') or '').upper() not in _SKIP_DEPLOYMENT_MODES
                ]
                skipped_env_count = before_count - len(envs)
                add_event('filter_selected_envs', f"filtered out {skipped_env_count} plugin-managed/internal envs, keeping {len(envs)}/{before_count}")
                record_step('filter_selected_envs', step_started, calls=len(envs))
            app.logger.info("[code-envs] listed=%s", len(envs))

            env_details: List[Dict[str, Any]] = []
            if envs and not deadline_reached('load_code_env_details'):
                step_started = time.time()
                bulk_usages_raw = _sdk_fetch(
                    'list_code_env_usages',
                    _BACKEND_SETTINGS['cache_ttl_code_envs'],
                    lambda: client.list_code_env_usages() or [],
                )
                usages_by_env_details: Dict[Tuple[str, str], List[Dict]] = {}
                for _u in bulk_usages_raw:
                    _k = (str(_u.get('envLang', '')).upper(), str(_u.get('envName', '')))
                    usages_by_env_details.setdefault(_k, []).append(_u)
                app.logger.info("[perf:ce] list_code_env_usages bulk elapsed=%.0fms count=%d", elapsed_ms(), len(bulk_usages_raw))
                env_details = _task_ce_env_details(
                    client,
                    envs,
                    catalog['project_info'],
                    size_by_env,
                    progress_meta,
                    deadline,
                    add_event,
                    lambda row: _append_progress_partial_row('code_envs', progress_run_id, row),
                    usages_by_env=usages_by_env_details,
                    user_email_by_login=user_email_by_login,
                )
                record_step('load_code_env_details', step_started, calls=progress_meta['envDetailsDone'])
                app.logger.info("[perf:ce] env_details elapsed=%.0fms envs=%d workers=%d", elapsed_ms(), len(env_details), min(_parallel_workers(_BACKEND_SETTINGS['code_env_detail_workers']), max(1, len(envs))))
            app.logger.info("[code-envs] details=%s elapsed=%.2fs", len(env_details), time.time() - started)

            # Phase 4: aggregate rows
            if env_details and not deadline_reached('aggregate_code_env_rows'):
                step_started = time.time()
                add_event('aggregate_code_env_rows', f"aggregating rows count={len(env_details)}")
                processed = 0
                for detail in env_details:
                    row = detail.get('row')
                    if not isinstance(row, dict):
                        continue
                    code_envs.append(row)
                    language = str(detail.get('language') or 'python')
                    version_label = str(detail.get('versionLabel') or row.get('version') or 'Unknown')
                    if language == 'r':
                        r_counts[version_label] = r_counts.get(version_label, 0) + 1
                    else:
                        python_counts[version_label] = python_counts.get(version_label, 0) + 1
                    processed += 1
                record_step('aggregate_code_env_rows', step_started, calls=processed)

            code_envs.sort(key=lambda item: (_coerce_int(item.get('sizeBytes'), 0), str(item.get('name') or '')), reverse=True)
            app.logger.info("[code-envs] done rows=%s elapsed=%.2fs", len(code_envs), time.time() - started)
            app.logger.info("[perf:ce] total elapsed=%.0fms", elapsed_ms())
            add_event('code_envs_done', f"code envs done rows={len(code_envs)} timedOut={timed_out}")

            api_calls = []
            for entry in sorted(op_stats.values(), key=lambda item: float(item.get('elapsedMs') or 0.0), reverse=True):
                calls = int(entry.get('calls') or 0)
                elapsed = float(entry.get('elapsedMs') or 0.0)
                avg_ms = (elapsed / calls) if calls > 0 else 0.0
                qps = (calls / (elapsed / 1000.0)) if calls > 0 and elapsed > 0 else 0.0
                api_calls.append({
                    'operation': entry.get('operation'),
                    'calls': calls,
                    'elapsedMs': round(elapsed, 2),
                    'avgMs': round(avg_ms, 2),
                    'qps': round(qps, 2),
                })

            selected_count = len(catalog['project_info']) if catalog is not None else 0
            benchmark_summary = {
                'enabled': True,
                'projectLimit': selected_count,
                'projectSelection': project_selection,
                'timeoutMs': timeout_ms,
                'timedOut': bool(timed_out),
                'timeoutAtStep': timeout_at_step,
                'totalElapsedMs': round(elapsed_ms(), 2),
                'remainingMs': remaining_ms(),
                'selectedProjectCount': selected_count,
                'selectedEnvKeyCount': 0,
                'steps': steps,
                'apiCalls': api_calls,
                'events': events,
            }
            summary = {
                'benchmark': {
                    **benchmark_summary,
                },
            }
            _update_progress_summary(True)
            _finish_progress('code_envs', progress_run_id, status='done', summary=benchmark_summary)

            return {
                'codeEnvs': code_envs,
                'pythonVersionCounts': python_counts,
                'rVersionCounts': r_counts,
                'totalEnvCount': total_env_count,
                'skippedEnvCount': skipped_env_count,
                'summary': summary,
            }
        except Exception as exc:
            timed_out_or_error = True
            add_event('code_envs_error', f"code env analysis failed: {exc}", 'error')
            _update_progress_summary(False)
            _finish_progress(
                'code_envs',
                progress_run_id,
                status='error',
                summary={
                    'enabled': True,
                    'projectLimit': progress_meta['selectedProjects'],
                    'projectSelection': project_selection,
                    'timeoutMs': timeout_ms,
                    'timedOut': bool(timed_out),
                    'timeoutAtStep': timeout_at_step,
                    'totalElapsedMs': round(elapsed_ms(), 2),
                    'remainingMs': remaining_ms(),
                    'steps': steps,
                    'apiCalls': api_calls if 'api_calls' in locals() else [],
                    'events': events,
                },
                error=str(exc),
            )
            raise
        finally:
            setattr(_THREAD_LOCAL, 'bench_record_op', previous_recorder)

    data = _cache_get('code_envs', _BACKEND_SETTINGS['cache_ttl_code_envs'], loader)
    return jsonify(data)


@app.route('/api/code-envs/sizes')
def api_code_envs_sizes():
    """Lazy-load code env sizes via global footprint. Cached for 300s."""
    def loader():
        if not _footprint_available():
            return {}
        client = g.client
        return _get_code_env_size_map(client)
    size_map = _cache_get('code_envs_sizes', _BACKEND_SETTINGS['cache_ttl_projects'], loader)
    available = _footprint_available() and bool(size_map)
    reason = _footprint_unavailable_reason() if not _footprint_available() else None
    return jsonify({
        'sizes': size_map,
        'available': available,
        'reason': reason,
    })


@app.route('/api/code-envs/progress')
def api_code_envs_progress():
    since_raw = request.args.get('since', '0')
    run_id = request.args.get('runId')
    rows_since_raw = request.args.get('rowsSince', '0')
    try:
        since = max(0, int(str(since_raw or '0')))
    except Exception:
        since = 0
    try:
        rows_since = max(0, int(str(rows_since_raw or '0')))
    except Exception:
        rows_since = 0
    payload = _read_progress('code_envs', since=since, run_id=run_id, rows_since=rows_since)
    return jsonify(payload)


@app.route('/api/code-envs-progress')
def api_code_envs_progress_alias():
    return api_code_envs_progress()


# ── Code env replacement helpers ─────────────────────────────────────────────

_CER_SURFACE_TYPES = {'PROJECT', 'RECIPE', 'NOTEBOOK', 'WEBAPP', 'SCENARIO'}


def _cer_path_get(raw: Any, path: str) -> Any:
    current = raw
    for part in path.split('.'):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _cer_path_set(raw: Dict[str, Any], path: str, value: Any) -> None:
    current = raw
    parts = path.split('.')
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _cer_env_selection(env_name: str) -> Dict[str, Any]:
    return {'envMode': 'EXPLICIT_ENV', 'envName': env_name}


def _cer_selection_env_name(selection: Any) -> Optional[str]:
    if not isinstance(selection, dict):
        return None
    for key in ('envName', 'codeEnvName', 'name'):
        value = selection.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _cer_kernel_spec_name(env: Dict[str, Any], detail: Optional[Dict[str, Any]] = None) -> Optional[str]:
    for payload in (env, detail or {}):
        if not isinstance(payload, dict):
            continue
        value = _extract_nested_text(
            payload,
            'kernelSpecName',
            'desc.kernelSpecName',
            'settings.kernelSpecName',
            'spec.kernelSpecName',
            'jupyterKernelSpecName',
            'desc.jupyterKernelSpecName',
        )
        if value:
            return value
    return None


def _cer_env_catalog(client: Any) -> Dict[Tuple[str, str], Dict[str, Any]]:
    catalog: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for env in client.list_code_envs() or []:
        if not isinstance(env, dict):
            continue
        name = str(env.get('envName') or env.get('name') or env.get('id') or '').strip()
        if not name:
            continue
        language = _normalize_language(env.get('envLang') or env.get('language') or env.get('type'))
        catalog[(language, name)] = env
    return catalog


def _cer_fetch_env_detail(client: Any, language: str, env_name: str) -> Dict[str, Any]:
    try:
        detail = client._perform_json('GET', f"/admin/code-envs/{language.upper()}/{env_name}")
        return detail if isinstance(detail, dict) else {}
    except Exception:
        return {}


def _cer_object_type(raw_type: Any, usage_type: Any = None) -> str:
    text = str(raw_type or usage_type or '').strip().upper().replace('-', '_')
    if text in _CER_SURFACE_TYPES:
        return text
    if 'PROJECT' in text:
        return 'PROJECT'
    if 'RECIPE' in text:
        return 'RECIPE'
    if 'NOTEBOOK' in text or 'JUPYTER' in text:
        return 'NOTEBOOK'
    if 'WEBAPP' in text or 'WEB_APP' in text:
        return 'WEBAPP'
    if 'SCENARIO' in text:
        return 'SCENARIO'
    return text or 'UNKNOWN'


def _cer_build_usage_rows(
    client: Any,
    source_env_name: str,
    source_language: str,
    project_filter: Optional[set] = None,
    type_filter: Optional[set] = None,
) -> List[Dict[str, Any]]:
    project_info = _build_project_info(client, 0, include_settings=False)
    rows: List[Dict[str, Any]] = []
    for raw_usage in client.list_code_env_usages() or []:
        if not isinstance(raw_usage, dict):
            raw_usage = _usage_to_dict(raw_usage)
        env_name = str(raw_usage.get('envName') or raw_usage.get('codeEnvName') or '').strip()
        env_lang = _normalize_language(raw_usage.get('envLang') or raw_usage.get('codeEnvLanguage') or source_language)
        if env_name != source_env_name or env_lang != source_language:
            continue
        normalized = _normalize_usage_entry(raw_usage, project_info)
        project_key = str(normalized.get('projectKey') or '')
        if project_filter and project_key not in project_filter:
            continue
        object_type = _cer_object_type(normalized.get('objectType'), normalized.get('usageType'))
        if type_filter and object_type not in type_filter:
            continue
        object_id = str(normalized.get('objectId') or '')
        if object_type == 'PROJECT' and not object_id:
            object_id = project_key
        rows.append({
            'id': '|'.join([project_key, object_type, object_id, source_language, source_env_name]),
            'projectKey': project_key,
            'projectName': normalized.get('projectName') or project_key,
            'objectType': object_type,
            'objectId': object_id,
            'objectName': normalized.get('objectName') or object_id or project_key,
            'sourceLanguage': source_language,
            'sourceEnvName': source_env_name,
        })
    return _dedupe_usage_entries(rows)


def _cer_replace_project_default(client: Any, row: Dict[str, Any], source_env_name: str, target_env_name: str, language: str) -> Tuple[str, Optional[str]]:
    settings = client.get_project(row['projectKey']).get_settings()
    raw = settings.get_raw()
    lang_key = 'r' if language == 'r' else 'python'
    path = f'settings.codeEnvs.{lang_key}'
    selection = _cer_path_get(raw, path)
    if selection is None:
        path = f'codeEnvs.{lang_key}'
        selection = _cer_path_get(raw, path)
    current = _cer_selection_env_name(selection)
    if current != source_env_name:
        return ('skipped', f'Current project default is {current or "unset"}')
    next_selection = dict(selection) if isinstance(selection, dict) else {}
    next_selection['envName'] = target_env_name
    if not next_selection.get('envMode'):
        next_selection['envMode'] = 'EXPLICIT_ENV'
    _cer_path_set(raw, path, next_selection)
    settings.save()
    return ('updated', None)


def _cer_recipe_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    recipe = raw.get('recipe')
    return recipe if isinstance(recipe, dict) else raw


def _cer_replace_env_selection_payload(raw: Dict[str, Any], source_env_name: str, target_env_name: str, path: str = 'params.envSelection') -> Tuple[bool, Optional[str]]:
    selection = _cer_path_get(raw, path)
    current = _cer_selection_env_name(selection)
    if current != source_env_name:
        return (False, f'Current env is {current or "unset"}')
    _cer_path_set(raw, path, _cer_env_selection(target_env_name))
    return (True, None)


def _cer_replace_recipe(client: Any, row: Dict[str, Any], source_env_name: str, target_env_name: str) -> Tuple[str, Optional[str]]:
    project_key = row['projectKey']
    recipe_id = row['objectId']
    raw = client._perform_json('GET', f'/projects/{project_key}/recipes/{recipe_id}')
    payload = _cer_recipe_payload(raw if isinstance(raw, dict) else {})
    ok, reason = _cer_replace_env_selection_payload(payload, source_env_name, target_env_name)
    if not ok:
        return ('skipped', reason)
    client._perform_json('PUT', f'/projects/{project_key}/recipes/{recipe_id}', body=raw)
    return ('updated', None)


def _cer_replace_webapp(client: Any, row: Dict[str, Any], source_env_name: str, target_env_name: str) -> Tuple[str, Optional[str]]:
    project_key = row['projectKey']
    webapp_id = row['objectId']
    raw = client._perform_json('GET', f'/projects/{project_key}/webapps/{webapp_id}')
    ok, reason = _cer_replace_env_selection_payload(raw if isinstance(raw, dict) else {}, source_env_name, target_env_name)
    if not ok:
        return ('skipped', reason)
    client._perform_empty('PUT', f'/projects/{project_key}/webapps/{webapp_id}', body=raw)
    return ('updated', None)


def _cer_replace_scenario(client: Any, row: Dict[str, Any], source_env_name: str, target_env_name: str) -> Tuple[str, Optional[str]]:
    project_key = row['projectKey']
    scenario_id = row['objectId']
    raw = client._perform_json('GET', f'/projects/{project_key}/scenarios/{scenario_id}')
    ok, reason = _cer_replace_env_selection_payload(raw if isinstance(raw, dict) else {}, source_env_name, target_env_name)
    if not ok:
        return ('skipped', reason)
    client._perform_empty('PUT', f'/projects/{project_key}/scenarios/{scenario_id}', body=raw)
    return ('updated', None)


def _cer_fetch_notebook_content(client: Any, project_key: str, notebook_id: str) -> Tuple[Dict[str, Any], Callable[[Dict[str, Any]], None]]:
    try:
        notebook = client.get_project(project_key).get_jupyter_notebook(notebook_id)
        content = notebook.get_content()
        raw = content.get_raw()
        if isinstance(raw, dict):
            def save_sdk(next_raw: Dict[str, Any]) -> None:
                if hasattr(content, 'set_raw'):
                    content.set_raw(next_raw)
                elif hasattr(content, '_data'):
                    content._data = next_raw
                if hasattr(content, 'save'):
                    content.save()
                elif hasattr(notebook, 'set_content'):
                    notebook.set_content(next_raw)
                else:
                    raise ValueError('Notebook content object does not support save')
            return raw, save_sdk
    except Exception:
        pass

    path = f'/projects/{project_key}/jupyter-notebooks/{notebook_id}/content'
    raw = client._perform_json('GET', path)
    if not isinstance(raw, dict):
        raw = {}

    def save_rest(next_raw: Dict[str, Any]) -> None:
        client._perform_json('PUT', path, body=next_raw)

    return raw, save_rest


def _cer_replace_notebook(
    client: Any,
    row: Dict[str, Any],
    source_env_name: str,
    target_kernel_spec: str,
    source_kernel_spec: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    project_key = row['projectKey']
    notebook_id = row['objectId']
    raw, save = _cer_fetch_notebook_content(client, project_key, notebook_id)
    kernelspec = _cer_path_get(raw, 'metadata.kernelspec')
    current = kernelspec.get('name') if isinstance(kernelspec, dict) else None
    accepted_sources = {source_env_name}
    if source_kernel_spec:
        accepted_sources.add(source_kernel_spec)
    if current not in accepted_sources:
        return ('skipped', f'Current notebook kernel is {current or "unset"}')
    next_kernel = dict(kernelspec) if isinstance(kernelspec, dict) else {}
    next_kernel['name'] = target_kernel_spec
    if next_kernel.get('display_name') in accepted_sources or not next_kernel.get('display_name'):
        next_kernel['display_name'] = target_kernel_spec
    _cer_path_set(raw, 'metadata.kernelspec', next_kernel)
    save(raw)
    return ('updated', None)


def _cer_apply_replace_row(
    client: Any,
    row: Dict[str, Any],
    source_env_name: str,
    target_env_name: str,
    source_language: str,
    target_kernel_spec: Optional[str],
    source_kernel_spec: Optional[str],
) -> Tuple[str, Optional[str]]:
    object_type = str(row.get('objectType') or '').upper()
    if object_type == 'PROJECT':
        return _cer_replace_project_default(client, row, source_env_name, target_env_name, source_language)
    if object_type == 'RECIPE':
        return _cer_replace_recipe(client, row, source_env_name, target_env_name)
    if object_type == 'WEBAPP':
        return _cer_replace_webapp(client, row, source_env_name, target_env_name)
    if object_type == 'SCENARIO':
        return _cer_replace_scenario(client, row, source_env_name, target_env_name)
    if object_type == 'NOTEBOOK':
        if not target_kernel_spec:
            return ('failed', 'Target code env does not expose kernelSpecName')
        return _cer_replace_notebook(client, row, source_env_name, target_kernel_spec, source_kernel_spec)
    return ('failed', f'Unsupported replacement surface: {object_type}')


def _cer_clear_replacement_caches() -> None:
    _cache_pop_matching(lambda key_text: (
        key_text in {'code_envs', 'code_envs_sizes', 'outreach', 'project_code_env_usage_full'}
        or key_text.startswith('outreach')
        or key_text.startswith('project_footprint')
    ))
    _clear_shared_project_code_env_usage()
    _bump_session_epoch()


@app.route('/api/code-envs/replace', methods=['POST'])
@advanced
def api_code_envs_replace():
    payload = request.get_json(silent=True) or {}
    source_env_name = str(payload.get('sourceEnvName') or '').strip()
    source_language = _normalize_language(payload.get('sourceLanguage') or 'python')
    target_env_name = str(payload.get('targetEnvName') or '').strip()
    dry_run = bool(payload.get('dryRun', True))
    if not source_env_name or not target_env_name:
        return jsonify({'error': 'sourceEnvName and targetEnvName are required'}), 400
    if source_env_name == target_env_name:
        return jsonify({'error': 'sourceEnvName and targetEnvName must differ'}), 400

    project_keys = payload.get('projectKeys')
    usage_types = payload.get('usageTypes')
    project_filter = {str(pk).strip() for pk in project_keys if str(pk).strip()} if isinstance(project_keys, list) else None
    type_filter = {_cer_object_type(t) for t in usage_types if str(t).strip()} if isinstance(usage_types, list) else None

    client = g.client
    catalog = _cer_env_catalog(client)
    target_env = catalog.get((source_language, target_env_name))
    if target_env is None:
        same_name = [(lang, name) for (lang, name) in catalog.keys() if name == target_env_name]
        if same_name:
            return jsonify({
                'error': f'Target code env language does not match sourceLanguage: {target_env_name}',
                'validTargetEnvNames': sorted(name for (lang, name) in catalog.keys() if lang == source_language),
            }), 400
        return jsonify({
            'error': f'Unknown targetEnvName: {target_env_name}',
            'validTargetEnvNames': sorted(name for (lang, name) in catalog.keys() if lang == source_language),
        }), 400

    source_env = catalog.get((source_language, source_env_name), {})
    target_detail = _cer_fetch_env_detail(client, source_language, target_env_name)
    source_detail = _cer_fetch_env_detail(client, source_language, source_env_name) if source_env else {}
    target_kernel_spec = _cer_kernel_spec_name(target_env, target_detail)
    source_kernel_spec = _cer_kernel_spec_name(source_env, source_detail) if source_env else None

    matched = _cer_build_usage_rows(
        client,
        source_env_name,
        source_language,
        project_filter=project_filter,
        type_filter=type_filter,
    )

    results: List[Dict[str, Any]] = []
    for row in matched:
        result = {
            'rowId': row.get('id'),
            'projectKey': row.get('projectKey'),
            'objectType': row.get('objectType'),
            'objectId': row.get('objectId'),
            'objectName': row.get('objectName'),
            'from': source_env_name,
            'to': target_env_name,
            'status': 'planned' if dry_run else 'updated',
        }
        if not dry_run:
            try:
                status, message = _cer_apply_replace_row(
                    client,
                    row,
                    source_env_name,
                    target_env_name,
                    source_language,
                    target_kernel_spec,
                    source_kernel_spec,
                )
                result['status'] = status
                if message:
                    result['error'] = message
            except Exception as exc:
                result['status'] = 'failed'
                result['error'] = str(exc)[:500]
        results.append(result)

    if not dry_run and any(r.get('status') == 'updated' for r in results):
        _cer_clear_replacement_caches()

    return jsonify({
        'dryRun': dry_run,
        'sourceEnvName': source_env_name,
        'sourceLanguage': source_language,
        'targetEnvName': target_env_name,
        'matchedRows': len(matched),
        'updatedRows': len([r for r in results if r.get('status') == 'updated']),
        'skippedRows': len([r for r in results if r.get('status') == 'skipped']),
        'failedRows': len([r for r in results if r.get('status') == 'failed']),
        'results': results,
    })


# ── Algorithm review: ship adk_notebook libs + scan notebooks into ADMINTOOLKIT ──
#
# Materializes a human-reviewable copy of the webapp's Dataiku-API logic inside the
# ADMINTOOLKIT project: writes the importable shared libraries into the project's
# Python library and creates one Jupyter notebook per scan card (verbatim source).
# Pure DSS-API writes → stays on g.client, no macro. API shapes verified live.

def _adk_review_plugin_root() -> str:
    """Plugin root dir, anchored on the imported adk_notebook package.

    adk_notebook lives at <root>/python-lib/adk_notebook/__init__.py, so two
    parents up from the package dir is the plugin root (where notebook-cards/ sits).
    """
    import adk_notebook
    pkg_dir = os.path.dirname(os.path.abspath(adk_notebook.__file__))
    python_lib_dir = os.path.dirname(pkg_dir)
    return os.path.dirname(python_lib_dir)


def _adk_review_lib_sources() -> Dict[str, str]:
    """{path-under-lib/python: source_text} for the first-party closure the cards
    import: the whole adk_notebook package plus llm_audit (reached via
    data.llm_audit_report → ``import llm_audit``)."""
    import adk_notebook
    import llm_audit
    out: Dict[str, str] = {}
    pkg_dir = os.path.dirname(os.path.abspath(adk_notebook.__file__))
    for fname in sorted(os.listdir(pkg_dir)):
        if fname.endswith('.py'):
            with open(os.path.join(pkg_dir, fname), 'r', encoding='utf-8') as fh:
                out['adk_notebook/' + fname] = fh.read()
    with open(os.path.abspath(llm_audit.__file__), 'r', encoding='utf-8') as fh:
        out['llm_audit.py'] = fh.read()
    return out


def _adk_review_card_sources() -> Dict[str, Tuple[str, str]]:
    """{notebook_name: (card_filename, source_text)} for the bundled scan cards.
    Notebook name = card filename stem (e.g. ai-compute__model-audit__llm-audit-table)."""
    cards_dir = os.path.join(_adk_review_plugin_root(), 'notebook-cards')
    out: Dict[str, Tuple[str, str]] = {}
    if not os.path.isdir(cards_dir):
        return out
    for fname in sorted(os.listdir(cards_dir)):
        if fname.endswith('.py') and '__' in fname:
            with open(os.path.join(cards_dir, fname), 'r', encoding='utf-8') as fh:
                out[fname[:-3]] = (fname, fh.read())
    return out


def _adk_review_resolve_kernel(client: Any) -> Tuple[str, bool, List[str]]:
    """Resolve the Jupyter kernel for the review notebooks.

    The webapp uses a contextual code env; in practice that resolves to the instance
    default (no plugin-managed Jupyter env exists — plugin envs have no kernel spec),
    whose builtin kernel is ``python3``. If ADMINTOOLKIT pins a python env that exposes
    a kernelSpecName, use it; otherwise fall back to python3 and warn."""
    warnings: List[str] = []
    try:
        raw = client.get_project(MACRO_PROJECT_KEY).get_settings().get_raw()
        env_cfg = (((raw.get('settings') or {}).get('codeEnvs') or {}).get('python') or {})
        if str(env_cfg.get('mode') or '').upper() not in ('', 'INHERIT', 'USE_BUILTIN_MODE'):
            env_name = str(env_cfg.get('envName') or '').strip()
            if env_name:
                catalog = _cer_env_catalog(client)
                env = catalog.get(('PYTHON', env_name)) or {}
                detail = _cer_fetch_env_detail(client, 'PYTHON', env_name)
                kernel = _cer_kernel_spec_name(env, detail)
                if kernel:
                    return kernel, False, warnings
    except Exception:
        pass
    warnings.append(
        "Notebooks use the builtin 'python3' kernel (the webapp inherits the instance "
        "default code env). Ensure that env has the 'rich' and 'python-dateutil' packages "
        "installed so the cards can run."
    )
    return 'python3', True, warnings


def _adk_review_card_title(source_text: str, fallback: str) -> str:
    """First non-empty line of the card's leading docstring (its display title)."""
    match = re.search(r'"""(.*?)"""', source_text, re.S)
    if match:
        for line in match.group(1).strip().splitlines():
            if line.strip():
                return line.strip()
    return fallback


def _adk_review_build_nbformat(card_filename: str, source_text: str, kernel_name: str) -> Dict[str, Any]:
    """nbformat-v4 notebook: markdown header + one code cell with verbatim card source."""
    title = _adk_review_card_title(source_text, card_filename)
    markdown = [
        "### %s\n" % title,
        "\n",
        "_Verbatim review copy of `notebook-cards/%s`._\n" % card_filename,
        "\n",
        "Imports the shared logic from the `adk_notebook` project library; "
        "run the cell below to reproduce the matching webapp card.",
    ]
    display = 'Python 3' if kernel_name == 'python3' else kernel_name
    return {
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": markdown},
            {"cell_type": "code", "metadata": {}, "execution_count": None,
             "outputs": [], "source": source_text.splitlines(keepends=True)},
        ],
        "metadata": {
            "kernelspec": {"name": kernel_name, "display_name": display, "language": "python"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def _adk_review_ensure_folder(parent: Any, name: str) -> Any:
    """get-or-add a child library folder (add_folder raises if it already exists)."""
    try:
        return parent.add_folder(name)
    except Exception:
        return parent.get_folder(name)


def _adk_review_write_library_file(lib: Any, rel_under_python: str, text: str) -> None:
    """Write text to lib/python/<rel_under_python>, creating folders as needed.
    Overwriting a fixed path is idempotent (verified: re-runs update, never duplicate)."""
    segments = rel_under_python.split('/')
    folder = _adk_review_ensure_folder(lib, 'python')
    for seg in segments[:-1]:
        folder = _adk_review_ensure_folder(folder, seg)
    try:
        lib_file = folder.add_file(segments[-1])
    except Exception:
        lib_file = folder.get_file(segments[-1])
    # Encode to UTF-8 bytes: passing str makes the SDK send a Latin-1 body, which
    # blows up on the em-dashes / "›" in the source files (verified live).
    lib_file.write(text.encode('utf-8'))


def _adk_review_upsert_notebook(project: Any, name: str, content: Dict[str, Any],
                                existing_names: set) -> str:
    """Create the notebook, or replace it if it already exists.

    create_jupyter_notebook raises on a duplicate name, and DSSNotebookContent in
    some DSS versions exposes no content-setter (only get_raw/save), so re-create via
    delete+create — verified idempotent (re-runs update content, never duplicate)."""
    if name in existing_names:
        try:
            project.get_jupyter_notebook(name).delete()
        except Exception:
            pass
        project.create_jupyter_notebook(name, content)
        return 'updated'
    project.create_jupyter_notebook(name, content)
    return 'created'


@app.route('/api/algorithm-review/create', methods=['POST'])
@advanced
def api_algorithm_review_create():
    """Write the adk_notebook shared libraries + one verbatim notebook per scan card
    into the ADMINTOOLKIT project, for human review of the Dataiku-API code."""
    client = g.client
    project = _resolve_macro_project(client)  # ADMINTOOLKIT; MacroProjectMissing → 409
    project_key = MACRO_PROJECT_KEY

    kernel_name, kernel_fallback, warnings = _adk_review_resolve_kernel(client)

    # 1. Shared libraries → project Python library (self-contained import closure).
    lib = project.get_library()
    lib_written: List[str] = []
    lib_errors: List[Dict[str, str]] = []
    for rel_path, text in sorted(_adk_review_lib_sources().items()):
        try:
            _adk_review_write_library_file(lib, rel_path, text)
            lib_written.append('python/' + rel_path)
        except Exception as exc:
            lib_errors.append({'file': rel_path, 'error': str(exc)[:500]})

    # 2. One Jupyter notebook per scan card (idempotent upsert by name).
    try:
        existing = client._perform_json('GET', '/projects/%s/jupyter-notebooks/' % project_key)
        existing_names = {(n.get('name') if isinstance(n, dict) else n) for n in (existing or [])}
    except Exception:
        existing_names = set()

    notebooks: List[Dict[str, Any]] = []
    for nb_name, (card_filename, source_text) in sorted(_adk_review_card_sources().items()):
        entry: Dict[str, Any] = {'file': card_filename, 'notebookName': nb_name}
        try:
            content = _adk_review_build_nbformat(card_filename, source_text, kernel_name)
            entry['status'] = _adk_review_upsert_notebook(project, nb_name, content, existing_names)
        except Exception as exc:
            entry['status'] = 'failed'
            entry['error'] = str(exc)[:500]
        notebooks.append(entry)

    return jsonify({
        'projectKey': project_key,
        'kernelEnv': kernel_name,
        'kernelFallbackUsed': kernel_fallback,
        'warnings': warnings,
        'library': {'written': lib_written, 'errors': lib_errors},
        'notebooks': notebooks,
        'createdCount': sum(1 for n in notebooks if n.get('status') == 'created'),
        'updatedCount': sum(1 for n in notebooks if n.get('status') == 'updated'),
        'failedCount': sum(1 for n in notebooks if n.get('status') == 'failed'),
    })


# ── Code env comparison helpers ─────────────────────────────────────────────

def _parse_spec_packages(spec: Any) -> Dict[str, str]:
    """Parse a spec package list into {normalized_name: version_spec}."""
    packages: Dict[str, str] = {}
    if not spec:
        return packages
    lines = spec if isinstance(spec, list) else str(spec).strip().split('\n')
    for line in lines:
        line = str(line).strip()
        if not line or line.startswith('#') or line.startswith('-'):
            continue
        m = re.match(r'^([A-Za-z0-9_.\-]+)(?:\[.*?\])?\s*(.*)', line)
        if m:
            name = re.sub(r'[-_.]+', '_', m.group(1)).lower()
            version = m.group(2).strip()
            packages[name] = version
    return packages


def _compare_code_envs_logic(
    envs: List[Tuple[str, str, Dict[str, str]]],
    max_diff: int = 3,
) -> Dict[str, Any]:
    """Classify environment relationships. Returns JSON-serializable result."""
    from collections import defaultdict

    pyver_map = {name: pyver for name, pyver, _ in envs}

    # Bucket by package-name fingerprint
    name_buckets: Dict[frozenset, List[Tuple[str, Dict[str, str]]]] = defaultdict(list)
    for name, pyver, packages in envs:
        key = frozenset(packages.keys())
        name_buckets[key].append((name, packages))

    green_groups: List[Dict[str, Any]] = []
    purple_groups: List[Dict[str, Any]] = []
    blue_groups: List[Dict[str, Any]] = []

    for pkg_names, members in name_buckets.items():
        if len(members) < 2:
            continue

        version_buckets: Dict[frozenset, List[str]] = defaultdict(list)
        for env_name, packages in members:
            vkey = frozenset(packages.items())
            version_buckets[vkey].append(env_name)

        for vkey, env_names in version_buckets.items():
            if len(env_names) < 2:
                continue
            py_sub: Dict[str, List[str]] = defaultdict(list)
            for en in env_names:
                py_sub[pyver_map[en]].append(en)

            # GREEN: same packages, same versions, same python
            for pv, names in py_sub.items():
                if len(names) >= 2:
                    green_groups.append({
                        'envNames': sorted(names),
                        'packageCount': len(dict(vkey)),
                        'pythonVersion': pv,
                    })

            # PURPLE: same packages, same versions, different python
            if len(py_sub) >= 2:
                all_names = sorted(env_names)
                pv_info = {en: pyver_map[en] for en in all_names}
                purple_groups.append({
                    'envNames': all_names,
                    'packageCount': len(dict(vkey)),
                    'pythonVersions': pv_info,
                })

        # BLUE: same package set, version diffs exist
        if len(version_buckets) >= 2:
            member_names = sorted(m[0] for m in members)
            diff_table: Dict[str, Dict[str, str]] = {}
            member_dict = {n: p for n, p in members}
            for pkg in sorted(pkg_names):
                versions = {n: member_dict[n].get(pkg, '') for n in member_names}
                if len(set(versions.values())) > 1:
                    diff_table[pkg] = versions
            if diff_table:
                total_pkgs = len(next(iter(member_dict.values())))
                blue_groups.append({
                    'envNames': member_names,
                    'packageCount': total_pkgs,
                    'diffCount': len(diff_table),
                    'diffs': diff_table,
                })

    # YELLOW: near-matches across different buckets (disabled — O(n^2) too slow)
    yellow_pairs: List[Dict[str, Any]] = []

    green_groups.sort(key=lambda g: g['envNames'][0])
    purple_groups.sort(key=lambda g: g['envNames'][0])
    blue_groups.sort(key=lambda g: g['envNames'][0])
    yellow_pairs.sort(key=lambda p: (p['envA'], p['envB']))

    return {
        'green': green_groups,
        'purple': purple_groups,
        'blue': blue_groups,
        'yellow': yellow_pairs,
        'analyzedCount': len(envs),
    }


@app.route('/api/code-envs/compare')
def api_code_envs_compare():
    max_diff = 1
    try:
        max_diff = max(1, int(request.args.get('maxDiff', '1')))
    except Exception:
        pass

    def loader():
        client = g.client
        ttl = _BACKEND_SETTINGS['cache_ttl_code_envs']
        env_listings = _sdk_fetch('list_code_envs', ttl, lambda: client.list_code_envs() or [])
        _SKIP = {'PLUGIN_MANAGED', 'DSS_INTERNAL'}
        envs: List[Tuple[str, str, Dict[str, str]]] = []

        def fetch_one(env_listing: Dict[str, Any]) -> Optional[Tuple[str, str, Dict[str, str]]]:
            name = env_listing.get('envName') or env_listing.get('name')
            lang = (env_listing.get('envLang') or env_listing.get('language') or 'PYTHON').upper()
            if not name or lang != 'PYTHON':
                return None
            try:
                c = _thread_client()
                raw = _sdk_fetch(
                    f'code_env_settings:{lang}:{name}', ttl,
                    lambda: _safe_get_raw(_bench_call('get_code_env', c.get_code_env, lang, name).get_settings()),
                )
                if str(raw.get('deploymentMode') or '').upper() in _SKIP:
                    return None
                packages = _parse_spec_packages(raw.get('specPackageList', ''))
                pyver_raw = (
                    raw.get('desc', {}).get('pythonInterpreter')
                    or raw.get('pythonInterpreter')
                    or ''
                )
                ver = str(pyver_raw).replace('PYTHON', '')
                if len(ver) == 2:
                    pyver = f'{ver[0]}.{ver[1]}'
                elif len(ver) >= 3:
                    pyver = f'{ver[0]}.{ver[1:]}'
                else:
                    pyver = str(pyver_raw) or 'unknown'
                return (name, pyver, packages)
            except Exception:
                return None

        workers = min(8, len(env_listings))
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(fetch_one, e): e for e in env_listings}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    envs.append(result)

        return _compare_code_envs_logic(envs, max_diff)

    data = _cache_get('code_envs_compare', _BACKEND_SETTINGS.get('cache_ttl_projects', 300), loader)
    return jsonify(data)


# ── Scan pipeline helpers: /api/project-footprint ────────────────────────────

def _task_pf_catalog(
    client: Any,
    add_event: Callable,
    limit_label: str,
    project_limit: int,
) -> Dict[str, Any]:
    add_event('load_project_catalog', 'loading project catalog')
    catalog = _list_projects_catalog_cheap(client)
    total_project_count = len(catalog)
    selected_catalog: List[Dict[str, str]] = catalog[:] if project_limit <= 0 else catalog[:project_limit]
    add_event('select_projects_by_key', f"selecting projects by key limit={limit_label}")
    project_info: Dict[str, Dict[str, str]] = {
        str(project.get('key') or ''): {
            'name': str(project.get('name') or project.get('key') or ''),
            'owner': str(project.get('owner') or 'Unknown'),
        }
        for project in selected_catalog
        if str(project.get('key') or '').strip()
    }
    project_keys = list(project_info.keys())
    return {
        'catalog': catalog,
        'total_project_count': total_project_count,
        'project_info': project_info,
        'project_keys': project_keys,
        'selected_count': len(project_keys),
    }


def _task_pf_footprint(
    project_keys: List[str],
    project_info: Dict[str, Dict[str, str]],
    deadline_ts: float,
    add_event: Callable,
    append_partial_row: Callable,
    progress_cb: Callable,
) -> Dict[str, Any]:
    """Runs in a background thread; acquires its own client via _thread_client().
    Emits partial rows immediately so the frontend can render before usage scan finishes."""
    if not project_keys:
        return {}
    client = _thread_client()
    add_event('load_project_footprint_map', f"loading project footprint map for {len(project_keys)} projects")
    project_footprints = _build_project_footprint_map_with_deadline(
        client,
        project_keys,
        deadline_ts=deadline_ts,
        progress_cb=progress_cb,
    )
    for pk in project_keys:
        meta = project_info.get(pk) or {}
        pf = project_footprints.get(pk)
        mdb = _collect_bucket_size_by_name(pf, lambda n: 'manageddataset' in n or ('managed' in n and 'dataset' in n))
        mfb = _collect_bucket_size_by_name(pf, lambda n: 'managedfolder' in n or ('managed' in n and 'folder' in n))
        bb = _collect_bucket_size_by_name(pf, lambda n: 'preparedbundle' in n or n.endswith('bundles') or 'bundle' in n)
        bc = _collect_bucket_file_count_by_name(pf, lambda n: 'preparedbundle' in n or n.endswith('bundles') or 'bundle' in n)
        total = _footprint_size(pf)
        if total <= 0:
            total = mdb + mfb + bb
        append_partial_row({
            'projectKey': pk,
            'name': str(meta.get('name') or pk).replace('_', ' '),
            'owner': meta.get('owner') or 'Unknown',
            'codeEnvCount': 0,
            'codeStudioCount': 0,
            'codeEnvBytes': 0,
            'managedDatasetsBytes': mdb,
            'managedFoldersBytes': mfb,
            'bundleBytes': bb,
            'bundleCount': bc,
            'totalBytes': total,
            'totalGB': total / float(1024 ** 3),
            'codeEnvHealth': _code_env_health(0),
        })
    return project_footprints


def _task_pf_usage_scan(
    project_info: Dict[str, Dict[str, str]],
    deadline_ts: float,
    add_event: Callable,
    progress_cb: Callable,
) -> Dict[str, Any]:
    """Runs in a background thread; acquires its own client via _thread_client()."""
    if not project_info:
        return {}
    client = _thread_client()
    add_event('collect_project_code_env_usage', f"collecting project code env usage for {len(project_info)} projects")
    return _get_shared_project_code_env_usage(
        client,
        project_info,
        {},
        include_project_object_scan=True,
        include_code_env_usage_api=False,
        deadline_ts=deadline_ts,
        progress_cb=progress_cb,
    )


def _format_saved_model_kind(model: Dict[str, Any]) -> str:
    model_type = str(model.get('type') or '').strip().upper()
    prediction_type = str(model.get('predictionType') or '').strip().upper()
    if model_type == 'CLUSTERING':
        return 'Clustering'
    labels = {
        'BINARY_CLASSIFICATION': 'Binary classification',
        'MULTICLASS': 'Multiclass',
        'MULTICLASS_CLASSIFICATION': 'Multiclass',
        'REGRESSION': 'Regression',
        'TIMESERIES_FORECAST': 'Time series forecast',
        'TIME_SERIES_FORECAST': 'Time series forecast',
    }
    if prediction_type in labels:
        return labels[prediction_type]
    if model_type == 'PREDICTION':
        return 'Prediction'
    return 'Unknown'


def _summarize_saved_models(saved_models: List[Dict[str, Any]]) -> Tuple[Dict[str, int], str]:
    counts: Dict[str, int] = {}
    for model in saved_models:
        kind = _format_saved_model_kind(model)
        counts[kind] = counts.get(kind, 0) + 1
    if not counts:
        return {}, ''
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    parts = [label if count == 1 else f"{count} {label}" for label, count in ordered]
    return counts, ', '.join(parts)


def _scan_saved_models_for_project(project_key: str) -> Tuple[str, List[Dict[str, Any]]]:
    client = _thread_client()
    project = client.get_project(project_key)
    rows: List[Dict[str, Any]] = []
    try:
        raw_models = project.list_saved_models() or []
    except Exception:
        return project_key, rows

    for raw in raw_models:
        if not isinstance(raw, dict):
            continue
        model_id = str(raw.get('id') or raw.get('smId') or raw.get('name') or '').strip()
        row: Dict[str, Any] = {
            'id': model_id,
            'name': str(raw.get('name') or model_id or 'Unnamed model'),
            'type': str(raw.get('type') or 'UNKNOWN').strip().upper() or 'UNKNOWN',
            'savedModelType': raw.get('savedModelType'),
            'backendType': raw.get('backendType'),
            'predictionType': raw.get('predictionType'),
            'versionsCount': _coerce_int(raw.get('versionsCount'), 0),
        }
        if not row.get('predictionType') and row.get('type') == 'PREDICTION' and model_id:
            try:
                settings = project.get_saved_model(model_id).get_settings().get_raw()
                mini_task = settings.get('miniTask') if isinstance(settings, dict) else None
                if isinstance(mini_task, dict):
                    row['predictionType'] = mini_task.get('predictionType')
                    row['backendType'] = row.get('backendType') or mini_task.get('backendType')
            except Exception:
                pass
        if model_id:
            try:
                sm = project.get_saved_model(model_id)
                versions = sm.list_versions() or []
                row['versionsCount'] = len(versions)
                active = sm.get_active_version()
                if isinstance(active, dict) and active.get('id') is not None:
                    row['activeVersionId'] = str(active.get('id'))
            except Exception:
                pass
        rows.append(row)
    return project_key, rows


def _task_pf_saved_models(
    project_keys: List[str],
    deadline_ts: float,
    add_event: Callable,
) -> Dict[str, Dict[str, Any]]:
    saved_models_by_project: Dict[str, Dict[str, Any]] = {}
    if not project_keys:
        return saved_models_by_project
    add_event('collect_project_saved_models', f"collecting saved models for {len(project_keys)} projects")
    max_workers = min(_parallel_workers(8), len(project_keys))
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = {}
        for project_key in project_keys:
            if time.time() > deadline_ts:
                add_event('collect_project_saved_models', 'deadline reached while submitting saved model scans', 'warn')
                break
            futures[pool.submit(_scan_saved_models_for_project, project_key)] = project_key
        try:
            for future in as_completed(list(futures.keys()), timeout=max(0.0, deadline_ts - time.time())):
                project_key = futures.get(future) or ''
                try:
                    pk, saved_models = future.result()
                except Exception as exc:
                    add_event('project_saved_models_error', f"saved model scan failed: {exc}", 'warn', project_key)
                    continue
                type_counts, summary = _summarize_saved_models(saved_models)
                saved_models_by_project[pk] = {
                    'savedModels': saved_models,
                    'savedModelCount': len(saved_models),
                    'savedModelTypeCounts': type_counts,
                    'savedModelSummary': summary,
                }
                add_event('project_saved_models_done', f"saved models={len(saved_models)}", 'info', pk)
        except FuturesTimeoutError:
            add_event('collect_project_saved_models', 'timeout while waiting for saved model scans', 'warn')
    return saved_models_by_project


def _task_pf_aggregate(
    project_keys: List[str],
    project_info: Dict[str, Dict[str, str]],
    project_footprints: Dict[str, Any],
    usage_data: Dict[str, Any],
    saved_model_data: Dict[str, Dict[str, Any]],
    deadline_ts: float,
    add_event: Callable,
) -> Dict[str, Any]:
    envs_by_project: Dict[str, set] = usage_data.get('envsByProject') or {k: set() for k in project_info.keys()}
    usage_breakdown_by_project = usage_data.get('usageBreakdownByProject') or {k: {} for k in project_info.keys()}
    usage_details_by_project = usage_data.get('usageDetailsByProject') or {k: [] for k in project_info.keys()}
    code_studios_by_project = usage_data.get('codeStudiosByProject') or {}

    project_rows: List[Dict[str, Any]] = []
    project_risks: List[float] = []
    total_gb_values: List[float] = []

    add_event('aggregate_project_rows', f"aggregating project rows for {len(project_keys)} projects")
    raw_rows: List[Dict[str, Any]] = []
    for project_key in project_keys:
        if time.time() > deadline_ts:
            add_event('aggregate_project_rows', 'deadline reached at step=aggregate_project_rows', 'warn')
            break
        project_started = time.time()
        add_event('project_aggregate_start', 'aggregating project row', 'info', project_key)
        meta = project_info.get(project_key) or {}
        project_footprint = project_footprints.get(project_key)

        managed_datasets_bytes = _collect_bucket_size_by_name(
            project_footprint,
            lambda n: 'manageddataset' in n or ('managed' in n and 'dataset' in n),
        )
        managed_folders_bytes = _collect_bucket_size_by_name(
            project_footprint,
            lambda n: 'managedfolder' in n or ('managed' in n and 'folder' in n),
        )
        project_env_keys = envs_by_project.get(project_key) or set()
        code_env_count = len(project_env_keys)
        bundle_bytes = _collect_bucket_size_by_name(
            project_footprint,
            lambda n: 'preparedbundle' in n or n.endswith('bundles') or 'bundle' in n,
        )
        bundle_count = _collect_bucket_file_count_by_name(
            project_footprint,
            lambda n: 'preparedbundle' in n or n.endswith('bundles') or 'bundle' in n,
        )
        total_bytes = _footprint_size(project_footprint)
        if total_bytes <= 0:
            total_bytes = managed_datasets_bytes + managed_folders_bytes + bundle_bytes
        total_gb = total_bytes / float(1024 ** 3)
        total_gb_values.append(total_gb)
        saved_model_meta = saved_model_data.get(project_key) or {}

        raw_row = {
            'projectKey': project_key,
            'name': str(meta.get('name') or project_key).replace('_', ' '),
            'owner': meta.get('owner') or 'Unknown',
            'codeEnvCount': code_env_count,
            'codeStudios': code_studios_by_project.get(project_key) or [],
            'codeStudioCount': len(code_studios_by_project.get(project_key) or []),
            'codeEnvBytes': 0,
            'managedDatasetsBytes': managed_datasets_bytes,
            'managedFoldersBytes': managed_folders_bytes,
            'bundleBytes': bundle_bytes,
            'bundleCount': bundle_count,
            'footprintBreakdown': _footprint_bucket_breakdown(project_footprint),
            'totalBytes': total_bytes,
            'totalGB': total_gb,
            'codeEnvHealth': _code_env_health(code_env_count),
            'usageBreakdown': usage_breakdown_by_project.get(project_key) or {},
            'usageDetails': usage_details_by_project.get(project_key) or [],
            'codeEnvKeys': sorted(list(project_env_keys)),
            'savedModelCount': _coerce_int(saved_model_meta.get('savedModelCount'), 0),
            'savedModels': saved_model_meta.get('savedModels') or [],
            'savedModelTypeCounts': saved_model_meta.get('savedModelTypeCounts') or {},
            'savedModelSummary': saved_model_meta.get('savedModelSummary') or '',
        }
        raw_rows.append(raw_row)
        add_event(
            'project_aggregate_done',
            (
                f"aggregate complete codeEnvCount={code_env_count} "
                f"total={_format_size_human(total_bytes)} bundles={bundle_count}"
            ),
            'info',
            project_key,
            event_elapsed_ms=(time.time() - project_started) * 1000.0,
        )

    avg_project_gb = (sum(total_gb_values) / len(total_gb_values)) if total_gb_values else 0.0
    add_event('compute_health_scores', f"computing health scores for {len(raw_rows)} projects")
    for row in raw_rows:
        if time.time() > deadline_ts:
            break
        total_gb = _coerce_float(row.get('totalGB'), 0.0)
        size_index = _project_size_index(total_gb, avg_project_gb)
        size_health = _project_size_health(total_gb, size_index)
        code_env_count = _coerce_int(row.get('codeEnvCount'), 0)
        env_risk = _code_env_risk(code_env_count)
        project_risk = (0.7 * env_risk) + (0.3 * size_index)
        project_risks.append(project_risk)
        row.update({
            'instanceAvgProjectGB': round(avg_project_gb, 4),
            'projectSizeIndex': round(size_index, 4),
            'projectSizeHealth': size_health,
            'codeEnvRisk': round(env_risk, 4),
            'projectRisk': round(project_risk, 4),
        })
        project_rows.append(row)

    return {
        'project_rows': project_rows,
        'project_risks': project_risks,
        'total_gb_values': total_gb_values,
    }


@app.route('/api/project-footprint')
def api_project_footprint():
    client = g.client

    def loader():
        timeout_ms = _BACKEND_SETTINGS['project_footprint_timeout_ms']
        project_limit = 0
        project_selection = 'all_by_project_key'
        limit_label = 'all' if project_limit <= 0 else str(project_limit)
        started = time.time()
        deadline = started + (timeout_ms / 1000.0)
        steps: List[Dict[str, Any]] = []
        op_stats: Dict[str, Dict[str, Any]] = {}
        benchmark_events: List[Dict[str, Any]] = []
        benchmark_timed_out = False
        timeout_at_step: Optional[str] = None
        deadline_pressure_steps: set = set()
        timeout_event_steps: set = set()
        timed_out_or_error = False
        progress_run_id = _start_progress('project_footprint')
        catalog_result: Optional[Dict[str, Any]] = None
        progress_meta: Dict[str, Any] = {
            'selectedProjects': 0,
            'projectFootprintDone': 0,
            'projectUsageDone': 0,
            'projectAggregateDone': 0,
            'catalogDone': False,
        }

        def elapsed_ms() -> float:
            return (time.time() - started) * 1000.0

        def remaining_ms() -> int:
            return max(0, int((deadline - time.time()) * 1000.0))

        def _compute_progress_pct(force_done: bool = False) -> int:
            if force_done:
                return 100
            footprint_total = max(0, int(progress_meta['selectedProjects']))
            usage_total = max(0, int(progress_meta['selectedProjects']))
            aggregate_total = max(0, int(progress_meta['selectedProjects']))
            footprint_ratio = min(1.0, float(progress_meta['projectFootprintDone']) / float(footprint_total)) if footprint_total > 0 else 0.0
            usage_ratio = min(1.0, float(progress_meta['projectUsageDone']) / float(usage_total)) if usage_total > 0 else 0.0
            aggregate_ratio = min(1.0, float(progress_meta['projectAggregateDone']) / float(aggregate_total)) if aggregate_total > 0 else 0.0
            pct = 0.0
            pct += 10.0 if progress_meta['catalogDone'] else 0.0
            pct += 50.0 * footprint_ratio
            pct += 25.0 * usage_ratio
            pct += 15.0 * aggregate_ratio
            if timed_out_or_error:
                return int(max(0.0, min(100.0, pct)))
            return int(max(0.0, min(99.0, pct)))

        def _infer_phase() -> str:
            if not progress_meta['catalogDone']:
                return 'catalog'
            if progress_meta['selectedProjects'] > 0 and progress_meta['projectFootprintDone'] < progress_meta['selectedProjects']:
                return 'footprint_fetch'
            if progress_meta['selectedProjects'] > 0 and progress_meta['projectUsageDone'] < progress_meta['selectedProjects']:
                return 'usage_scan'
            if progress_meta['selectedProjects'] > 0 and progress_meta['projectAggregateDone'] < progress_meta['selectedProjects']:
                return 'aggregate'
            return 'finalizing'

        def _update_progress_summary(force_done: bool = False) -> None:
            _set_progress_summary(
                'project_footprint',
                progress_run_id,
                {
                    'progressPct': _compute_progress_pct(force_done),
                    'phase': _infer_phase() if not force_done else 'done',
                    'selectedProjects': int(progress_meta['selectedProjects']),
                    'projectFootprintDone': int(progress_meta['projectFootprintDone']),
                    'projectUsageDone': int(progress_meta['projectUsageDone']),
                    'projectAggregateDone': int(progress_meta['projectAggregateDone']),
                    'timedOut': bool(benchmark_timed_out),
                    'timeoutAtStep': timeout_at_step,
                    'totalElapsedMs': round(elapsed_ms(), 2),
                    'remainingMs': remaining_ms(),
                },
            )

        def add_event(
            step: str,
            message: str,
            level: str = 'info',
            project_key: Optional[str] = None,
            event_elapsed_ms: Optional[float] = None,
        ) -> None:
            event: Dict[str, Any] = {
                'tMs': round(elapsed_ms(), 2),
                'level': level,
                'step': step,
                'message': message,
            }
            if project_key:
                event['projectKey'] = project_key
            if event_elapsed_ms is not None:
                event['elapsedMs'] = round(max(0.0, float(event_elapsed_ms)), 2)
            benchmark_events.append(event)
            _append_progress_event('project_footprint', progress_run_id, event)
            if step in ('project_footprint_fetch_ok', 'project_footprint_fetch_error', 'project_footprint_fetch_timeout') and project_key:
                progress_meta['projectFootprintDone'] += 1
            if step == 'project_env_refs_resolved' and project_key:
                progress_meta['projectUsageDone'] += 1
            if step == 'project_aggregate_done' and project_key:
                progress_meta['projectAggregateDone'] += 1
            _update_progress_summary(False)

        def progress_event(**kwargs) -> None:
            add_event(
                step=str(kwargs.get('step') or 'event'),
                message=str(kwargs.get('message') or ''),
                level=str(kwargs.get('level') or 'info'),
                project_key=kwargs.get('project_key'),
                event_elapsed_ms=kwargs.get('elapsed_ms'),
            )

        def deadline_reached(step_name: str) -> bool:
            nonlocal benchmark_timed_out, timeout_at_step, timed_out_or_error
            now = time.time()
            if now < deadline:
                if step_name not in deadline_pressure_steps and (deadline - now) <= 10.0:
                    deadline_pressure_steps.add(step_name)
                    add_event(step_name, f"deadline pressure: only {remaining_ms()}ms remaining", 'warn')
                return False
            benchmark_timed_out = True
            timed_out_or_error = True
            if timeout_at_step is None:
                timeout_at_step = step_name
            if step_name not in timeout_event_steps:
                timeout_event_steps.add(step_name)
                add_event(step_name, f"deadline reached at step={step_name}", 'warn')
            return True

        def record_step(name: str, step_start: float, calls: int = 0) -> None:
            elapsed = max(0.0, (time.time() - step_start) * 1000.0)
            avg_ms = (elapsed / calls) if calls > 0 else 0.0
            qps = (calls / (elapsed / 1000.0)) if calls > 0 and elapsed > 0 else 0.0
            steps.append({
                'name': name,
                'calls': int(calls),
                'elapsedMs': round(elapsed, 2),
                'avgMs': round(avg_ms, 2),
                'qps': round(qps, 2),
            })
            add_event(name, f"{name} done calls={calls}", 'info', event_elapsed_ms=elapsed)

        def record_op(name: str, elapsed_ms_value: float, calls: int = 1) -> None:
            entry = op_stats.setdefault(name, {'operation': name, 'calls': 0, 'elapsedMs': 0.0})
            entry['calls'] = int(entry.get('calls') or 0) + int(max(0, calls))
            entry['elapsedMs'] = float(entry.get('elapsedMs') or 0.0) + max(0.0, float(elapsed_ms_value))

        previous_recorder = getattr(_THREAD_LOCAL, 'bench_record_op', None)
        setattr(_THREAD_LOCAL, 'bench_record_op', record_op)

        try:
            # Phase 1: catalog (main thread)
            if not deadline_reached('load_project_catalog'):
                step_start = time.time()
                catalog_result = _task_pf_catalog(client, add_event, limit_label, project_limit)
                record_step('load_project_catalog', step_start, calls=catalog_result['selected_count'])
            else:
                catalog_result = {'catalog': [], 'total_project_count': 0, 'project_info': {}, 'project_keys': [], 'selected_count': 0}
            progress_meta['selectedProjects'] = catalog_result['selected_count']
            progress_meta['catalogDone'] = True
            _update_progress_summary(False)
            app.logger.info("[perf:pf] catalog elapsed=%.0fms projects=%d", elapsed_ms(), catalog_result['selected_count'])

            project_keys: List[str] = catalog_result['project_keys']
            project_info: Dict[str, Dict[str, str]] = catalog_result['project_info']
            total_project_count: int = catalog_result['total_project_count']

            # Phase 2: footprint + usage + saved models in parallel (off-thread)
            project_footprints: Dict[str, Any] = {}
            usage_data: Dict[str, Any] = {}
            saved_model_data: Dict[str, Dict[str, Any]] = {}
            if project_keys and not deadline_reached('load_project_footprint_map'):
                step_start_fp = time.time()
                with ThreadPoolExecutor(max_workers=3) as pool:
                    f_footprint = pool.submit(
                        _task_pf_footprint,
                        project_keys,
                        project_info,
                        deadline,
                        add_event,
                        lambda row: _append_progress_partial_row('project_footprint', progress_run_id, row),
                        progress_event,
                    )
                    f_usage = pool.submit(
                        _task_pf_usage_scan,
                        project_info,
                        deadline,
                        add_event,
                        progress_event,
                    )
                    f_saved_models = pool.submit(
                        _task_pf_saved_models,
                        project_keys,
                        deadline,
                        add_event,
                    )
                    project_footprints = f_footprint.result()
                    usage_data = f_usage.result()
                    saved_model_data = f_saved_models.result()
                record_step('load_project_footprint_map', step_start_fp, calls=len(project_keys))
                record_step('collect_project_code_env_usage', step_start_fp, calls=len(project_keys))
                record_step('collect_project_saved_models', step_start_fp, calls=len(project_keys))
            app.logger.info("[perf:pf] footprint_fetch elapsed=%.0fms projects=%d", elapsed_ms(), len(project_keys))
            app.logger.info("[perf:pf] usage_scan elapsed=%.0fms projects=%d", elapsed_ms(), len(project_keys))
            app.logger.info("[perf:pf] saved_model_scan elapsed=%.0fms projects=%d", elapsed_ms(), len(saved_model_data))

            # Phase 3: aggregate (main thread)
            agg_result: Dict[str, Any] = {'project_rows': [], 'project_risks': [], 'total_gb_values': []}
            if project_keys and not deadline_reached('aggregate_project_rows'):
                step_start = time.time()
                agg_result = _task_pf_aggregate(
                    project_keys,
                    project_info,
                    project_footprints,
                    usage_data,
                    saved_model_data,
                    deadline,
                    add_event,
                )
                record_step('aggregate_project_rows', step_start, calls=len(agg_result['project_rows']))
            app.logger.info("[perf:pf] aggregate elapsed=%.0fms rows=%d", elapsed_ms(), len(agg_result['project_rows']))

            project_rows: List[Dict[str, Any]] = agg_result['project_rows']
            total_gb_values: List[float] = agg_result['total_gb_values']
            project_risks: List[float] = agg_result['project_risks']

            project_rows.sort(key=lambda item: _coerce_int(item.get('totalBytes'), 0), reverse=True)
            avg_project_gb = (sum(total_gb_values) / len(total_gb_values)) if total_gb_values else 0.0
            avg_project_risk = (sum(project_risks) / len(project_risks)) if project_risks else 0.0

            api_calls = []
            for entry in sorted(op_stats.values(), key=lambda item: float(item.get('elapsedMs') or 0.0), reverse=True):
                calls = int(entry.get('calls') or 0)
                elapsed = float(entry.get('elapsedMs') or 0.0)
                avg_ms = (elapsed / calls) if calls > 0 else 0.0
                qps = (calls / (elapsed / 1000.0)) if calls > 0 and elapsed > 0 else 0.0
                api_calls.append({
                    'operation': entry.get('operation'),
                    'calls': calls,
                    'elapsedMs': round(elapsed, 2),
                    'avgMs': round(avg_ms, 2),
                    'qps': round(qps, 2),
                })

            app.logger.info("[perf:pf] total elapsed=%.0fms", elapsed_ms())
            benchmark_summary = {
                'enabled': True,
                'projectLimit': len(project_keys),
                'projectSelection': project_selection,
                'timeoutMs': timeout_ms,
                'timedOut': bool(benchmark_timed_out),
                'timeoutAtStep': timeout_at_step,
                'totalElapsedMs': round(elapsed_ms(), 2),
                'remainingMs': remaining_ms(),
                'totalProjectCount': total_project_count,
                'selectedProjectCount': len(project_keys),
                'steps': steps,
                'apiCalls': api_calls,
                'events': benchmark_events,
            }
            summary = {
                'instanceProjectRiskAvg': round(avg_project_risk, 4),
                'instanceAvgProjectGB': round(avg_project_gb, 4),
                'projectCount': len(project_rows),
                'footprintAvailable': _footprint_available(),
                'footprintReason': _footprint_unavailable_reason(),
                'benchmark': benchmark_summary,
            }
            app.logger.info(
                "[project-footprint] benchmark done rows=%s selected=%s total=%s elapsed=%.2fs timedOut=%s",
                len(project_rows),
                len(project_keys),
                total_project_count,
                time.time() - started,
                benchmark_timed_out,
            )
            add_event(
                'project_footprint_done',
                f"project footprint done rows={len(project_rows)} selected={len(project_keys)} total={total_project_count} timedOut={benchmark_timed_out}",
            )
            # Surface per-project scan failures collected during the footprint/usage phases.
            _scan_error_area = {
                'project_footprint_fetch_error': 'footprint',
                'project_footprint_fetch_timeout': 'footprint',
                'project_code_studios_error': 'code_studios',
                'project_permissions_error': 'permissions',
                'project_saved_models_error': 'saved_models',
            }
            scan_errors: List[Dict[str, Any]] = []
            failed_project_keys: set = set()
            for ev in benchmark_events:
                area = _scan_error_area.get(ev.get('step'))
                if not area:
                    continue
                pk = ev.get('projectKey') or ''
                scan_errors.append({
                    'projectKey': pk,
                    'area': area,
                    'error': str(ev.get('message') or '')[:240],
                })
                if pk:
                    failed_project_keys.add(pk)
            _update_progress_summary(True)
            _finish_progress('project_footprint', progress_run_id, status='done', summary=benchmark_summary)
            return {
                'projects': project_rows,
                'summary': summary,
                'scanErrors': scan_errors,
                'failedProjectCount': len(failed_project_keys),
                'scannedProjectCount': len(project_keys),
            }
        except Exception as exc:
            timed_out_or_error = True
            add_event('project_footprint_error', f"project footprint analysis failed: {exc}", 'error')
            _update_progress_summary(False)
            _finish_progress(
                'project_footprint',
                progress_run_id,
                status='error',
                summary={
                    'enabled': True,
                    'projectLimit': progress_meta['selectedProjects'],
                    'projectSelection': project_selection,
                    'timeoutMs': timeout_ms,
                    'timedOut': bool(benchmark_timed_out),
                    'timeoutAtStep': timeout_at_step,
                    'totalElapsedMs': round(elapsed_ms(), 2),
                    'remainingMs': remaining_ms(),
                    'totalProjectCount': catalog_result['total_project_count'] if catalog_result else 0,
                    'selectedProjectCount': progress_meta['selectedProjects'],
                    'steps': steps,
                    'apiCalls': [
                        {
                            'operation': entry.get('operation'),
                            'calls': int(entry.get('calls') or 0),
                            'elapsedMs': round(float(entry.get('elapsedMs') or 0.0), 2),
                            'avgMs': round((float(entry.get('elapsedMs') or 0.0) / max(1, int(entry.get('calls') or 0))), 2),
                            'qps': round((int(entry.get('calls') or 0) / max(0.001, float(entry.get('elapsedMs') or 0.0) / 1000.0)), 2),
                        }
                        for entry in sorted(op_stats.values(), key=lambda item: float(item.get('elapsedMs') or 0.0), reverse=True)
                    ],
                    'events': benchmark_events,
                },
                error=str(exc),
            )
            raise
        finally:
            setattr(_THREAD_LOCAL, 'bench_record_op', previous_recorder)

    data = loader()
    return jsonify(data)


@app.route('/api/project-footprint/progress')
def api_project_footprint_progress():
    since_raw = request.args.get('since', '0')
    run_id = request.args.get('runId')
    rows_since_raw = request.args.get('rowsSince', '0')
    try:
        since = max(0, int(str(since_raw or '0')))
    except Exception:
        since = 0
    try:
        rows_since = max(0, int(str(rows_since_raw or '0')))
    except Exception:
        rows_since = 0
    payload = _read_progress('project_footprint', since=since, run_id=run_id, rows_since=rows_since)
    return jsonify(payload)


@app.route('/api/project-footprint-progress')
def api_project_footprint_progress_alias():
    return api_project_footprint_progress()



@app.route('/api/tools/email/preview', methods=['POST'])
def api_tools_email_preview():
    payload = request.get_json(silent=True) or {}
    _valid_campaigns = {
        'project', 'code_env', 'code_studio', 'auto_scenario',
        'disabled_user', 'deprecated_code_env', 'default_code_env',
        'overshared_project', 'scenario_frequency', 'empty_project',
        'large_flow', 'orphan_notebooks', 'scenario_failing',
        'inactive_project', 'unused_code_env',
    }
    campaign = str(payload.get('campaign') or 'project').strip().lower()
    if campaign not in _valid_campaigns:
        campaign = 'project'

    template_payload = payload.get('template') if isinstance(payload.get('template'), dict) else {}
    defaults = _default_email_template(campaign)
    subject_template = str(template_payload.get('subject') or defaults['subject'])
    body_template = str(template_payload.get('body') or defaults['body'])
    recipients = payload.get('recipients')
    if not isinstance(recipients, list):
        recipients = []

    previews: List[Dict[str, Any]] = []
    for recipient in recipients:
        if not isinstance(recipient, dict):
            continue

        owner = str(recipient.get('owner') or recipient.get('recipientKey') or 'Unknown')
        to_email = str(recipient.get('email') or owner).strip()
        project_keys = sorted({str(key) for key in (recipient.get('projectKeys') or []) if str(key).strip()})
        code_env_names = sorted({str(name) for name in (recipient.get('codeEnvNames') or []) if str(name).strip()})
        usage_details = [
            usage for usage in (recipient.get('usageDetails') or [])
            if isinstance(usage, dict)
        ]
        usage_details = _dedupe_usage_entries(usage_details)
        if campaign == 'project':
            object_lines = _usage_lines_grouped_by_project(usage_details)
        else:
            object_lines = _usage_lines_grouped_by_code_env(usage_details)

        variables = {
            'owner': owner,
            'owner_email': to_email,
            'project_count': str(len(project_keys)),
            'code_env_count': str(len(code_env_names)),
            'object_count': str(len(usage_details)),
            'project_list': '\n'.join([f"- {key}" for key in project_keys]) if project_keys else '- none',
            'code_env_list': '\n'.join([f"- {name}" for name in code_env_names]) if code_env_names else '- none',
            'objects_list': '\n'.join(object_lines),
            'project_keys': ', '.join(project_keys) if project_keys else 'none',
            'code_envs': ', '.join(code_env_names) if code_env_names else 'none',
        }

        projects_data = recipient.get('projects') or []
        code_studio_lines = []
        for proj in projects_data:
            if not isinstance(proj, dict):
                continue
            pname = str(proj.get('name') or proj.get('projectKey') or 'Unknown')
            pkey = str(proj.get('projectKey') or '')
            cs_count = _coerce_int(proj.get('codeStudioCount'), 0)
            code_studio_lines.append(f"- {pname} ({pkey}): {cs_count} code studios")
        variables['code_studio_list'] = '\n'.join(code_studio_lines) if code_studio_lines else '- none'

        scenario_lines = []
        for proj in projects_data:
            if not isinstance(proj, dict):
                continue
            auto_scenarios = proj.get('autoScenarios') or []
            if not auto_scenarios:
                continue
            pname = str(proj.get('name') or proj.get('projectKey') or 'Unknown')
            pkey = str(proj.get('projectKey') or '')
            scenario_lines.append(f"Project: {pname} ({pkey})")
            for sc in auto_scenarios:
                if not isinstance(sc, dict):
                    continue
                sc_name = str(sc.get('name') or sc.get('id') or 'Unknown')
                sc_type = str(sc.get('type') or 'unknown')
                trigger_count = _coerce_int(sc.get('triggerCount'), 0)
                scenario_lines.append(f"  - {sc_name} (type={sc_type}, triggers={trigger_count})")
        variables['scenario_list'] = '\n'.join(scenario_lines) if scenario_lines else '- none'

        inactive_project_lines = []
        for proj in projects_data:
            if not isinstance(proj, dict):
                continue
            pname = str(proj.get('name') or proj.get('projectKey') or 'Unknown')
            pkey = str(proj.get('projectKey') or '')
            days_inactive = _coerce_int(proj.get('daysInactive'), 0)
            if days_inactive > 0:
                inactive_project_lines.append(f"- {pname} ({pkey}): inactive for {days_inactive} days")
            else:
                inactive_project_lines.append(f"- {pname} ({pkey})")
        variables['inactive_project_list'] = '\n'.join(inactive_project_lines) if inactive_project_lines else '- none'

        # Build project_env_list: project → code envs → objects (where used)
        # Group usage_details by projectKey → codeEnvName → object lines
        _pel_grouped: Dict[str, Dict[str, List[str]]] = {}
        _pel_seen: set = set()
        for u in usage_details:
            if not isinstance(u, dict):
                continue
            pk = str(u.get('projectKey') or '').strip()
            ce = str(u.get('codeEnvName') or u.get('codeEnvKey') or '').strip()
            if not pk or not ce:
                continue
            usage_type = str(u.get('usageType') or '').strip().upper()
            _pel_grouped.setdefault(pk, {}).setdefault(ce, [])
            # Skip PROJECT-level defaults for object lines (they have no real object)
            if usage_type == 'PROJECT':
                continue
            obj_label = _email_object_type_label(u.get('objectType'), usage_type)
            obj_name = str(u.get('objectName') or u.get('objectId') or '').strip()
            if obj_name:
                sig = (pk, ce.lower(), obj_label.lower(), obj_name)
                if sig not in _pel_seen:
                    _pel_seen.add(sig)
                    _pel_grouped[pk][ce].append(f"      {obj_label}: {obj_name}")

        project_env_lines: List[str] = []
        for proj in projects_data:
            if not isinstance(proj, dict):
                continue
            pkey = str(proj.get('projectKey') or '')
            pname = str(proj.get('name') or pkey)
            ce_count = _coerce_int(proj.get('codeEnvCount'), 0)
            header = pname if pname == pkey else f"{pname} ({pkey})"
            if ce_count:
                header += f" — {ce_count} code envs"
            project_env_lines.append(header)
            env_data = _pel_grouped.get(pkey, {})
            if env_data:
                for env_name in sorted(env_data.keys(), key=lambda e: e.lower()):
                    project_env_lines.append(f"  - {env_name}")
                    for obj_line in sorted(env_data[env_name], key=lambda l: l.lower()):
                        project_env_lines.append(obj_line)
            else:
                # Fallback: use per-project code env names (from projects array)
                proj_env_names = sorted(set(str(n) for n in (proj.get('codeEnvNames') or []) if str(n).strip()))
                for name in proj_env_names:
                    project_env_lines.append(f"  - {name}")
        variables['project_env_list'] = '\n'.join(project_env_lines) if project_env_lines else '- none'

        # Build rich HTML for all list variables
        _rich_html_map = {
            'project_env_list': (_PROJECT_ENV_MARKER, _build_project_env_html(projects_data, _pel_grouped)),
            'project_list': (_PROJECT_LIST_MARKER, _build_items_html(project_keys)),
            'code_env_list': (_CODE_ENV_LIST_MARKER, _build_items_html(code_env_names, accent='#00897b')),
            'objects_list': (_OBJECTS_LIST_MARKER, _build_objects_html(usage_details, group_by_project=(campaign == 'project'))),
            'code_studio_list': (_CODE_STUDIO_LIST_MARKER, _build_code_studio_html(projects_data)),
            'scenario_list': (_SCENARIO_LIST_MARKER, _build_scenario_html(projects_data)),
            'inactive_project_list': (_INACTIVE_LIST_MARKER, _build_inactive_projects_html(projects_data)),
        }

        _preview_debug = {
            'usageDetailsCount': len(usage_details),
            'usageTypes': sorted({str(u.get('usageType') or '') for u in usage_details}),
            'envGroups': {k: list(v.keys()) for k, v in _pel_grouped.items()},
            'projectsInRecipient': [
                {'projectKey': proj.get('projectKey'), 'codeEnvNames': proj.get('codeEnvNames')}
                for proj in projects_data if isinstance(proj, dict)
            ],
        }
        app.logger.info("[tools] email-preview campaign=%s owner=%s debug=%s", campaign, owner, _preview_debug)

        # Swap list variables with markers for rich HTML injection
        for _var_name, (_marker, _html_val) in _rich_html_map.items():
            if '{{' + _var_name + '}}' in body_template:
                variables[_var_name] = _marker

        rendered_body_text = _render_template_text(body_template, variables)
        body_html = _text_body_to_html(rendered_body_text)

        # Inject rich HTML for all list variables
        for _var_name, (_marker, _html_val) in _rich_html_map.items():
            if _marker in body_html:
                body_html = body_html.replace(_marker, _html_val)
        # Replace footer placeholders in the final HTML wrapper
        admin_email = str(payload.get('adminEmail') or 'dss-admin@your-company.com').strip()
        chat_channel_url = str(payload.get('chatChannelUrl') or '#').strip()
        body_html = body_html.replace('{{admin_email}}', admin_email)
        body_html = body_html.replace('{{chat_channel_url}}', chat_channel_url)
        preview = {
            'recipientKey': str(recipient.get('recipientKey') or owner),
            'owner': owner,
            'to': to_email,
            'projectKeys': project_keys,
            'codeEnvNames': code_env_names,
            'projectKeyForSend': recipient.get('projectKeyForSend') or (project_keys[0] if project_keys else None) or os.environ.get('DKU_CURRENT_PROJECT_KEY', ''),
            'objectCount': len(usage_details),
            'subject': _render_template_text(subject_template, variables),
            'body': body_html,
            'usageDetails': usage_details,
            '_debug': _preview_debug,
        }

        previews.append(preview)

    app.logger.info("[tools] preview campaign=%s recipients=%s", campaign, len(previews))
    return jsonify({
        'campaign': campaign,
        'template': {
            'subject': subject_template,
            'body': body_template,
        },
        'previews': previews,
        'count': len(previews),
    })


@app.route('/api/tools/email/send', methods=['POST'])
@advanced
def api_tools_email_send():
    client = g.client
    payload = request.get_json(silent=True) or {}
    campaign = str(payload.get('campaign') or 'project').strip().lower()

    requested_channel = str(payload.get('channelId') or '').strip() or None
    plain_text = _parse_bool(payload.get('plainText'), True)

    previews = payload.get('previews')
    if not isinstance(previews, list):
        previews = []

    channels = _list_mail_channels(client)
    if not channels:
        app.logger.warning("[tools] send failed: no DSS mail channel configured")
        return jsonify({'error': 'No DSS mail channel configured'}), 400

    # Priority: request payload > plugin param > first available
    effective_channel = requested_channel or _get_configured_mail_channel() or None
    selected = channels[0]
    if effective_channel:
        for channel in channels:
            if channel.get('id') == effective_channel:
                selected = channel
                break
    selected_id = str(selected.get('id') or '')

    channel_obj = _get_mail_channel(client, selected_id)
    if channel_obj is None:
        app.logger.warning("[tools] send failed: cannot resolve mail channel %s", selected_id)
        return jsonify({'error': f'Unable to load mail channel: {selected_id}'}), 400

    results: List[Dict[str, Any]] = []
    sent_count = 0
    for preview in previews:
        if not isinstance(preview, dict):
            continue
        recipient_key = str(preview.get('recipientKey') or '')
        to_email = str(preview.get('to') or '').strip()
        project_key = str(preview.get('projectKeyForSend') or '').strip()
        if not project_key:
            project_key = os.environ.get('DKU_CURRENT_PROJECT_KEY', '')
        subject = str(preview.get('subject') or '').strip()
        body = str(preview.get('body') or '')

        to_email = re.sub(r'[\r\n]', '', to_email)
        subject = re.sub(r'[\r\n]', '', subject)
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', to_email):
            results.append({
                'recipientKey': recipient_key,
                'to': to_email,
                'projectKeyForSend': project_key,
                'status': 'error',
                'error': 'Invalid email address format',
            })
            continue

        if not to_email or not project_key or not subject:
            results.append({
                'recipientKey': recipient_key,
                'to': to_email,
                'projectKeyForSend': project_key,
                'status': 'error',
                'error': 'Missing to/projectKeyForSend/subject',
            })
            continue

        try:
            channel_obj.send(project_key, [to_email], subject, body, plain_text=plain_text)
            sent_count += 1
            results.append({
                'recipientKey': recipient_key,
                'to': to_email,
                'projectKeyForSend': project_key,
                'status': 'sent',
            })
        except Exception as exc:
            app.logger.warning("[tools] send failed recipient=%s to=%s: %s", recipient_key, to_email, exc)
            results.append({
                'recipientKey': recipient_key,
                'to': to_email,
                'projectKeyForSend': project_key,
                'status': 'error',
                'error': str(exc),
            })

    app.logger.info(
        "[tools] send campaign=%s channel=%s requested=%s sent=%s total=%s",
        campaign,
        selected_id,
        len(previews),
        sent_count,
        len(results),
    )
    return jsonify({
        'campaign': campaign,
        'channelId': selected_id,
        'requestedCount': len(previews),
        'sentCount': sent_count,
        'results': results,
    })


# ── Code Env Cleaner helpers ──

def _cec_filter_envs(envs):
    """Filter out plugin-managed and DSS-internal environments."""
    return [
        e for e in envs
        if e.get("deploymentMode", "") not in ("PLUGIN_MANAGED", "DSS_INTERNAL")
    ]


def _cec_fetch_env_with_usages(client, env_info):
    """Fetch usage info for a single env and return result dict + timing."""
    env_name = env_info["envName"]
    env_lang = env_info["envLang"]
    t0 = time.time()

    try:
        usages = client._perform_json(
            "GET", "/admin/code-envs/%s/%s/usages" % (env_lang, env_name)
        )
        usage_count = len(usages) if isinstance(usages, list) else 0
    except Exception:
        usages = []
        usage_count = -1

    usage_ms = int((time.time() - t0) * 1000)

    return {
        "envName": env_name,
        "envLang": env_lang,
        "deploymentMode": env_info.get("deploymentMode", ""),
        "owner": env_info.get("owner", ""),
        "pythonInterpreter": env_info.get("pythonInterpreter", ""),
        "usageCount": usage_count,
        "usages": usages if isinstance(usages, list) else [],
    }, usage_ms


@app.route('/api/cache/clear', methods=['POST'])
def api_cache_clear():
    """Clear the in-memory cache so subsequent requests fetch fresh data."""
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE_INFLIGHT_ERRORS.clear()
    _clear_shared_project_code_env_usage()
    _get_sdk_cache().invalidate_all(_instance_id())
    _footprint_reset_negative_cache()
    new_epoch = _bump_session_epoch()
    return jsonify({'ok': True, 'sessionEpoch': new_epoch})


@app.route('/api/session/epoch', methods=['GET'])
def api_session_epoch():
    return jsonify({'sessionEpoch': _get_session_epoch()})


@app.route('/api/managed-folders', methods=['GET'])
def api_managed_folders():
    """List managed folders in the active support project."""
    client = g.client
    project = _active_support_project(client)
    folders = project.list_managed_folders()
    return jsonify({
        'folders': [
            {'id': f['id'], 'name': f.get('name') or f['id']}
            for f in folders
        ]
    })


@app.route('/api/tools/code-env-cleaner/scan')
def api_code_env_cleaner_scan():
    """Stream code env data via SSE for real-time progress."""
    threads = request.args.get("threads", "1", type=str)
    try:
        threads = max(1, min(20, int(threads)))
    except (ValueError, TypeError):
        threads = 1

    def generate():
        t0 = time.time()
        client = g.client

        try:
            all_envs = client._perform_json("GET", "/admin/code-envs/")
        except Exception as e:
            yield "event: error\ndata: %s\n\n" % json.dumps({"error": str(e)})
            return

        filtered = _cec_filter_envs(all_envs)
        list_ms = int((time.time() - t0) * 1000)

        yield "event: init\ndata: %s\n\n" % json.dumps({
            "total": len(filtered),
            "list_ms": list_ms,
            "threads": threads,
        })

        if threads <= 1:
            for i, env_info in enumerate(filtered):
                result, usage_ms = _cec_fetch_env_with_usages(client, env_info)
                result["index"] = i
                result["usage_ms"] = usage_ms
                yield "event: env\ndata: %s\n\n" % json.dumps(result)
        else:
            counter = [0]
            with ThreadPoolExecutor(max_workers=threads) as pool:
                futures = {
                    pool.submit(_cec_fetch_env_with_usages, client, env_info): env_info
                    for env_info in filtered
                }
                for future in as_completed(futures):
                    result, usage_ms = future.result()
                    result["index"] = counter[0]
                    result["usage_ms"] = usage_ms
                    counter[0] += 1
                    yield "event: env\ndata: %s\n\n" % json.dumps(result)

        total_ms = int((time.time() - t0) * 1000)
        yield "event: done\ndata: %s\n\n" % json.dumps({"total_ms": total_ms})

    return Response(stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route('/api/tools/code-env-cleaner/<lang>/<name>', methods=['DELETE'])
@advanced
def api_code_env_cleaner_delete(lang, name):
    """Backup to managed folder then delete a code env after verifying the confirmation header."""
    import tempfile

    confirm = request.headers.get("X-Confirm-Name", "")
    if confirm != name:
        return jsonify({"error": "Confirmation header does not match env name"}), 400

    folder_id = request.args.get("folderId", "").strip()
    if not folder_id:
        return jsonify({"error": "folderId query parameter is required"}), 400

    client = g.client
    project = _active_support_project(client)

    # Validate managed folder exists
    try:
        dest_folder = project.get_managed_folder(folder_id)
        dest_folder.get_definition()  # verify it exists
    except Exception as e:
        app.logger.error("[code-env-cleaner] invalid folder %s: %s", folder_id, e)
        return jsonify({"error": "Invalid managed folder: %s" % str(e)}), 400

    # Fetch the code env definition
    try:
        env_def = client._perform_json("GET", "/admin/code-envs/%s/%s/" % (lang, name))
    except Exception as e:
        app.logger.error("[code-env-cleaner] fetch failed for %s/%s: %s", lang, name, e)
        return jsonify({"error": "Failed to fetch env definition: %s" % str(e)}), 500

    # Backup first — build ZIP to temp file, upload to managed folder
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', name)
    zip_filename = "%s.zip" % safe_name
    try:
        env_lang = lang.lower()
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=True) as tmp:
            with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
                # Directory entries (match DSS on-disk export exactly)
                for d in ["%s/", "%s/spec/", "%s/actual/"]:
                    zf.writestr(zipfile.ZipInfo(d % env_lang), "")
                # desc.json — strip owner (not present in on-disk version)
                desc = dict(env_def.get("desc") or env_def)
                desc.pop("owner", None)
                zf.writestr("%s/desc.json" % env_lang, json.dumps(desc, indent=2))
                # spec/requirements.txt
                zf.writestr("%s/spec/requirements.txt" % env_lang, env_def.get("specPackageList", ""))
                # spec/resources_init.py (field is resourcesInitScript, NOT specResourcesInit)
                zf.writestr("%s/spec/resources_init.py" % env_lang, env_def.get("resourcesInitScript", ""))
                # spec/environment.spec
                zf.writestr("%s/spec/environment.spec" % env_lang, env_def.get("specCondaEnvironment", ""))
                # actual/requirements.txt
                zf.writestr("%s/actual/requirements.txt" % env_lang, env_def.get("actualPackageList", ""))
            # Upload to managed folder
            with open(tmp.name, "rb") as f:
                dest_folder.put_file(zip_filename, f)
    except Exception as e:
        app.logger.error("[code-env-cleaner] backup/upload failed for %s/%s: %s", lang, name, e)
        return jsonify({"error": "Backup upload failed — deletion aborted: %s" % str(e)}), 500

    # Delete code env
    try:
        client._perform_empty("DELETE", "/admin/code-envs/%s/%s/" % (lang, name))
    except Exception as e:
        app.logger.error("[code-env-cleaner] delete failed for %s/%s: %s", lang, name, e)
        return jsonify({"error": "Delete failed (backup saved to managed folder): %s" % str(e)}), 500

    # Invalidate caches so subsequent fetches reflect the deletion
    _cache_pop('code_envs')
    _cache_pop('tools_outreach_data')
    _cache_pop('project_code_env_usage_full')
    _clear_shared_project_code_env_usage()

    app.logger.info("[code-env-cleaner] backed up %s to managed folder %s and deleted %s/%s", zip_filename, folder_id, lang, name)
    return jsonify({"backed_up_to": "managed folder", "zip_name": zip_filename, "deleted": name}), 200


# ── Compute Fabric: container execution scan / replace ────────────────────────

_CEX_CODE_RECIPE_TYPES = {'python', 'r'}
_CEX_NON_CARRIER_RECIPE_TYPES = {'pyspark', 'spark_scala', 'spark_sql_query', 'shell'}


def _cex_path_get(raw: Any, path: str) -> Any:
    current = raw
    for part in path.split('.'):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _cex_path_set(raw: Dict[str, Any], path: str, value: Any) -> None:
    current = raw
    parts = path.split('.')
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _cex_selection(config: Optional[str], mode: str = 'EXPLICIT_CONTAINER') -> Dict[str, Any]:
    if config == '__INHERIT__':
        return {'containerMode': 'INHERIT'}
    if str(mode or '').upper() == 'EXPLICIT_CONTAINER' and config:
        return {'containerMode': 'EXPLICIT_CONTAINER', 'containerConf': config}
    return {'containerMode': mode}


def _cex_effective(selection: Any, fallback: Optional[str]) -> Tuple[str, Optional[str], bool]:
    if not isinstance(selection, dict):
        return 'MISSING', None, False
    mode = str(selection.get('containerMode') or 'INHERIT').upper()
    explicit = selection.get('containerConf')
    if mode == 'EXPLICIT_CONTAINER' and explicit:
        return mode, str(explicit), False
    if mode == 'INHERIT':
        return mode, fallback, True
    return mode, None, False


def _cex_clean_config(config: Dict[str, Any]) -> Dict[str, Any]:
    keys = [
        'name', 'type', 'usableBy', 'allowedGroups', 'workloadType', 'dockerNetwork',
        'kubernetesNamespace', 'repositoryURL', 'baseImageType', 'prePushMode',
        'nodeSelector', 'dockerTLSVerify',
    ]
    return {key: config.get(key) for key in keys if key in config}


def _cex_add_row(rows: List[Dict[str, Any]], **kwargs) -> None:
    selection = kwargs.pop('selection', None)
    fallback_config = kwargs.pop('fallback_config', None)
    inherited_from = kwargs.pop('inherited_from', None)
    mode, effective, inherited = _cex_effective(selection, fallback_config)
    container_conf = str(selection.get('containerConf')) if isinstance(selection, dict) and selection.get('containerConf') else None
    row = {
        'id': '|'.join([
            str(kwargs.get('project_key') or ''),
            str(kwargs.get('object_type') or ''),
            str(kwargs.get('object_id') or ''),
            str(kwargs.get('surface') or ''),
            str(kwargs.get('raw_path') or ''),
        ]),
        'projectKey': kwargs.get('project_key') or '',
        'projectName': kwargs.get('project_name') or kwargs.get('project_key') or '',
        'objectType': kwargs.get('object_type') or '',
        'objectId': kwargs.get('object_id') or '',
        'objectName': kwargs.get('object_name') or kwargs.get('object_id') or '',
        'surface': kwargs.get('surface') or '',
        'surfaceLabel': kwargs.get('surface_label') or kwargs.get('surface') or '',
        'rawPath': kwargs.get('raw_path') or '',
        'containerMode': mode,
        'containerConf': container_conf,
        'effectiveContainerConf': effective,
        'inheritedFrom': inherited_from if inherited else None,
        'writable': bool(kwargs.get('writable')),
        'replacementSupported': bool(kwargs.get('replacement_supported')),
        'notes': kwargs.get('notes') or '',
        'overrideLevel': kwargs.get('override_level') or '',
        'objectSubtype': kwargs.get('object_subtype') or '',
        'projectConfig': kwargs.get('project_config'),
    }
    extra = kwargs.get('extra')
    if isinstance(extra, dict):
        row.update(extra)
    rows.append(row)


def _cex_explicit_config(selection: Any) -> Optional[str]:
    if not isinstance(selection, dict):
        return None
    mode = str(selection.get('containerMode') or 'INHERIT').upper()
    conf = selection.get('containerConf')
    if mode == 'EXPLICIT_CONTAINER' and conf:
        return str(conf)
    return None


def _cex_is_same_config(left: Optional[str], right: Optional[str]) -> bool:
    return bool(left) and bool(right) and str(left) == str(right)


def _cex_is_visible_project_override(selection: Any, global_default: Optional[str]) -> bool:
    conf = _cex_explicit_config(selection)
    return bool(conf) and not _cex_is_same_config(conf, global_default)


def _cex_is_visible_job_override(selection: Any, project_config: Optional[str], global_default: Optional[str]) -> bool:
    conf = _cex_explicit_config(selection)
    if not conf:
        return False
    if _cex_is_same_config(conf, global_default):
        return False
    if _cex_is_same_config(conf, project_config):
        return False
    return True


def _cex_group_project_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        project_key = str(row.get('projectKey') or '')
        if not project_key:
            continue
        group = groups.setdefault(project_key, {
            'projectKey': project_key,
            'projectName': row.get('projectName') or project_key,
            'projectOverrides': [],
            'jobOverrides': [],
        })
        if row.get('overrideLevel') == 'project':
            group['projectOverrides'].append(row)
        elif row.get('overrideLevel') == 'job':
            group['jobOverrides'].append(row)
    return [
        group for group in sorted(groups.values(), key=lambda item: str(item.get('projectKey') or ''))
        if group.get('projectOverrides') or group.get('jobOverrides')
    ]


def _cex_cache_key(project_filter: Optional[set]) -> str:
    if project_filter:
        digest = hashlib.sha1('\n'.join(sorted(project_filter)).encode('utf-8')).hexdigest()
        return f'container_execs:{digest}'
    return 'container_execs'


def _cex_cached_scan(cache_key: str, ttl: int) -> Optional[Dict[str, Any]]:
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE.get(_cache_key(cache_key))
        cached_value = cached.get('value') if cached and now - cached.get('ts', 0) < ttl else None
    return cached_value if isinstance(cached_value, dict) else None


def _cex_execution_config_names(client: Any) -> List[str]:
    try:
        settings = client.get_general_settings().get_raw()
        container_settings = settings.get('containerSettings') if isinstance(settings, dict) else {}
        configs_raw = container_settings.get('executionConfigs') if isinstance(container_settings, dict) else []
        return sorted({str(cfg.get('name')) for cfg in (configs_raw or []) if isinstance(cfg, dict) and cfg.get('name')})
    except Exception:
        return []


def _cex_item_raw(item: Any) -> Dict[str, Any]:
    raw = getattr(item, '_data', item)
    return raw if isinstance(raw, dict) else {}


def _cex_scan(
    client: Any,
    project_keys_filter: Optional[set] = None,
    timeout_ms: Optional[int] = None,
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    started = time.time()
    deadline = started + (float(timeout_ms) / 1000.0) if timeout_ms else None
    timed_out = False
    usage_rows: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    non_carrier_counts: Dict[str, int] = {
        'jupyterNotebooks': 0,
        'sqlNotebooks': 0,
        'scenarios': 0,
        'apiServices': 0,
        'sparkRecipes': 0,
        'shellRecipes': 0,
        'modelEvaluationStores': 0,
        'modelComparisons': 0,
    }

    def event(step: str, message: str, project_key: str = '', level: str = 'info') -> None:
        events.append({
            'tMs': round((time.time() - started) * 1000.0, 2),
            'level': level,
            'step': step,
            'message': message,
            'projectKey': project_key,
        })

    def should_stop(step: str, project_key: str = '') -> bool:
        nonlocal timed_out
        if deadline is None or time.time() <= deadline:
            return False
        if not timed_out:
            timed_out = True
            event('timeout', f'container exec scan exceeded timeoutMs={timeout_ms} at {step}', project_key, 'warn')
        return True

    configs_raw: List[Dict[str, Any]] = []
    global_default = None
    try:
        settings = client.get_general_settings().get_raw()
        container_settings = settings.get('containerSettings') if isinstance(settings, dict) else {}
        if isinstance(container_settings, dict):
            configs_raw = [cfg for cfg in (container_settings.get('executionConfigs') or []) if isinstance(cfg, dict)]
            if container_settings.get('defaultExecutionConfig'):
                global_default = str(container_settings.get('defaultExecutionConfig'))
    except Exception as exc:
        event('general_settings_error', str(exc)[:200], '*', 'warn')

    configs = [_cex_clean_config(cfg) for cfg in configs_raw]
    config_names = sorted({str(cfg.get('name')) for cfg in configs_raw if cfg.get('name')})
    template_default_by_id: Dict[str, Optional[str]] = {}
    try:
        for template_item in client.list_code_studio_templates() or []:
            raw_item = _cex_item_raw(template_item)
            template_id = str(raw_item.get('id') or raw_item.get('templateId') or raw_item.get('name') or '').strip()
            if not template_id:
                continue
            try:
                template_raw = client.get_code_studio_template(template_id).get_settings().get_raw()
            except Exception as exc:
                event('code_studio_template_error', str(exc)[:200], '*', 'warn')
                template_raw = raw_item
            default_conf = template_raw.get('defaultContainerConf') if isinstance(template_raw, dict) else None
            template_default_by_id[template_id] = str(default_conf) if default_conf else None
    except Exception as exc:
        event('code_studio_templates_error', str(exc)[:200], '*', 'warn')

    catalog = _list_projects_catalog_cheap(client)
    if project_keys_filter:
        catalog = [project for project in catalog if project.get('key') in project_keys_filter]

    if progress_cb:
        progress_cb({'event': 'init', 'total': len(catalog)})

    scanned_projects = 0
    for project_meta in catalog:
        if should_stop('project_loop', str(project_meta.get('key') or '')):
            break
        project_key = str(project_meta.get('key') or '')
        project_name = str(project_meta.get('name') or project_key)
        if not project_key:
            continue
        try:
            project = client.get_project(project_key)
            settings_raw = project.get_settings().get_raw()
        except Exception as exc:
            event('project_settings_error', str(exc)[:200], project_key, 'warn')
            scanned_projects += 1
            if progress_cb:
                progress_cb({'event': 'progress', 'scanned': scanned_projects, 'total': len(catalog), 'projectKey': project_key})
            continue

        code_sel = _cex_path_get(settings_raw, 'settings.container')
        visual_sel = _cex_path_get(settings_raw, 'settings.containerForVisualRecipesWorkloads')
        webapp_sel = _cex_path_get(settings_raw, 'settings.virtualWebAppBackendSettings.infra.containerSelection')
        code_mode, code_effective, _ = _cex_effective(code_sel, global_default)
        visual_mode, visual_effective, _ = _cex_effective(visual_sel, global_default)
        webapp_mode, webapp_effective, _ = _cex_effective(webapp_sel, global_default)

        for surface, label, path, selection, mode, notes in (
            ('project_code_default', 'Project code workload default', 'settings.container', code_sel, code_mode, 'Default for Python/R code workloads'),
            ('project_visual_default', 'Project visual recipe default', 'settings.containerForVisualRecipesWorkloads', visual_sel, visual_mode, 'Default for visual recipes using the DSS engine'),
            ('project_webapp_default', 'Project webapp backend default', 'settings.virtualWebAppBackendSettings.infra.containerSelection', webapp_sel, webapp_mode, 'Default for webapp backends'),
        ):
            if mode != 'EXPLICIT_CONTAINER' or not _cex_is_visible_project_override(selection, global_default):
                continue
            _cex_add_row(
                usage_rows,
                project_key=project_key,
                project_name=project_name,
                object_type='PROJECT',
                object_id=project_key,
                object_name=project_name,
                surface=surface,
                surface_label=label,
                raw_path=path,
                selection=selection,
                fallback_config=global_default,
                inherited_from='global default',
                writable=True,
                replacement_supported=True,
                notes=notes,
                override_level='project',
                object_subtype=label,
                project_config=global_default,
            )

        remap = _cex_path_get(settings_raw, 'bundleContainerSettings.remapping')
        if isinstance(remap, dict):
            for idx, item in enumerate(remap.get('containerExecs') or []):
                if not isinstance(item, dict):
                    continue
                for field in ('source', 'target'):
                    conf = item.get(field)
                    if not conf:
                        continue
                    non_carrier_counts['bundleRemaps'] = non_carrier_counts.get('bundleRemaps', 0) + 1

        try:
            recipes = project.list_recipes() or []
        except Exception as exc:
            event('recipes_error', str(exc)[:200], project_key, 'warn')
            recipes = []
        for recipe_item in recipes:
            if not isinstance(recipe_item, dict):
                continue
            recipe_name = str(recipe_item.get('name') or recipe_item.get('id') or '')
            recipe_type = str(recipe_item.get('type') or '').lower()
            if not recipe_name:
                continue
            try:
                recipe_raw = client._perform_json('GET', f'/projects/{project_key}/recipes/{recipe_name}')
                recipe_def = recipe_raw.get('recipe') if isinstance(recipe_raw, dict) else None
            except Exception as exc:
                event('recipe_error', f'{recipe_name}: {exc}'[:200], project_key, 'warn')
                continue
            if not isinstance(recipe_def, dict):
                continue
            if recipe_type in _CEX_CODE_RECIPE_TYPES:
                selection = _cex_path_get(recipe_def, 'params.containerSelection')
                if isinstance(selection, dict):
                    mode, _, _ = _cex_effective(selection, code_effective)
                    if mode != 'EXPLICIT_CONTAINER' or not _cex_is_visible_job_override(selection, code_effective, global_default):
                        continue
                    _cex_add_row(
                        usage_rows,
                        project_key=project_key,
                        project_name=project_name,
                        object_type='RECIPE',
                        object_id=recipe_name,
                        object_name=recipe_name,
                        surface='recipe_code',
                        surface_label='Python/R code recipe',
                        raw_path='recipe.params.containerSelection',
                        selection=selection,
                        fallback_config=code_effective,
                        inherited_from='project code workload default',
                        writable=True,
                        replacement_supported=True,
                        notes=f'{recipe_type} recipe',
                        override_level='job',
                        object_subtype=f'{recipe_type} recipe',
                        project_config=code_effective,
                        extra={'recipeType': recipe_type},
                    )
            elif recipe_type in _CEX_NON_CARRIER_RECIPE_TYPES:
                non_carrier_counts['shellRecipes' if recipe_type == 'shell' else 'sparkRecipes'] += 1

            visual_selection = _cex_path_get(recipe_def, 'params.engineParams.containerSelection')
            if isinstance(visual_selection, dict):
                mode, _, _ = _cex_effective(visual_selection, visual_effective)
                if mode != 'EXPLICIT_CONTAINER' or not _cex_is_visible_job_override(visual_selection, visual_effective, global_default):
                    continue
                _cex_add_row(
                    usage_rows,
                    project_key=project_key,
                    project_name=project_name,
                    object_type='RECIPE',
                    object_id=recipe_name,
                    object_name=recipe_name,
                    surface='recipe_visual',
                    surface_label='Visual recipe',
                    raw_path='recipe.params.engineParams.containerSelection',
                    selection=visual_selection,
                    fallback_config=visual_effective,
                    inherited_from='project visual recipe default',
                    writable=True,
                    replacement_supported=True,
                    notes=f'{recipe_type} recipe using DSS engine',
                    override_level='job',
                    object_subtype=f'{recipe_type} visual recipe',
                    project_config=visual_effective,
                    extra={'recipeType': recipe_type},
                )

        try:
            webapps = project.list_webapps() or []
        except Exception as exc:
            event('webapps_error', str(exc)[:200], project_key, 'warn')
            webapps = []
        for webapp_item in webapps:
            webapp_raw = _cex_item_raw(webapp_item)
            webapp_id = str(webapp_raw.get('id') or '')
            if not webapp_id:
                continue
            try:
                detail = project.get_webapp(webapp_id).get_settings().get_raw()
            except Exception as exc:
                event('webapp_error', f'{webapp_id}: {exc}'[:200], project_key, 'warn')
                continue
            selection = _cex_path_get(detail, 'params.infra.containerSelection')
            if isinstance(selection, dict):
                mode, _, _ = _cex_effective(selection, webapp_effective)
                if mode != 'EXPLICIT_CONTAINER' or not _cex_is_visible_job_override(selection, webapp_effective, global_default):
                    continue
                _cex_add_row(
                    usage_rows,
                    project_key=project_key,
                    project_name=project_name,
                    object_type='WEBAPP',
                    object_id=webapp_id,
                    object_name=str(detail.get('name') or webapp_raw.get('name') or webapp_id),
                    surface='webapp_backend',
                    surface_label='Webapp backend',
                    raw_path='params.infra.containerSelection',
                    selection=selection,
                    fallback_config=webapp_effective,
                    inherited_from='project webapp backend default',
                    writable=True,
                    replacement_supported=True,
                    notes=str(detail.get('type') or webapp_raw.get('type') or 'webapp'),
                    override_level='job',
                    object_subtype=str(detail.get('type') or webapp_raw.get('type') or 'webapp'),
                    project_config=webapp_effective,
                )

        try:
            lab = client._perform_json('GET', f'/projects/{project_key}/models/lab/')
            tasks = lab.get('mlTasks') if isinstance(lab, dict) else []
        except Exception as exc:
            event('ml_tasks_error', str(exc)[:200], project_key, 'warn')
            tasks = []
        for task in tasks or []:
            if not isinstance(task, dict):
                continue
            analysis_id = str(task.get('analysisId') or '')
            task_id = str(task.get('mlTaskId') or '')
            if not analysis_id or not task_id:
                continue
            try:
                task_settings = client._perform_json('GET', f'/projects/{project_key}/models/lab/{analysis_id}/{task_id}/settings')
            except Exception as exc:
                event('ml_task_error', f'{task_id}: {exc}'[:200], project_key, 'warn')
                continue
            selection = task_settings.get('containerSelection') if isinstance(task_settings, dict) else None
            if isinstance(selection, dict):
                mode, _, _ = _cex_effective(selection, code_effective)
                if mode != 'EXPLICIT_CONTAINER' or not _cex_is_visible_job_override(selection, code_effective, global_default):
                    continue
                _cex_add_row(
                    usage_rows,
                    project_key=project_key,
                    project_name=project_name,
                    object_type='ML_TASK',
                    object_id=f'{analysis_id}/{task_id}',
                    object_name=str(task.get('mlTaskName') or task_id),
                    surface='ml_task',
                    surface_label='ML task',
                    raw_path='containerSelection',
                    selection=selection,
                    fallback_config=code_effective,
                    inherited_from='project/container default',
                    writable=True,
                    replacement_supported=True,
                    notes=str(task.get('taskType') or ''),
                    override_level='job',
                    object_subtype=str(task.get('taskType') or 'ML task'),
                    project_config=code_effective,
                    extra={'analysisId': analysis_id, 'mlTaskId': task_id},
                )

        for key, getter in (
            ('jupyterNotebooks', lambda: project.list_jupyter_notebooks(as_type='listitems')),
            ('sqlNotebooks', lambda: project.list_sql_notebooks(as_type='listitems')),
            ('scenarios', lambda: project.list_scenarios()),
            ('apiServices', lambda: project.list_api_services(as_type='listitems')),
            ('modelEvaluationStores', lambda: project.list_model_evaluation_stores()),
            ('modelComparisons', lambda: project.list_model_comparisons()),
        ):
            try:
                non_carrier_counts[key] += len(getter() or [])
            except Exception as exc:
                event(f'{key}_error', str(exc)[:200], project_key, 'warn')

        try:
            studios = project.list_code_studios(as_type='listitems') or []
        except Exception:
            studios = []
        for studio_item in studios:
            studio_raw = _cex_item_raw(studio_item)
            studio_id = str(studio_raw.get('id') or '')
            template_id = str(studio_raw.get('templateId') or '')
            if not studio_id:
                continue
            if template_id and template_default_by_id.get(template_id):
                non_carrier_counts['codeStudioTemplateReferences'] = non_carrier_counts.get('codeStudioTemplateReferences', 0) + 1

        scanned_projects += 1
        if progress_cb:
            progress_cb({'event': 'progress', 'scanned': scanned_projects, 'total': len(catalog), 'projectKey': project_key})

    by_config: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    by_mode: Dict[str, int] = {}
    explicit = supported = 0
    project_override_rows = 0
    job_override_rows = 0
    projects_with_explicit = set()
    for row in usage_rows:
        conf = str(row.get('containerConf') or row.get('effectiveContainerConf') or 'none')
        by_config[conf] = by_config.get(conf, 0) + 1
        typ = str(row.get('objectType') or 'UNKNOWN')
        by_type[typ] = by_type.get(typ, 0) + 1
        mode = str(row.get('containerMode') or 'UNKNOWN')
        by_mode[mode] = by_mode.get(mode, 0) + 1
        explicit += 1 if mode == 'EXPLICIT_CONTAINER' else 0
        supported += 1 if row.get('replacementSupported') else 0
        project_override_rows += 1 if row.get('overrideLevel') == 'project' else 0
        job_override_rows += 1 if row.get('overrideLevel') == 'job' else 0
        if row.get('projectKey'):
            projects_with_explicit.add(str(row.get('projectKey')))

    project_rows = _cex_group_project_rows(usage_rows)
    project_override_count = len([row for row in project_rows if row.get('projectOverrides')])

    scan_errors = [
        {
            'projectKey': str(ev.get('projectKey')),
            'area': str(ev.get('area') or ev.get('step') or 'scan'),
            'error': str(ev.get('message') or ev.get('error') or '')[:240],
        }
        for ev in events
        if ev.get('level') in ('warn', 'error') and ev.get('projectKey') and ev.get('projectKey') != '*'
    ]
    failed_project_count = len({err['projectKey'] for err in scan_errors})

    return {
        'configs': configs,
        'usageRows': usage_rows,
        'projectRows': project_rows,
        'summary': {
            'configCount': len(configs),
            'usageCount': len(usage_rows),
            'explicitUsageCount': explicit,
            'inheritedUsageCount': 0,
            'replacementSupportedCount': supported,
            'projectOverrideCount': project_override_count,
            'projectOverrideRowCount': project_override_rows,
            'jobOverrideCount': job_override_rows,
            'byConfig': by_config,
            'byObjectType': by_type,
            'byMode': by_mode,
            'projectCount': len(catalog),
            'projectUsageCount': len(projects_with_explicit),
        },
        'nonCarrierCounts': non_carrier_counts,
        'events': events[-500:],
        'scanErrors': scan_errors,
        'failedProjectCount': failed_project_count,
        'scannedProjectCount': len(catalog),
        'timedOut': timed_out,
        'elapsedMs': round((time.time() - started) * 1000.0, 2),
        'configNames': config_names,
        'globalDefaultConfig': global_default,
    }


def _cex_replace_project_settings(client: Any, row: Dict[str, Any], target_config: str) -> None:
    settings = client.get_project(row['projectKey']).get_settings()
    raw = settings.get_raw()
    _cex_path_set(raw, str(row['rawPath']), _cex_selection(target_config))
    settings.save()


def _cex_replace_recipe(client: Any, row: Dict[str, Any], target_config: str) -> None:
    project_key = row['projectKey']
    recipe_name = row['objectId']
    raw = client._perform_json('GET', f'/projects/{project_key}/recipes/{recipe_name}')
    path = str(row['rawPath'])
    if path.startswith('recipe.'):
        path = path[len('recipe.'):]
    _cex_path_set(raw.setdefault('recipe', {}), path, _cex_selection(target_config))
    client._perform_json('PUT', f'/projects/{project_key}/recipes/{recipe_name}', body=raw)


def _cex_replace_webapp(client: Any, row: Dict[str, Any], target_config: str) -> None:
    project_key = row['projectKey']
    webapp_id = row['objectId']
    raw = client._perform_json('GET', f'/projects/{project_key}/webapps/{webapp_id}')
    _cex_path_set(raw, str(row['rawPath']), _cex_selection(target_config))
    client._perform_empty('PUT', f'/projects/{project_key}/webapps/{webapp_id}', body=raw)


def _cex_try_private_mltask_save(
    browser_ctx: Optional[Dict[str, Any]],
    project_key: str,
    analysis_id: str,
    mltask_settings: Dict[str, Any],
    diag: Dict[str, Any],
) -> Tuple[bool, Optional[str]]:
    """Attempt POST /dip/api/analysis/cml/save-settings using forwarded browser session.

    Populates `diag['privateAttempt']` with verbose info regardless of outcome.
    Returns (ok, error_message).
    """
    import requests as _rq

    ctx = browser_ctx or {}
    origin = str(ctx.get('origin') or '').rstrip('/')
    cookie_header = str(ctx.get('cookie_header') or '')
    xsrf = str(ctx.get('xsrf') or '')
    referer = str(ctx.get('referer') or '')

    attempt = {
        'originLen': len(origin),
        'origin': origin if len(origin) < 100 else origin[:97] + '...',
        'cookieHeaderLen': len(cookie_header),
        'cookieCount': cookie_header.count(';') + 1 if cookie_header else 0,
        'cookieNames': ctx.get('cookie_names', []),
        'xsrfPresent': bool(xsrf),
        'xsrfLen': len(xsrf),
        'xsrfSource': ctx.get('xsrf_source') or '',
        'referer': referer if len(referer) < 120 else referer[:117] + '...',
    }
    diag['privateAttempt'] = attempt

    if not origin or not cookie_header or not xsrf:
        attempt['skipped'] = 'missing browser context (origin/cookies/xsrf)'
        return False, attempt['skipped']

    url = f"{origin}/dip/api/analysis/cml/save-settings"
    headers = {
        'Cookie': cookie_header,
        'x-xsrf-token': xsrf,
        'Accept': 'application/json',
        'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
    }
    body = {
        'projectKey': project_key,
        'analysisId': analysis_id,
        'mlTask': json.dumps(mltask_settings),
    }
    attempt['url'] = url
    attempt['bodyFields'] = sorted(body.keys())
    attempt['mlTaskBodyLen'] = len(body['mlTask'])

    try:
        r = _rq.post(url, data=body, headers=headers, verify=False, timeout=30)
        attempt['status'] = r.status_code
        attempt['responseLen'] = len(r.text or '')
        attempt['responseSnippet'] = (r.text or '')[:400]
        if 200 <= r.status_code < 300:
            return True, None
        return False, f"HTTP {r.status_code}: {(r.text or '')[:200]}"
    except Exception as e:
        attempt['exception'] = str(e)[:300]
        return False, str(e)[:300]


def _cex_replace_ml_task(
    client: Any,
    row: Dict[str, Any],
    target_config: str,
    browser_ctx: Optional[Dict[str, Any]] = None,
    diag: Optional[Dict[str, Any]] = None,
) -> None:
    # Public API (POST /projects/{pk}/models/lab/{aid}/{tid}/settings) NPEs for
    # ML tasks that were never fully designed (no preprocessingParams). The DSS
    # UI uses the private endpoint with the user's session cookies, so we do
    # the same with the forwarded browser context.
    project_key = row['projectKey']
    analysis_id = row.get('analysisId')
    task_id = row.get('mlTaskId')
    if not analysis_id or not task_id:
        parts = str(row.get('objectId') or '').split('/', 1)
        if len(parts) == 2:
            analysis_id, task_id = parts
    if not analysis_id or not task_id:
        raise ValueError('Missing ML task identifiers')

    raw = client._perform_json(
        'GET', f'/projects/{project_key}/models/lab/{analysis_id}/{task_id}/settings'
    )
    _cex_path_set(raw, str(row['rawPath']), _cex_selection(target_config))

    if diag is None:
        diag = {}
    diag['projectKey'] = project_key
    diag['analysisId'] = analysis_id
    diag['taskId'] = task_id
    diag['settingsTopKeys'] = sorted(raw.keys())
    diag['containerSelection'] = raw.get('containerSelection')

    ok, err = _cex_try_private_mltask_save(browser_ctx, project_key, analysis_id, raw, diag)
    try:
        app.logger.info(
            "[cex:mltask] pk=%s aid=%s tid=%s save=%s",
            project_key, analysis_id, task_id, 'ok' if ok else 'failed',
        )
    except Exception:
        pass
    if not ok:
        raise RuntimeError(f"ML task save failed: {err}")


def _cex_replace_code_studio_template(client: Any, row: Dict[str, Any], target_config: str) -> None:
    template_id = str(row.get('templateId') or row.get('objectId') or '')
    if not template_id:
        raise ValueError('Missing Code Studio template id')
    settings = client.get_code_studio_template(template_id).get_settings()
    raw = settings.get_raw()
    raw_path = str(row.get('rawPath') or '')
    if raw_path == 'defaultContainerConf':
        raw['defaultContainerConf'] = target_config
    elif raw_path.startswith('containerConfs['):
        idx = int(row.get('listIndex'))
        raw.setdefault('containerConfs', [])[idx] = target_config
    else:
        raise ValueError(f'Unsupported template raw path: {raw_path}')
    settings.save()


def _cex_replace_bundle_remap(client: Any, row: Dict[str, Any], target_config: str) -> None:
    settings = client.get_project(row['projectKey']).get_settings()
    raw = settings.get_raw()
    idx = int(row.get('listIndex'))
    field = str(row.get('listField') or '')
    items = _cex_path_get(raw, 'bundleContainerSettings.remapping.containerExecs')
    if not isinstance(items, list) or idx >= len(items) or not isinstance(items[idx], dict):
        raise ValueError('Bundle remap row no longer exists')
    items[idx][field] = target_config
    settings.save()


def _cex_apply_replace_row(
    client: Any,
    row: Dict[str, Any],
    target_config: str,
    browser_ctx: Optional[Dict[str, Any]] = None,
    diag: Optional[Dict[str, Any]] = None,
) -> None:
    surface = str(row.get('surface') or '')
    if surface.startswith('project_'):
        return _cex_replace_project_settings(client, row, target_config)
    if surface in ('recipe_code', 'recipe_visual'):
        return _cex_replace_recipe(client, row, target_config)
    if surface == 'webapp_backend':
        return _cex_replace_webapp(client, row, target_config)
    if surface == 'ml_task':
        return _cex_replace_ml_task(client, row, target_config, browser_ctx=browser_ctx, diag=diag)
    if surface.startswith('code_studio_template_'):
        return _cex_replace_code_studio_template(client, row, target_config)
    if surface == 'bundle_remapping':
        return _cex_replace_bundle_remap(client, row, target_config)
    raise ValueError(f'Unsupported replacement surface: {surface}')


@app.route('/api/container-execs')
def api_container_execs():
    client = g.client
    project_keys_arg = request.args.get('projectKeys', '').strip()
    project_filter = {part.strip() for part in project_keys_arg.split(',') if part.strip()} if project_keys_arg else None

    def loader():
        timeout_ms = int(_BACKEND_SETTINGS.get('container_exec_timeout_ms', 600000))
        return _cex_scan(client, project_keys_filter=project_filter, timeout_ms=timeout_ms)

    cache_key = _cex_cache_key(project_filter)
    data = _cache_get(cache_key, _BACKEND_SETTINGS.get('cache_ttl_projects', 600), loader)
    return jsonify(data)


@app.route('/api/container-execs/stream')
def api_container_execs_stream():
    project_keys_arg = request.args.get('projectKeys', '').strip()
    project_filter = {part.strip() for part in project_keys_arg.split(',') if part.strip()} if project_keys_arg else None
    cache_key = _cex_cache_key(project_filter)
    ttl = int(_BACKEND_SETTINGS.get('cache_ttl_projects', 600))

    def sse(event_name: str, payload: Dict[str, Any]) -> str:
        return "event: %s\ndata: %s\n\n" % (event_name, json.dumps(payload))

    # Hoist client and host_id out of the SSE generator so the worker thread
    # captures them by closure. `g` is request-scoped and is NOT available
    # inside a threading.Thread spawned by the request handler.
    request_client = g.client
    request_host_id = getattr(g, 'host_id', 'local')

    def generate():
        cached_value = _cex_cached_scan(cache_key, ttl)
        if cached_value is not None:
            total = ((cached_value.get('summary') or {}).get('projectCount') or 0) if isinstance(cached_value, dict) else 0
            yield sse('init', {'total': total, 'cached': True})
            yield sse('done', cached_value)
            return

        events_q: "queue.Queue[Dict[str, Any]]" = queue.Queue()

        def progress_cb(payload: Dict[str, Any]) -> None:
            events_q.put(dict(payload))

        def worker() -> None:
            previous_host_id = getattr(_THREAD_LOCAL, 'host_id', None)
            _THREAD_LOCAL.host_id = request_host_id
            try:
                # Captured from the enclosing request context — DO NOT touch g here.
                client = request_client
                timeout_ms = int(_BACKEND_SETTINGS.get('container_exec_timeout_ms', 600000))
                result = _cex_scan(
                    client,
                    project_keys_filter=project_filter,
                    timeout_ms=timeout_ms,
                    progress_cb=progress_cb,
                )
                with _CACHE_LOCK:
                    _CACHE[_cache_key(cache_key)] = {'ts': time.time(), 'value': result}
                events_q.put({'event': 'done', 'payload': result})
            except Exception as exc:
                events_q.put({'event': 'error', 'error': str(exc)[:500]})
            finally:
                if previous_host_id is None:
                    try:
                        delattr(_THREAD_LOCAL, 'host_id')
                    except AttributeError:
                        pass
                else:
                    _THREAD_LOCAL.host_id = previous_host_id

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        while True:
            item = events_q.get()
            event_name = str(item.pop('event', 'progress'))
            if event_name == 'done':
                yield sse('done', item.get('payload') if isinstance(item.get('payload'), dict) else {})
                break
            yield sse(event_name, item)
            if event_name == 'error':
                break

    return Response(stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


# ---------- K8S Insights ---------- #

_K8S_INSIGHTS_PROBE_NAMES = [
    'probe_pods', 'probe_nodes', 'probe_daemonsets', 'probe_replicasets',
    'probe_deployments_all', 'probe_deployments_kubesystem', 'probe_pdbs',
    'probe_events', 'probe_top_pods', 'probe_top_nodes',
    'probe_kubectl_version', 'probe_dss_general_settings',
    'probe_managed_cluster_dir', 'probe_eks_plugin_gpu_driver',
]


@app.route('/api/k8s-insights/clusters')
def api_k8s_insights_clusters():
    """List clusters available for audit on the active host.

    "Available" means: registered in DSS (so orphan filesystem dirs from
    deleted clusters are dropped) AND currently has a kubeconfig file on the
    host (DSS writes that file when a cluster is "started" and removes it
    when stopped, so kubeconfig presence ≈ "turned on").
    """
    client = g.client
    try:
        data = _k8s_insights_macro(client, operation='list-clusters')
    except MacroProjectMissing:
        raise
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:200]}'}), 502

    # Cross-reference with DSS's cluster registry to drop orphan FS dirs and
    # enrich with state/type/architecture for the UI.
    dss_by_id: Dict[str, Dict[str, Any]] = {}
    dss_error: Optional[str] = None
    try:
        for c in (client.list_clusters() or []):
            cid = c.get('id') if isinstance(c, dict) else None
            if cid:
                dss_by_id[cid] = c
    except Exception as exc:
        dss_error = f'{type(exc).__name__}: {str(exc)[:200]}'

    fs_clusters = data.get('clusters') or []
    fs_by_id = {fc.get('id'): fc for fc in fs_clusters if fc.get('id')}
    available: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []
    # Iterate by DSS-registry membership when possible (so we surface DSS-known
    # clusters that don't have a FS dir yet). Fall back to FS-only listing.
    candidate_ids = list(dss_by_id.keys()) if dss_by_id else list(fs_by_id.keys())
    for cid in candidate_ids:
        fc = fs_by_id.get(cid) or {'id': cid, 'hasKubeconfig': False}
        dss_meta = dss_by_id.get(cid) or {}
        state = dss_meta.get('state')
        is_available = bool(fc.get('hasKubeconfig')) or state == 'RUNNING'
        entry = {
            **fc,
            'id': cid,
            'state': state,
            'type': dss_meta.get('type'),
            'architecture': dss_meta.get('architecture'),
            'name': dss_meta.get('name') or cid,
        }
        if is_available:
            available.append(entry)
        else:
            diagnostics.append({
                'id': cid,
                'state': state,
                'type': dss_meta.get('type'),
                'hasKubeconfig': bool(fc.get('hasKubeconfig')),
                'baseDir': fc.get('baseDir'),
                'dirFiles': fc.get('dirFiles') or [],
            })

    return jsonify({
        **data,
        'clusters': available,
        'unavailable': diagnostics,
        'totalDiscovered': len(fs_clusters),
        'dssRegistryError': dss_error,
    })


@app.route('/api/k8s-insights/clusters/health')
def api_k8s_insights_clusters_health():
    """Parallel `kubectl version` probe across every DSS-known cluster.

    Used by the picker to render per-cluster health dots without forcing the
    user to run a full audit just to discover that an attachment is stale.
    """
    client = g.client
    try:
        data = _k8s_insights_macro(client, operation='cluster-health')
    except MacroProjectMissing:
        raise
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:200]}', 'clusters': []}), 502
    return jsonify(data)


@app.route('/api/k8s-insights/pod-describe')
def api_k8s_insights_pod_describe():
    """`kubectl describe pod <name> -n <ns>` for one pod on the audited cluster.

    Returns the raw describe output as text/plain so the UI renders it verbatim
    in a <pre> via `fetchText`; failures surface as a non-2xx whose body carries
    the reason. The host-bound kubectl call runs inside the K8S Insights macro.
    """
    cluster_id = (request.args.get('clusterId') or '').strip()
    namespace = (request.args.get('ns') or '').strip()
    pod_name = (request.args.get('name') or '').strip()
    if not cluster_id or not namespace or not pod_name:
        return jsonify({'ok': False, 'error': 'clusterId, ns and name are required'}), 400
    client = g.client
    try:
        data = _k8s_insights_macro(
            client,
            operation='describe-pod',
            cluster_id=cluster_id,
            namespace=namespace,
            pod_name=pod_name,
        )
    except MacroProjectMissing:
        raise
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:200]}'}), 502
    if not data.get('ok'):
        return jsonify({'ok': False, 'error': data.get('error') or 'describe failed'}), 502
    return Response(data.get('text') or '', mimetype='text/plain; charset=utf-8')


@app.route('/api/k8s-insights/stream')
def api_k8s_insights_stream():
    """SSE wrapper around the K8S Insights macro.

    The macro itself is synchronous (probes are run server-side in parallel,
    then rules evaluate), but we surface progress events as best we can:
      init  -> {clusterId, totalProbes}
      probe -> {name, ok, durationMs} (synthesized from result.probes)
      done  -> full payload
    """
    cluster_id = (request.args.get('clusterId') or '').strip()
    rules_filter = (request.args.get('rulesFilter') or '').strip()
    request_client = g.client
    request_host_id = getattr(g, 'host_id', 'local')

    def sse(event_name: str, payload: Dict[str, Any]) -> str:
        return "event: %s\ndata: %s\n\n" % (event_name, json.dumps(payload))

    def generate():
        events_q: "queue.Queue[Dict[str, Any]]" = queue.Queue()

        def worker() -> None:
            previous_host_id = getattr(_THREAD_LOCAL, 'host_id', None)
            _THREAD_LOCAL.host_id = request_host_id
            try:
                result = _k8s_insights_macro(
                    request_client,
                    operation='audit',
                    cluster_id=cluster_id,
                    rules_filter=rules_filter,
                )
                # Synthesize probe-progress events from the result for the UI.
                probes_summary = (result.get('probes') or {}) if isinstance(result, dict) else {}
                for name in _K8S_INSIGHTS_PROBE_NAMES:
                    p = probes_summary.get(name) or {}
                    events_q.put({'event': 'probe', 'payload': {
                        'name': name,
                        'ok': bool(p.get('ok')),
                        'error': p.get('error'),
                        'durationMs': int(p.get('durationMs') or 0),
                    }})
                events_q.put({'event': 'done', 'payload': result})
            except MacroProjectMissing:
                events_q.put({'event': 'error', 'payload': {'error': 'macro-project-missing'}})
            except Exception as exc:
                events_q.put({'event': 'error', 'payload': {'error': f'{type(exc).__name__}: {str(exc)[:500]}'}})
            finally:
                if previous_host_id is None:
                    try:
                        delattr(_THREAD_LOCAL, 'host_id')
                    except AttributeError:
                        pass
                else:
                    _THREAD_LOCAL.host_id = previous_host_id

        yield sse('init', {'clusterId': cluster_id, 'totalProbes': len(_K8S_INSIGHTS_PROBE_NAMES)})
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        while True:
            item = events_q.get()
            event_name = str(item.get('event') or 'progress')
            payload = item.get('payload') or {}
            yield sse(event_name, payload if isinstance(payload, dict) else {'payload': payload})
            if event_name in ('done', 'error'):
                break

    return Response(stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route('/api/container-execs/replace', methods=['POST'])
@advanced
def api_container_execs_replace():
    payload = request.get_json(silent=True) or {}
    source_config = str(payload.get('sourceConfig') or '').strip()
    target_config = str(payload.get('targetConfig') or '').strip()
    dry_run = bool(payload.get('dryRun', True))
    if not source_config or not target_config:
        return jsonify({'error': 'sourceConfig and targetConfig are required'}), 400
    if source_config == target_config:
        return jsonify({'error': 'sourceConfig and targetConfig must differ'}), 400
    project_keys = payload.get('projectKeys')
    object_types = payload.get('objectTypes')
    project_filter = {str(pk).strip() for pk in project_keys if str(pk).strip()} if isinstance(project_keys, list) else None
    type_filter = {str(t).strip().upper() for t in object_types if str(t).strip()} if isinstance(object_types, list) else None

    target_is_inherit = target_config == '__INHERIT__'
    client = g.client
    _dss_xsrf_cookie = next(
        (name for name in request.cookies.keys() if name.startswith('dss_xsrf_token_')),
        '',
    )
    browser_ctx = {
        'origin': request.headers.get('Origin') or '',
        'referer': request.headers.get('Referer') or '',
        'cookie_header': request.headers.get('Cookie') or '',
        'cookie_names': sorted(request.cookies.keys()),
        'xsrf': request.cookies.get(_dss_xsrf_cookie, '') if _dss_xsrf_cookie else '',
        'xsrf_source': _dss_xsrf_cookie,
    }
    cheap_config_names = set(_cex_execution_config_names(client))
    if not target_is_inherit and cheap_config_names and target_config not in cheap_config_names:
        return jsonify({
            'error': f'Unknown targetConfig: {target_config}',
            'validConfigNames': sorted(cheap_config_names),
        }), 400

    ttl = int(_BACKEND_SETTINGS.get('cache_ttl_projects', 600))
    cache_key = _cex_cache_key(project_filter)
    scan = _cex_cached_scan(cache_key, ttl)
    scan_cached = scan is not None
    if scan is None:
        scan = _cex_scan(
            client,
            project_keys_filter=project_filter,
            timeout_ms=int(_BACKEND_SETTINGS.get('container_exec_timeout_ms', 600000)),
        )
        with _CACHE_LOCK:
            _CACHE[_cache_key(cache_key)] = {'ts': time.time(), 'value': scan}

    config_names = set(scan.get('configNames') or [])
    if not target_is_inherit and target_config not in config_names:
        return jsonify({
            'error': f'Unknown targetConfig: {target_config}',
            'validConfigNames': sorted(config_names),
            'scanCached': scan_cached,
        }), 400

    visible_source_configs = {
        str(row.get('containerConf') or '')
        for row in (scan.get('usageRows') or [])
        if isinstance(row, dict)
        and row.get('containerMode') == 'EXPLICIT_CONTAINER'
        and row.get('replacementSupported')
        and row.get('containerConf')
    }
    if source_config not in config_names and source_config not in visible_source_configs:
        return jsonify({
            'error': f'Source config is not a current config and is not present in explicit replaceable overrides: {source_config}',
            'validConfigNames': sorted(config_names),
            'visibleSourceConfigs': sorted(visible_source_configs),
            'scanCached': scan_cached,
        }), 400

    matched = []
    for row in scan.get('usageRows') or []:
        if not isinstance(row, dict):
            continue
        if type_filter and str(row.get('objectType') or '').upper() not in type_filter:
            continue
        if row.get('containerMode') != 'EXPLICIT_CONTAINER':
            continue
        if row.get('containerConf') != source_config:
            continue
        if not row.get('replacementSupported'):
            continue
        if target_is_inherit:
            surface = str(row.get('surface') or '')
            if surface.startswith('code_studio_template_') or surface == 'bundle_remapping':
                continue
        matched.append(row)

    results: List[Dict[str, Any]] = []
    for row in matched:
        result = {
            'rowId': row.get('id'),
            'projectKey': row.get('projectKey'),
            'objectType': row.get('objectType'),
            'objectId': row.get('objectId'),
            'objectName': row.get('objectName'),
            'surface': row.get('surface'),
            'rawPath': row.get('rawPath'),
            'from': source_config,
            'to': target_config,
            'status': 'planned' if dry_run else 'updated',
        }
        if not dry_run:
            row_diag = {} if str(row.get('surface') or '') == 'ml_task' else None
            try:
                _cex_apply_replace_row(client, row, target_config, browser_ctx=browser_ctx, diag=row_diag)
            except Exception as exc:
                result['status'] = 'failed'
                result['error'] = str(exc)[:500]
            if row_diag is not None:
                result['diag'] = row_diag
        results.append(result)

    if not dry_run:
        _cache_pop_matching(lambda key_text: str(key_text).startswith('container_execs'))
        _bump_session_epoch()

    return jsonify({
        'dryRun': dry_run,
        'sourceConfig': source_config,
        'targetConfig': target_config,
        'scanCached': scan_cached,
        'matchedRows': len(matched),
        'updatedRows': len([r for r in results if r.get('status') == 'updated']),
        'skippedRows': 0,
        'failedRows': len([r for r in results if r.get('status') == 'failed']),
        'results': results,
    })


@app.route('/api/tools/inactive-projects', methods=['GET'])
def api_tools_inactive_projects():
    """List inactive projects using lastModifiedOn derived from a per-project git-log walk (via _list_projects_catalog) for edit-accurate timestamps; cached."""
    from datetime import datetime, timezone

    def _load():
        client = g.client
        catalog = _list_projects_catalog(client)
        inactive_threshold_days = _outreach_thresholds.get('inactive_project_days', 180)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        results = []
        for entry in catalog:
            last_modified_ms = entry.get('lastModifiedOn')
            if last_modified_ms is None:
                continue
            try:
                days_inactive = (now_ms - int(last_modified_ms)) / (1000 * 60 * 60 * 24)
            except (TypeError, ValueError):
                continue
            if days_inactive < inactive_threshold_days:
                continue
            results.append({
                'projectKey': entry['key'],
                'name': entry.get('name', entry['key']),
                'owner': entry.get('owner', 'Unknown'),
                'daysInactive': round(days_inactive),
            })
        return {'projects': results}

    data = _cache_get('inactive_projects', _BACKEND_SETTINGS['cache_ttl_inactive'], _load)
    return jsonify(data)


@app.route('/api/tools/project-cleaner/<project_key>', methods=['DELETE'])
@advanced
def api_project_cleaner_delete(project_key):
    """Backup to managed folder then delete an inactive project after verifying the confirmation header."""
    import tempfile

    confirm = request.headers.get("X-Confirm-Name", "")
    if confirm != project_key:
        return jsonify({"error": "Confirmation header does not match project key"}), 400

    folder_id = request.args.get("folderId", "").strip()
    if not folder_id:
        return jsonify({"error": "folderId query parameter is required"}), 400

    client = g.client
    plugin_project = _active_support_project(client)

    # Validate managed folder exists
    try:
        dest_folder = plugin_project.get_managed_folder(folder_id)
        dest_folder.get_definition()  # verify it exists
    except Exception as e:
        app.logger.error("[project-cleaner] invalid folder %s: %s", folder_id, e)
        return jsonify({"error": "Invalid managed folder: %s" % str(e)}), 400

    target_project = client.get_project(project_key)

    # Backup first — export to temp file, upload to managed folder
    safe_key = re.sub(r'[^a-zA-Z0-9._-]', '_', project_key)
    zip_filename = "%s.zip" % safe_key
    try:
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=True) as tmp:
            target_project.export_to_file(tmp.name)
            with open(tmp.name, "rb") as f:
                dest_folder.put_file(zip_filename, f)
    except Exception as e:
        app.logger.error("[project-cleaner] backup/upload failed for %s: %s", project_key, e)
        return jsonify({"error": "Backup upload failed — deletion aborted: %s" % str(e)}), 500

    # Delete project
    try:
        target_project.delete()
    except Exception as e:
        app.logger.error("[project-cleaner] delete failed for %s: %s", project_key, e)
        return jsonify({"error": "Delete failed (backup saved to managed folder): %s" % str(e)}), 500

    # Invalidate caches
    _cache_pop('tools_outreach_data')
    _cache_pop('inactive_projects')

    app.logger.info("[project-cleaner] backed up %s to managed folder %s and deleted %s", zip_filename, folder_id, project_key)
    return jsonify({"backed_up_to": "managed folder", "zip_name": zip_filename, "deleted": project_key}), 200


@app.route('/api/tools/plugins/compare', methods=['POST'])
def api_tools_plugins_compare():
    """Compare local (Design) plugins with a remote host configured as a preset."""
    payload = request.get_json(silent=True) or {}
    target_host_id = (payload.get('targetHostId') or '').strip()
    if not target_host_id or target_host_id == 'local':
        return jsonify({"error": "targetHostId is required and must reference a remote-dss-host preset"}), 400

    cfg = _remote_host_config(target_host_id)
    if cfg is None:
        return jsonify({"error": "invalid-host-id", "hostId": target_host_id}), 400

    try:
        local_client = g.client
        local_plugins_raw = local_client.list_plugins()
    except Exception as e:
        return jsonify({"error": "Failed to fetch local plugins: %s" % str(e)}), 500

    try:
        remote_client = _build_remote_client(cfg)
        remote_plugins_raw = remote_client.list_plugins()
    except Exception as e:
        return jsonify({"error": "Failed to fetch remote plugins: %s" % str(e)}), 500

    def _parse_plugins(raw_list):
        out = {}
        for p in raw_list:
            if isinstance(p, dict):
                meta = p.get('meta') or {}
                pid = p.get('id') or p.get('name') or meta.get('label')
                if not pid:
                    continue
                out[pid] = {
                    'label': meta.get('label') or pid,
                    'version': p.get('version'),
                    'isDev': bool(p.get('isDev', False)),
                }
            else:
                pid = str(p)
                if pid:
                    out[pid] = {'label': pid, 'version': None, 'isDev': False}
        return out

    local_map = _parse_plugins(local_plugins_raw)
    remote_map = _parse_plugins(remote_plugins_raw)
    all_ids = sorted(set(list(local_map.keys()) + list(remote_map.keys())))

    rows = []
    for pid in all_ids:
        local = local_map.get(pid)
        remote = remote_map.get(pid)
        rows.append({
            'id': pid,
            'label': (local or remote or {}).get('label', pid),
            'localVersion': local['version'] if local else None,
            'remoteVersion': remote['version'] if remote else None,
            'isDev': (local or {}).get('isDev', False),
        })

    return jsonify({"rows": rows})


@app.route('/api/tools/plugins/deploy-one', methods=['POST'])
@advanced
def api_tools_plugins_deploy_one():
    body = request.get_json(force=True) or {}
    target_host_id = (body.get('targetHostId') or '').strip()
    plugin_id = (body.get('pluginId') or '').strip()

    if not target_host_id or target_host_id == 'local' or not plugin_id:
        return jsonify({"error": "targetHostId (remote preset) and pluginId are required"}), 400

    cfg = _remote_host_config(target_host_id)
    if cfg is None:
        return jsonify({"error": "invalid-host-id", "hostId": target_host_id}), 400

    local_client = g.client
    remote_client = _build_remote_client(cfg)

    # Strategy 1: dev plugin → download stream and upload archive
    try:
        stream = local_client.download_plugin_stream(plugin_id)
        remote_client.install_plugin_from_archive(stream)
        return jsonify({"ok": True, "method": "archive"})
    except Exception as e:
        dev_error = str(e)

    # Strategy 2: non-dev (store) plugin → install from store on remote
    try:
        remote_client.install_plugin_from_store(plugin_id)
        return jsonify({"ok": True, "method": "store"})
    except Exception as e:
        store_error = str(e)

    return jsonify({
        "error": "Failed to deploy plugin '%s'. Archive: %s | Store: %s" % (plugin_id, dev_error, store_error)
    }), 500


def _scan_plugin_usages(client: Any, plugin_id: str) -> Dict[str, Any]:
    """Fetch + summarize usages for one plugin. Returns the fields to merge
    into the pluginDetails row (projectsUsingCount/projectsUsing/missingTypes
    or usagesError on failure)."""
    raw = client.get_plugin(plugin_id).list_usages().get_raw() or {}
    usages = raw.get('usages') or []
    missing_raw = raw.get('missingTypes') or []

    per_project: Dict[str, Dict[str, Any]] = {}
    for u in usages:
        if not isinstance(u, dict):
            continue
        pk = u.get('projectKey') or ''
        if not pk:
            continue
        kind = u.get('elementKind') or ''
        bucket = per_project.setdefault(pk, {
            'projectKey': pk,
            'elementKinds': {},
            'objects': [],
        })
        if kind:
            bucket['elementKinds'][kind] = bucket['elementKinds'].get(kind, 0) + 1
        bucket['objects'].append({
            'elementKind': kind,
            'elementType': u.get('elementType') or '',
            'objectType': u.get('objectType') or '',
            'objectId': u.get('objectId') or '',
        })

    grouped = list(per_project.values())
    grouped.sort(key=lambda g_: (-len(g_['objects']), g_['projectKey']))
    grouped = grouped[:50]

    missing_types = []
    for m in missing_raw:
        if not isinstance(m, dict):
            continue
        missing_types.append({
            'missingType': m.get('missingType') or '',
            'objectType': m.get('objectType') or '',
            'projectKey': m.get('projectKey') or '',
            'objectId': m.get('objectId') or '',
        })

    return {
        'projectsUsingCount': len(per_project),
        'projectsUsing': grouped,
        'missingTypes': missing_types,
    }


def _latest_store_plugin_versions(client: Any) -> Dict[str, str]:
    """Map of plugin id -> latest store version, for the plugin-currency column.

    Mirrors the public snippet: fetch the store catalog for the active host's DSS
    major version from update.dataiku.com and key each item's storeVersion by id.
    Best-effort: on any failure (network, parse, unknown version) returns {} so the
    plugins endpoint still loads, just without a Latest column. The DSS major is
    read the same way as _image_cleaner_release_info, falling back to "14"."""
    import requests

    out: Dict[str, str] = {}
    try:
        if _safe_request_host_id() != 'local':
            metrics = _cache_get(
                'host_metrics',
                _BACKEND_SETTINGS['cache_ttl_overview'],
                lambda: _host_metrics_macro(client),
            )
            version_info = metrics.get('version') if isinstance(metrics, dict) else {}
        else:
            version_info = _safe_read_json(os.path.join(_dip_home(), 'dss-version.json')) or {}
        version_info = version_info or {}
        version = (
            version_info.get('product_version')
            or version_info.get('version')
            or version_info.get('dssVersion')
        )
        major = str(version or '').split('.')[0]
        dataiku_version = major if major.isdigit() else '14'

        url = f'https://update.dataiku.com/dss/{dataiku_version}/plugins/list.json'
        resp = requests.get(
            url,
            headers={'Content-Type': 'application/json'},
            verify=True,
            timeout=(3, 10),
        )
        resp.raise_for_status()
        for item in (resp.json().get('items') or []):
            if isinstance(item, dict):
                pid = item.get('id')
                store_version = item.get('storeVersion')
                if pid and store_version:
                    out[str(pid)] = str(store_version)
    except Exception as exc:
        app.logger.warning("[plugins] latest store-version fetch failed: %s", exc)
    return out


@app.route('/api/plugins')
def api_plugins():
    client = g.client

    def loader():
        plugins = []
        plugin_details = []
        _all_plugins = _sdk_fetch(
            'list_plugins',
            _BACKEND_SETTINGS['cache_ttl_overview'],
            lambda: list(client.list_plugins()),
        )
        for p in _all_plugins:
            if isinstance(p, dict):
                meta = p.get('meta') or {}
                pid = p.get('id') or p.get('name') or meta.get('label')
                if not pid:
                    continue
                plugins.append(pid)
                plugin_details.append({
                    'id': pid,
                    'label': meta.get('label') or pid,
                    'installedVersion': p.get('version'),
                    'isDev': bool(p.get('isDev', False)),
                })
            else:
                pid = str(p)
                if pid:
                    plugins.append(pid)
                    plugin_details.append({'id': pid, 'label': pid})
        plugins.sort()
        plugin_details.sort(key=lambda d: d.get('id', ''))

        # Plugin currency: latest store version per plugin id (best-effort, cached
        # separately so a short plugins-cache TTL doesn't refetch the store catalog).
        latest_versions = _cache_get(
            'plugin_store_versions',
            _BACKEND_SETTINGS['cache_ttl_overview'],
            lambda: _latest_store_plugin_versions(client),
        )
        for row in plugin_details:
            latest = latest_versions.get(row.get('id') or '')
            if latest:
                row['latestVersion'] = latest

        return {'plugins': plugins, 'pluginDetails': plugin_details, 'pluginsCount': len(plugins)}

    data = _cache_get('plugins', _BACKEND_SETTINGS['cache_ttl_plugins'], loader)
    return jsonify(data)


@app.route('/api/plugins/usages')
def api_plugins_usages():
    """Per-plugin usage scan, split out of /api/plugins so the cheap plugin list
    (names/versions) loads fast and these expensive get_plugin().list_usages()
    fan-outs (2 chained DSS calls x N plugins) fill in asynchronously. Returns a
    map keyed by plugin id; the frontend merges it into the pluginDetails rows."""
    client = g.client

    def loader():
        _all_plugins = _sdk_fetch(
            'list_plugins',
            _BACKEND_SETTINGS['cache_ttl_overview'],
            lambda: list(client.list_plugins()),
        )
        pids = []
        for p in _all_plugins:
            if isinstance(p, dict):
                meta = p.get('meta') or {}
                pid = p.get('id') or p.get('name') or meta.get('label')
            else:
                pid = str(p)
            if pid:
                pids.append(pid)

        # Fan out per-plugin usage scans in parallel. Per-plugin SDK call is
        # cached so subsequent loads within the cache window are free.
        usage_ttl = int(_BACKEND_SETTINGS.get('cache_ttl_plugins', 600))
        workers = max(1, int(_BACKEND_SETTINGS.get('parallel_workers_default', 8) or 8))
        usage_by_pid: Dict[str, Dict[str, Any]] = {}
        if pids:
            def _fetch_one(pid: str) -> Dict[str, Any]:
                return _sdk_fetch(
                    'plugin_usages:' + pid,
                    usage_ttl,
                    lambda: _scan_plugin_usages(client, pid),
                )
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {ex.submit(_fetch_one, pid): pid for pid in pids}
                for fut in as_completed(futures):
                    pid = futures[fut]
                    try:
                        usage_by_pid[pid] = fut.result()
                    except Exception as exc:
                        usage_by_pid[pid] = {
                            'projectsUsingCount': None,
                            'projectsUsing': [],
                            'missingTypes': [],
                            'usagesError': str(exc),
                        }

        return {'usagesByPlugin': usage_by_pid}

    data = _cache_get('plugin_usages_all', _BACKEND_SETTINGS['cache_ttl_plugins'], loader)
    return jsonify(data)


@app.route('/api/mail-channels')
def api_mail_channels():
    client = g.client
    channels = _list_mail_channels(client)
    return jsonify({
        'channels': channels,
        'configuredMailChannel': _get_configured_mail_channel(),
    })


@app.route('/api/sanity-check')
def api_sanity_check():
    t0 = time.time()
    try:
        client = g.client
        if not hasattr(client, 'perform_instance_sanity_check'):
            # Older DSS versions (<14.4) do not expose this API.
            msg = 'perform_instance_sanity_check() not available on this DSS version'
            app.logger.warning("[sanity-check] %s", msg)
            return jsonify({'error': msg, 'messages': []}), 501
        result = client.perform_instance_sanity_check(wait=True)
        raw = result._data or {}
        messages = [
            {
                'severity': m.get('severity'),
                'code': m.get('code'),
                'title': m.get('title'),
                'details': m.get('details'),
                'message': m.get('message'),
                'extraInfoSummary': m.get('extraInfoSummary'),
                'extraInfoDetails': m.get('extraInfoDetails'),
            }
            for m in raw.get('messages', [])
        ]
        app.logger.info(
            "[sanity-check] ok elapsed=%.0fms messages=%d maxSeverity=%s",
            (time.time() - t0) * 1000.0, len(messages), raw.get('maxSeverity'),
        )
        return jsonify({
            'messages': messages,
            'hasError': raw.get('error', False),
            'hasWarning': raw.get('warning', False),
            'hasSuccess': raw.get('success', False),
            'maxSeverity': raw.get('maxSeverity'),
        })
    except Exception as e:
        app.logger.exception(
            "[sanity-check] failed elapsed=%.0fms exc_type=%s",
            (time.time() - t0) * 1000.0, type(e).__name__,
        )
        return jsonify({'error': f"{type(e).__name__}: {e}", 'messages': []}), 500


@app.route('/api/logs/errors')
def api_logs_errors():
    client = g.client
    dip_home = _dip_home()

    def loader():
        log_content = None
        try:
            log_content = client.get_log('backend.log')
        except Exception:
            log_content = _safe_read_text(os.path.join(dip_home, 'run', 'backend.log'))
        return _parse_log_errors(log_content)

    data = _cache_get('log_errors', _BACKEND_SETTINGS['cache_ttl_log_errors'], loader)
    return jsonify(data)


@app.route('/api/llms')
def api_llms():
    def loader():
        project = _local_toolkit_project()
        llms = project.list_llms()
        return [
            {'id': llm['id'], 'label': llm.get('friendlyName') or llm['id'], 'type': llm.get('type', '')}
            for llm in llms if llm.get('type') != 'RETRIEVAL_AUGMENTED'
        ]
    try:
        completion_llms = _cache_get('llms', 60, loader)
        return jsonify({'llms': completion_llms})
    except CacheLoaderTimeout:
        raise
    except Exception as e:
        return jsonify({'error': str(e), 'llms': []}), 500


def _find_llm_ids(d: Any):
    """Recursively find all llmId values in a dict/list."""
    if isinstance(d, dict):
        for k, v in d.items():
            if k == 'llmId' and isinstance(v, str) and v:
                yield v
            else:
                yield from _find_llm_ids(v)
    elif isinstance(d, list):
        for item in d:
            yield from _find_llm_ids(item)


_LLM_AUDIT_STRUCTURED_RECIPE_PREFIXES = ('prompt', 'nlp_llm_')
_LLM_AUDIT_CODE_RECIPE_TYPES = frozenset({
    'python', 'r', 'pyspark', 'spark_scala', 'scala', 'sql_query', 'sql_script',
})


def _llm_audit_scan_project_references(
    client: Any,
    project_key: str,
    llm_id_regex: Optional[Any],
) -> List[Dict[str, Any]]:
    """Return per-asset llmId hits in one project.

    Each hit: {llmId, assetType: 'recipe'|'notebook'|'knowledge_bank'|'agent',
               assetName, recipeType}. Deduped by (assetType, assetName, llmId).
    Scans prompt/LLM recipes, knowledge banks, agents (structured walk), code
    recipes and Jupyter notebooks (literal llmId regex match). Per-asset try/
    except — one bad asset can't take out the project scan.
    """
    hits: List[Dict[str, Any]] = []
    seen_hits: set = set()

    def add_hit(llm_id: str, asset_type: str, asset_name: str, recipe_type: Optional[str]) -> None:
        k = (asset_type, asset_name, llm_id)
        if k in seen_hits:
            return
        seen_hits.add(k)
        hits.append({
            'llmId': llm_id,
            'assetType': asset_type,
            'assetName': asset_name,
            'recipeType': recipe_type,
        })

    project = client.get_project(project_key)

    try:
        recipes = project.list_recipes() or []
    except Exception as exc:
        app.logger.debug("[llm_audit_usage] list_recipes failed for %s: %s", project_key, exc)
        recipes = []

    structured_recipes = []
    code_recipes = []
    for r in recipes:
        if not isinstance(r, dict):
            continue
        rtype = r.get('type', '') or ''
        if rtype.startswith(_LLM_AUDIT_STRUCTURED_RECIPE_PREFIXES) or 'llm' in rtype.lower():
            structured_recipes.append(r)
        elif rtype in _LLM_AUDIT_CODE_RECIPE_TYPES:
            code_recipes.append(r)

    for r in structured_recipes:
        rtype = r.get('type', '') or ''
        rname = r.get('name') or ''
        try:
            recipe = project.get_recipe(rname)
            settings = recipe.get_settings()
            payload = settings.get_json_payload() if hasattr(settings, 'get_json_payload') else None
            if not payload:
                raw_str = settings.get_payload() if hasattr(settings, 'get_payload') else ''
                try:
                    payload = json.loads(raw_str) if raw_str else {}
                except Exception:
                    payload = {}
            if not payload:
                continue
            for llm_id in _find_llm_ids(payload):
                add_hit(llm_id, 'recipe', rname, rtype)
        except Exception as exc:
            app.logger.debug("[llm_audit_usage] recipe %s/%s failed: %s",
                             project_key, rname, exc)

    try:
        kbs = project.list_knowledge_banks() or []
    except Exception as exc:
        app.logger.debug("[llm_audit_usage] list_knowledge_banks failed for %s: %s", project_key, exc)
        kbs = []
    for kb in kbs:
        kb_id = kb.get('id') if isinstance(kb, dict) else None
        if not kb_id:
            continue
        try:
            kb_settings = project.get_knowledge_bank(kb_id).get_settings()
            raw = kb_settings.get_raw() if hasattr(kb_settings, 'get_raw') else kb_settings
            for llm_id in _find_llm_ids(raw):
                add_hit(llm_id, 'knowledge_bank', kb_id, None)
        except Exception as exc:
            app.logger.debug("[llm_audit_usage] knowledge_bank %s/%s failed: %s",
                             project_key, kb_id, exc)

    try:
        agents = project.list_agents() or []
    except Exception as exc:
        app.logger.debug("[llm_audit_usage] list_agents failed for %s: %s", project_key, exc)
        agents = []
    for ag in agents:
        ag_id = ag.get('id') if isinstance(ag, dict) else None
        if not ag_id:
            continue
        try:
            ag_settings = project.get_agent(ag_id).get_settings()
            raw = ag_settings.get_raw() if hasattr(ag_settings, 'get_raw') else ag_settings
            for llm_id in _find_llm_ids(raw):
                add_hit(llm_id, 'agent', ag_id, None)
        except Exception as exc:
            app.logger.debug("[llm_audit_usage] agent %s/%s failed: %s",
                             project_key, ag_id, exc)

    if llm_id_regex is not None:
        for r in code_recipes:
            rtype = r.get('type', '') or ''
            rname = r.get('name') or ''
            try:
                recipe = project.get_recipe(rname)
                settings = recipe.get_settings()
                payload_str = settings.get_payload() if hasattr(settings, 'get_payload') else ''
                if not payload_str:
                    continue
                for match in llm_id_regex.findall(payload_str):
                    add_hit(match, 'recipe', rname, rtype)
            except Exception as exc:
                app.logger.debug("[llm_audit_usage] code_recipe %s/%s failed: %s",
                                 project_key, rname, exc)

        try:
            notebooks = project.list_jupyter_notebooks() or []
        except Exception as exc:
            app.logger.debug("[llm_audit_usage] list_jupyter_notebooks failed for %s: %s",
                             project_key, exc)
            notebooks = []
        for nb in notebooks:
            nb_name = getattr(nb, 'notebook_name', None)
            if not nb_name:
                continue
            try:
                raw = nb.get_content().get_raw()
                if isinstance(raw, str):
                    source_text = raw
                else:
                    try:
                        source_text = json.dumps(raw)
                    except Exception:
                        source_text = str(raw)
                for match in llm_id_regex.findall(source_text):
                    add_hit(match, 'notebook', nb_name, None)
            except Exception as exc:
                app.logger.debug("[llm_audit_usage] notebook %s/%s failed: %s",
                                 project_key, nb_name, exc)

    return hits


def _llm_audit_scan_project(client: Any, project_key: str) -> List[Dict[str, Any]]:
    """List LLMs for one project and tag each row with the project key."""
    project = client.get_project(project_key)
    out: List[Dict[str, Any]] = []
    for llm in project.list_llms() or []:
        if not isinstance(llm, dict):
            continue
        # Skip meta-wrappers (agents, retrieval-augmented LLMs) — they are compositions
        # over real LLMs, not models that can be obsolete/current themselves.
        # Mirrors llm_audit.NOT_APPLICABLE_TYPES.
        if llm.get('type') in llm_audit.NOT_APPLICABLE_TYPES:
            continue
        out.append({
            'projectKey': project_key,
            'llmId': llm.get('id'),
            'type': llm.get('type'),
            'connection': llm.get('connection'),
            'rawModel': llm.get('model') or llm.get('deployment'),
            'model': llm.get('model'),
            'deployment': llm.get('deployment'),
            'friendlyName': llm.get('friendlyName'),
            'friendlyNameShort': llm.get('friendlyNameShort'),
        })
    return out


@app.route('/api/llm-audit')
def api_llm_audit():
    if not _llm_audit_available:
        return jsonify({'error': 'llm_audit module unavailable',
                        'rows': [], 'summary': {}, 'pricingFetchedAt': None}), 500

    def loader():
        client = g.client
        started = time.time()
        run_id = _start_progress('llm_audit')
        events: List[Dict[str, Any]] = []

        def add_event(step: str, message: str, level: str = 'info', project_key: Optional[str] = None) -> None:
            ev: Dict[str, Any] = {
                'tMs': round((time.time() - started) * 1000.0, 2),
                'level': level,
                'step': step,
                'message': message,
            }
            if project_key:
                ev['projectKey'] = project_key
            events.append(ev)
            _append_progress_event('llm_audit', run_id, ev)

        def set_summary(progress_pct: float, phase: str, **extra: Any) -> None:
            payload: Dict[str, Any] = {
                'progressPct': int(max(0, min(100, round(progress_pct)))),
                'phase': phase,
                'totalElapsedMs': round((time.time() - started) * 1000.0, 2),
            }
            payload.update(extra)
            _set_progress_summary('llm_audit', run_id, payload)

        try:
            # Phase 1: pricing catalog (cached separately so multiple runs share it).
            set_summary(2, 'pricing')
            add_event('pricing_fetch', 'fetching LiteLLM pricing catalog')
            pricing_timeout = int(_BACKEND_SETTINGS.get('llm_audit_pricing_timeout_sec', 30))
            pricing_ttl = int(_BACKEND_SETTINGS.get('cache_ttl_llm_pricing', 21600))
            pricing_fetched_at: List[Optional[str]] = [None]

            def _pricing_loader() -> Dict[str, Any]:
                lookup = llm_audit.build_lookup(timeout=pricing_timeout)
                pricing_fetched_at[0] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                return {'lookup': lookup, 'fetchedAt': pricing_fetched_at[0]}

            try:
                pricing_blob = _cache_get('llm_audit_pricing', pricing_ttl, _pricing_loader)
            except llm_audit.PricingFetchError as exc:
                add_event('pricing_fetch_failed', f'pricing fetch failed: {exc}', 'error')
                raise
            lookup = pricing_blob['lookup']
            pricing_fetched_at_iso = pricing_blob.get('fetchedAt')
            add_event('pricing_ready', f'pricing lookup has {len(lookup)} entries')

            # Phase 2: instance connections (for CustomLLM unwrap).
            set_summary(8, 'connections')
            add_event('connections_fetch', 'fetching instance connections')
            try:
                connections_by_name = client.list_connections() or {}
            except Exception as exc:
                connections_by_name = {}
                add_event('connections_failed', f'list_connections failed: {exc}', 'warn')

            # Phase 3: project catalog.
            set_summary(12, 'catalog')
            projects = client.list_projects() or []
            project_keys = [p.get('projectKey') for p in projects if isinstance(p, dict) and p.get('projectKey')]
            total_projects = len(project_keys)
            add_event('catalog_ready', f'found {total_projects} project(s)')

            # Phase 4: parallel per-project list_llms().
            set_summary(15, 'scan', projectsTotal=total_projects, projectsDone=0)
            llm_rows: List[Dict[str, Any]] = []
            workers = max(1, int(_BACKEND_SETTINGS.get('parallel_workers_default', 8) or 8))
            project_name_lookup: Dict[str, str] = {}
            for _p in projects:
                if isinstance(_p, dict) and _p.get('projectKey'):
                    project_name_lookup[_p['projectKey']] = _p.get('name') or _p['projectKey']
            if total_projects > 0:
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    futures = {ex.submit(_llm_audit_scan_project, client, pk): pk for pk in project_keys}
                    done = 0
                    for fut in as_completed(futures):
                        pk = futures[fut]
                        try:
                            project_rows = fut.result()
                            llm_rows.extend(project_rows)
                            for pr in project_rows:
                                if not isinstance(pr, dict):
                                    continue
                                _append_progress_partial_row('llm_audit', run_id, {
                                    'projectKey': pr.get('projectKey') or pk,
                                    'projectName': project_name_lookup.get(pr.get('projectKey') or pk, pk),
                                    'llmId': pr.get('llmId'),
                                    'friendlyName': pr.get('friendlyName'),
                                    'friendlyNameShort': pr.get('friendlyNameShort'),
                                    'type': pr.get('type'),
                                    'connection': pr.get('connection'),
                                    'rawModel': pr.get('rawModel'),
                                    'partial': True,
                                })
                        except Exception as exc:
                            add_event('scan_project_failed', f'{pk}: {exc}', 'warn', project_key=pk)
                        done += 1
                        # Throttle progress updates every project (lightweight).
                        scan_pct = 15.0 + 70.0 * (done / max(1, total_projects))
                        set_summary(scan_pct, 'scan',
                                    projectsTotal=total_projects, projectsDone=done,
                                    llmRowsTotal=len(llm_rows))

            add_event('scan_done', f'collected {len(llm_rows)} LLM profile rows across {total_projects} project(s)')

            # Phase 4b: per-project asset scan for actual llmId references.
            set_summary(50, 'usage_scan', projectsTotal=total_projects, projectsDone=0)
            llm_id_universe = sorted({row.get('llmId') for row in llm_rows if row.get('llmId')})
            llm_id_regex = None
            if llm_id_universe:
                try:
                    llm_id_regex = re.compile('|'.join(re.escape(i) for i in llm_id_universe))
                except Exception as exc:
                    add_event('usage_regex_failed', f'failed to compile llmId regex: {exc}', 'warn')

            projects_using_by_llm_id: Dict[str, set] = {}
            assets_by_project_llm: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
            if total_projects > 0:
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    futures = {
                        ex.submit(_llm_audit_scan_project_references, client, pk, llm_id_regex): pk
                        for pk in project_keys
                    }
                    done = 0
                    for fut in as_completed(futures):
                        pk = futures[fut]
                        try:
                            referenced = fut.result()
                            for hit in referenced:
                                llm_id = hit.get('llmId')
                                if not llm_id:
                                    continue
                                projects_using_by_llm_id.setdefault(llm_id, set()).add(pk)
                                assets_by_project_llm.setdefault(pk, {}).setdefault(llm_id, []).append({
                                    'assetType': hit.get('assetType'),
                                    'assetName': hit.get('assetName'),
                                    'recipeType': hit.get('recipeType'),
                                })
                        except Exception as exc:
                            add_event('usage_scan_project_failed', f'{pk}: {exc}', 'warn', project_key=pk)
                        done += 1
                        usage_pct = 50.0 + 35.0 * (done / max(1, total_projects))
                        set_summary(usage_pct, 'usage_scan',
                                    projectsTotal=total_projects, projectsDone=done,
                                    llmRowsTotal=len(llm_rows))

            add_event('usage_scan_done',
                      f'{sum(len(v) for v in projects_using_by_llm_id.values())} project-references '
                      f'across {len(projects_using_by_llm_id)} distinct llmId(s)')

            # Phase 5: classify and dedupe by (projectKey, llmId).
            set_summary(88, 'classify', llmRowsTotal=len(llm_rows))
            project_names: Dict[str, str] = {}
            for p in projects:
                if isinstance(p, dict) and p.get('projectKey'):
                    project_names[p['projectKey']] = p.get('name') or p['projectKey']

            seen: set = set()
            classified_rows: List[Dict[str, Any]] = []
            for row in llm_rows:
                key = (row.get('projectKey'), row.get('llmId'))
                if key in seen:
                    continue
                seen.add(key)
                verdict = llm_audit.classify_llm(row, lookup, connections_by_name=connections_by_name)
                llm_id = row.get('llmId') or ''
                using_set = projects_using_by_llm_id.get(llm_id, set())
                referencing_sorted = sorted(using_set)
                merged = {
                    'projectKey': row.get('projectKey'),
                    'projectName': project_names.get(row.get('projectKey') or '', row.get('projectKey') or ''),
                    'llmId': row.get('llmId'),
                    'friendlyName': row.get('friendlyName'),
                    'friendlyNameShort': row.get('friendlyNameShort'),
                    'type': row.get('type'),
                    'connection': row.get('connection'),
                    'rawModel': row.get('rawModel'),
                }
                merged.update(verdict)
                merged['projectsUsing'] = len(using_set)
                merged['referencingProjects'] = referencing_sorted[:50]
                merged['usageAssets'] = assets_by_project_llm.get(
                    row.get('projectKey') or '', {}
                ).get(llm_id, [])
                classified_rows.append(merged)

            summary = llm_audit.summarize_rows(classified_rows)
            summary['pricingFetchedAt'] = pricing_fetched_at_iso
            summary['totalElapsedMs'] = round((time.time() - started) * 1000.0, 2)

            # Surface per-project scan failures collected during phases 4/4b.
            _scan_error_area = {
                'scan_project_failed': 'scan',
                'usage_scan_project_failed': 'usage_scan',
            }
            scan_errors: List[Dict[str, Any]] = []
            failed_project_keys: set = set()
            for ev in events:
                area = _scan_error_area.get(ev.get('step'))
                if not area:
                    continue
                pk = ev.get('projectKey') or ''
                scan_errors.append({
                    'projectKey': pk,
                    'area': area,
                    'error': str(ev.get('message') or '')[:240],
                })
                if pk:
                    failed_project_keys.add(pk)
            summary['scanErrors'] = scan_errors
            summary['failedProjectCount'] = len(failed_project_keys)
            summary['scannedProjectCount'] = total_projects

            set_summary(100, 'done',
                        projectsTotal=total_projects,
                        projectsDone=total_projects,
                        llmsTotal=summary.get('llmsTotal', 0),
                        countsByStatus=summary.get('countsByStatus', {}),
                        distinctModelsByStatus=summary.get('distinctModelsByStatus', {}))
            _finish_progress('llm_audit', run_id, status='ok', summary=None)

            return {
                'rows': classified_rows,
                'summary': summary,
                'pricingFetchedAt': pricing_fetched_at_iso,
                'events': events,
            }
        except Exception as exc:
            _finish_progress('llm_audit', run_id, status='error', error=str(exc))
            raise

    try:
        ttl = int(_BACKEND_SETTINGS.get('cache_ttl_llm_audit', 600))
        data = _cache_get('llm_audit', ttl, loader)
        return jsonify(data)
    except Exception as exc:
        return jsonify({'error': str(exc), 'rows': [], 'summary': {}, 'pricingFetchedAt': None}), 500


@app.route('/api/llm-audit/progress')
def api_llm_audit_progress():
    since_raw = request.args.get('since', '0')
    run_id = request.args.get('runId')
    rows_since_raw = request.args.get('rowsSince', '0')
    try:
        since = max(0, int(str(since_raw or '0')))
    except Exception:
        since = 0
    try:
        rows_since = max(0, int(str(rows_since_raw or '0')))
    except Exception:
        rows_since = 0
    payload = _read_progress('llm_audit', since=since, run_id=run_id, rows_since=rows_since)
    return jsonify(payload)


@app.route('/api/debug/perf')
def api_debug_perf():
    """Return performance debug data without triggering any scans."""
    try:
        cache = _get_sdk_cache()
        cache_keys = cache.get_cache_keys() if hasattr(cache, 'get_cache_keys') else []
        sdk_stats = cache.get_stats() if hasattr(cache, 'get_stats') else {}
    except Exception:
        cache_keys = []
        sdk_stats = {}
    with _BACKEND_SETTINGS_LOCK:
        settings = dict(_BACKEND_SETTINGS)
    ce_benchmark = None
    pf_benchmark = None
    ce_val = _cache_peek('code_envs')
    if isinstance(ce_val, dict):
        ce_benchmark = ce_val.get('summary', {}).get('benchmark')
    # Extract benchmarks from progress (PF doesn't use _cache_get; CE as fallback)
    progress_summaries: Dict[str, Any] = {}
    with _PROGRESS_LOCK:
        for k, v in _PROGRESS.items():
            summary = v.get('summary')
            if isinstance(summary, dict):
                # Strip events array to keep response small
                progress_summaries[k] = {
                    key: val for key, val in summary.items() if key != 'events'
                }
                if k == 'project_footprint' and pf_benchmark is None:
                    pf_benchmark = summary
                if k == 'code_envs' and ce_benchmark is None:
                    ce_benchmark = summary
    # Strip events from benchmarks to keep response small
    if isinstance(ce_benchmark, dict):
        ce_benchmark = {k: v for k, v in ce_benchmark.items() if k != 'events'}
    if isinstance(pf_benchmark, dict):
        pf_benchmark = {k: v for k, v in pf_benchmark.items() if k != 'events'}
    return jsonify({
        'cache_keys': cache_keys,
        'sdk_cache_stats': sdk_stats,
        'backend_settings': settings,
        'last_code_envs_benchmark': ce_benchmark,
        'last_project_footprint_benchmark': pf_benchmark,
        'progress_summaries': progress_summaries,
    })


@app.route('/api/debug/workers')
def api_debug_workers():
    """Introspect the webapp's gunicorn process tree to discover worker count.

    Returns this worker's pid/ppid, the master's cmdline, and a list of sibling
    workers (processes whose PPid matches our own). Read-only; touches /proc only.
    """
    import os as _os

    def _read_proc(path):
        try:
            with open(path, 'r') as fh:
                return fh.read()
        except OSError:
            return None

    def _read_status(pid):
        text = _read_proc('/proc/{}/status'.format(pid))
        if not text:
            return None
        out = {}
        for line in text.splitlines():
            if ':' in line:
                k, _, v = line.partition(':')
                out[k.strip()] = v.strip()
        return out

    def _read_cmdline(pid):
        text = _read_proc('/proc/{}/cmdline'.format(pid))
        if not text:
            return None
        return text.replace('\x00', ' ').strip()

    self_pid = _os.getpid()
    parent_pid = _os.getppid()
    self_cmd = _read_cmdline(self_pid)
    parent_cmd = _read_cmdline(parent_pid)
    parent_status = _read_status(parent_pid)

    siblings = []
    try:
        for entry in _os.listdir('/proc'):
            if not entry.isdigit():
                continue
            pid = int(entry)
            status = _read_status(pid)
            if not status:
                continue
            ppid_raw = status.get('PPid', '0').split()[0]
            try:
                ppid = int(ppid_raw)
            except ValueError:
                continue
            if ppid == parent_pid:
                siblings.append({
                    'pid': pid,
                    'name': status.get('Name'),
                    'threads': int((status.get('Threads') or '0').split()[0]),
                    'cmdline': _read_cmdline(pid),
                })
    except OSError as exc:
        return jsonify({'error': 'listdir /proc failed: {}'.format(exc)}), 500

    siblings.sort(key=lambda r: r['pid'])

    cpu_count = _os.cpu_count()
    return jsonify({
        'self_pid': self_pid,
        'parent_pid': parent_pid,
        'self_cmdline': self_cmd,
        'parent_cmdline': parent_cmd,
        'parent_name': (parent_status or {}).get('Name'),
        'siblings': siblings,
        'worker_count': len(siblings),
        'cpu_count': cpu_count,
    })


@app.route('/api/logs/raw-tail')
def api_logs_raw_tail():
    """Return the last 100K characters of backend.log as plain text."""
    max_chars = 100_000
    try:
        client = g.client
        dip_home = _dip_home()
        log_content = None
        try:
            log_content = client.get_log('backend.log')
        except Exception:
            log_content = _safe_read_text(os.path.join(dip_home, 'run', 'backend.log'))
        text = _coerce_log_text(log_content) or ''
        if len(text) > max_chars:
            text = text[-max_chars:]
        return jsonify({'text': text, 'chars': len(text)})
    except Exception as e:
        return jsonify({'error': str(e), 'text': '', 'chars': 0}), 500


@app.route('/api/logs/ai-analysis', methods=['POST'])
def api_logs_ai_analysis():
    """Stream AI log analysis via SSE with phase updates and token streaming."""
    body = request.get_json(force=True)
    llm_id = body.get('llmId', '').strip()
    custom_system_prompt = (body.get('systemPrompt') or '').strip()
    client_user_message = (body.get('userMessage') or '').strip()

    _DEFAULT_SYSTEM_PROMPT = (
        "You are an expert Dataiku DSS administrator and backend engineer "
        "analyzing error logs from a DSS instance's backend.log file.\n\n"
        "Before answering, think step-by-step through each error carefully. For each error pattern:\n"
        "- Reason through what component, subsystem, or configuration could cause it.\n"
        "- Search the web for the specific error message, Java exception, or stack trace to find "
        "known issues, Dataiku Knowledge Base articles, community posts, or release notes.\n"
        "- Cross-reference with official Dataiku documentation (doc.dataiku.com) for configuration "
        "guidance, known limitations, and recommended fixes.\n"
        "- Only after researching, provide your diagnosis and remediation.\n\n"
        "Your task:\n"
        "1. Identify the root cause of each distinct error or error pattern.\n"
        "2. Assess severity (Critical / Warning / Informational).\n"
        "3. Provide specific, actionable remediation steps, including links to relevant "
        "documentation or KB articles when available.\n"
        "4. Group related errors sharing a root cause.\n"
        "5. Highlight data loss risk, security issues, or service outage indicators.\n\n"
        "Format: markdown with headings per issue, bullet points for remediation. "
        "Start with a 2-3 sentence Executive Summary."
    )
    system_prompt = custom_system_prompt if custom_system_prompt else _DEFAULT_SYSTEM_PROMPT

    def generate():
        if not llm_id:
            yield "event: error\ndata: %s\n\n" % json.dumps({"error": "llmId is required"})
            return

        try:
            yield "event: phase\ndata: %s\n\n" % json.dumps({"phase": "Preparing log data"})

            project = _local_toolkit_project()

            if client_user_message:
                # Frontend provided the pre-built user message — use it directly
                user_message = client_user_message
                log_chars = len(user_message)
            else:
                # Fallback: build user message from cache/disk (backward compat)
                dip_home = _dip_home()

                def loader():
                    log_content = None
                    try:
                        log_content = client.get_log('backend.log')
                    except Exception:
                        log_content = _safe_read_text(os.path.join(dip_home, 'run', 'backend.log'))
                    return _parse_log_errors(log_content)

                log_data = _cache_get('log_errors', _BACKEND_SETTINGS['cache_ttl_log_errors'], loader)
                raw_errors = log_data.get('rawLogErrors', [])

                if not raw_errors:
                    yield "event: done\ndata: %s\n\n" % json.dumps({
                        "analysis": "No log errors found to analyze.",
                        "llmId": llm_id, "logCharsAnalyzed": 0,
                    })
                    return

                error_text = '\n---\n'.join('\n'.join(block.get('data', [])) for block in raw_errors)
                max_chars = 100_000
                if len(error_text) > max_chars:
                    error_text = error_text[-max_chars:]
                log_chars = len(error_text)

                log_stats = log_data.get('logStats', {})
                user_message = (
                    "Analyze the following DSS backend.log errors.\n"
                    "Stats: %d unique errors, %d total log lines.\n\n"
                    "```\n%s\n```"
                ) % (log_stats.get('Unique Errors', 0), log_stats.get('Total Lines', 0), error_text)

            yield "event: phase\ndata: %s\n\n" % json.dumps({"phase": "Sending to LLM"})

            completion = project.get_llm(llm_id).new_completion()
            completion.settings['maxOutputTokens'] = 4096
            # completion.settings['temperature'] = 0.3  # disabled – not supported by some small LLMs (e.g. GPT-5 mini/nano)
            completion.with_message(message=system_prompt, role='system')
            completion.with_message(message=user_message, role='user')

            # Try streaming first, fall back to non-streamed
            streamed = False
            try:
                yield "event: phase\ndata: %s\n\n" % json.dumps({"phase": "Generating analysis"})
                resp_stream = completion.execute_streamed()
                for chunk in resp_stream:
                    text = str(chunk.text) if hasattr(chunk, 'text') else ''
                    if text:
                        streamed = True
                        yield "event: chunk\ndata: %s\n\n" % json.dumps({"text": text})
            except (AttributeError, TypeError):
                # execute_streamed() not available, fall back
                resp = completion.execute()
                analysis_text = str(resp.text)
                yield "event: chunk\ndata: %s\n\n" % json.dumps({"text": analysis_text})
                streamed = False

            yield "event: done\ndata: %s\n\n" % json.dumps({
                "llmId": llm_id,
                "logCharsAnalyzed": log_chars,
                "streamed": streamed,
            })
        except Exception as e:
            yield "event: error\ndata: %s\n\n" % json.dumps({"error": str(e)})

    return Response(stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route('/api/report/generate', methods=['POST'])
def api_report_generate():
    """Generate a quarterly health check report via LLM Mesh. SSE with phase-only events."""
    body = request.get_json(force=True)
    llm_id = (body.get('llmId') or '').strip()
    diagnostic_data = body.get('diagnosticData') or {}

    _REPORT_SYSTEM_PROMPT = (
        "You are a senior Dataiku Technical Account Manager (TAM) creating a quarterly health check "
        "presentation for a customer's technical leadership. This will be rendered as an 18-slide "
        "HTML slideshow that the TAM presents live to the customer.\n\n"
        "Think deeply about the diagnostic data before writing. Analyze cross-cutting patterns, "
        "correlate issues across sections, and identify root causes. Take your time.\n\n"
        "=== VOICE & TONE ===\n"
        "- You are a trusted advisor, not a monitoring tool.\n"
        "- Use first-person plural: 'we recommend', 'our analysis shows', 'we observed'.\n"
        "- Lead with POSITIVES before concerns. Always acknowledge what's working well.\n"
        "- Frame findings in BUSINESS IMPACT: 'training pipeline reliability' not 'OutOfMemoryError'.\n"
        "- Cite exact numbers, project names, config values. Never be vague.\n"
        "- Reference doc.dataiku.com links where relevant.\n\n"
        "=== SLIDE LAYOUT DETAILS ===\n"
        "Your output populates 18 slides. Here is exactly how each slide renders:\n\n"
        "SLIDE 1 (Title): Static - company name, date, DSS version. You don't write this.\n\n"
        "SLIDE 2 (Executive Summary): LEFT COLUMN shows a large health score number (computed separately). "
        "RIGHT COLUMN shows your 'overall_status' text in a callout box. BELOW both columns, "
        "your 3 'findings' display as numbered cards in a row. Each finding should be ONE bullet point "
        "(1-2 sentences max) that a VP can read in 5 seconds.\n\n"
        "SLIDES 3-13 (Data Slides): Each has this layout:\n"
        "  LEFT COLUMN: 4 large metric cards showing numbers from the actual data (you don't write these).\n"
        "  RIGHT COLUMN: Your 'narrative' text in a callout box. This is the ONLY text you control on these slides.\n"
        "  BELOW the callout: optional extras (highlights, risks, warnings, upgrade_paths) shown as badges or bullet items.\n\n"
        "  CRITICAL: The narrative is displayed in a tall callout box with large font (1.25rem). "
        "Use BULLET POINTS (with bullet char), NOT paragraphs. 3-5 bullets per slide. "
        "Each bullet: one clear observation with a specific number or finding.\n"
        "  Format example:\n"
        "    '\\u2022 42 projects with healthy adoption across the organization\\n"
        "\\u2022 ML Pipeline (PROJ1) leads with 156 versions, indicating critical production use\\n"
        "\\u2022 Consider version retention policy for projects exceeding 100 versions'\n\n"
        "  The slides are:\n"
        "    Slide 3: Instance Overview - DSS version, OS, CPU, Python\n"
        "    Slide 4: Projects Overview - project count, health score\n"
        "    Slide 5: Project Footprint - storage analysis, top projects by size\n"
        "    Slide 6: Code Environments - env count, Python/R version distribution\n"
        "    Slide 7: Code Env Health - health score, unused envs, upgrade paths\n"
        "    Slide 8: Filesystem Health - mount point usage percentages\n"
        "    Slide 9: Memory & JVM - heap settings, system RAM\n"
        "    Slide 10: Connections - connection types, counts\n"
        "    Slide 11: Issues & Risks - disabled features, plugins, risk level\n"
        "    Slide 12: Users & Activity - user counts by role\n"
        "    Slide 13: Log Analysis - error counts, patterns\n\n"
        "  For 'highlights', 'risks', 'warnings', 'upgrade_paths' arrays: "
        "these render as small badge pills. Keep each item UNDER 10 words.\n"
        "  For 'patterns' array: renders in monospace. Keep each under 80 chars.\n\n"
        "SLIDES 14-16 (Recommendations): Each slide shows a 2-column grid of cards.\n"
        "  Each card has: a numbered indicator, a bold TITLE (Spectral serif, ~5 words), "
        "a DESCRIPTION paragraph (Roboto, 1-2 sentences with specific action), "
        "and an IMPACT badge (green pill, ~5-8 words on business value).\n"
        "  Slide 14: Critical (2-3 items) - production stability / data loss risks\n"
        "  Slide 15: Important (3-5 items) - address this quarter to prevent escalation\n"
        "  Slide 16: Nice-to-Have (2-3 items) - efficiency and governance optimizations\n\n"
        "SLIDE 17 (Action Plan): Vertical timeline with numbered steps.\n"
        "  Each step: action text (what to do), timeline (when), effort badge (low/medium/high).\n"
        "  Include 5-7 items ordered by priority. Use concrete timelines: "
        "'next maintenance window', 'within 30 days', 'Q2 2025', NOT 'soon' or 'when possible'.\n\n"
        "SLIDE 18 (Closing): Static - 'Next Steps' with TAM contact prompt. You don't write this.\n\n"
        "=== OUTPUT FORMAT ===\n"
        "Return ONLY valid JSON (no markdown fences, no commentary outside the JSON).\n"
        '{\n'
        '  "slides": {\n'
        '    "executive_summary": {\n'
        '      "findings": [\n'
        '        "One-sentence finding for card 1 (most impactful)",\n'
        '        "One-sentence finding for card 2",\n'
        '        "One-sentence finding for card 3"\n'
        '      ],\n'
        '      "overall_status": "STATUS_LABEL - one sentence summary"\n'
        '    },\n'
        '    "instance_overview": { "narrative": "bullet point text with newlines" },\n'
        '    "projects": { "narrative": "...", "highlights": ["short badge text", "..."] },\n'
        '    "project_footprint": { "narrative": "...", "risks": ["short risk badge", "..."] },\n'
        '    "code_envs": { "narrative": "..." },\n'
        '    "code_env_health": { "narrative": "...", "upgrade_paths": ["short path", "..."] },\n'
        '    "filesystem": { "narrative": "...", "warnings": ["short warning", "..."] },\n'
        '    "memory": { "narrative": "...", "tuning_recs": ["short rec", "..."] },\n'
        '    "connections": { "narrative": "..." },\n'
        '    "issues": { "narrative": "...", "risk_level": "low|medium|high|critical" },\n'
        '    "users": { "narrative": "..." },\n'
        '    "logs": { "narrative": "...", "patterns": ["error pattern < 80 chars", "..."] },\n'
        '    "rec_critical": { "items": [{\n'
        '      "title": "Short Title (3-5 words)",\n'
        '      "description": "Specific action: what to change, where, and why. 1-2 sentences.",\n'
        '      "impact": "Business impact in 5-8 words"\n'
        '    }] },\n'
        '    "rec_important": { "items": [{ "title": "...", "description": "...", "impact": "..." }] },\n'
        '    "rec_nice_to_have": { "items": [{ "title": "...", "description": "...", "impact": "..." }] },\n'
        '    "action_plan": { "priorities": [{\n'
        '      "action": "Specific task an admin can execute",\n'
        '      "timeline": "Concrete timeframe",\n'
        '      "effort": "low|medium|high"\n'
        '    }] }\n'
        '  }\n'
        '}\n\n'
        "STATUS_LABEL must be one of: HEALTHY, GOOD WITH CAVEATS, MODERATE RISK, or NEEDS ATTENTION.\n\n"
        "Remember: ALL narrative fields must use bullet points (\\u2022), not paragraphs. "
        "3-5 bullets per narrative. Each bullet starts with \\u2022 and contains ONE observation with a number."
    )

    def generate():
        if not llm_id:
            yield "event: error\ndata: %s\n\n" % json.dumps({"error": "llmId is required"})
            return
        if not diagnostic_data:
            yield "event: error\ndata: %s\n\n" % json.dumps({"error": "No diagnostic data provided. Please wait for all data to load."})
            return

        try:
            yield "event: phase\ndata: %s\n\n" % json.dumps({"phase": "Preparing data"})

            project = _local_toolkit_project()

            user_message = "Analyze this DSS instance diagnostic data:\n\n" + json.dumps(diagnostic_data, indent=None, default=str)

            yield "event: phase\ndata: %s\n\n" % json.dumps({"phase": "Analyzing diagnostics"})

            completion = project.get_llm(llm_id).new_completion()
            completion.settings['maxOutputTokens'] = 32768
            # Allow extended thinking for deeper analysis
            try:
                completion.settings['budgetTokens'] = 100000
            except Exception:
                pass  # Not all LLM backends support budgetTokens
            completion.with_message(message=_REPORT_SYSTEM_PROMPT, role='system')
            completion.with_message(message=user_message, role='user')

            # Streamed call — avoids LLM Mesh gateway timeout (~263s)
            report_parts = []
            char_count = 0
            for chunk in completion.execute_streamed():
                if chunk.type == "footer":
                    break
                if chunk.type == "content" and chunk.text:
                    report_parts.append(chunk.text)
                    char_count += len(chunk.text)
                    yield "event: chunk\ndata: %s\n\n" % json.dumps({
                        "text": chunk.text,
                        "totalChars": char_count,
                    })
                elif chunk.type == "event":
                    yield "event: phase\ndata: %s\n\n" % json.dumps({
                        "phase": "Thinking: %s" % (chunk.event_kind or "reasoning"),
                    })

            report_text = ''.join(report_parts)

            # Strip markdown fences if present
            import re
            report_text = re.sub(r'^```(?:json)?\s*\n?', '', report_text)
            report_text = re.sub(r'\n?```\s*$', '', report_text).strip()

            yield "event: done\ndata: %s\n\n" % json.dumps({
                "report": report_text,
                "llmId": llm_id,
            })
        except Exception as e:
            yield "event: error\ndata: %s\n\n" % json.dumps({"error": str(e)})

    return Response(stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route('/api/dir-tree')
def api_dir_tree():
    client = g.client
    dip_home = _dip_home()
    max_depth = request.args.get('maxDepth', type=int) or 3
    path = request.args.get('path')
    raw_scope = (request.args.get('scope') or 'dss').strip().lower()
    if raw_scope in ('global', 'all', 'unknown'):
        raw_scope = 'dss'
    scope = raw_scope if raw_scope in ('dss', 'project') else 'dss'
    project_key = (request.args.get('projectKey') or '').strip() or None
    if scope != 'project':
        project_key = None

    # Layer 1: cache the raw footprint payload (expensive DSS API call)
    footprint_scope = 'all-dss' if scope == 'dss' else scope
    footprint_cache_key = f"footprint:{footprint_scope}:{project_key or '-'}"

    def footprint_loader():
        return _compute_footprint_payload(client, footprint_scope, project_key)

    cached_footprint = _cache_get(footprint_cache_key, _BACKEND_SETTINGS['cache_ttl_dir_tree'], footprint_loader)

    # Layer 2: cache the tree view (cheap in-memory tree build from cached payload)
    tree_cache_key = f"dir_tree:{scope}:{project_key or '-'}:{path or 'root'}:{max_depth}"

    def tree_loader():
        return _build_dir_tree_from_footprint(
            client,
            dip_home,
            max_depth,
            target_path=path,
            scope=scope,
            project_key=project_key,
            footprint_payload=cached_footprint,
        )

    data = _cache_get(tree_cache_key, _BACKEND_SETTINGS['cache_ttl_dir_tree'], tree_loader)
    return jsonify(data)


@app.route('/api/settings', methods=['GET'])
def api_settings_get():
    with _BACKEND_SETTINGS_LOCK:
        return jsonify({'current': dict(_BACKEND_SETTINGS), 'defaults': dict(_BACKEND_SETTINGS_DEFAULTS)})


@app.route('/api/settings/threshold-defaults', methods=['GET'])
def api_settings_threshold_defaults():
    try:
        from db_adapter import load_plugin_threshold_defaults
        return jsonify(load_plugin_threshold_defaults())
    except Exception:
        return jsonify({})


# ── DB Health ──

_PG_DRIVER = None  # 'psycopg2' | None
_PG_DRIVER_CHECKED = False
_PG_DRIVER_LOG = []  # tracks every attempt for UI visibility
_dbhealth_log = logging.getLogger(__name__)
_DBHEALTH_CONFIG = None  # cached DbHealthConfig


def _get_dbhealth_config():
    """Get cached DB Health plugin config (connection name + password)."""
    global _DBHEALTH_CONFIG
    if _DBHEALTH_CONFIG is None:
        try:
            from db_adapter import load_dbhealth_config
            _DBHEALTH_CONFIG = load_dbhealth_config()
        except Exception:
            from dataclasses import dataclass
            from typing import Optional as Opt
            @dataclass(frozen=True)
            class _Fallback:
                connection_name: Opt[str] = None
                password: Opt[str] = None
            _DBHEALTH_CONFIG = _Fallback()
    return _DBHEALTH_CONFIG


def _ensure_pg_driver():
    """Try to get psycopg2, or auto-install it. Logs every attempt to _PG_DRIVER_LOG."""
    global _PG_DRIVER, _PG_DRIVER_CHECKED
    if _PG_DRIVER_CHECKED:
        return _PG_DRIVER
    _PG_DRIVER_CHECKED = True
    log = _PG_DRIVER_LOG

    # 1. Try psycopg2 (already installed)
    try:
        import psycopg2  # noqa: F401
        _PG_DRIVER = 'psycopg2'
        log.append('[OK] psycopg2 already installed')
        return _PG_DRIVER
    except ImportError as exc:
        log.append('[FAIL] import psycopg2: %s' % exc)

    # 2. Try pip install with multiple strategies to dodge permission issues (AlmaLinux 9 / RHEL 9)
    _tmp_target = os.path.join(tempfile.gettempdir(), 'dku_psycopg2')
    _datadir_target = os.path.join(os.environ.get('DIP_HOME', '/tmp'), 'lib', 'python', 'psycopg2')
    install_attempts = [
        ('pip install (default)', [sys.executable, '-m', 'pip', 'install', 'psycopg2-binary', '--quiet']),
        ('pip install --user', [sys.executable, '-m', 'pip', 'install', 'psycopg2-binary', '--quiet', '--user']),
        ('pip install --break-system-packages', [sys.executable, '-m', 'pip', 'install', 'psycopg2-binary', '--quiet', '--break-system-packages']),
        ('pip install --target %s' % _tmp_target, [sys.executable, '-m', 'pip', 'install', 'psycopg2-binary', '--quiet', '--target', _tmp_target]),
        ('pip install --target %s' % _datadir_target, [sys.executable, '-m', 'pip', 'install', 'psycopg2-binary', '--quiet', '--target', _datadir_target]),
        ('pip install --prefix %s' % sys.prefix, [sys.executable, '-m', 'pip', 'install', 'psycopg2-binary', '--quiet', '--prefix', sys.prefix]),
    ]
    for label, cmd in install_attempts:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                log.append('[FAIL] %s: %s' % (label, (result.stderr.strip() or 'exit %d' % result.returncode)[:150]))
                continue
            # --target installs need the path on sys.path before import works
            for tgt in (_tmp_target, _datadir_target):
                if tgt not in sys.path and os.path.isdir(tgt):
                    sys.path.insert(0, tgt)
            try:
                import psycopg2  # noqa: F401
                _PG_DRIVER = 'psycopg2'
                log.append('[OK] %s — import succeeded' % label)
                return _PG_DRIVER
            except ImportError as exc:
                log.append('[FAIL] %s — pip succeeded but import failed: %s' % (label, exc))
        except Exception as exc:
            log.append('[FAIL] %s: %s' % (label, str(exc)[:150]))

    # 3. Try adding common site-packages paths and re-importing
    _pyver_short = sys.version[:3]
    _pyver_tuple = '%d.%d' % sys.version_info[:2]
    for extra_path in [
        '/usr/lib/python3/dist-packages',
        '/usr/local/lib/python3/dist-packages',
        '/usr/lib64/python%s/site-packages' % _pyver_tuple,
        '/usr/lib/python%s/site-packages' % _pyver_tuple,
        '/usr/local/lib64/python%s/site-packages' % _pyver_tuple,
        '/usr/local/lib/python%s/site-packages' % _pyver_tuple,
        os.path.expanduser('~/.local/lib/python%s/site-packages' % _pyver_tuple),
        os.path.expanduser('~/.local/lib64/python%s/site-packages' % _pyver_tuple),
        os.path.join(sys.prefix, 'lib', 'python%s' % _pyver_short, 'site-packages'),
        os.path.join(sys.prefix, 'lib', 'python%s' % _pyver_tuple, 'site-packages'),
        os.path.join(sys.prefix, 'lib64', 'python%s' % _pyver_tuple, 'site-packages'),
        _tmp_target,
        _datadir_target,
    ]:
        if not os.path.isdir(extra_path):
            log.append('[SKIP] path probe %s — not a directory' % extra_path)
            continue
        if extra_path in sys.path:
            log.append('[SKIP] path probe %s — already in sys.path' % extra_path)
            continue
        sys.path.insert(0, extra_path)
        try:
            __import__('psycopg2')
            _PG_DRIVER = 'psycopg2'
            log.append('[OK] path probe %s — import succeeded' % extra_path)
            return _PG_DRIVER
        except ImportError as exc:
            log.append('[FAIL] path probe %s: %s' % (extra_path, exc))

    log.append('[RESULT] All attempts failed — will need user-provided password for psql fallback')
    _PG_DRIVER = None
    return _PG_DRIVER


def _get_pg_conn_params(connection_name: str) -> dict:
    """Extract PG connection params from a DSS connection definition."""
    client = g.client
    defn = client.get_connection(connection_name).get_definition()
    params = defn.get('params', {})
    return {
        'host': params.get('host', 'localhost'),
        'port': int(params.get('port', 5432)),
        'dbname': params.get('db', params.get('database', params.get('dbname', ''))),
        'user': params.get('user', ''),
        'password': params.get('password', ''),
    }


def _pg_direct_connect(connection_name: str, user_password: str = ''):
    """Get a PG connection with autocommit using psycopg2."""
    p = _get_pg_conn_params(connection_name)
    driver = _ensure_pg_driver()
    if driver == 'psycopg2':
        import psycopg2
        pw = user_password or p['password']
        conn = psycopg2.connect(
            host=p['host'], port=p['port'], dbname=p['dbname'],
            user=p['user'], password=pw,
            options='-c statement_timeout=60000',
        )
        conn.autocommit = True
        return conn
    raise ImportError("No PG driver available")


def _pg_exec_ddl(connection_name: str, sql_template: str, table_name: str, user_password: str = ''):
    """Execute a DDL-like statement (VACUUM/ANALYZE) that needs autocommit.
    Tries: 1) psycopg2 with autocommit, 2) psql CLI with user-provided password.
    If psycopg2 is not available and no password is provided, returns needsPassword.

    Phase 2 short-circuit: VACUUM/ANALYZE on a remote host is not supported.
    The dbhealth-query macro's _READ_ONLY_RE rejects writes, and routing
    through local psycopg2 would either fail (firewall) or target the wrong
    database silently. Surface a clear error instead.
    """
    if _safe_request_host_id() != 'local':
        return {
            'success': False,
            'error': 'Maintenance writes (VACUUM/ANALYZE) on remote hosts are not yet supported. '
                     'Run them from the local DSS or via the host\'s own maintenance tooling.',
            'remoteUnsupported': True,
        }

    safe_table = '"%s"' % table_name.replace('"', '""')
    full_sql = sql_template.replace('{}', safe_table)
    p = _get_pg_conn_params(connection_name)
    errors = []

    # Strategy 1: psycopg2 with autocommit
    driver = _ensure_pg_driver()
    if driver:
        try:
            conn = _pg_direct_connect(connection_name, user_password=user_password)
            try:
                import psycopg2.sql as pg2sql
                with conn.cursor() as cur:
                    cur.execute(pg2sql.SQL(sql_template).format(pg2sql.Identifier(table_name)))
                return {'success': True, 'method': driver}
            finally:
                conn.close()
        except Exception as exc:
            err_str = str(exc).lower()
            if not user_password and ('password authentication failed' in err_str or 'fe_sendauth' in err_str):
                return {'needsPassword': True, 'reason': 'Database auth failed — please provide the password'}
            errors.append('%s: %s' % (driver, str(exc)))

    # Strategy 2: psql CLI with user-provided password
    if not user_password:
        return {'needsPassword': True, 'reason': 'psycopg2 not available — please provide the database password'}

    try:
        psql_cmd = ['psql', '-h', str(p['host']), '-p', str(p['port']),
                    '-U', p['user'], '-d', p['dbname'], '-c', full_sql]
        result = subprocess.run(psql_cmd, env=dict(os.environ, PGPASSWORD=user_password),
                                capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            return {'success': True, 'method': 'psql'}
        errors.append('psql: %s' % (result.stderr.strip() or result.stdout.strip())[:200])
    except FileNotFoundError:
        errors.append('psql: not found on this server')
    except Exception as exc:
        errors.append('psql: %s' % str(exc))

    raise RuntimeError('All methods failed: ' + '; '.join(errors))


def _list_pg_connections() -> list:
    """Return PostgreSQL connections with metadata."""
    def _loader():
        try:
            client = g.client
            all_conns = client.list_connections()
            result = []
            items = all_conns.items() if isinstance(all_conns, dict) else [(c.get('name'), c) for c in all_conns]
            for name, info in items:
                if not isinstance(info, dict):
                    continue
                conn_type = info.get('type', '')
                if conn_type != 'PostgreSQL':
                    continue
                params = info.get('params', {})
                result.append({
                    'name': name,
                    'type': conn_type,
                    'host': params.get('host', ''),
                    'port': params.get('port', 5432),
                    'db': params.get('db', params.get('database', params.get('dbname', ''))),
                })
            return result
        except Exception as exc:
            logging.getLogger(__name__).warning("[db-health] list_connections failed: %s", exc)
            return []
    return _cache_get('_pg_connections', 300, _loader)


def _sanitize_pg_error(err_msg: str) -> str:
    """Strip internal paths and IPs from PostgreSQL error messages."""
    sanitized = re.sub(r'(/[^\s:]+)+', '<path>', str(err_msg))
    sanitized = re.sub(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', '<ip>', sanitized)
    return sanitized


def _validate_pg_connection(connection_name: str):
    """Validate connection name against known PostgreSQL connections. Returns error response or None."""
    if not connection_name:
        return jsonify({'error': 'Missing connection parameter'}), 400
    known = [c['name'] for c in _list_pg_connections()]
    if connection_name not in known:
        return jsonify({'error': 'Unknown or non-PostgreSQL connection'}), 400
    return None


_ACTUAL_READ_METHOD = {}  # tracks what actually worked per connection


class _NeedsPasswordError(RuntimeError):
    """Raised when DB auth fails and user must provide password."""
    pass


def _pg_query_rows(connection_name: str, sql: str, user_password: str = ''):
    """Execute a read query. Routes through the dbhealth-query macro when the
    active host is remote (so psycopg2 + .pgpass run on the target host's
    service account). Local path tries psycopg2 then psql fallback."""
    if _safe_request_host_id() != 'local':
        result = _dbhealth_macro(
            g.client,
            operation='run-query',
            sql=sql,
            connection=connection_name,
            password=user_password,
        )
        if not result.get('ok'):
            err = (result.get('error') or '').lower()
            if 'password authentication failed' in err or 'fe_sendauth' in err:
                raise _NeedsPasswordError(f"remote dbhealth auth failed: {result.get('error')}")
            raise RuntimeError(f"remote dbhealth query failed: {result.get('error')}")
        cols = result.get('columns') or []
        rows = result.get('rows') or []
        _ACTUAL_READ_METHOD[connection_name] = 'macro:dbhealth-query'
        return [dict(zip(cols, r)) for r in rows]

    driver = _ensure_pg_driver()
    if driver:
        try:
            conn = _pg_direct_connect(connection_name, user_password=user_password)
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    cols = [d[0] for d in cur.description]
                    _ACTUAL_READ_METHOD[connection_name] = driver
                    return [dict(zip(cols, row)) for row in cur.fetchall()]
            finally:
                conn.close()
        except Exception as exc:
            err_str = str(exc).lower()
            # Auth failure with stored password — ask user for the real one
            if not user_password and ('password authentication failed' in err_str or 'fe_sendauth' in err_str):
                raise _NeedsPasswordError("psycopg2 auth failed: %s" % exc)
            raise RuntimeError("psycopg2 query failed: %s" % exc)

    # psycopg2 not available — try psql with user-provided password
    if not user_password:
        raise _NeedsPasswordError("psycopg2 not available — password required for psql fallback")
    p = _get_pg_conn_params(connection_name)
    psql_cmd = [
        'psql', '-h', str(p['host']), '-p', str(p['port']),
        '-U', p['user'], '-d', p['dbname'],
        '-F', '\t', '--no-align', '-c', sql,
    ]
    try:
        result = subprocess.run(psql_cmd, env=dict(os.environ, PGPASSWORD=user_password),
                                capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError("psql: %s" % (result.stderr.strip() or 'exit %d' % result.returncode)[:200])
        all_lines = [l for l in result.stdout.strip().split('\n') if l.strip()]
        if len(all_lines) < 2:
            _ACTUAL_READ_METHOD[connection_name] = 'psql'
            return []
        headers = all_lines[0].split('\t')
        rows = []
        for line in all_lines[1:]:
            if line.startswith('(') and line.endswith(')'):
                continue
            vals = line.split('\t')
            rows.append(dict(zip(headers, vals)))
        _ACTUAL_READ_METHOD[connection_name] = 'psql'
        return rows
    except FileNotFoundError:
        raise RuntimeError("psql CLI not found on this server")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("psql failed: %s" % exc)


@app.route('/api/tools/db-health/connections')
def api_db_health_connections():
    try:
        cfg = _get_dbhealth_config()
        return jsonify({
            'connections': _list_pg_connections(),
            'configuredConnection': cfg.connection_name or '',
            'hasConfiguredPassword': bool(cfg.password),
        })
    except Exception as exc:
        return jsonify({'error': _sanitize_pg_error(str(exc))}), 500


@app.route('/api/tools/db-health/overview')
def api_db_health_overview():
    connection_name = request.args.get('connection', '')
    user_password = request.args.get('password', '') or _get_dbhealth_config().password or '' or _get_dbhealth_config().password or ''
    validation = _validate_pg_connection(connection_name)
    if validation:
        return validation

    driver = _ensure_pg_driver()

    warnings = []
    query_method = driver or ('psql' if user_password else 'none')
    result = {
        'dbSize': '', 'dbSizeBytes': 0, 'version': '',
        'tableCount': 0, 'totalDeadTuples': 0, 'totalLiveTuples': 0,
        'canWrite': False, 'queryMethod': query_method,
        'driverLog': list(_PG_DRIVER_LOG),
        'warnings': warnings,
    }
    try:
        rows = _pg_query_rows(connection_name,
            "SELECT pg_size_pretty(pg_database_size(current_database())) as db_size,"
            " pg_database_size(current_database()) as db_size_bytes,"
            " current_setting('server_version') as version",
            user_password=user_password)
        if rows:
            result['dbSize'] = str(rows[0].get('db_size', ''))
            result['dbSizeBytes'] = int(rows[0].get('db_size_bytes', 0))
            result['version'] = str(rows[0].get('version', ''))
    except _NeedsPasswordError as exc:
        return jsonify({
            'needsPassword': True,
            'driverLog': list(_PG_DRIVER_LOG),
            'reason': str(exc),
        })
    except Exception as exc:
        warnings.append('Could not fetch database size: %s' % _sanitize_pg_error(str(exc)))

    try:
        rows = _pg_query_rows(connection_name,
            "SELECT count(*) as table_count, coalesce(sum(n_dead_tup),0) as total_dead,"
            " coalesce(sum(n_live_tup),0) as total_live"
            " FROM pg_stat_user_tables",
            user_password=user_password)
        if rows:
            result['tableCount'] = int(rows[0].get('table_count', 0))
            result['totalDeadTuples'] = int(rows[0].get('total_dead', 0))
            result['totalLiveTuples'] = int(rows[0].get('total_live', 0))
    except Exception as exc:
        warnings.append('Could not fetch table stats: %s' % _sanitize_pg_error(str(exc)))

    # Detect write access — use same query path that already works for reads
    try:
        write_rows = _pg_query_rows(connection_name,
            "SELECT current_user as cu, current_setting('is_superuser') as su",
            user_password=user_password)
        if write_rows:
            cu = write_rows[0].get('cu', '')
            su = write_rows[0].get('su', '')
            if su == 'on':
                result['canWrite'] = True
        if not result['canWrite']:
            try:
                maint_rows = _pg_query_rows(connection_name,
                    "SELECT pg_has_role(current_user, 'pg_maintain', 'MEMBER') as m",
                    user_password=user_password)
                if maint_rows and maint_rows[0].get('m'):
                    result['canWrite'] = True
            except Exception:
                pass  # pg_maintain role may not exist on PG < 15
    except Exception:
        pass

    result['warnings'] = warnings
    return jsonify(result)


@app.route('/api/tools/db-health/tables')
def api_db_health_tables():
    connection_name = request.args.get('connection', '')
    user_password = request.args.get('password', '') or _get_dbhealth_config().password or ''
    validation = _validate_pg_connection(connection_name)
    if validation:
        return validation
    warnings = []
    tables = []
    try:
        rows = _pg_query_rows(connection_name,
            "SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) as total_size,"
            " pg_total_relation_size(relid) as total_size_bytes,"
            " n_live_tup, n_dead_tup,"
            " CASE WHEN n_live_tup + n_dead_tup > 0"
            "      THEN round(n_dead_tup::numeric / (n_live_tup + n_dead_tup), 4)"
            "      ELSE 0 END as bloat_ratio,"
            " last_vacuum, last_autovacuum, last_analyze"
            " FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC",
            user_password=user_password)
        for r in rows:
            tables.append({
                'name': str(r.get('relname', '')),
                'totalSize': str(r.get('total_size', '')),
                'totalSizeBytes': int(r.get('total_size_bytes', 0)),
                'rowCount': int(r.get('n_live_tup', 0)),
                'deadTuples': int(r.get('n_dead_tup', 0)),
                'bloatRatio': float(r.get('bloat_ratio', 0)),
                'lastVacuum': str(r.get('last_vacuum', '') or ''),
                'lastAutovacuum': str(r.get('last_autovacuum', '') or ''),
                'lastAnalyze': str(r.get('last_analyze', '') or ''),
            })
    except Exception as exc:
        warnings.append('Could not fetch table details: %s' % _sanitize_pg_error(str(exc)))
    return jsonify({'tables': tables, 'warnings': warnings})


@app.route('/api/tools/db-health/per-project')
def api_db_health_per_project():
    connection_name = request.args.get('connection', '')
    user_password = request.args.get('password', '') or _get_dbhealth_config().password or ''
    validation = _validate_pg_connection(connection_name)
    if validation:
        return validation
    warnings = []
    result = {'projects': [], 'system': {}, 'isRuntimeDb': False, 'warnings': warnings}
    try:
        # Detect RuntimeDB by checking for known tables
        detect_rows = _pg_query_rows(connection_name,
            "SELECT count(*) as cnt FROM pg_tables"
            " WHERE schemaname='public' AND lower(tablename) IN ('dss_metadata', 'scenario_runs', 'job')",
            user_password=user_password)
        is_runtime = detect_rows and int(detect_rows[0].get('cnt', 0)) >= 2
        result['isRuntimeDb'] = is_runtime

        # Get all tables with sizes
        table_rows = _pg_query_rows(connection_name,
            "SELECT relname, pg_total_relation_size(relid) as size_bytes, n_live_tup"
            " FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC",
            user_password=user_password)

        if not is_runtime:
            # Not RuntimeDB — all tables go to system bucket
            system_tables = []
            total_bytes = 0
            for r in table_rows:
                sz = int(r.get('size_bytes', 0))
                total_bytes += sz
                system_tables.append({
                    'name': str(r.get('relname', '')),
                    'sizeBytes': sz,
                    'rowCount': int(r.get('n_live_tup', 0)),
                })
            result['system'] = {'tables': system_tables, 'totalBytes': total_bytes}
            result['warnings'] = warnings
            return jsonify(result)

        # RuntimeDB — find project columns
        col_rows = _pg_query_rows(connection_name,
            "SELECT table_name, column_name FROM information_schema.columns"
            " WHERE table_schema='public'"
            " AND (column_name ILIKE '%%projectkey%%' OR column_name ILIKE '%%project_key%%')",
            user_password=user_password)
        table_project_col = {}
        for r in col_rows:
            tname = str(r.get('table_name', ''))
            cname = str(r.get('column_name', ''))
            if tname and cname:
                table_project_col[tname.lower()] = {'table': tname, 'column': cname}

        project_sizes: Dict[str, Dict[str, Any]] = {}
        system_tables = []
        system_total = 0

        for r in table_rows:
            relname = str(r.get('relname', ''))
            sz = int(r.get('size_bytes', 0))
            row_count = int(r.get('n_live_tup', 0))
            lookup = table_project_col.get(relname.lower())
            if not lookup:
                system_total += sz
                system_tables.append({'name': relname, 'sizeBytes': sz, 'rowCount': row_count})
                continue
            # Query per-project breakdown for this table
            try:
                proj_rows = _pg_query_rows(connection_name,
                    "SELECT \"%s\" as pkey, count(*) as cnt FROM \"%s\" GROUP BY \"%s\""
                    % (lookup['column'], lookup['table'], lookup['column']),
                    user_password=user_password)
                total_rows = sum(int(pr.get('cnt', 0)) for pr in proj_rows)
                for pr in proj_rows:
                    pkey = str(pr.get('pkey', '') or 'Unknown')
                    cnt = int(pr.get('cnt', 0))
                    # Estimate size proportional to row count
                    est_size = int(sz * cnt / total_rows) if total_rows > 0 else 0
                    if pkey not in project_sizes:
                        project_sizes[pkey] = {'projectKey': pkey, 'sizeBytes': 0, 'tableCount': 0, 'rowCount': 0}
                    project_sizes[pkey]['sizeBytes'] += est_size
                    project_sizes[pkey]['tableCount'] += 1
                    project_sizes[pkey]['rowCount'] += cnt
            except Exception as exc:
                warnings.append('Could not break down table %s: %s' % (relname, _sanitize_pg_error(str(exc))))
                system_total += sz
                system_tables.append({'name': relname, 'sizeBytes': sz, 'rowCount': row_count})

        result['projects'] = sorted(project_sizes.values(), key=lambda p: p['sizeBytes'], reverse=True)
        result['system'] = {'tables': system_tables, 'totalBytes': system_total}
    except Exception as exc:
        warnings.append('Per-project query failed: %s' % _sanitize_pg_error(str(exc)))
    result['warnings'] = warnings
    return jsonify(result)


@app.route('/api/tools/db-health/vacuum', methods=['POST'])
@advanced
def api_db_health_vacuum():
    body = request.get_json(force=True, silent=True) or {}
    connection_name = body.get('connection', '')
    table_name = body.get('table', '')
    validation = _validate_pg_connection(connection_name)
    if validation:
        return validation
    if not table_name:
        return jsonify({'error': 'Missing table parameter'}), 400

    user_password = body.get('password', '') or _get_dbhealth_config().password or ''

    # Whitelist: validate table name against pg_stat_user_tables
    try:
        valid_tables = _pg_query_rows(connection_name,
            "SELECT relname FROM pg_stat_user_tables",
            user_password=user_password)
        valid_names = {str(r.get('relname', '')) for r in valid_tables}
        if table_name not in valid_names:
            return jsonify({'error': 'Invalid table name'}), 400
    except Exception as exc:
        return jsonify({'error': 'Could not validate table: %s' % _sanitize_pg_error(str(exc))}), 500

    try:
        result = _pg_exec_ddl(connection_name, "VACUUM {}", table_name, user_password=user_password)
        return jsonify(result)
    except Exception as exc:
        return jsonify({'error': _sanitize_pg_error(str(exc))}), 500


@app.route('/api/tools/db-health/analyze', methods=['POST'])
@advanced
def api_db_health_analyze():
    body = request.get_json(force=True, silent=True) or {}
    connection_name = body.get('connection', '')
    table_name = body.get('table', '')
    validation = _validate_pg_connection(connection_name)
    if validation:
        return validation
    if not table_name:
        return jsonify({'error': 'Missing table parameter'}), 400

    user_password = body.get('password', '') or _get_dbhealth_config().password or ''

    # Whitelist: validate table name against pg_stat_user_tables
    try:
        valid_tables = _pg_query_rows(connection_name,
            "SELECT relname FROM pg_stat_user_tables",
            user_password=user_password)
        valid_names = {str(r.get('relname', '')) for r in valid_tables}
        if table_name not in valid_names:
            return jsonify({'error': 'Invalid table name'}), 400
    except Exception as exc:
        return jsonify({'error': 'Could not validate table: %s' % _sanitize_pg_error(str(exc))}), 500

    try:
        result = _pg_exec_ddl(connection_name, "ANALYZE {}", table_name, user_password=user_password)
        return jsonify(result)
    except Exception as exc:
        return jsonify({'error': _sanitize_pg_error(str(exc))}), 500



# ── Image Cleaner (multi-cloud: ECR, ACR, GAR) ─────────────────────────

_IMAGE_CLEANER_CLIENTS: Dict[Tuple[str, str], Any] = {}
_IMAGE_CLEANER_CLIENTS_LOCK = threading.Lock()


def _ensure_pkg(import_name: str, pip_name: Optional[str] = None, log_tag: str = 'image-cleaner'):
    """Import a package, auto-installing if necessary. 5-attempt strategy (same as legacy _ensure_boto3)."""
    pip_name = pip_name or import_name
    try:
        return __import__(import_name)
    except ImportError:
        pass

    safe_tag = import_name.replace('.', '_')
    _tmp_target = os.path.join(tempfile.gettempdir(), 'dku_%s' % safe_tag)
    _datadir_target = os.path.join(os.environ.get('DIP_HOME', '/tmp'), 'lib', 'python', safe_tag)
    install_attempts = [
        ('pip install (default)', [sys.executable, '-m', 'pip', 'install', pip_name, '--quiet']),
        ('pip install --user', [sys.executable, '-m', 'pip', 'install', pip_name, '--quiet', '--user']),
        ('pip install --break-system-packages', [sys.executable, '-m', 'pip', 'install', pip_name, '--quiet', '--break-system-packages']),
        ('pip install --target %s' % _tmp_target, [sys.executable, '-m', 'pip', 'install', pip_name, '--quiet', '--target', _tmp_target]),
        ('pip install --target %s' % _datadir_target, [sys.executable, '-m', 'pip', 'install', pip_name, '--quiet', '--target', _datadir_target]),
    ]
    for label, cmd in install_attempts:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                continue
            for tgt in (_tmp_target, _datadir_target):
                if tgt not in sys.path and os.path.isdir(tgt):
                    sys.path.insert(0, tgt)
            try:
                mod = __import__(import_name)
                app.logger.info("[%s] %s installed via %s", log_tag, import_name, label)
                return mod
            except ImportError:
                pass
        except Exception:
            pass

    raise ImportError("%s is not installed and auto-install failed. Install %s in the DSS Python environment."
                      % (import_name, pip_name))


def _ensure_boto3():
    return _ensure_pkg('boto3', 'boto3', 'image-cleaner')


def _parse_version_tuple(v):
    """Parse '14.5.1' or '14.5.1-beta1' into (major, minor, patch); return None if unparseable."""
    m = re.match(r'^(\d+)\.(\d+)\.(\d+)', v)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _pick_closest_dss_version(requested, available):
    """Pick the best download-page entry for `requested` from `available` (list of (version, date)).

    Order of preference:
      1. Exact match.
      2. Base version with any pre-release suffix stripped (e.g. 14.5.1-beta1 → 14.5.1).
      3. Highest stable (non-prerelease) version with (major, minor, patch) <= requested.
    Returns (version, date) or None.
    """
    for v, d in available:
        if v == requested:
            return v, d
    base = re.split(r'[-+]', requested, 1)[0]
    if base != requested:
        for v, d in available:
            if v == base:
                return v, d
    req_key = _parse_version_tuple(requested)
    if req_key is None:
        return None
    best = None
    for v, d in available:
        if re.search(r'[-+]', v):
            continue
        vk = _parse_version_tuple(v)
        if vk is None or vk > req_key:
            continue
        if best is None or vk > best[0]:
            best = (vk, v, d)
    return (best[1], best[2]) if best else None


def _image_cleaner_release_info():
    """Get DSS version and its release date from downloads.dataiku.com.

    Falls back to the closest stable version if the exact (e.g. beta) version is not published.
    """
    from datetime import timedelta
    import urllib.request

    t0 = time.time()
    if _safe_request_host_id() != 'local':
        metrics = _cache_get('host_metrics', _BACKEND_SETTINGS['cache_ttl_overview'], lambda: _host_metrics_macro(g.client))
        version_info = metrics.get('version') if isinstance(metrics, dict) else {}
    else:
        dip_home = _dip_home()
        version_info = _safe_read_json(os.path.join(dip_home, 'dss-version.json')) or {}
    version = version_info.get('product_version') or version_info.get('version') or version_info.get('dssVersion')
    if not version:
        raise ValueError("Cannot determine DSS version from dss-version.json")
    t1 = time.time()
    app.logger.info("[perf:image-cleaner] version_read=%.0fms version=%s", (t1 - t0) * 1000, version)

    url = 'https://downloads.dataiku.com/public/dss/'
    req = urllib.request.Request(url, headers={'User-Agent': 'AdminToolkit/1.0'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        html = resp.read().decode('utf-8', errors='replace')
    t2 = time.time()
    app.logger.info("[perf:image-cleaner] http_fetch=%.0fms url=%s bytes=%d", (t2 - t1) * 1000, url, len(html))

    available = re.findall(
        r'<a href="([^"/]+)/">\1/</a></td>\s*<td[^>]*>\s*(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}',
        html,
    )
    picked = _pick_closest_dss_version(version, available)
    t3 = time.time()
    app.logger.info(
        "[perf:image-cleaner] regex_parse=%.0fms versions_available=%d picked=%s",
        (t3 - t2) * 1000, len(available), picked[0] if picked else None,
    )
    if not picked:
        raise ValueError(
            "DSS version %s not found on downloads.dataiku.com and no fallback version available" % version
        )

    matched_version, matched_date = picked
    fallback_used = matched_version != version
    if fallback_used:
        app.logger.warning(
            "[image-cleaner] DSS version %s not published; falling back to closest available: %s (%s)",
            version, matched_version, matched_date,
        )

    release_date = datetime.strptime(matched_date, '%Y-%m-%d').date()
    max_cutoff = release_date - timedelta(days=2)

    return {
        'version': version,
        'matchedVersion': matched_version,
        'releaseDate': matched_date,
        'maxCutoffDate': max_cutoff.isoformat(),
        'fallbackUsed': fallback_used,
    }


def _image_cleaner_validate_cutoff(cutoff_str):
    """Validate cutoff and enforce server-side max. Returns (cutoff_date, release_info)."""
    try:
        cutoff = datetime.strptime(cutoff_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        raise ValueError("Invalid cutoff date format, expected YYYY-MM-DD")
    info = _image_cleaner_release_info()
    max_cutoff = datetime.strptime(info['maxCutoffDate'], '%Y-%m-%d').date()
    if cutoff > max_cutoff:
        raise ValueError("Cutoff %s exceeds maximum allowed %s" % (cutoff_str, info['maxCutoffDate']))
    return cutoff, info


def _matches_dataiku(name: str) -> bool:
    n = (name or '').lower()
    return 'dataiku' in n or 'dku' in n


# ── RegistryAdapter interface ──

class RegistryAdapter:
    """Base. Subclasses implement list_repositories / list_images / head_image / delete_images.

    list_images returns [{digest, tags, pushedAt (isoformat)}]
    head_image returns {pushedAt: date} or None if missing
    delete_images returns (deleted, failed) — lists of {repo, digest[, reason]}
    """
    provider = ''

    def list_repositories(self) -> List[str]:
        raise NotImplementedError

    def list_images(self, repo: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def head_image(self, repo: str, digest: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def delete_images(self, repo: str, digests: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        raise NotImplementedError


# ── EcrAdapter ──

class EcrAdapter(RegistryAdapter):
    provider = 'ecr'

    def __init__(self, region: str):
        if not region:
            raise ValueError("Cannot detect AWS region. Set AWS_DEFAULT_REGION environment variable "
                             "or configure a region in ~/.aws/config on the DSS server.")
        boto3 = _ensure_boto3()
        self._client = boto3.client('ecr', region_name=region)
        self._region = region
        app.logger.info("[image-cleaner] ecr client created region=%s", region)

    def list_repositories(self) -> List[str]:
        out: List[str] = []
        pag = self._client.get_paginator('describe_repositories')
        for page in pag.paginate():
            for r in page.get('repositories', []):
                name = r['repositoryName']
                if _matches_dataiku(name):
                    out.append(name)
        out.sort()
        return out

    def list_images(self, repo: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        pag = self._client.get_paginator('describe_images')
        for page in pag.paginate(repositoryName=repo):
            for img in page.get('imageDetails', []):
                pushed = img.get('imagePushedAt')
                if pushed is None:
                    continue
                out.append({
                    'digest': img.get('imageDigest', ''),
                    'tags': img.get('imageTags', []),
                    'pushedAt': pushed.isoformat() if hasattr(pushed, 'isoformat') else str(pushed),
                })
        return out

    def head_image(self, repo: str, digest: str) -> Optional[Dict[str, Any]]:
        try:
            resp = self._client.describe_images(repositoryName=repo, imageIds=[{'imageDigest': digest}])
        except Exception:
            return None
        details = resp.get('imageDetails', [])
        if not details:
            return None
        pushed = details[0].get('imagePushedAt')
        if pushed is None:
            return None
        pushed_date = pushed.date() if hasattr(pushed, 'date') else datetime.fromisoformat(str(pushed)).date()
        return {'pushedAt': pushed_date}

    def delete_images(self, repo: str, digests: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        deleted: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        try:
            resp = self._client.batch_delete_image(
                repositoryName=repo,
                imageIds=[{'imageDigest': d} for d in digests],
            )
            for d in resp.get('imageIds', []):
                deleted.append({'repo': repo, 'digest': d.get('imageDigest', '')})
            for f in resp.get('failures', []):
                failed.append({
                    'repo': repo,
                    'digest': f.get('imageId', {}).get('imageDigest', ''),
                    'reason': f.get('failureReason', ''),
                })
        except Exception as e:
            for d in digests:
                failed.append({'repo': repo, 'digest': d, 'reason': str(e)})
        return deleted, failed


# ── AcrAdapter (raw REST) ──
# NOTE: Response field names (lastUpdateTime, manifests[]) taken from Azure docs;
# needs one live run against a real ACR to confirm — see verification block in plan.

class AcrAdapter(RegistryAdapter):
    provider = 'acr'

    def __init__(self, registry_host: str):
        if not registry_host:
            raise ValueError("Cannot detect ACR registry host from DSS containerSettings. "
                             "Configure an executionConfig with a *.azurecr.io repositoryURL.")
        _ensure_pkg('azure.identity', 'azure-identity', 'image-cleaner')
        from azure.identity import DefaultAzureCredential
        cred = DefaultAzureCredential()
        self._host = registry_host.rstrip('/')
        self._registry_url = 'https://' + self._host
        aad = cred.get_token('https://management.azure.com/.default')
        self._aad_token = aad.token
        app.logger.info("[image-cleaner] acr adapter created host=%s", self._host)

    def _get_access_token(self, scope: str) -> str:
        import urllib.request, urllib.parse
        data = urllib.parse.urlencode({
            'grant_type': 'access_token',
            'service': self._host,
            'access_token': self._aad_token,
        }).encode()
        req = urllib.request.Request(
            self._registry_url + '/oauth2/exchange',
            data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            refresh_token = json.loads(resp.read().decode())['refresh_token']

        data2 = urllib.parse.urlencode({
            'grant_type': 'refresh_token',
            'service': self._host,
            'scope': scope,
            'refresh_token': refresh_token,
        }).encode()
        req2 = urllib.request.Request(
            self._registry_url + '/oauth2/token',
            data=data2,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            method='POST',
        )
        with urllib.request.urlopen(req2, timeout=10) as resp2:
            return json.loads(resp2.read().decode())['access_token']

    def _paginated_get(self, path: str, scope: str) -> List[Dict[str, Any]]:
        import urllib.request
        tok = self._get_access_token(scope)
        bodies: List[Dict[str, Any]] = []
        while True:
            req = urllib.request.Request(
                self._registry_url + path,
                headers={'Authorization': 'Bearer ' + tok},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode())
                link_hdr = resp.headers.get('Link', '')
            bodies.append(body)
            if not link_hdr:
                break
            m = re.search(r'<([^>]+)>;\s*rel="next"', link_hdr)
            if not m:
                break
            path = m.group(1)
        return bodies

    def list_repositories(self) -> List[str]:
        out: List[str] = []
        for body in self._paginated_get('/acr/v1/_catalog', 'registry:catalog:*'):
            for name in body.get('repositories', []):
                if _matches_dataiku(name):
                    out.append(name)
        out.sort()
        return out

    def list_images(self, repo: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for body in self._paginated_get('/acr/v1/%s/_manifests' % repo, 'repository:%s:metadata_read' % repo):
            for m in body.get('manifests', []):
                pushed = m.get('lastUpdateTime') or m.get('createdTime')
                if not pushed:
                    continue
                out.append({
                    'digest': m.get('digest', ''),
                    'tags': list(m.get('tags', []) or []),
                    'pushedAt': str(pushed),
                })
        return out

    def head_image(self, repo: str, digest: str) -> Optional[Dict[str, Any]]:
        import urllib.request
        try:
            tok = self._get_access_token('repository:%s:metadata_read' % repo)
            req = urllib.request.Request(
                self._registry_url + '/acr/v1/%s/_manifests/%s' % (repo, digest),
                headers={'Authorization': 'Bearer ' + tok},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read().decode())
        except Exception:
            return None
        pushed = body.get('lastUpdateTime') or body.get('createdTime')
        if not pushed:
            return None
        try:
            pushed_date = datetime.fromisoformat(str(pushed).replace('Z', '+00:00')).date()
        except Exception:
            return None
        return {'pushedAt': pushed_date}

    def delete_images(self, repo: str, digests: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        import urllib.request
        tok = self._get_access_token('repository:%s:delete' % repo)
        deleted: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        for d in digests:
            try:
                req = urllib.request.Request(
                    self._registry_url + '/v2/%s/manifests/%s' % (repo, d),
                    headers={'Authorization': 'Bearer ' + tok},
                    method='DELETE',
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if 200 <= resp.status < 300:
                        deleted.append({'repo': repo, 'digest': d})
                    else:
                        failed.append({'repo': repo, 'digest': d, 'reason': 'HTTP %s' % resp.status})
            except Exception as e:
                failed.append({'repo': repo, 'digest': d, 'reason': str(e)})
        return deleted, failed


# ── GarAdapter ──
# NOTE: DeleteVersion path construction from docs; needs one live run to confirm.

class GarAdapter(RegistryAdapter):
    provider = 'gar'

    def __init__(self, project: Optional[str], location: Optional[str]):
        if not project or not location:
            raise ValueError("Cannot detect GCP project/location for Artifact Registry. "
                             "Set GOOGLE_APPLICATION_CREDENTIALS or run on GCE, or configure "
                             "containerSettings with a *-docker.pkg.dev repositoryURL.")
        _ensure_pkg('google.auth', 'google-auth', 'image-cleaner')
        _ensure_pkg('google.cloud.artifactregistry_v1', 'google-cloud-artifact-registry', 'image-cleaner')
        from google.cloud import artifactregistry_v1
        self._client = artifactregistry_v1.ArtifactRegistryClient()
        self._project = project
        self._location = location
        app.logger.info("[image-cleaner] gar client created project=%s location=%s", project, location)

    def _parent(self) -> str:
        return 'projects/%s/locations/%s' % (self._project, self._location)

    def list_repositories(self) -> List[str]:
        from google.cloud import artifactregistry_v1
        out: List[str] = []
        req = artifactregistry_v1.ListRepositoriesRequest(parent=self._parent())
        for repo in self._client.list_repositories(request=req):
            fmt = getattr(repo, 'format_', None)
            if fmt is not None and getattr(fmt, 'name', '') != 'DOCKER':
                continue
            short = repo.name.split('/')[-1]
            if _matches_dataiku(short):
                out.append(repo.name)  # full resource name — consumed by list_images
        out.sort()
        return out

    def list_images(self, repo: str) -> List[Dict[str, Any]]:
        from google.cloud import artifactregistry_v1
        out: List[Dict[str, Any]] = []
        req = artifactregistry_v1.ListDockerImagesRequest(parent=repo)
        for img in self._client.list_docker_images(request=req):
            pushed = img.upload_time
            digest = img.name.split('@')[-1] if '@' in img.name else img.name.split('/')[-1]
            out.append({
                'digest': digest,
                'tags': list(img.tags) if img.tags else [],
                'pushedAt': pushed.isoformat() if pushed else '',
            })
        return out

    def head_image(self, repo: str, digest: str) -> Optional[Dict[str, Any]]:
        from google.cloud import artifactregistry_v1
        try:
            req = artifactregistry_v1.ListDockerImagesRequest(parent=repo)
            for img in self._client.list_docker_images(request=req):
                if digest in img.name:
                    pushed = img.upload_time
                    if not pushed:
                        return None
                    return {'pushedAt': pushed.date()}
        except Exception:
            return None
        return None

    def delete_images(self, repo: str, digests: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        from google.cloud import artifactregistry_v1
        deleted: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        name_by_digest: Dict[str, str] = {}
        try:
            req = artifactregistry_v1.ListDockerImagesRequest(parent=repo)
            for img in self._client.list_docker_images(request=req):
                for d in digests:
                    if d in img.name:
                        name_by_digest[d] = img.name
        except Exception as e:
            for d in digests:
                failed.append({'repo': repo, 'digest': d, 'reason': 'list failed: %s' % e})
            return deleted, failed

        for d in digests:
            full_name = name_by_digest.get(d)
            if not full_name:
                failed.append({'repo': repo, 'digest': d, 'reason': 'image not found'})
                continue
            try:
                pkg_part, _, dg = full_name.partition('@')
                pkg_name = pkg_part.replace('/dockerImages/', '/packages/')
                version_name = '%s/versions/%s' % (pkg_name, dg)
                del_req = artifactregistry_v1.DeleteVersionRequest(name=version_name, force=True)
                op = self._client.delete_version(request=del_req)
                op.result(timeout=60)
                deleted.append({'repo': repo, 'digest': d})
            except Exception as e:
                failed.append({'repo': repo, 'digest': d, 'reason': str(e)})
        return deleted, failed


# ── Detection ──

def _image_cleaner_walk_container_settings() -> Optional[Dict[str, str]]:
    """Walk containerSettings.executionConfigs[] looking for a recognizable registry URL.
    Returns {provider, registryUrl} or None. Never raises."""
    try:
        try:
            client = g.client
        except RuntimeError:
            client = dataiku.api_client()
        settings = client.get_general_settings().get_raw()
    except Exception:
        return None
    cs = settings.get('containerSettings') if isinstance(settings, dict) else None
    if not isinstance(cs, dict):
        return None

    configs = cs.get('executionConfigs') or []
    default_name = cs.get('defaultExecutionConfig')
    ordered: List[Dict[str, Any]] = []
    for c in configs:
        if isinstance(c, dict) and c.get('name') == default_name:
            ordered.insert(0, c)
        elif isinstance(c, dict):
            ordered.append(c)
    generic = cs.get('executionConfigsGenericOverrides')
    if isinstance(generic, dict):
        ordered.append(generic)

    ecr_re = re.compile(r'^(?:https?://)?\d+\.dkr\.ecr\.([a-z0-9-]+)\.amazonaws\.com', re.I)
    acr_re = re.compile(r'^(?:https?://)?([a-zA-Z0-9]+\.azurecr\.io)', re.I)
    gar_re = re.compile(r'^(?:https?://)?([a-z0-9-]+-docker\.pkg\.dev|(?:[a-z0-9-]+\.)?gcr\.io)', re.I)

    for c in ordered:
        url = (c.get('repositoryURL') or '').strip()
        if not url:
            continue
        if ecr_re.match(url):
            return {'provider': 'ecr', 'registryUrl': url}
        if acr_re.match(url):
            return {'provider': 'acr', 'registryUrl': url}
        if gar_re.match(url):
            return {'provider': 'gar', 'registryUrl': url}
    return None


def _imds_probe_aws(timeout: float = 2.0) -> Optional[str]:
    import urllib.request
    try:
        token_req = urllib.request.Request(
            'http://169.254.169.254/latest/api/token',
            headers={'X-aws-ec2-metadata-token-ttl-seconds': '30'},
            method='PUT',
        )
        token = urllib.request.urlopen(token_req, timeout=timeout).read().decode().strip()
        region_req = urllib.request.Request(
            'http://169.254.169.254/latest/meta-data/placement/region',
            headers={'X-aws-ec2-metadata-token': token},
        )
        return urllib.request.urlopen(region_req, timeout=timeout).read().decode().strip() or None
    except Exception:
        return None


def _imds_probe_azure(timeout: float = 2.0) -> Optional[str]:
    import urllib.request
    try:
        req = urllib.request.Request(
            'http://169.254.169.254/metadata/instance?api-version=2021-02-01',
            headers={'Metadata': 'true'},
        )
        body = urllib.request.urlopen(req, timeout=timeout).read().decode()
        data = json.loads(body)
        return (data.get('compute') or {}).get('location') or None
    except Exception:
        return None


def _imds_probe_gcp(timeout: float = 2.0) -> Optional[str]:
    import urllib.request
    try:
        req = urllib.request.Request(
            'http://metadata.google.internal/computeMetadata/v1/project/project-id',
            headers={'Metadata-Flavor': 'Google'},
        )
        return urllib.request.urlopen(req, timeout=timeout).read().decode().strip() or None
    except Exception:
        return None


def _imds_probe_gcp_zone(timeout: float = 2.0) -> Optional[str]:
    import urllib.request
    try:
        req = urllib.request.Request(
            'http://metadata.google.internal/computeMetadata/v1/instance/zone',
            headers={'Metadata-Flavor': 'Google'},
        )
        z = urllib.request.urlopen(req, timeout=timeout).read().decode().strip()
        return z.split('/')[-1] if z else None
    except Exception:
        return None


def _imds_probe_parallel() -> Optional[Dict[str, str]]:
    """Race AWS/Azure/GCP IMDS probes; return {provider, hint} of first hit."""
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {
            ex.submit(_imds_probe_aws): 'ecr',
            ex.submit(_imds_probe_azure): 'acr',
            ex.submit(_imds_probe_gcp): 'gar',
        }
        try:
            for fut in as_completed(list(futures), timeout=3):
                try:
                    result = fut.result()
                except Exception:
                    continue
                if result:
                    return {'provider': futures[fut], 'hint': result}
        except FuturesTimeoutError:
            pass
    return None


def _ipnet_probe() -> Optional[str]:
    """Option C: look up outbound IP → whereismyinstance.com → cloud."""
    import urllib.request
    ip: Optional[str] = None
    for url in ('https://checkip.amazonaws.com', 'https://api.ipify.org'):
        try:
            ip = urllib.request.urlopen(url, timeout=3).read().decode().strip()
            if ip:
                break
        except Exception:
            continue
    if not ip:
        return None
    try:
        with urllib.request.urlopen('https://whereismyinstance.com/api/%s' % ip, timeout=5) as resp:
            body = json.loads(resp.read().decode())
    except Exception:
        return None
    cloud = (body.get('cloud') or '').lower()
    if 'amazon' in cloud:
        return 'ecr'
    if 'microsoft' in cloud or 'azure' in cloud:
        return 'acr'
    if 'google' in cloud:
        return 'gar'
    return None


def _ecr_detect_region() -> Optional[str]:
    for var in ('AWS_DEFAULT_REGION', 'AWS_REGION'):
        val = os.environ.get(var, '').strip()
        if val:
            return val
    r = _imds_probe_aws()
    if r:
        return r
    try:
        result = subprocess.run(['aws', 'configure', 'get', 'region'],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    # Also try containerSettings (repositoryURL gives us the region)
    info = _image_cleaner_walk_container_settings()
    if info and info.get('provider') == 'ecr':
        m = re.match(r'.*\.dkr\.ecr\.([a-z0-9-]+)\.amazonaws\.com', info['registryUrl'], re.I)
        if m:
            return m.group(1)
    return None


def _acr_detect_registry() -> Optional[str]:
    info = _image_cleaner_walk_container_settings()
    if info and info.get('provider') == 'acr':
        return info['registryUrl'].replace('https://', '').replace('http://', '').rstrip('/')
    return None


def _gar_detect_project_location() -> Tuple[Optional[str], Optional[str]]:
    info = _image_cleaner_walk_container_settings()
    if info and info.get('provider') == 'gar':
        url = info['registryUrl'].replace('https://', '').replace('http://', '')
        m = re.match(r'^([a-z0-9-]+)-docker\.pkg\.dev/([^/]+)', url, re.I)
        if m:
            return m.group(2), m.group(1)
        m2 = re.match(r'^(?:([a-z0-9-]+)\.)?gcr\.io/([^/]+)', url, re.I)
        if m2:
            legacy = (m2.group(1) or 'us').lower()
            loc = {'us': 'us', 'eu': 'europe', 'asia': 'asia'}.get(legacy, 'us')
            return m2.group(2), loc
    try:
        _ensure_pkg('google.auth', 'google-auth', 'image-cleaner')
        import google.auth
        _creds, project = google.auth.default()
        zone = _imds_probe_gcp_zone()
        location = zone.rsplit('-', 1)[0] if zone else 'us'
        return project, location
    except Exception:
        return None, None


def _image_cleaner_adapter(provider: str) -> RegistryAdapter:
    """Return a cached adapter for the given provider. Raises on misconfiguration."""
    provider = (provider or '').lower().strip()
    if provider == 'ecr':
        scope = _ecr_detect_region() or ''
    elif provider == 'acr':
        scope = _acr_detect_registry() or ''
    elif provider == 'gar':
        proj, loc = _gar_detect_project_location()
        scope = '%s/%s' % (proj or '', loc or '')
    else:
        raise ValueError("Unknown provider %r (expected ecr|acr|gar)" % provider)
    key = (provider, scope)
    with _IMAGE_CLEANER_CLIENTS_LOCK:
        if key not in _IMAGE_CLEANER_CLIENTS:
            if provider == 'ecr':
                _IMAGE_CLEANER_CLIENTS[key] = EcrAdapter(region=scope)
            elif provider == 'acr':
                _IMAGE_CLEANER_CLIENTS[key] = AcrAdapter(registry_host=scope)
            else:  # gar
                proj, loc = _gar_detect_project_location()
                _IMAGE_CLEANER_CLIENTS[key] = GarAdapter(project=proj, location=loc)
        return _IMAGE_CLEANER_CLIENTS[key]


def _image_cleaner_error_hint(provider: str) -> str:
    if provider == 'ecr':
        return "Ensure the DSS host has an AWS IAM role or access keys with ECR read/delete permissions."
    if provider == 'acr':
        return "Run `az login` on the DSS host, or assign a managed identity with AcrDelete role."
    if provider == 'gar':
        return "Set GOOGLE_APPLICATION_CREDENTIALS or attach a GCP service account with artifactregistry.repositories.deletePackages."
    return ""


# ── Endpoints ──

@app.route('/api/tools/image-cleaner/detect-provider')
def api_image_cleaner_detect_provider():
    """A (containerSettings) → B (IMDS race) → C (whereismyinstance). Never throws."""
    t0 = time.time()
    a = _image_cleaner_walk_container_settings()
    if a:
        app.logger.info("[image-cleaner] detect via dss-config in %.0fms", (time.time()-t0)*1000)
        return jsonify({'provider': a['provider'], 'registryUrl': a['registryUrl'], 'source': 'dss-config'})
    if _safe_request_host_id() != 'local':
        try:
            result = _image_cleaner_macro(g.client, 'detect-provider')
        except Exception as e:
            app.logger.error("[image-cleaner] remote detect macro failed: %s", e)
            return jsonify({
                'provider': None,
                'registryUrl': None,
                'source': 'target-macro',
                'error': str(e),
            }), 502
        app.logger.info("[image-cleaner] remote detect via macro in %.0fms", (time.time()-t0)*1000)
        return jsonify({
            'provider': result.get('provider'),
            'registryUrl': result.get('registryUrl'),
            'source': result.get('source') or 'target-macro',
            'error': result.get('error'),
        })
    b = _imds_probe_parallel()
    if b:
        app.logger.info("[image-cleaner] detect via imds in %.0fms", (time.time()-t0)*1000)
        return jsonify({'provider': b['provider'], 'registryUrl': None, 'source': 'imds'})
    c = _ipnet_probe()
    if c:
        app.logger.info("[image-cleaner] detect via ipnet in %.0fms", (time.time()-t0)*1000)
        return jsonify({'provider': c, 'registryUrl': None, 'source': 'ipnet'})
    app.logger.info("[image-cleaner] detect MISS in %.0fms", (time.time()-t0)*1000)
    return jsonify({'provider': None, 'registryUrl': None, 'source': 'none'})


@app.route('/api/tools/image-cleaner/release-date')
def api_image_cleaner_release_date():
    t0 = time.time()
    provider = (request.args.get('provider') or 'ecr').strip().lower()
    try:
        info = _image_cleaner_release_info()
        if _safe_request_host_id() == 'local':
            try:
                t3 = time.time()
                _image_cleaner_adapter(provider)
                app.logger.info("[perf:image-cleaner] adapter_prewarm=%.0fms provider=%s", (time.time()-t3)*1000, provider)
            except Exception as e:
                app.logger.info("[perf:image-cleaner] adapter_prewarm FAILED provider=%s: %s", provider, e)
        app.logger.info("[perf:image-cleaner] release-date total=%.0fms provider=%s", (time.time()-t0)*1000, provider)
        return jsonify(info)
    except Exception as e:
        app.logger.error("[image-cleaner] release-date error (%.0fms): %s", (time.time()-t0)*1000, e)
        return jsonify({'error': str(e)}), 500


@app.route('/api/tools/image-cleaner/scan')
def api_image_cleaner_scan():
    provider = (request.args.get('provider') or 'ecr').strip().lower()
    cutoff_str = request.args.get('cutoff', '').strip()
    if not cutoff_str:
        return jsonify({'error': 'Missing cutoff parameter'}), 400
    try:
        cutoff, info = _image_cleaner_validate_cutoff(cutoff_str)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    if _safe_request_host_id() != 'local':
        def generate_remote():
            t0 = time.time()
            try:
                result = _image_cleaner_macro(g.client, 'scan', provider=provider, cutoff=cutoff_str)
            except Exception as e:
                app.logger.error("[image-cleaner] remote scan macro failed: %s", e)
                yield "event: error\ndata: %s\n\n" % json.dumps({
                    'error': str(e),
                    'provider': provider,
                    'hint': _image_cleaner_error_hint(provider),
                })
                return
            if not result.get('ok'):
                yield "event: error\ndata: %s\n\n" % json.dumps({
                    'error': result.get('error') or 'Remote image-cleaner macro failed',
                    'provider': provider,
                    'hint': _image_cleaner_error_hint(provider),
                })
                return
            repos = result.get('repos') or []
            yield "event: init\ndata: %s\n\n" % json.dumps({
                "total": len(repos),
                "cutoff": cutoff_str,
                "maxCutoffDate": info['maxCutoffDate'],
                "provider": provider,
                "source": "target-macro",
            })
            for repo in repos:
                yield "event: repo\ndata: %s\n\n" % json.dumps(repo)
            yield "event: done\ndata: %s\n\n" % json.dumps({"total_ms": int((time.time()-t0)*1000)})

        return Response(stream_with_context(generate_remote()), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    def generate():
        t0 = time.time()
        try:
            adapter = _image_cleaner_adapter(provider)
            repos = adapter.list_repositories()
        except Exception as e:
            yield "event: error\ndata: %s\n\n" % json.dumps({
                'error': str(e),
                'provider': provider,
                'hint': _image_cleaner_error_hint(provider),
            })
            return

        yield "event: init\ndata: %s\n\n" % json.dumps({
            "total": len(repos),
            "cutoff": cutoff_str,
            "maxCutoffDate": info['maxCutoffDate'],
            "provider": provider,
        })

        for repo in repos:
            try:
                raw = adapter.list_images(repo)
                images: List[Dict[str, Any]] = []
                for img in raw:
                    pushed_iso = img.get('pushedAt', '')
                    if not pushed_iso:
                        continue
                    try:
                        pushed_date = datetime.fromisoformat(str(pushed_iso).replace('Z', '+00:00')).date()
                    except Exception:
                        continue
                    images.append({
                        'digest': img.get('digest', ''),
                        'tags': img.get('tags', []) or [],
                        'pushedAt': pushed_iso,
                        'deletable': pushed_date < cutoff,
                    })
                images.sort(key=lambda x: x['pushedAt'])
                repo_display = repo.split('/')[-1] if provider == 'gar' else repo
                yield "event: repo\ndata: %s\n\n" % json.dumps({'name': repo_display, 'images': images})
            except Exception as e:
                yield "event: repo\ndata: %s\n\n" % json.dumps({'name': repo, 'images': [], 'error': str(e)})

        yield "event: done\ndata: %s\n\n" % json.dumps({"total_ms": int((time.time()-t0)*1000)})

    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/tools/image-cleaner/delete', methods=['POST'])
@advanced
def api_image_cleaner_delete():
    body = request.get_json(force=True, silent=True) or {}
    provider = (body.get('provider') or 'ecr').strip().lower()
    cutoff_str = (body.get('cutoff') or '').strip()
    images = body.get('images', [])
    dry_run = bool(body.get('dryRun', False))
    if not cutoff_str:
        return jsonify({'error': 'Missing cutoff'}), 400
    if not images:
        return jsonify({'error': 'No images specified'}), 400
    try:
        cutoff, _info = _image_cleaner_validate_cutoff(cutoff_str)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if _safe_request_host_id() != 'local':
        try:
            result = _image_cleaner_macro(
                g.client,
                'delete',
                provider=provider,
                cutoff=cutoff_str,
                images_json=json.dumps(images),
                dryRun=dry_run,
            )
        except Exception as e:
            app.logger.error("[image-cleaner] remote delete macro failed: %s", e)
            return jsonify({
                'ok': False,
                'error': str(e),
                'provider': provider,
                'hint': _image_cleaner_error_hint(provider),
            }), 502
        status = 200 if result.get('ok') else 400
        return jsonify(result), status
    try:
        adapter = _image_cleaner_adapter(provider)
    except Exception as e:
        return jsonify({
            'error': str(e), 'provider': provider,
            'hint': _image_cleaner_error_hint(provider),
        }), 502

    preflight_errors = []
    for img in images:
        repo = img.get('repositoryName', '')
        digest = img.get('imageDigest', '')
        if not repo or not digest:
            preflight_errors.append({'repo': repo, 'digest': digest, 'reason': 'missing repo or digest'})
            continue
        head = adapter.head_image(repo, digest)
        if head is None:
            preflight_errors.append({'repo': repo, 'digest': digest, 'reason': 'image not found'})
            continue
        pushed_date = head['pushedAt']
        if pushed_date >= cutoff:
            preflight_errors.append({
                'repo': repo, 'digest': digest,
                'reason': 'pushed %s is not before cutoff %s' % (pushed_date.isoformat(), cutoff_str),
            })

    if preflight_errors:
        return jsonify({'error': 'Preflight failed — no images were deleted',
                        'preflight_errors': preflight_errors}), 400

    by_repo: Dict[str, List[str]] = {}
    for img in images:
        by_repo.setdefault(img['repositoryName'], []).append(img['imageDigest'])

    deleted: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for repo, digests in by_repo.items():
        if dry_run:
            d = [{'repo': repo, 'digest': digest, 'dryRun': True} for digest in digests]
            f = []
        else:
            d, f = adapter.delete_images(repo, digests)
        deleted.extend(d)
        failed.extend(f)

    app.logger.info("[image-cleaner] provider=%s dryRun=%s deleted=%d failed=%d", provider, dry_run, len(deleted), len(failed))
    return jsonify({'dryRun': dry_run, 'deleted': deleted, 'failed': failed})


# ── Code Studio template replacement ────────────────────────────────────────

def _cs_tmpl_template_index(client: Any) -> Dict[str, Dict[str, str]]:
    """Return {templateId: {id, label, description}} for fast joins."""
    try:
        items = client.list_code_studio_templates(as_type='listitems')
    except Exception:
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for item in items:
        raw = getattr(item, '_data', {}) or {}
        tid = str(raw.get('id') or '')
        if not tid:
            continue
        desc = raw.get('desc') or {}
        out[tid] = {
            'id': tid,
            'label': str(raw.get('label') or desc.get('label') or tid),
            'description': str(desc.get('shortDesc') or ''),
        }
    return out


def _cs_tmpl_list_one_project(client: Any, project_key: str,
                              template_index: Dict[str, Dict[str, str]],
                              include_state: bool) -> List[Dict[str, Any]]:
    """Return code studios for a single project. list_code_studios() returns a slim
    payload (no libName), so we enrich each entry via get_settings()."""
    project = client.get_project(project_key)
    items = project.list_code_studios(as_type='listitems')
    studios: List[Dict[str, Any]] = []
    for item in items:
        raw = getattr(item, '_data', {}) or {}
        tid = str(raw.get('templateId') or '')
        tpl = template_index.get(tid) or {}
        cs_id = str(raw.get('id') or '')
        entry = {
            'id': cs_id,
            'name': str(raw.get('name') or cs_id),
            'owner': str(raw.get('owner') or ''),
            'templateId': tid,
            'templateLabel': tpl.get('label') or (raw.get('desc') or {}).get('label') or tid,
            'libName': '',
            'state': None,
        }
        if cs_id:
            cs_handle = project.get_code_studio(cs_id)
            try:
                settings_raw = cs_handle.get_settings().get_raw()
                entry['libName'] = str(settings_raw.get('libName') or '')
                if not tid:
                    tid = str(settings_raw.get('templateId') or '')
                    entry['templateId'] = tid
                    entry['templateLabel'] = (template_index.get(tid) or {}).get('label') or tid
            except Exception:
                pass
            if include_state:
                try:
                    entry['state'] = cs_handle.get_status().state
                except Exception:
                    entry['state'] = None
        studios.append(entry)
    return studios


@app.route('/api/cs-template/projects')
def api_cs_template_projects():
    include_state = request.args.get('includeState', '1') != '0'
    client = g.client
    try:
        projects = client.list_projects() or []
    except Exception as exc:
        return jsonify({'error': str(exc)[:300]}), 502
    project_keys = [str(p.get('projectKey') or '') for p in projects if p.get('projectKey')]

    template_index = _cs_tmpl_template_index(client)
    result: List[Dict[str, Any]] = []
    timeout_seconds = max(5, int(_BACKEND_SETTINGS.get('cs_template_list_timeout_ms', 60000) / 1000))

    def load(pk: str) -> Tuple[str, List[Dict[str, Any]]]:
        try:
            return pk, _cs_tmpl_list_one_project(client, pk, template_index, include_state)
        except Exception as exc:
            app.logger.info("[cs-tmpl] list pk=%s error=%s", pk, str(exc)[:200])
            return pk, []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(load, pk): pk for pk in project_keys}
        try:
            for fut in as_completed(futures, timeout=timeout_seconds):
                pk, studios = fut.result()
                if studios:
                    result.append({'projectKey': pk, 'codeStudios': studios})
        except FuturesTimeoutError:
            app.logger.info("[cs-tmpl] projects scan timed out after %ss", timeout_seconds)

    result.sort(key=lambda r: r['projectKey'])
    return jsonify({'projects': result, 'templates': list(template_index.values())})


@app.route('/api/cs-template/templates')
def api_cs_template_templates():
    client = g.client
    return jsonify({'templates': list(_cs_tmpl_template_index(client).values())})


def _cs_tmpl_lib_dir(project_key: str, lib_name: str) -> str:
    """Code Studio *resources* zone (libName-keyed, not versioned)."""
    return os.path.join(_dip_home().rstrip('/'), 'lib', 'code_studio', project_key, lib_name)


def _cs_tmpl_versioned_dir(project_key: str, cs_id: str) -> str:
    """Code Studio *versioned* zone (csId-keyed, lives in project config tree)."""
    return os.path.join(_dip_home().rstrip('/'), 'config', 'projects', project_key, 'code_studios', cs_id)


_CS_TMPL_COPY_MACRO_ID = 'pyrunnable_admin-toolkit_cs-template-copy-files'


def _cs_tmpl_macro_files(project: Any, src_dir: str, dst_dir: Optional[str] = None) -> Dict[str, Any]:
    """Delegate per-CS file ops to the plugin macro (runs as `dataiku`), so
    we can read/write `<DIP_HOME>/config/projects/<pk>/code_studios/<csId>/`
    (mode 0700) and `<DIP_HOME>/lib/code_studio/<pk>/<libName>/` (owned by
    `dataiku:dataiku`) regardless of webapp impersonation.

    `dst_dir=None` → walk-only (no writes); else copy with no-overwrite policy.
    Both modes return the same shape: count/totalBytes/copied/skipped/errors/debug
    plus `walked` for walk-only."""
    walk_only = dst_dir is None
    params: Dict[str, Any] = {'src_dir': src_dir}
    if walk_only:
        params['walk_only'] = True
    else:
        params['dst_dir'] = dst_dir
    try:
        macro = project.get_macro(_CS_TMPL_COPY_MACRO_ID)
        run_id = macro.run(params=params, wait=True)
        result = macro.get_result(run_id, as_type='json')
        if not isinstance(result, dict):
            return {
                'count': 0, 'totalBytes': 0, 'walked': [], 'copied': [], 'skipped': [],
                'errors': [{'path': '', 'error': f'macro returned non-dict: {type(result).__name__}'}],
                'debug': {'macroId': _CS_TMPL_COPY_MACRO_ID, 'runId': run_id, 'walkOnly': walk_only},
            }
        result.setdefault('count', 0)
        result.setdefault('totalBytes', 0)
        result.setdefault('walked', [])
        result.setdefault('copied', [])
        result.setdefault('skipped', [])
        result.setdefault('errors', [])
        result.setdefault('debug', {})
        if isinstance(result.get('debug'), dict):
            result['debug']['macroId'] = _CS_TMPL_COPY_MACRO_ID
            result['debug']['runId'] = run_id
            result['debug']['walkOnly'] = walk_only
        return result
    except Exception as exc:
        return {
            'count': 0, 'totalBytes': 0, 'walked': [], 'copied': [], 'skipped': [],
            'errors': [{'path': '', 'error': f'macro run failed: {type(exc).__name__}: {str(exc)[:280]}'}],
            'debug': {'macroId': _CS_TMPL_COPY_MACRO_ID, 'walkOnly': walk_only, 'error': str(exc)[:300]},
        }


def _cs_tmpl_planned_name(old_name: str, new_template_id: str) -> str:
    suffix = '-' + new_template_id
    if old_name.endswith(suffix):
        return old_name + '-2'
    return old_name + suffix


@app.route('/api/cs-template/migrate', methods=['POST'])
@advanced
def api_cs_template_migrate():
    payload = request.get_json(silent=True) or {}
    project_key = str(payload.get('projectKey') or '').strip()
    code_studio_id = str(payload.get('codeStudioId') or '').strip()
    new_template_id = str(payload.get('newTemplateId') or '').strip()
    dry_run = bool(payload.get('dryRun', True))
    force = bool(payload.get('force', False))

    if not project_key or not code_studio_id or not new_template_id:
        return jsonify({
            'status': 'error',
            'error': 'projectKey, codeStudioId, newTemplateId are required',
        })

    started = time.time()
    steps: List[Dict[str, Any]] = []

    def step(step_name: str, status: str, **extra: Any) -> None:
        steps.append({'name': step_name, 'status': status, **extra})

    client = g.client
    template_index = _cs_tmpl_template_index(client)
    if new_template_id not in template_index:
        return jsonify({
            'status': 'error',
            'error': f'Unknown templateId: {new_template_id}',
            'validTemplateIds': sorted(template_index.keys()),
        })

    try:
        project = client.get_project(project_key)
        cs = project.get_code_studio(code_studio_id)
        old_raw = cs.get_settings().get_raw()
    except Exception as exc:
        return jsonify({
            'status': 'error',
            'error': f'Failed to read code studio settings: {str(exc)[:300]}',
        })

    old_template_id = str(old_raw.get('templateId') or '')
    old_lib_name = str(old_raw.get('libName') or '')
    old_name = str(old_raw.get('name') or code_studio_id)
    old_owner = str(old_raw.get('owner') or '')

    if old_template_id == new_template_id:
        return jsonify({
            'status': 'error',
            'error': 'Code studio is already on the target template',
            'old': {
                'id': code_studio_id, 'name': old_name,
                'templateId': old_template_id, 'libName': old_lib_name,
            },
        })

    src_dir = _cs_tmpl_lib_dir(project_key, old_lib_name) if old_lib_name else ''
    ver_src_dir = _cs_tmpl_versioned_dir(project_key, code_studio_id)
    # Both walks go through the macro (runs as `dataiku`) so they can see
    # mode-0700 dirs the impersonated webapp user can't read.
    src_walk = _cs_tmpl_macro_files(project, src_dir) if src_dir else {'count': 0, 'totalBytes': 0, 'errors': []}
    ver_walk = _cs_tmpl_macro_files(project, ver_src_dir)
    src_count = src_walk.get('count') or 0
    src_bytes = src_walk.get('totalBytes') or 0
    ver_count = ver_walk.get('count') or 0
    ver_bytes = ver_walk.get('totalBytes') or 0
    _walk_errors = (src_walk.get('errors') or []) + (ver_walk.get('errors') or [])
    step('walk-source',
         'ok' if not _walk_errors else 'error',
         resources={'sourceDir': src_dir, 'count': src_count, 'totalBytes': src_bytes,
                    'errors': len(src_walk.get('errors') or [])},
         versioned={'sourceDir': ver_src_dir, 'count': ver_count, 'totalBytes': ver_bytes,
                    'errors': len(ver_walk.get('errors') or [])},
         count=src_count + ver_count,
         totalBytes=src_bytes + ver_bytes)

    try:
        state = cs.get_status().state
    except Exception as exc:
        state = None
        step('read-state', 'error', error=str(exc)[:300])
    else:
        step('read-state', 'ok', state=state)

    planned_name = _cs_tmpl_planned_name(old_name, new_template_id)

    base_response = {
        'old': {
            'id': code_studio_id,
            'name': old_name,
            'templateId': old_template_id,
            'libName': old_lib_name,
            'state': state,
            'owner': old_owner,
        },
        'new': {
            'plannedName': planned_name,
            'plannedTemplateId': new_template_id,
            'plannedTemplateLabel': template_index[new_template_id]['label'],
        },
        'files': {
            'count': src_count + ver_count,
            'totalBytes': src_bytes + ver_bytes,
            'resources': {
                'sourceDir': src_dir, 'count': src_count, 'totalBytes': src_bytes,
                'walked': src_walk.get('walked') or [],
            },
            'versioned': {
                'sourceDir': ver_src_dir, 'count': ver_count, 'totalBytes': ver_bytes,
                'walked': ver_walk.get('walked') or [],
            },
        },
        'steps': steps,
        'warnings': [],
        'durationMs': int((time.time() - started) * 1000),
    }

    if dry_run:
        base_response['status'] = 'planned'
        base_response['durationMs'] = int((time.time() - started) * 1000)
        return jsonify(base_response)

    # Live migration
    if state == 'RUNNING':
        try:
            fut = cs.stop()
            fut.wait_for_result(timeout=120)
            step('stop-old', 'ok')
        except Exception as exc:
            step('stop-old', 'error', error=str(exc)[:300])
            if not force:
                base_response['status'] = 'error'
                base_response['error'] = f'Failed to stop running code studio: {str(exc)[:300]}'
                base_response['durationMs'] = int((time.time() - started) * 1000)
                return jsonify(base_response)
            base_response['warnings'].append('proceeded despite stop failure (force=true)')

    try:
        new_handle = project.create_code_studio(planned_name, new_template_id)
        final_name = planned_name
        step('create-new', 'ok', createdName=final_name)
    except Exception as exc:
        step('create-new', 'error', error=str(exc)[:300])
        base_response['status'] = 'error'
        base_response['error'] = f'Failed to create new code studio: {str(exc)[:300]}'
        base_response['durationMs'] = int((time.time() - started) * 1000)
        return jsonify(base_response)

    try:
        new_raw = new_handle.get_settings().get_raw()
    except Exception as exc:
        step('read-new-settings', 'error', error=str(exc)[:300])
        base_response['status'] = 'error'
        base_response['error'] = f'Created CS but failed to read its settings: {str(exc)[:300]}'
        base_response['durationMs'] = int((time.time() - started) * 1000)
        return jsonify(base_response)

    new_lib_name = str(new_raw.get('libName') or '')
    new_cs_id = str(new_raw.get('id') or '')
    dst_dir = _cs_tmpl_lib_dir(project_key, new_lib_name) if new_lib_name else ''
    ver_dst_dir = _cs_tmpl_versioned_dir(project_key, new_cs_id) if new_cs_id else ''

    _empty_summary: Dict[str, Any] = {'count': 0, 'totalBytes': 0, 'copied': [], 'skipped': [], 'errors': []}
    # Always call the macro for live copy — it short-circuits cleanly when src
    # doesn't exist or is empty. Skip only if we have no destination to copy to.
    if dst_dir and src_dir:
        resources_summary = _cs_tmpl_macro_files(project, src_dir, dst_dir)
    else:
        resources_summary = dict(_empty_summary)
    if ver_dst_dir and ver_src_dir:
        versioned_summary = _cs_tmpl_macro_files(project, ver_src_dir, ver_dst_dir)
    else:
        versioned_summary = dict(_empty_summary)

    def _agg(*summaries: Dict[str, Any]) -> Dict[str, Any]:
        return {
            'count':      sum(s.get('count', 0)      for s in summaries),
            'totalBytes': sum(s.get('totalBytes', 0) for s in summaries),
            'copied':     [c for s in summaries for c in (s.get('copied') or [])],
            'skipped':    [k for s in summaries for k in (s.get('skipped') or [])],
            'errors':     [e for s in summaries for e in (s.get('errors') or [])],
        }
    copy_summary = _agg(resources_summary, versioned_summary)

    _res_dbg = resources_summary.get('debug') or {}
    _rt = _res_dbg.get('runtime') or {}
    _dst = _res_dbg.get('dst_dir_stat') or _res_dbg.get('dst_dir_stat_after') or {}
    _dst_parent = _res_dbg.get('dst_parent_stat') or {}
    step(
        'copy-files',
        'ok' if not copy_summary['errors'] else 'error',
        count=copy_summary['count'],
        totalBytes=copy_summary['totalBytes'],
        skipped=len(copy_summary['skipped']),
        errors=len(copy_summary['errors']),
        resources={'count': resources_summary.get('count', 0),
                   'totalBytes': resources_summary.get('totalBytes', 0),
                   'errors': len(resources_summary.get('errors') or [])},
        versioned={'count': versioned_summary.get('count', 0),
                   'totalBytes': versioned_summary.get('totalBytes', 0),
                   'errors': len(versioned_summary.get('errors') or [])},
        asUser=f"{_rt.get('euser')}({_rt.get('euid')}):{_rt.get('egroup')}({_rt.get('egid')})",
        dstOwner=f"{_dst.get('owner')}({_dst.get('uid')}):{_dst.get('group')}({_dst.get('gid')}) mode={_dst.get('mode')}",
        dstParentOwner=f"{_dst_parent.get('owner')}({_dst_parent.get('uid')}):{_dst_parent.get('group')}({_dst_parent.get('gid')}) mode={_dst_parent.get('mode')}",
    )
    app.logger.info(
        "[cs-tmpl] copy as %s(%s):%s(%s) -> resourcesDst=%s versionedDst=%s; resCount=%d verCount=%d errors=%d",
        _rt.get('euser'), _rt.get('euid'), _rt.get('egroup'), _rt.get('egid'),
        dst_dir, ver_dst_dir,
        resources_summary.get('count') or 0, versioned_summary.get('count') or 0,
        len(copy_summary['errors']),
    )

    # Sanity verify
    try:
        verify_raw = new_handle.get_settings().get_raw()
        if str(verify_raw.get('templateId') or '') == new_template_id:
            step('verify-new-template', 'ok')
        else:
            step('verify-new-template', 'error', got=verify_raw.get('templateId'))
            base_response['warnings'].append(
                f"new CS templateId={verify_raw.get('templateId')!r}, expected {new_template_id!r}"
            )
    except Exception as exc:
        step('verify-new-template', 'error', error=str(exc)[:300])

    app.logger.info(
        "[cs-tmpl] migrate pk=%s oldId=%s newId=%s oldTpl=%s newTpl=%s filesCopied=%d",
        project_key, code_studio_id, new_cs_id, old_template_id, new_template_id,
        copy_summary.get('count') or 0,
    )

    base_response['status'] = 'migrated'
    base_response['new'].update({
        'id': new_cs_id,
        'name': final_name,
        'templateId': new_template_id,
        'libName': new_lib_name,
    })
    base_response['files'] = {
        'count': src_count + ver_count,
        'totalBytes': src_bytes + ver_bytes,
        'copied': copy_summary.get('count', 0),
        'copiedBytes': copy_summary.get('totalBytes', 0),
        'skipped': copy_summary.get('skipped', []),
        'errors': copy_summary.get('errors', []),
        'resources': {
            'sourceDir': src_dir,
            'targetDir': dst_dir,
            'count': src_count,
            'totalBytes': src_bytes,
            'walked': src_walk.get('walked') or [],
            'copied': resources_summary.get('count', 0),
            'copiedBytes': resources_summary.get('totalBytes', 0),
            'skipped': resources_summary.get('skipped', []),
            'errors': resources_summary.get('errors', []),
            'debug': resources_summary.get('debug'),
        },
        'versioned': {
            'sourceDir': ver_src_dir,
            'targetDir': ver_dst_dir,
            'count': ver_count,
            'totalBytes': ver_bytes,
            'walked': ver_walk.get('walked') or [],
            'copied': versioned_summary.get('count', 0),
            'copiedBytes': versioned_summary.get('totalBytes', 0),
            'skipped': versioned_summary.get('skipped', []),
            'errors': versioned_summary.get('errors', []),
            'debug': versioned_summary.get('debug'),
        },
    }
    base_response['durationMs'] = int((time.time() - started) * 1000)
    return jsonify(base_response)
