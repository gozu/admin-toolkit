import logging
from typing import Optional

import dataiku
from flask import Flask, Response, g, jsonify, request

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
# Flat re-imports for the app-wide hooks / error handlers below, plus the
# names the backend tests exercise as `backend.<name>` (see __all__).
from adk_backend.caching import (
    CacheLoaderTimeout, _cache_get, _handle_cache_loader_timeout,
)
from adk_backend.clients import (
    MACRO_PROJECT_KEY, MACRO_PROJECT_DEFAULT_NAME, MacroProjectMissing,
    ThreadPoolExecutor, _local_toolkit_client, _resolve_client,
)
from adk_backend.sysinfo import (
    _instance_info_from_install_map, _parse_install_ini_map,
)
# Red-unlock token helpers live with their routes; the app-wide @advanced gate
# (_check_red_unlock below) still validates the cookie on every request.
from adk_backend.routes.auth import _RED_COOKIE_NAME, _verify_red_token

# Names with no in-module reference are re-exported for tests/backend/*,
# which patch or call them via `backend.<name>`.
__all__ = [
    'app', 'dataiku', 'g',
    'MACRO_PROJECT_KEY', 'ThreadPoolExecutor',
    '_cache_get', '_local_toolkit_client', '_resolve_client',
    '_instance_info_from_install_map', '_parse_install_ini_map',
]

app.register_error_handler(CacheLoaderTimeout, _handle_cache_loader_timeout)


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


# ─────────────────────────────────────────────────────────────────────────
# Blueprint registration
#
# Feature route groups live in python-lib/adk_backend/routes/ (one module per
# group, each exposing `bp`). App-wide hooks declared above (@before_request
# client attach / host-ready / red-unlock gates, @errorhandler for
# CacheLoaderTimeout and MacroProjectMissing) apply to blueprint views too.
# ─────────────────────────────────────────────────────────────────────────
from adk_backend.routes.algorithm_review import bp as algorithm_review_bp
from adk_backend.routes.auth import bp as auth_bp
from adk_backend.routes.code_env_replace import bp as code_env_replace_bp
from adk_backend.routes.code_envs import bp as code_envs_bp
from adk_backend.routes.connections import bp as connections_bp
from adk_backend.routes.container_execs import bp as container_execs_bp
from adk_backend.routes.cs_template import bp as cs_template_bp
from adk_backend.routes.dataset_export import bp as dataset_export_bp
from adk_backend.routes.db_health import bp as db_health_bp
from adk_backend.routes.debug import bp as debug_bp
from adk_backend.routes.dir_tree import bp as dir_tree_bp
from adk_backend.routes.email_tools import bp as email_tools_bp
from adk_backend.routes.feedback import bp as feedback_bp
from adk_backend.routes.footprint import bp as footprint_bp
from adk_backend.routes.hosts import bp as hosts_bp
from adk_backend.routes.image_cleaner import bp as image_cleaner_bp
from adk_backend.routes.k8s_insights import bp as k8s_insights_bp
from adk_backend.routes.llm_tools import bp as llm_tools_bp
from adk_backend.routes.logs import bp as logs_bp
from adk_backend.routes.misc import bp as misc_bp
from adk_backend.routes.overview import bp as overview_bp
from adk_backend.routes.plugins import bp as plugins_bp
from adk_backend.routes.projects import bp as projects_bp
from adk_backend.routes.settings import bp as settings_bp

app.register_blueprint(algorithm_review_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(code_env_replace_bp)
app.register_blueprint(code_envs_bp)
app.register_blueprint(connections_bp)
app.register_blueprint(container_execs_bp)
app.register_blueprint(cs_template_bp)
app.register_blueprint(dataset_export_bp)
app.register_blueprint(db_health_bp)
app.register_blueprint(debug_bp)
app.register_blueprint(dir_tree_bp)
app.register_blueprint(email_tools_bp)
app.register_blueprint(feedback_bp)
app.register_blueprint(footprint_bp)
app.register_blueprint(hosts_bp)
app.register_blueprint(image_cleaner_bp)
app.register_blueprint(k8s_insights_bp)
app.register_blueprint(llm_tools_bp)
app.register_blueprint(logs_bp)
app.register_blueprint(misc_bp)
app.register_blueprint(overview_bp)
app.register_blueprint(plugins_bp)
app.register_blueprint(projects_bp)
app.register_blueprint(settings_bp)
