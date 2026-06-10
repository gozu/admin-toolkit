"""Backend-settings routes: current/default perf settings + plugin threshold
defaults. `_BACKEND_SETTINGS` (adk_backend.settings) is the ONE shared dict —
read under its lock here; any mutation elsewhere is in place, never a rebind."""
from flask import Blueprint, jsonify

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


@bp.route('/api/settings/threshold-defaults', methods=['GET'])
def api_settings_threshold_defaults():
    try:
        from db_adapter import load_plugin_threshold_defaults
        return jsonify(load_plugin_threshold_defaults())
    except Exception:
        return jsonify({})
