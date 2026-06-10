"""Misc small routes — mode probe, cache clear, managed-folder + mail-channel listing."""
from flask import Blueprint, g, jsonify

from adk_backend.caching import (
    _CACHE, _CACHE_INFLIGHT_ERRORS, _CACHE_LOCK,
    _bump_session_epoch, _clear_shared_project_code_env_usage,
)
from adk_backend.clients import _active_support_project, _get_sdk_cache, _instance_id
from adk_backend.footprint import _footprint_reset_negative_cache
from adk_backend.mail import _get_configured_mail_channel, _list_mail_channels

bp = Blueprint('misc', __name__)


@bp.route('/api/mode')
def api_mode():
    return jsonify({'mode': 'live'})


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


@bp.route('/api/mail-channels')
def api_mail_channels():
    client = g.client
    channels = _list_mail_channels(client)
    return jsonify({
        'channels': channels,
        'configuredMailChannel': _get_configured_mail_channel(),
    })
