"""Startup cache pre-warm: replays the frontend's staged load order against
the backend itself so the heavy caches are hot before the first page load.

The webapp backend is launched as `python -m dataiku.webapps.backend
<run-dir>/start_command.json`; that JSON carries the HTTP port the Flask app
binds, so we self-GET over 127.0.0.1. Self-requests go through the normal
@before_request client resolution (no X-DSS-Host-Id header → local host) and
_cache_get in-flight coalescing — a user load arriving mid-warm joins the
in-flight loader instead of triggering a second scan.
"""
import json
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from adk_backend.settings import _BACKEND_SETTINGS

_LOGGER = logging.getLogger(__name__)
_PREWARM_LOCK = threading.Lock()
_PREWARM_STARTED = False

# Introspection for /api/debug/perf: the webapp's own log lines get flooded
# out of the DSS backend.log tail, so this dict is the only reliable way to
# see what prewarm did on a live instance.
_PREWARM_STATUS = {'state': 'not-started', 'port': None, 'stages': {}}

# Staged like useApiDataLoader phases 1→3: cheap core data, then the two
# page-gating scans, then the heavy audits, then the slow sizes tail. The DSS
# API saturates around ~40 concurrent calls, so stages run sequentially just
# like the frontend's loader.
_PREWARM_STAGES = [
    ('core', ['/api/overview', '/api/connections', '/api/users', '/api/plugins']),
    ('page-gating', ['/api/project-footprint', '/api/code-envs']),
    ('audits', ['/api/llm-audit', '/api/plugins/usages']),
    ('sizes-tail', ['/api/code-envs/sizes']),
]
_PREWARM_REQUEST_TIMEOUT = 900
_PREWARM_BOOT_PROBES = 30


def _find_port(obj):
    if isinstance(obj, dict):
        port = obj.get('port')
        if isinstance(port, int):
            return port
        for value in obj.values():
            found = _find_port(value)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_port(value)
            if found:
                return found
    return None


def _backend_port():
    for arg in reversed(sys.argv):
        if not str(arg).endswith('.json'):
            continue
        try:
            with open(arg, 'r', encoding='utf-8') as fh:
                return _find_port(json.load(fh))
        except Exception as exc:
            _LOGGER.warning("[prewarm] could not read %s: %s", arg, exc)
    return None


def _warm_one(session, base, path):
    started = time.time()
    try:
        resp = session.get(base + path, timeout=_PREWARM_REQUEST_TIMEOUT)
        _LOGGER.info("[prewarm] %s status=%s elapsed=%.1fs", path, resp.status_code, time.time() - started)
    except Exception as exc:
        _LOGGER.warning("[prewarm] %s failed after %.1fs: %s", path, time.time() - started, exc)


def _prewarm_worker(port):
    try:
        import requests
        base = f"http://127.0.0.1:{port}"
        session = requests.Session()
        _PREWARM_STATUS['state'] = 'probing'
        for _ in range(_PREWARM_BOOT_PROBES):
            try:
                if session.get(base + '/api/mode', timeout=5).status_code == 200:
                    break
            except Exception as exc:
                _PREWARM_STATUS['lastProbeError'] = f'{type(exc).__name__}: {str(exc)[:120]}'
            time.sleep(2)
        else:
            _LOGGER.warning("[prewarm] backend never answered /api/mode on port %s — skipping", port)
            _PREWARM_STATUS['state'] = 'backend-unreachable'
            return

        _PREWARM_STATUS['state'] = 'warming'
        total_started = time.time()
        for stage_name, paths in _PREWARM_STAGES:
            stage_started = time.time()
            with ThreadPoolExecutor(max_workers=len(paths)) as pool:
                futures = [pool.submit(_warm_one, session, base, path) for path in paths]
                for future in as_completed(futures):
                    future.result()
            elapsed = time.time() - stage_started
            _PREWARM_STATUS['stages'][stage_name] = round(elapsed, 1)
            _LOGGER.info("[prewarm] stage=%s done elapsed=%.1fs", stage_name, elapsed)
        _PREWARM_STATUS['state'] = 'done'
        _LOGGER.info("[prewarm] all stages done total=%.1fs", time.time() - total_started)
    except Exception as exc:
        _PREWARM_STATUS['state'] = f'crashed: {type(exc).__name__}: {str(exc)[:200]}'
        _LOGGER.warning("[prewarm] worker crashed: %s", exc)


def start_cache_prewarm():
    """Spawn the pre-warm daemon thread (no-op when disabled or re-invoked)."""
    global _PREWARM_STARTED
    with _PREWARM_LOCK:
        if _PREWARM_STARTED:
            return
        _PREWARM_STARTED = True
    if not _BACKEND_SETTINGS.get('prewarm_on_start', 1):
        _LOGGER.info("[prewarm] disabled via prewarm_on_start=0")
        _PREWARM_STATUS['state'] = 'disabled'
        return
    port = _backend_port()
    _PREWARM_STATUS['port'] = port
    if not port:
        _LOGGER.warning("[prewarm] backend port not found in start command — skipping")
        _PREWARM_STATUS['state'] = 'no-port'
        _PREWARM_STATUS['argv'] = [str(a) for a in sys.argv][:12]
        return
    _LOGGER.info("[prewarm] starting on port %s", port)
    _PREWARM_STATUS['state'] = 'spawned'
    threading.Thread(target=_prewarm_worker, args=(port,), name='cache-prewarm', daemon=True).start()
