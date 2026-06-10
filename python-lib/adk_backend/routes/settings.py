"""Backend-settings routes: current/default perf settings + plugin threshold
defaults. `_BACKEND_SETTINGS` (adk_backend.settings) is the ONE shared dict —
read under its lock here; any mutation is in place, never a rebind."""
from flask import Blueprint, jsonify, request

from adk_backend.settings import (
    _BACKEND_SETTINGS,
    _BACKEND_SETTINGS_DEFAULTS,
    _BACKEND_SETTINGS_LOCK,
)

bp = Blueprint('settings', __name__)


@bp.route('/api/settings', methods=['GET'])
def api_settings_get():
    with _BACKEND_SETTINGS_LOCK:
        return jsonify({'current': dict(_BACKEND_SETTINGS), 'defaults': dict(_BACKEND_SETTINGS_DEFAULTS)})


@bp.route('/api/settings/update', methods=['POST'])
def api_settings_update():
    """Update known backend settings at runtime (until the next backend
    restart, when saved plugin config re-merges). All settings are ints;
    unknown keys and non-int values are rejected, not applied."""
    payload = request.get_json(silent=True) or {}
    updated = {}
    rejected = []
    with _BACKEND_SETTINGS_LOCK:
        for key, value in payload.items():
            if key not in _BACKEND_SETTINGS:
                rejected.append(key)
                continue
            try:
                _BACKEND_SETTINGS[key] = int(value)
            except (TypeError, ValueError):
                rejected.append(key)
                continue
            updated[key] = _BACKEND_SETTINGS[key]
        current = dict(_BACKEND_SETTINGS)
    return jsonify({'updated': updated, 'rejected': rejected, 'current': current})


@bp.route('/api/settings/threshold-defaults', methods=['GET'])
def api_settings_threshold_defaults():
    try:
        from db_adapter import load_plugin_threshold_defaults
        return jsonify(load_plugin_threshold_defaults())
    except Exception:
        return jsonify({})
