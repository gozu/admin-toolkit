"""In-memory response cache: host scoping, in-flight coalescing, session epoch."""

import hashlib
import logging
import threading
import time
from typing import Any, Dict, Optional

from flask import g, jsonify

from adk_backend.context import _THREAD_LOCAL

_LOGGER = logging.getLogger(__name__)

_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()
_SHARED_USAGE_SCANS: Dict[str, Dict[str, Any]] = {}
_SHARED_USAGE_SCANS_LOCK = threading.Lock()


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


def _handle_cache_loader_timeout(exc: CacheLoaderTimeout):
    _LOGGER.warning("[cache] loader timeout for key=%s after %.1fs", exc.key, exc.timeout)
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

