"""Multi-instance host routes: list presets, probe a host, bootstrap ADMINTOOLKIT.

These three endpoints are exempted from backend.py's _check_host_ready gate —
they exist precisely to diagnose / fix a broken host config.
"""

from typing import Any, Dict

from flask import Blueprint, jsonify, request

from adk_backend.clients import (
    MACRO_PROJECT_DEFAULT_NAME,
    MACRO_PROJECT_KEY,
    _list_remote_hosts,
    _resolve_client,
)

bp = Blueprint('hosts', __name__)


@bp.route('/api/hosts')
def api_hosts():
    """List local + remote-preset hosts. API keys are never returned."""
    hosts = [{'id': 'local', 'label': 'Local DSS', 'url': ''}]
    hosts.extend(_list_remote_hosts())
    return jsonify(hosts)


@bp.route('/api/hosts/check', methods=['POST'])
def api_hosts_check():
    """Probe a host: reachable? plugin installed? ADMINTOOLKIT exists?"""
    payload = request.get_json(silent=True) or {}
    host_id = (payload.get('hostId') or 'local').strip()
    try:
        client = _resolve_client(host_id)
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:200]}'})
    result: Dict[str, Any] = {
        'ok': True,
        'pluginInstalled': False,
        'pluginVersion': None,
        'adminToolkitProjectExists': False,
    }
    try:
        plugins = client.list_plugins() or []
        for plug in plugins:
            if isinstance(plug, dict) and plug.get('id') == 'admin-toolkit':
                result['pluginInstalled'] = True
                result['pluginVersion'] = plug.get('version')
                break
    except Exception as exc:
        result['ok'] = False
        result['error'] = f'list_plugins failed: {str(exc)[:200]}'
        return jsonify(result)
    try:
        project = client.get_project(MACRO_PROJECT_KEY)
        project.get_summary()
        result['adminToolkitProjectExists'] = True
    except Exception:
        result['adminToolkitProjectExists'] = False
    return jsonify(result)


@bp.route('/api/hosts/macro-project', methods=['POST'])
def api_hosts_macro_project():
    """Create the ADMINTOOLKIT project on the active host."""
    payload = request.get_json(silent=True) or {}
    host_id = (payload.get('hostId') or 'local').strip()
    name = (payload.get('name') or MACRO_PROJECT_DEFAULT_NAME).strip() or MACRO_PROJECT_DEFAULT_NAME
    try:
        client = _resolve_client(host_id)
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:200]}'}), 400
    try:
        client.create_project(MACRO_PROJECT_KEY, name, owner='admin')
        return jsonify({'ok': True, 'projectKey': MACRO_PROJECT_KEY, 'name': name})
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:300]}'}), 500
