"""Agent actuator backend — small pure-dataikuapi action implementations.

One POST dispatch (`/api/tools/admin-actions/<action>`, @advanced) plus cheap
inventory GETs. Every impl runs on `g.client` (the per-host DSSClient), so a
new action here is automatically fleet-routable — the multi-instance rule's
B-api pattern. Host-bound filesystem work stays out: that goes through the
fs-cleanup macro (also in this module, `/api/tools/fs-cleanup/*`).

Backups go to a managed folder in the toolkit support project (same doctrine
as the project/code-env cleaners: deletes always back up first). Connection
definition backups may carry credential material — the folder is admin-scoped.
"""
import json
import logging
import re
import tempfile

from flask import Blueprint, g, jsonify, request

from adk_backend.clients import _active_support_project
from adk_backend.macros import _fs_cleanup_macro
from adk_backend.utils import advanced
from atk_agent_common.policies import settings_paths

bp = Blueprint('admin_actions', __name__)
_LOGGER = logging.getLogger(__name__)

# The toolkit must never uninstall itself (or lose its own support tooling).
_PROTECTED_PLUGIN_IDS = ('admin-toolkit',)


def _backup_folder_handle(client, folder_id):
    """Validated managed-folder handle in the toolkit support project
    (factored from the project-cleaner's backup half)."""
    plugin_project = _active_support_project(client)
    dest = plugin_project.get_managed_folder(folder_id)
    dest.get_definition()  # verify it exists
    return dest


def _safe_name(name):
    return re.sub(r'[^a-zA-Z0-9._-]', '_', str(name or ''))


def _backup_json(client, folder_id, filename, payload):
    dest = _backup_folder_handle(client, folder_id)
    data = json.dumps(payload, indent=2, default=str).encode('utf-8')
    import io
    dest.put_file(filename, io.BytesIO(data))
    return filename


def _redact_secrets(node):
    """Recursively replace values whose key matches the secret-material
    blacklist — definition reads must never leak credentials to an agent."""
    if isinstance(node, dict):
        return {k: ('<redacted>' if isinstance(k, str)
                    and settings_paths.BLOCKED_SEGMENT_RE.search(k)
                    else _redact_secrets(v))
                for k, v in node.items()}
    if isinstance(node, list):
        return [_redact_secrets(v) for v in node]
    return node


# ── inventory GETs (read-only grounding for planners) ────────────────────────


@bp.route('/api/tools/admin-actions/connection-definition')
def api_admin_actions_connection_definition():
    """Secret-redacted definition of one connection — the read side of
    connection-update's drift guard."""
    name = (request.args.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name query parameter is required'}), 400
    try:
        definition = g.client.get_connection(name).get_definition()
    except Exception as exc:
        return jsonify({'error': 'Connection %r not readable: %s' % (name, str(exc)[:200])}), 404
    return jsonify({'ok': True, 'name': name, 'definition': _redact_secrets(definition)})


@bp.route('/api/tools/admin-actions/plugin-usages')
def api_admin_actions_plugin_usages():
    """Usage preflight for plugin-uninstall: every component usage across
    projects, plus unresolvable (missing-type) usages."""
    plugin_id = (request.args.get('pluginId') or '').strip()
    if not plugin_id:
        return jsonify({'error': 'pluginId query parameter is required'}), 400
    try:
        raw = g.client.get_plugin(plugin_id).list_usages().get_raw()
    except Exception as exc:
        return jsonify({'error': 'Plugin %r usages not readable: %s'
                                 % (plugin_id, str(exc)[:200])}), 404
    usages = raw.get('usages') or []
    return jsonify({'ok': True, 'pluginId': plugin_id,
                    'usageCount': len(usages), 'usages': usages[:50],
                    'missingTypes': raw.get('missingTypes') or []})


# ── action impls (dispatched by the POST route below) ───────────────────────


def _impl_connection_test(client, body):
    name = body.get('name') or ''
    result = client.get_connection(name).test()
    return {'ok': bool((result or {}).get('connectionOK')), 'name': name,
            'result': _redact_secrets(result or {})}


def _impl_connection_delete(client, body):
    name = body.get('name') or ''
    folder_id = body.get('folderId') or ''
    conn = client.get_connection(name)
    definition = conn.get_definition()  # backup keeps credentials — admin-scoped folder
    filename = 'connection-%s.json' % _safe_name(name)
    _backup_json(client, folder_id, filename, definition)
    conn.delete()
    return {'ok': True, 'deleted': name, 'backupFile': filename}


def _impl_connection_update(client, body):
    name = body.get('name') or ''
    path = (body.get('path') or '').strip()
    segments = settings_paths.parse_path(path)  # raises on garbage
    for seg in segments:
        if isinstance(seg, str) and settings_paths.BLOCKED_SEGMENT_RE.search(seg):
            return {'ok': False, 'error': 'path %r is blocked: segment %r matches the '
                                          'secret-material blacklist' % (path, seg)}
    conn = client.get_connection(name)
    definition = conn.get_definition()
    current = settings_paths.get_at(definition, path)
    expected = body.get('expectedCurrent')
    if json.dumps(current, sort_keys=True, default=str) != json.dumps(expected, sort_keys=True, default=str):
        return {'ok': False,
                'error': 'Connection %s %s drifted between plan and execute '
                         '(expected %s, found %s) — refusing.'
                         % (name, path, json.dumps(expected, default=str)[:200],
                            json.dumps(current, default=str)[:200])}
    settings_paths.set_at(definition, path, body.get('newValue'))
    conn.set_definition(definition)
    return {'ok': True, 'name': name, 'path': path,
            'before': current, 'after': body.get('newValue')}


def _impl_cluster_detach(client, body):
    cluster_id = body.get('clusterId') or ''
    folder_id = body.get('folderId') or ''
    cluster = client.get_cluster(cluster_id)
    definition = cluster.get_definition()
    filename = 'cluster-%s.json' % _safe_name(cluster_id)
    _backup_json(client, folder_id, filename, definition)
    cluster.delete()  # removes the DSS attachment only — cloud resources untouched
    return {'ok': True, 'detached': cluster_id, 'backupFile': filename}


def _impl_plugin_uninstall(client, body):
    plugin_id = body.get('pluginId') or ''
    folder_id = body.get('folderId') or ''
    if plugin_id in _PROTECTED_PLUGIN_IDS:
        return {'ok': False, 'error': 'Refusing to uninstall %r — the toolkit never '
                                      'removes itself.' % plugin_id}
    plugin = client.get_plugin(plugin_id)
    usages = plugin.list_usages().get_raw().get('usages') or []
    if usages:  # never trust the plan — re-check at execute time
        return {'ok': False,
                'error': 'Plugin %r is used by %d object(s) — uninstall refused. '
                         'First usages: %s'
                         % (plugin_id, len(usages), json.dumps(usages[:5], default=str)[:400])}
    filename = 'plugin-%s.zip' % _safe_name(plugin_id)
    dest = _backup_folder_handle(client, folder_id)
    with tempfile.NamedTemporaryFile(suffix='.zip', delete=True) as tmp:
        client.download_plugin_to_file(plugin_id, tmp.name)
        with open(tmp.name, 'rb') as fh:
            dest.put_file(filename, fh)
    future = plugin.delete(force=False)
    result = future.wait_for_result() if future is not None else None
    return {'ok': True, 'uninstalled': plugin_id, 'backupFile': filename,
            'result': result}


_ACTION_IMPLS = {
    'connection-test': _impl_connection_test,
    'connection-delete': _impl_connection_delete,
    'connection-update': _impl_connection_update,
    'cluster-detach': _impl_cluster_detach,
    'plugin-uninstall': _impl_plugin_uninstall,
}


@bp.route('/api/tools/admin-actions/<action>', methods=['POST'])
@advanced
def api_admin_actions_execute(action):
    impl = _ACTION_IMPLS.get(action)
    if impl is None:
        return jsonify({'error': 'Unknown admin action %r' % action}), 404
    body = request.get_json(force=True, silent=True) or {}
    try:
        result = impl(g.client, body)
    except settings_paths.SettingsPathError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    except Exception as exc:
        _LOGGER.error('[admin-actions] %s failed: %s', action, exc)
        return jsonify({'ok': False, 'error': '%s: %s' % (type(exc).__name__, str(exc)[:300])}), 500
    status = 200 if result.get('ok') else 409
    if not result.get('ok'):
        _LOGGER.info('[admin-actions] %s refused: %s', action, result.get('error'))
    else:
        _LOGGER.info('[admin-actions] %s ok: %s', action, json.dumps(result, default=str)[:300])
    return jsonify(result), status


# ── fs-cleanup (B-macro pattern: host filesystem work stays in the macro) ────


@bp.route('/api/tools/fs-cleanup/scan')
def api_fs_cleanup_scan():
    try:
        result = _fs_cleanup_macro(
            g.client, 'scan',
            policy=(request.args.get('policy') or 'webappruns').strip(),
            project_key=(request.args.get('projectKey') or '').strip() or None,
            min_age_days=request.args.get('minAgeDays', type=int),
            keep_last_runs=request.args.get('keepLastRuns', type=int))
    except Exception as exc:
        _LOGGER.error('[fs-cleanup] scan macro failed: %s', exc)
        return jsonify({'ok': False, 'error': str(exc)}), 502
    return jsonify(result), 200 if result.get('ok') else 400


@bp.route('/api/tools/fs-cleanup/delete', methods=['POST'])
@advanced
def api_fs_cleanup_delete():
    body = request.get_json(force=True, silent=True) or {}
    dry_run = bool(body.get('dryRun', True))
    try:
        result = _fs_cleanup_macro(
            g.client, 'delete',
            policy=(body.get('policy') or 'webappruns').strip(),
            project_key=(body.get('projectKey') or '').strip() or None,
            min_age_days=body.get('minAgeDays'),
            keep_last_runs=body.get('keepLastRuns'),
            max_delete_gb=body.get('maxDeleteGB'),
            dry_run=dry_run)
    except Exception as exc:
        _LOGGER.error('[fs-cleanup] delete macro failed: %s', exc)
        return jsonify({'ok': False, 'error': str(exc)}), 502
    _LOGGER.info('[fs-cleanup] delete dryRun=%s deletedRuns=%s reclaimedGB=%s',
                 dry_run, result.get('totalDeletedRuns'), result.get('totalReclaimedGB'))
    return jsonify(result), 200 if result.get('ok') else 400
