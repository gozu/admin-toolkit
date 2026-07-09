"""Misc small routes — mode probe, cache clear, managed-folder + mail-channel listing."""
import io
import logging
import re

from flask import Blueprint, g, jsonify, request

from adk_backend.caching import (
    _CACHE, _CACHE_INFLIGHT_ERRORS, _CACHE_LOCK,
    _bump_session_epoch, _clear_shared_project_code_env_usage,
)
from adk_backend.clients import (
    _active_support_project, _get_sdk_cache, _instance_id, _local_toolkit_client,
)
from adk_backend.footprint import _footprint_reset_negative_cache
from adk_backend.mail import _get_configured_mail_channel, _list_mail_channels

bp = Blueprint('misc', __name__)

_LOGGER = logging.getLogger(__name__)

ARCHIVE_FOLDER_NAME = 'admin-toolkit-archive'


def _archive_folder_connection() -> str:
    """The configured archive connection from LOCAL plugin settings ('' if unset).

    Unified `toolkit_storage_connection` wins; legacy per-feature key is the
    fallback (new-key-first, same pattern as the DB connections)."""
    raw = _local_toolkit_client().get_plugin('admin-toolkit').get_settings().get_raw()
    config = raw.get('config', {}) if isinstance(raw, dict) else {}
    return (config.get('toolkit_storage_connection')
            or config.get('archive_folder_connection') or '').strip()


def _resolve_archive_folder(project, connection: str, create: bool = True):
    """Find (or create, on `connection`) the toolkit's archive managed folder
    in `project`. Returns the folder list entry dict, or None."""
    for f in project.list_managed_folders():
        if f.get('name') == ARCHIVE_FOLDER_NAME:
            return f
    if not create or not connection:
        return None
    folder = project.create_managed_folder(ARCHIVE_FOLDER_NAME, connection_name=connection)
    return {'id': folder.id, 'name': ARCHIVE_FOLDER_NAME}


_PLUGIN_VERSION = ''


def _plugin_version() -> str:
    """Installed admin-toolkit version via the local DSS API, cached once
    resolved. Read at runtime (not a disk path: DSS runs this code from a
    webappruns/ snapshot with no plugin.json alongside) so the frontend gets
    it from /api/mode instead of a build-time constant — version-only bumps
    then don't rewrite resource/dist/."""
    global _PLUGIN_VERSION
    if not _PLUGIN_VERSION:
        try:
            for plug in _local_toolkit_client().list_plugins() or []:
                if isinstance(plug, dict) and plug.get('id') == 'admin-toolkit':
                    _PLUGIN_VERSION = str(plug.get('version') or '')
                    break
        except Exception:
            pass
    return _PLUGIN_VERSION


@bp.route('/api/mode')
def api_mode():
    return jsonify({'mode': 'live', 'version': _plugin_version()})


@bp.route('/api/cache/clear', methods=['POST'])
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


@bp.route('/api/managed-folders', methods=['GET'])
def api_managed_folders():
    """List managed folders in the active support project.

    When an Archive Folders Connection is configured in the plugin settings,
    find-or-create the 'admin-toolkit-archive' folder on it and report its id
    as `archiveDefaultId` — the cleaners' pickers default to it."""
    client = g.client
    project = _active_support_project(client)
    archive_default_id = ''
    connection = ''
    try:
        connection = _archive_folder_connection()
        if connection:
            entry = _resolve_archive_folder(project, connection)
            archive_default_id = (entry or {}).get('id') or ''
    except Exception as exc:
        # Remote host may lack the configured connection — listing still works.
        _LOGGER.warning('[managed-folders] archive folder resolution failed: %s', exc)
    folders = project.list_managed_folders()
    return jsonify({
        'folders': [
            {'id': f['id'], 'name': f.get('name') or f['id']}
            for f in folders
        ],
        'archiveDefaultId': archive_default_id,
        'archiveConnection': connection,
    })


@bp.route('/api/archive/store', methods=['POST'])
def api_archive_store():
    """Store a client-built export zip into the archive managed folder.

    Body: raw zip bytes. Query: ?name=<file name>. No-op ({stored: false})
    unless an Archive Folders Connection is configured."""
    try:
        connection = _archive_folder_connection()
    except Exception as exc:
        return jsonify({'stored': False, 'reason': str(exc)[:200]})
    if not connection:
        return jsonify({'stored': False, 'reason': 'not-configured'})
    name = re.sub(r'[^A-Za-z0-9._ ()-]', '_', request.args.get('name') or 'export.zip')[:120]
    data = request.get_data()
    if not data:
        return jsonify({'error': 'empty body'}), 400
    try:
        project = _active_support_project(g.client)
        entry = _resolve_archive_folder(project, connection)
        if entry is None:
            return jsonify({'stored': False, 'reason': 'archive folder unavailable'})
        project.get_managed_folder(entry['id']).put_file(name, io.BytesIO(data))
        return jsonify({'stored': True, 'folderId': entry['id'], 'name': name,
                        'bytes': len(data)})
    except Exception as exc:
        _LOGGER.warning('[archive-store] upload failed: %s', exc)
        return jsonify({'error': str(exc)[:300]}), 500


@bp.route('/api/mail-channels')
def api_mail_channels():
    client = g.client
    channels = _list_mail_channels(client)
    return jsonify({
        'channels': channels,
        'configuredMailChannel': _get_configured_mail_channel(),
    })
