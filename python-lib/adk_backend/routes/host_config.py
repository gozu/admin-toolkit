"""Host Config routes — install.ini / systemd drop-in / limits.d via the
host-config macro.

Host-bound filesystem work, so everything goes through the macro (multi-
instance rule); the key/directive whitelists + drift guard live INSIDE the
macro script, not here.
"""
import json
import logging

from flask import Blueprint, g, jsonify, request

from adk_backend.macros import _host_config_macro
from adk_backend.utils import advanced

bp = Blueprint('host_config', __name__)
_LOGGER = logging.getLogger(__name__)


@bp.route('/api/tools/host-config/read')
def api_host_config_read():
    try:
        result = _host_config_macro(g.client, 'read')
    except Exception as exc:
        _LOGGER.error('[host-config] read macro failed: %s', exc)
        return jsonify({'ok': False, 'error': str(exc)}), 502
    return jsonify(result), 200 if result.get('ok') else 400


@bp.route('/api/tools/host-config/apply', methods=['POST'])
@advanced
def api_host_config_apply():
    body = request.get_json(force=True, silent=True) or {}
    try:
        result = _host_config_macro(
            g.client, 'apply',
            file=body.get('file'),
            section=body.get('section'),
            key=body.get('key'),
            value=body.get('value'),
            expected_current=json.dumps(body.get('expectedCurrent'), default=str))
    except Exception as exc:
        _LOGGER.error('[host-config] apply macro failed: %s', exc)
        return jsonify({'ok': False, 'error': str(exc)}), 502
    status = 200 if result.get('ok') else 409
    _LOGGER.info('[host-config] apply %s: %s', 'ok' if result.get('ok') else 'refused',
                 json.dumps(result, default=str)[:300])
    return jsonify(result), status
