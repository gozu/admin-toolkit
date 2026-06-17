"""Cost / CRU routes — per-project compute parsed from the host audit logs.

All host access lives inside the cru-audit macro (python-runnables/cru-audit/);
these routes only do DSS-client macro invocation. The macro is blocking (one
multi-second pass over ~20 rotated files), so the stream runs it in a worker
thread and emits an indeterminate init → done, mirroring container_execs.
"""
import json
import logging
import queue
import threading
import time
from typing import Any, Dict

from flask import Blueprint, g, jsonify, request

from adk_backend.caching import _CACHE, _CACHE_LOCK, _cache_get, _cache_key
from adk_backend.context import _THREAD_LOCAL
from adk_backend.macros import _cru_audit_macro
from adk_backend.settings import _BACKEND_SETTINGS
from adk_backend.utils import _sse_response

bp = Blueprint('cru', __name__)
_LOGGER = logging.getLogger(__name__)

_CRU_CACHE_KEY = 'cru'


def _max_files_arg() -> int:
    try:
        return max(0, int(request.args.get('maxFiles', '0')))
    except (TypeError, ValueError):
        return 0


@bp.route('/api/cru')
def api_cru():
    client = g.client
    max_files = _max_files_arg()

    def loader():
        return _cru_audit_macro(client, max_files=max_files)

    ttl = int(_BACKEND_SETTINGS.get('cache_ttl_projects', 600))
    data = _cache_get(_CRU_CACHE_KEY, ttl, loader)
    return jsonify(data)


@bp.route('/api/cru/stream')
def api_cru_stream():
    max_files = _max_files_arg()
    ttl = int(_BACKEND_SETTINGS.get('cache_ttl_projects', 600))

    def sse(event_name: str, payload: Dict[str, Any]) -> str:
        return "event: %s\ndata: %s\n\n" % (event_name, json.dumps(payload))

    # Hoist client and host_id out of the SSE generator so the worker thread
    # captures them by closure. `g` is request-scoped and is NOT available
    # inside a threading.Thread spawned by the request handler.
    request_client = g.client
    request_host_id = getattr(g, 'host_id', 'local')

    def generate():
        now = time.time()
        with _CACHE_LOCK:
            cached = _CACHE.get(_cache_key(_CRU_CACHE_KEY))
            cached_value = cached.get('value') if cached and now - cached.get('ts', 0) < ttl else None
        if isinstance(cached_value, dict):
            yield sse('init', {'cached': True, 'message': 'Loading cached compute usage…'})
            yield sse('done', cached_value)
            return

        events_q: "queue.Queue[Dict[str, Any]]" = queue.Queue()

        def worker() -> None:
            previous_host_id = getattr(_THREAD_LOCAL, 'host_id', None)
            _THREAD_LOCAL.host_id = request_host_id
            try:
                # Captured from the enclosing request context — DO NOT touch g here.
                result = _cru_audit_macro(request_client, max_files=max_files)
                with _CACHE_LOCK:
                    _CACHE[_cache_key(_CRU_CACHE_KEY)] = {'ts': time.time(), 'value': result}
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

        yield sse('init', {'message': 'Parsing host audit logs…'})

        while True:
            item = events_q.get()
            event_name = str(item.pop('event', 'progress'))
            if event_name == 'done':
                yield sse('done', item.get('payload') if isinstance(item.get('payload'), dict) else {})
                break
            yield sse(event_name, item)
            if event_name == 'error':
                break

    return _sse_response(generate)
