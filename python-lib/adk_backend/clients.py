"""DSS client resolution: local/remote hosts, thread-context propagation, SDK cache."""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor as _BaseThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

import dataiku
import dataikuapi
from flask import abort, g, request

from adk_backend.caching import _cache_get
from adk_backend.context import _THREAD_LOCAL
from adk_backend.settings import _BACKEND_SETTINGS
from adk_backend.sysinfo import _dip_home, _safe_read_text
from adk_backend.utils import _bench_call

_LOGGER = logging.getLogger(__name__)

_SDK_CACHE: Optional[Any] = None
_INSTANCE_ID_CACHED: Optional[str] = None


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
    _LOGGER.debug("[perf:sdk_cache] GET key=%s elapsed=%.1fms", cache_key, (time.time() - t0) * 1000.0)
    return result


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
    _LOGGER.debug("[perf:catalog_cheap] elapsed=%.0fms count=%d", (time.time() - t_total) * 1000, len(out))
    return out


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
        _LOGGER.warning(
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

