import json
import hashlib
import os
import platform
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import TimeoutError as FuturesTimeoutError, as_completed
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
    _CACHE, _CACHE_LOCK, _CACHE_INFLIGHT_ERRORS,
    CacheLoaderTimeout, _get_session_epoch, _bump_session_epoch, _cache_key,
    _cache_peek, _cache_pop_matching, _cache_get, _handle_cache_loader_timeout,
    _clear_shared_project_code_env_usage,
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
    _coerce_int, local_only, advanced,
    _cex_item_raw, _find_llm_ids,
)
from adk_backend.clients import (
    _THREAD_LOCAL, _get_sdk_cache, _instance_id, _sdk_fetch,
    MACRO_PROJECT_KEY, MACRO_PROJECT_DEFAULT_NAME, MacroProjectMissing,
    _remote_host_config, _build_remote_client, _safe_request_host_id,
    _active_dss_client, _resolve_client, ThreadPoolExecutor, _resolve_macro_project,
    _local_toolkit_client, _local_toolkit_project, _active_support_project,
    _list_projects_catalog_cheap,
)
from adk_backend.footprint import (
    _compute_footprint_payload,
    _footprint_reset_negative_cache, _footprint_details_map,
)
from adk_backend.usage_scan import (
    _dedupe_usage_entries,
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


def _parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'y', 'on')


def _scope_root(scope: str, project_key: Optional[str]) -> Dict[str, str]:
    if scope == 'all':
        return {'name': '/', 'path': '/'}
    if scope == 'global':
        return {'name': 'global', 'path': '/dss-data/global'}
    if scope == 'project' and project_key:
        return {'name': project_key, 'path': f'/dss-data/projects/{project_key}'}
    return {'name': 'dss_data', 'path': '/dss-data'}


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


# ─────────────────────────────────────────────────────────────────────────
# In-app feedback (EAP)
#
# Admins have no other channel to report bugs / ideas while the toolkit is in
# Early Access Preview, and the repo is private (a client-only GitHub-issue
# link 404s for non-collaborators). So the backend emails feedback — with
# optional file/image attachments — to a fixed recipient via the same DSS mail
# channel the outreach campaigns already use. The endpoint is public (no auth),
# so honeypot + per-worker rate limit + strict caps are mandatory.
# ─────────────────────────────────────────────────────────────────────────
FEEDBACK_RECIPIENT = 'alex.kaos@dataiku.com'
_FEEDBACK_MAX_MSG_LEN = 5000
_FEEDBACK_MAX_FILES = 5
_FEEDBACK_MAX_FILE_BYTES = 8 * 1024 * 1024  # 8 MB per file
_FEEDBACK_ALLOWED_EXT = {
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg',
    '.pdf', '.txt', '.log',
}
_FEEDBACK_RATE_MAX = 5            # max submissions …
_FEEDBACK_RATE_WINDOW_S = 600     # … per 10 minutes, per (host, client IP)
# Per-gunicorn-worker (not global) — acceptable for EAP volume.
_FEEDBACK_RATE: Dict[str, List[float]] = {}


def _feedback_safe_name(name: str) -> str:
    """Sanitize an uploaded filename to a safe basename so the emailed
    attachment keeps a meaningful name instead of a random temp name."""
    base = os.path.basename((name or '').replace('\\', '/')).strip()
    base = re.sub(r'[^A-Za-z0-9._-]', '_', base).lstrip('.')
    return (base or 'attachment')[:120]


@app.route('/api/feedback', methods=['POST'])
@local_only
def api_feedback():
    """Email in-app feedback (+ optional attachments) to a fixed recipient.

    @local_only: the mail channel and plugin config live on the LOCAL DSS, so a
    remote-host view must not break feedback — g.client is the local client."""
    # Honeypot: a real user never sees the `website` field; bots fill it.
    # Silently accept + drop so the bot can't tell it was rejected.
    if (request.form.get('website') or '').strip():
        return jsonify({'ok': True})

    # Rate limit on host + client IP (per worker — fine for EAP volume).
    rate_key = f"{getattr(g, 'host_id', 'local')}|{request.remote_addr or '?'}"
    now = time.time()
    recent = [t for t in _FEEDBACK_RATE.get(rate_key, []) if now - t < _FEEDBACK_RATE_WINDOW_S]
    if len(recent) >= _FEEDBACK_RATE_MAX:
        _FEEDBACK_RATE[rate_key] = recent
        return jsonify({
            'error': 'rate-limited',
            'message': 'Please wait a moment before sending more feedback.',
        }), 429

    fb_type = (request.form.get('type') or '').strip().lower()
    if fb_type not in ('bug', 'idea', 'other'):
        return jsonify({'error': 'Invalid feedback type'}), 400

    message = (request.form.get('message') or '').strip()
    if not message:
        return jsonify({'error': 'Message is required'}), 400
    if len(message) > _FEEDBACK_MAX_MSG_LEN:
        return jsonify({'error': f'Message exceeds {_FEEDBACK_MAX_MSG_LEN} characters'}), 400

    reply_email = re.sub(r'[\r\n]', '', (request.form.get('email') or '').strip())
    if reply_email and not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', reply_email):
        return jsonify({'error': 'Invalid reply email address'}), 400

    diagnostics = (request.form.get('diagnostics') or '').strip()

    # Validate attachments before touching the filesystem or the mail channel.
    uploads = [u for u in request.files.getlist('attachments') if u and u.filename]
    if len(uploads) > _FEEDBACK_MAX_FILES:
        return jsonify({'error': f'Too many files (max {_FEEDBACK_MAX_FILES})'}), 400
    for up in uploads:
        ext = os.path.splitext(up.filename)[1].lower()
        if ext not in _FEEDBACK_ALLOWED_EXT:
            return jsonify({'error': f'File type not allowed: {up.filename}'}), 400
        try:
            up.stream.seek(0, os.SEEK_END)
            size = up.stream.tell()
            up.stream.seek(0)
        except (OSError, ValueError):
            size = 0
        if size > _FEEDBACK_MAX_FILE_BYTES:
            return jsonify({
                'error': f'File too large (max {_FEEDBACK_MAX_FILE_BYTES // (1024 * 1024)} MB): {up.filename}',
            }), 400

    client = g.client
    if client is None:
        return jsonify({'error': 'No DSS mail channel configured'}), 400
    channels = _list_mail_channels(client)
    if not channels:
        app.logger.warning("[feedback] send failed: no DSS mail channel configured")
        return jsonify({'error': 'No DSS mail channel configured'}), 400
    selected = _get_configured_mail_channel() or channels[0]['id']
    channel_obj = _get_mail_channel(client, selected)
    if channel_obj is None:
        app.logger.warning("[feedback] send failed: cannot resolve mail channel %s", selected)
        return jsonify({'error': 'No DSS mail channel configured'}), 400

    # Must use the macro-project fallback, NOT the empty-string self-reject path.
    project_key = os.environ.get('DKU_CURRENT_PROJECT_KEY') or MACRO_PROJECT_KEY

    subject = re.sub(r'[\r\n]', '', f'[admin-toolkit feedback] {fb_type}')
    body_lines = [message, '']
    if reply_email:
        body_lines.append(f'Reply-to: {reply_email}')
        body_lines.append('')
    if diagnostics:
        body_lines.append('Diagnostics:')
        body_lines.append(diagnostics)
    body = '\n'.join(body_lines)

    # send() wants list[BufferedReader] (real open file objects). werkzeug's
    # FileStorage.stream (a SpooledTemporaryFile) isn't guaranteed compatible,
    # so stage each upload to its own temp dir under a sanitized original name
    # and hand over open 'rb' handles; close + delete them in finally.
    handles: List[Any] = []
    temp_paths: List[str] = []
    temp_dirs: List[str] = []
    try:
        for up in uploads:
            tmpdir = tempfile.mkdtemp(prefix='admin-toolkit-feedback-')
            temp_dirs.append(tmpdir)
            dest = os.path.join(tmpdir, _feedback_safe_name(up.filename))
            up.save(dest)
            temp_paths.append(dest)
            handles.append(open(dest, 'rb'))
        channel_obj.send(
            project_key, [FEEDBACK_RECIPIENT], subject, body,
            attachments=handles or None, plain_text=True,
        )
    except Exception as exc:
        app.logger.warning("[feedback] send failed: %s", exc)
        return jsonify({'error': f'Failed to send feedback: {exc}'}), 502
    finally:
        for h in handles:
            try:
                h.close()
            except Exception:
                pass
        for p in temp_paths:
            try:
                os.unlink(p)
            except Exception:
                pass
        for d in temp_dirs:
            try:
                os.rmdir(d)
            except Exception:
                pass

    _FEEDBACK_RATE[rate_key] = recent + [now]
    app.logger.info(
        "[feedback] sent type=%s files=%d host=%s",
        fb_type, len(uploads), getattr(g, 'host_id', 'local'),
    )
    return jsonify({'ok': True})


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


# ── Save Tables as Datasets ─────────────────────────────────────────────────
# Persist the UI's rendered tables as managed Dataiku datasets, one per table,
# in the toolkit's OWN project on a connection the admin picks in plugin
# settings. Local-scoped (like DB Health): writes go through the in-process
# `dataiku` package on the local DSS, so the host header is intentionally
# ignored here. Empty connection setting ⇒ feature disabled.

_DATASET_NAME_BADCHARS = re.compile(r'[^A-Za-z0-9_]+')


def _sanitize_dss_name(raw: Any, fallback: str) -> str:
    """Coerce an arbitrary UI label into a valid DSS dataset/column name:
    keep [A-Za-z0-9_], collapse other runs to '_', strip leading/trailing '_',
    and prefix with '_' if it starts with a digit or ends up empty."""
    s = _DATASET_NAME_BADCHARS.sub('_', str(raw or '')).strip('_')
    if not s:
        s = fallback
    if s[0].isdigit():
        s = '_' + s
    return s


def _dedupe_dss_names(names: List[str]) -> List[str]:
    """Append _2, _3, … to duplicate names (case-insensitive), order-preserving
    and collision-aware (the suffixed form is itself checked for uniqueness)."""
    used = set()
    out = []
    for name in names:
        candidate = name
        n = 1
        while candidate.lower() in used:
            n += 1
            candidate = '%s_%d' % (name, n)
        used.add(candidate.lower())
        out.append(candidate)
    return out


def _dataset_export_connection() -> str:
    """The configured target connection from LOCAL plugin settings ('' if unset)."""
    raw = _local_toolkit_client().get_plugin('admin-toolkit').get_settings().get_raw()
    config = raw.get('config', {}) if isinstance(raw, dict) else {}
    return (config.get('dataset_export_connection') or '').strip()


@app.route('/api/tools/dataset-export/config')
@local_only
def api_dataset_export_config():
    """Report whether the feature is enabled (drives toolbar button state)."""
    try:
        return jsonify({
            'configuredConnection': _dataset_export_connection(),
            'project': dataiku.default_project_key(),
        })
    except Exception as exc:
        return jsonify({'error': str(exc)[:200]}), 500


@app.route('/api/tools/dataset-export/save', methods=['POST'])
@local_only
def api_dataset_export_save():
    """Save each posted UI table as a managed dataset in the toolkit's project.
    Overwrites in place on repeat; every column is string-typed."""
    try:
        connection = _dataset_export_connection()
        if not connection:
            return jsonify({
                'error': 'No connection is configured for Save Tables as Datasets. '
                         'An administrator must select one in the Admin Toolkit plugin settings.',
            }), 400

        body = request.get_json(silent=True) or {}
        tables = body.get('tables') or []
        if not isinstance(tables, list) or not tables:
            return jsonify({'error': 'No tables provided.'}), 400

        import pandas as pd

        project = _local_toolkit_project()
        project_key = dataiku.default_project_key()

        # Backend is the naming authority: sanitize + dedupe across all tables.
        raw_names = [t.get('name') if isinstance(t, dict) else '' for t in tables]
        sane_names = [_sanitize_dss_name(n, 'table_%d' % (i + 1)) for i, n in enumerate(raw_names)]
        dataset_names = _dedupe_dss_names(sane_names)

        try:
            existing = {d.get('name') for d in project.list_datasets() if isinstance(d, dict)}
        except Exception:
            existing = set()

        results = []
        for i, table in enumerate(tables):
            ds_name = dataset_names[i]
            ui_name = raw_names[i] if isinstance(raw_names[i], str) else ''
            try:
                cols_raw = (table.get('columns') if isinstance(table, dict) else None) or []
                rows_raw = (table.get('rows') if isinstance(table, dict) else None) or []
                col_names = _dedupe_dss_names(
                    [_sanitize_dss_name(c, 'col_%d' % (j + 1)) for j, c in enumerate(cols_raw)]
                )
                if not col_names:
                    results.append({'name': ui_name, 'datasetName': ds_name,
                                    'status': 'error', 'rows': 0, 'error': 'Table has no columns'})
                    continue

                # Normalize every row to the column count; all cells are strings.
                width = len(col_names)
                norm_rows = []
                for r in rows_raw:
                    cells = list(r) if isinstance(r, (list, tuple)) else [r]
                    cells = [('' if c is None else str(c)) for c in cells[:width]]
                    cells += [''] * (width - len(cells))
                    norm_rows.append(cells)

                already = ds_name in existing
                if not already:
                    project.new_managed_dataset(ds_name).with_store_into(connection).create()
                    existing.add(ds_name)

                df = pd.DataFrame(norm_rows, columns=col_names, dtype=str)
                dataiku.Dataset(ds_name).write_with_schema(df)

                results.append({'name': ui_name, 'datasetName': ds_name,
                                'status': 'overwritten' if already else 'created',
                                'rows': len(norm_rows)})
            except Exception as exc:
                results.append({'name': ui_name, 'datasetName': ds_name,
                                'status': 'error', 'rows': 0, 'error': str(exc)[:300]})

        return jsonify({'project': project_key, 'connection': connection, 'results': results})
    except Exception as exc:
        return jsonify({'error': str(exc)[:300]}), 500


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
from adk_backend.routes.footprint import bp as footprint_bp
from adk_backend.routes.hosts import bp as hosts_bp
from adk_backend.routes.projects import bp as projects_bp

app.register_blueprint(auth_bp)
app.register_blueprint(code_env_replace_bp)
app.register_blueprint(code_envs_bp)
app.register_blueprint(connections_bp)
app.register_blueprint(footprint_bp)
app.register_blueprint(hosts_bp)
app.register_blueprint(projects_bp)
