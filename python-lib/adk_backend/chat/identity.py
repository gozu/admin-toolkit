"""Best-effort per-user identity for chat scoping — Agent Hub ws_utils.py
pattern: the webapp is served same-origin with DSS, so the browser's DSS
session cookies reach us and the LOCAL client can resolve them via
get_auth_info_from_browser_headers (10s cache keyed by the header tuple).

Called inside chat routes only — no global before_request, zero blast radius
on every other endpoint. Any failure (older DSS, no session, API hiccup)
degrades to a shared "__anonymous__" bucket rather than an error.
"""

import logging
import threading
import time

from flask import request

_LOGGER = logging.getLogger(__name__)

ANONYMOUS_USER = '__anonymous__'
_CACHE_TTL_S = 10.0
_CACHE_MAX = 128

_cache = {}  # header tuple -> (monotonic_ts, user_id)
_cache_lock = threading.Lock()


def resolve_chat_user():
    """authIdentifier of the browsing DSS user, or ANONYMOUS_USER."""
    try:
        headers = dict(request.headers)
    except Exception:
        return ANONYMOUS_USER
    key = tuple(sorted((str(k), str(v)) for k, v in headers.items()))
    now = time.monotonic()

    with _cache_lock:
        entry = _cache.get(key)
        if entry is not None and now - entry[0] <= _CACHE_TTL_S:
            return entry[1]

    user_id = ANONYMOUS_USER
    try:
        from adk_backend.clients import _local_thread_client
        auth = _local_thread_client().get_auth_info_from_browser_headers(headers)
        user_id = (auth or {}).get('authIdentifier') or ANONYMOUS_USER
    except Exception as exc:
        _LOGGER.debug('chat identity resolution failed (-> anonymous): %s', exc)

    with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[key] = (now, user_id)
    return user_id
