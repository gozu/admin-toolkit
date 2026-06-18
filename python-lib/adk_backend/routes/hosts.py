"""Multi-instance host routes: list presets, probe a host, bootstrap ADMINTOOLKIT.

These endpoints are exempted from backend.py's _check_host_ready gate — they
exist precisely to diagnose / fix a broken host config (including the one-click
install-toolkit flow that turns a plugin-less remote green).
"""

import json
import time
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from adk_backend.clients import (
    MACRO_PROJECT_DEFAULT_NAME,
    MACRO_PROJECT_KEY,
    _build_remote_client,
    _list_remote_hosts,
    _remote_host_config,
    _resolve_client,
)
from adk_backend.utils import _sse_response

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


@bp.route('/api/hosts/install-toolkit', methods=['POST'])
def api_hosts_install_toolkit():
    """One-click turnkey install of the Admin Toolkit onto a reachable remote.

    Source of truth is the plugin running on THIS controller, streamed live via
    download_plugin_stream → install_plugin_from_archive on the remote, so the
    remote receives the exact version the controller runs (no version drift, no
    bundled zip-of-itself, no git/network egress). Macros ship inside the
    archive, so installing the plugin installs them too. Three steps:

      1. install  — install (or update_from_zip) the plugin on the remote,
      2. codeenv  — build + select the plugin's managed code env on the remote,
      3. project  — create the ADMINTOOLKIT support project if absent.

    Streams one SSE `step` event per phase: {step, status, msg?, error?} where
    status ∈ active|done|error, then a terminal {step:'complete', status:'done'}.
    All ops are pure DSS-API calls → they stay on the client (no macro needed).
    """
    payload = request.get_json(silent=True) or {}
    host_id = (payload.get('hostId') or '').strip()
    if not host_id or host_id == 'local':
        return jsonify({'error': 'hostId must reference a remote-dss-host preset'}), 400
    # _remote_host_config raises RemoteKeysLocked (→ 409 via @errorhandler, pops
    # the unlock modal) when the preset key is encrypted and we hold no key.
    cfg = _remote_host_config(host_id)
    if cfg is None:
        return jsonify({'error': 'invalid-host-id', 'hostId': host_id}), 400
    local_client = _resolve_client('local')
    remote_client = _build_remote_client(cfg)

    def sse(step: str, status: str, msg: str = None, error: str = None) -> str:
        evt: Dict[str, Any] = {'step': step, 'status': status}
        if msg is not None:
            evt['msg'] = msg
        if error is not None:
            evt['error'] = error
        return "event: step\ndata: %s\n\n" % json.dumps(evt)

    def generate():
        # ── Step 1: install (or update) the plugin on the remote ──
        yield sse('install', 'active', 'Installing plugin on remote…')
        try:
            already_installed = False
            for plug in (remote_client.list_plugins() or []):
                if isinstance(plug, dict) and plug.get('id') == 'admin-toolkit':
                    already_installed = True
                    break
            stream = local_client.download_plugin_stream('admin-toolkit')
            if already_installed:
                remote_client.get_plugin('admin-toolkit').update_from_zip(stream)
            else:
                remote_client.install_plugin_from_archive(stream)
            yield sse('install', 'done',
                      'Plugin updated' if already_installed else 'Plugin installed')
        except Exception as exc:
            yield sse('install', 'error', error=f'{type(exc).__name__}: {str(exc)[:300]}')
            return

        # ── Step 2: build + select the plugin's managed code env on the remote ──
        yield sse('codeenv', 'active', 'Checking code env…')
        try:
            plugin = remote_client.get_plugin('admin-toolkit')
            settings = plugin.get_settings()
            if (settings.get_raw() or {}).get('codeEnvName'):
                yield sse('codeenv', 'done', 'Code env already built')
            else:
                # plugin default interpreter; future result carries envName.
                future = plugin.create_code_env()
                if future.job_id:
                    polls = 0
                    while True:
                        state = future.peek_state() or {}
                        if state.get('hasResult') or not state.get('alive', True):
                            break
                        polls += 1
                        if polls > 240:  # ~20 min cap at 5s/poll
                            raise Exception('code env build timed out after ~20 min')
                        yield sse('codeenv', 'active', 'Building code env… (~%ds)' % (polls * 5))
                        time.sleep(5)
                    result = future.get_result() or {}
                else:
                    result = future.wait_for_result() or {}
                env_name = (result or {}).get('envName')
                if not env_name:
                    raise Exception('code env build returned no envName')
                settings.set_code_env(env_name)
                settings.save()
                yield sse('codeenv', 'done', 'Code env built: %s' % env_name)
        except Exception as exc:
            yield sse('codeenv', 'error', error=f'{type(exc).__name__}: {str(exc)[:300]}')
            return

        # ── Step 3: create the ADMINTOOLKIT support project if absent ──
        yield sse('project', 'active', 'Creating support project…')
        try:
            exists = False
            try:
                remote_client.get_project(MACRO_PROJECT_KEY).get_summary()
                exists = True
            except Exception:
                exists = False
            if exists:
                yield sse('project', 'done', 'Support project already exists')
            else:
                remote_client.create_project(
                    MACRO_PROJECT_KEY, MACRO_PROJECT_DEFAULT_NAME, owner='admin')
                yield sse('project', 'done', 'Support project created')
        except Exception as exc:
            yield sse('project', 'error', error=f'{type(exc).__name__}: {str(exc)[:300]}')
            return

        yield sse('complete', 'done', 'Admin Toolkit installed')

    return _sse_response(generate)
