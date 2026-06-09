"""Small coercion, concurrency-sizing, benchmark and JSON-safety helpers."""

import os
import time
from typing import Any

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
