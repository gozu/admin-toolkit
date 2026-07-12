"""Small coercion, concurrency-sizing, benchmark and JSON-safety helpers."""

import os
import time
from typing import Any, Dict, Optional, Tuple

from flask import Response, stream_with_context

from adk_backend.context import _THREAD_LOCAL
from adk_backend.settings import _BACKEND_SETTINGS

def local_only(view_func):
    """Mark a Flask route as local-only: it reads local-DSS-only state and
    must not be 502'd by _check_host_ready when a remote host is unreachable.

    Used for tracking endpoints that read from the local SQL tracking DB and
    have no dependency on the active host's DSS API client. Add between
    @app.route(...) and `def api_*(...)`."""
    view_func._admin_toolkit_local_only = True
    return view_func


def advanced(view_func):
    """Mark a Flask route as advanced: it is 403'd unless the request carries
    a valid unlock cookie. Add between @app.route(...) and `def api_*(...)`."""
    view_func._admin_toolkit_advanced = True
    return view_func


# Per-DSS-host cache of studioExternalUrl (rarely changes; one general-
# settings read per host per 10 minutes).
_STUDIO_URL_CACHE: Dict[str, Tuple[float, Optional[str]]] = {}
_STUDIO_URL_TTL_S = 600


def studio_external_url(client: Any) -> Optional[str]:
    """studioExternalUrl from the host's general settings, or None. The base
    for project deep links in agent/email surfaces — they have no browser
    window.origin (unlike the frontend's codeEnvUsageLinks)."""
    cache_key = str(getattr(client, 'host', '') or '')
    now = time.time()
    hit = _STUDIO_URL_CACHE.get(cache_key)
    if hit is not None and now - hit[0] < _STUDIO_URL_TTL_S:
        return hit[1]
    url: Optional[str] = None
    try:
        url = str((client.get_general_settings().get_raw() or {})
                  .get('studioExternalUrl') or '').rstrip('/') or None
    except Exception:
        url = None
    _STUDIO_URL_CACHE[cache_key] = (now, url)
    return url


def project_deep_link(client: Any, project_key: Any) -> Optional[str]:
    """<studioExternalUrl>/projects/<KEY>/ or None when the URL is unset."""
    base = studio_external_url(client)
    key = str(project_key or '').strip()
    return '%s/projects/%s/' % (base, key) if base and key else None


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


def _coerce_progress_params(since_raw: Any, rows_since_raw: Any) -> Tuple[int, int]:
    """Parse the since/rowsSince progress-poll params (0 on garbage)."""
    try:
        since = max(0, int(str(since_raw or '0')))
    except Exception:
        since = 0
    try:
        rows_since = max(0, int(str(rows_since_raw or '0')))
    except Exception:
        rows_since = 0
    return since, rows_since


def _sse_response(generate) -> Response:
    """Standard SSE Response wrapper (mimetype + no-cache/no-buffer headers)."""
    return Response(stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


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



def _cex_item_raw(item: Any) -> Dict[str, Any]:
    raw = getattr(item, '_data', item)
    return raw if isinstance(raw, dict) else {}


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
