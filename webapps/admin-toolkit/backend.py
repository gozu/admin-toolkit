import json
import os
import platform
import re
import time
from concurrent.futures import as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import logging

import dataiku
from flask import Flask, Response, g, jsonify, request, stream_with_context

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

# ── Shared infrastructure (python-lib/adk_backend) ──
# Re-imported flat so the remaining route code (and tests patching
# backend.<name>) keep working unqualified.
from adk_backend.settings import (
    _BACKEND_SETTINGS, _BACKEND_SETTINGS_LOCK, _BACKEND_SETTINGS_DEFAULTS,
)
from adk_backend.caching import (
    CacheLoaderTimeout,
    _cache_peek, _cache_get, _handle_cache_loader_timeout,
)
from adk_backend.progress import (
    _PROGRESS, _PROGRESS_LOCK, _start_progress, _append_progress_event,
    _append_progress_partial_row, _finish_progress, _set_progress_summary, _read_progress,
    _notify_progress,
)
from adk_backend.sysinfo import (
    _dip_home, _safe_read_text, _safe_read_json, _run_command,
    _parse_memory_info, _parse_system_limits, _parse_filesystem_info, _get_cpu_cores,
    _get_os_info, _parse_supervisord_restart, _find_spark_version,
)
from adk_backend.logparse import (
    _coerce_log_text, _parse_log_errors,
)
from adk_backend.utils import (
    _coerce_int, advanced, _find_llm_ids,
)
from adk_backend.clients import ( _get_sdk_cache,
    MACRO_PROJECT_KEY, MACRO_PROJECT_DEFAULT_NAME, MacroProjectMissing,
    _safe_request_host_id,
    _resolve_client, ThreadPoolExecutor,
    _local_toolkit_client, _local_toolkit_project,
)
from adk_backend.footprint import (
    _compute_footprint_payload,
    _footprint_details_map,
)
from adk_backend.mail import (
    _get_configured_mail_channel, _list_mail_channels,
)
# Red-unlock token helpers live with their routes; the app-wide @advanced gate
# (_check_red_unlock below) still validates the cookie on every request.
from adk_backend.routes.auth import _RED_COOKIE_NAME, _verify_red_token
# Code-env catalog/kernel helpers live with the replace routes; the
# algorithm-review routes below reuse them.
from adk_backend.routes.code_env_replace import (
    _cer_env_catalog, _cer_fetch_env_detail, _cer_kernel_spec_name,
)

app.register_error_handler(CacheLoaderTimeout, _handle_cache_loader_timeout)


try:
    import llm_audit
    _llm_audit_available = True
except Exception:
    _llm_audit_available = False


# Visual / non-code recipe types that never reference a code environment.
# Skipping these avoids unnecessary per-recipe API calls.
@app.route('/__ping')
def ping():
    return jsonify({'status': 'ok'})





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


from adk_backend.macros import (
    _host_metrics_macro,
    _process_metrics_macro,
)


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


def _scope_root(scope: str, project_key: Optional[str]) -> Dict[str, str]:
    if scope == 'all':
        return {'name': '/', 'path': '/'}
    if scope == 'global':
        return {'name': 'global', 'path': '/dss-data/global'}
    if scope == 'project' and project_key:
        return {'name': project_key, 'path': f'/dss-data/projects/{project_key}'}
    return {'name': 'dss_data', 'path': '/dss-data'}


_PYTHON_WEBAPP_TYPES = {'DASH', 'STANDARD', 'BOKEH'}


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


@app.route('/api/java-memory')
def api_java_memory():
    dip_home = _dip_home()
    content = _safe_read_text(os.path.join(dip_home, 'bin', 'env-default.sh')) or ''
    return content


# ── Algorithm review: ship adk_notebook libs + scan notebooks into ADMINTOOLKIT ──
#
# Materializes a human-reviewable copy of the webapp's Dataiku-API logic inside the
# ADMINTOOLKIT project: writes the importable shared libraries into the project's
# Python library and creates one Jupyter notebook per scan card (verbatim source).
# Pure DSS-API writes → stays on g.client, no macro. API shapes verified live.

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

    Cards live in ``adk_notebook/cards/`` (inside python-lib) — that tree is the only
    plugin dir copied into the webapp backend's per-run sandbox, so it's reliably
    present at runtime (the plugin root / notebook-cards/ are NOT copied). Notebook
    name = card filename stem (e.g. ai-compute__model-audit__llm-audit-table)."""
    import adk_notebook
    cards_dir = os.path.join(os.path.dirname(os.path.abspath(adk_notebook.__file__)), 'cards')
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

    The notebooks must run on the plugin's OWN managed code env — the env that ships
    rich + python-dateutil + the cloud SDKs the cards need. The plugin's *declared*
    ``codeEnvName`` is NOT reliable on its own: when a plugin env is rebuilt, DSS can
    create a version-suffixed sibling (``…_managed_1`` / ``_2`` / ``_3``) while the
    declared name lags at the stale base. So resolve the whole managed-env *family*
    (the base name plus its ``_N`` siblings) and pick the NEWEST member that has a
    Jupyter kernel — that is the current env (verified live: a plugin's ``codeEnvName``
    normally points at its highest-suffixed env). Fall back to builtin ``python3`` +
    warn only if no family member has a Jupyter kernel yet (the ``installJupyterSupport``
    build hasn't run). A notebook's kernel is independent of its project's default."""
    try:
        plugin_settings = client.get_plugin('admin-toolkit').get_settings().get_raw()
        declared = str((plugin_settings or {}).get('codeEnvName') or '').strip()
        base = re.sub(r'_\d+$', '', declared)  # strip a trailing _N to get the family base
        if base:
            catalog = _cer_env_catalog(client)
            fam_re = re.compile(r'^' + re.escape(base) + r'(_\d+)?$')

            def _suffix(name: str) -> int:
                match = re.search(r'_(\d+)$', name)
                return int(match.group(1)) if match else 0

            family = sorted(
                (
                    name
                    for (lang, name), env in catalog.items()
                    if lang == 'PYTHON'
                    and fam_re.match(name)
                    and env.get('deploymentMode') in (None, 'PLUGIN_MANAGED')
                ),
                key=_suffix,
                reverse=True,  # newest suffix first; the un-suffixed base counts as 0
            )
            for name in family:
                env = catalog.get(('PYTHON', name)) or {}
                kernel = _cer_kernel_spec_name(env, _cer_fetch_env_detail(client, 'PYTHON', name))
                if kernel:
                    if name == declared:
                        return kernel, False, []
                    return kernel, False, [
                        f"Plugin's declared code env '{declared}' is stale; "
                        f"using newer build '{name}'."
                    ]
            newest = family[0] if family else (declared or 'plugin_admin-toolkit_managed')
            return 'python3', True, [
                f"Plugin code env '{newest}' has no Jupyter kernel yet — rebuild it with "
                "Jupyter support (Administration → Plugins → Code env → Rebuild), then "
                "re-run. Notebooks use the builtin 'python3' kernel meanwhile."
            ]
    except Exception:
        pass
    return 'python3', True, [
        "Could not resolve the plugin code env; notebooks use the builtin 'python3' "
        "kernel. Ensure it has 'rich' + 'python-dateutil' so the cards can run."
    ]


def _adk_review_audit_code_env_kernel(client: Any) -> str:
    """Create-or-reuse a managed 'admintoolkitaudit' env (rich + dateutil + Jupyter
    support; dataiku APIs are auto-provided) and trigger its build; return its kernel."""
    name = 'admintoolkitaudit'
    if ('PYTHON', name) not in _cer_env_catalog(client):
        env = client.create_code_env('PYTHON', name, 'DESIGN_MANAGED')
        settings = env.get_settings()
        settings.set_required_packages('rich', 'python-dateutil')
        settings.get_raw().setdefault('desc', {})['installJupyterSupport'] = True
        settings.save()
        env.update_packages(wait=False)  # async build
    return 'py-dku-venv-' + name


def _adk_review_card_title(source_text: str, fallback: str) -> str:
    """First non-empty line of the card's leading docstring (its display title)."""
    match = re.search(r'"""(.*?)"""', source_text, re.S)
    if match:
        for line in match.group(1).strip().splitlines():
            if line.strip():
                return line.strip()
    return fallback


_ADK_REVIEW_PREFLIGHT_CELL = '''\
try:
    import rich
except ImportError:
    print(
        "This notebook needs the 'rich' package, which isn't in the current kernel's code env.\\n"
        "Fix: switch the kernel (Kernel menu -> Change kernel) to a code env that has rich:\\n"
        "  - 'admintoolkitaudit'  (create it via the webapp's 'Create review notebooks' action,\\n"
        "     ticking 'create a dedicated code env'), or\\n"
        "  - the plugin env  'plugin_admin-toolkit_managed'."
    )
    raise SystemExit("rich is not available in this kernel - see the note above.")
'''


def _adk_review_build_nbformat(card_filename: str, source_text: str, kernel_name: str) -> Dict[str, Any]:
    """nbformat-v4 notebook: markdown header + a `rich` preflight cell + the verbatim
    card code cell."""
    title = _adk_review_card_title(source_text, card_filename)
    markdown = [
        "### %s\n" % title,
        "\n",
        "_Verbatim review copy of `notebook-cards/%s`._\n" % card_filename,
        "\n",
        "_Requires a kernel with the `rich` package (e.g. `admintoolkitaudit`, or the plugin env)._\n",
        "\n",
        "Imports the shared logic from the `adk_notebook` project library; "
        "run the cells below to reproduce the matching webapp card.",
    ]
    display = 'Python 3' if kernel_name == 'python3' else kernel_name
    return {
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": markdown},
            {"cell_type": "code", "metadata": {"tags": ["preflight"]}, "execution_count": None,
             "outputs": [], "source": _ADK_REVIEW_PREFLIGHT_CELL.splitlines(keepends=True)},
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
    into the project that hosts this webapp (on the local instance), for human review
    of the Dataiku-API code."""
    client = _local_toolkit_client()            # LOCAL instance (not the remote host-selector)
    project = _local_toolkit_project()           # the project the webapp is added to
    project_key = dataiku.default_project_key()

    body = request.get_json(silent=True) or {}
    if body.get('createCodeEnv'):
        try:
            kernel_name, kernel_fallback = _adk_review_audit_code_env_kernel(client), False
            warnings = ["Code env 'admintoolkitaudit' is building (~a few min) — reopen the notebooks once it's ready."]
        except Exception as exc:
            kernel_name, kernel_fallback, warnings = _adk_review_resolve_kernel(client)
            warnings = ["Couldn't create 'admintoolkitaudit' (%s); used '%s'." % (str(exc)[:120], kernel_name)] + warnings
    else:
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


# ─────────────────────────────────────────────────────────────────────────
# Blueprint registration
#
# Feature route groups live in python-lib/adk_backend/routes/ (one module per
# group, each exposing `bp`). App-wide hooks declared above (@before_request
# client attach / host-ready / red-unlock gates, @errorhandler for
# CacheLoaderTimeout and MacroProjectMissing) apply to blueprint views too.
# ─────────────────────────────────────────────────────────────────────────
from adk_backend.routes.auth import bp as auth_bp
from adk_backend.routes.code_env_replace import bp as code_env_replace_bp
from adk_backend.routes.code_envs import bp as code_envs_bp
from adk_backend.routes.connections import bp as connections_bp
from adk_backend.routes.container_execs import bp as container_execs_bp
from adk_backend.routes.cs_template import bp as cs_template_bp
from adk_backend.routes.dataset_export import bp as dataset_export_bp
from adk_backend.routes.db_health import bp as db_health_bp
from adk_backend.routes.email_tools import bp as email_tools_bp
from adk_backend.routes.feedback import bp as feedback_bp
from adk_backend.routes.footprint import bp as footprint_bp
from adk_backend.routes.hosts import bp as hosts_bp
from adk_backend.routes.image_cleaner import bp as image_cleaner_bp
from adk_backend.routes.k8s_insights import bp as k8s_insights_bp
from adk_backend.routes.misc import bp as misc_bp
from adk_backend.routes.plugins import bp as plugins_bp
from adk_backend.routes.projects import bp as projects_bp

app.register_blueprint(auth_bp)
app.register_blueprint(code_env_replace_bp)
app.register_blueprint(code_envs_bp)
app.register_blueprint(connections_bp)
app.register_blueprint(container_execs_bp)
app.register_blueprint(cs_template_bp)
app.register_blueprint(dataset_export_bp)
app.register_blueprint(db_health_bp)
app.register_blueprint(email_tools_bp)
app.register_blueprint(feedback_bp)
app.register_blueprint(footprint_bp)
app.register_blueprint(hosts_bp)
app.register_blueprint(image_cleaner_bp)
app.register_blueprint(k8s_insights_bp)
app.register_blueprint(misc_bp)
app.register_blueprint(plugins_bp)
app.register_blueprint(projects_bp)
