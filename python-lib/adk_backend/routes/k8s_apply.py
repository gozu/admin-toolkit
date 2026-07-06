"""K8S Apply routes — policy-gated kubectl mutations via the k8s-apply macro.

The verb/kind/namespace/token whitelist is enforced INSIDE the macro; these
routes are transport. preview is read-only (get + server dry-run twins), so
it is not red-gated; execute is @advanced.
"""
import json
import logging

from flask import Blueprint, g, jsonify, request

from adk_backend.macros import _k8s_apply_macro
from adk_backend.utils import advanced

bp = Blueprint('k8s_apply', __name__)
_LOGGER = logging.getLogger(__name__)


def _macro_args(body):
    commands = body.get('commands') or []
    if not isinstance(commands, list):
        raise ValueError('commands must be a JSON array of kubectl arg strings')
    return {
        'cluster_id': str(body.get('clusterId') or '').strip(),
        'commands_json': json.dumps([str(c) for c in commands]),
        'manifest_yaml': body.get('manifestYaml') or '',
    }


@bp.route('/api/tools/k8s-apply/preview', methods=['POST'])
def api_k8s_apply_preview():
    body = request.get_json(force=True, silent=True) or {}
    try:
        args = _macro_args(body)
    except ValueError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    try:
        result = _k8s_apply_macro(g.client, 'preview', dry_run=True, **args)
    except Exception as exc:
        _LOGGER.error('[k8s-apply] preview macro failed: %s', exc)
        return jsonify({'ok': False, 'error': str(exc)}), 502
    return jsonify(result), 200 if result.get('ok') else 400


@bp.route('/api/tools/k8s-apply/execute', methods=['POST'])
@advanced
def api_k8s_apply_execute():
    body = request.get_json(force=True, silent=True) or {}
    try:
        args = _macro_args(body)
    except ValueError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    try:
        result = _k8s_apply_macro(g.client, 'execute', dry_run=False, **args)
    except Exception as exc:
        _LOGGER.error('[k8s-apply] execute macro failed: %s', exc)
        return jsonify({'ok': False, 'error': str(exc)}), 502
    _LOGGER.info('[k8s-apply] execute cluster=%s ok=%s commands=%d',
                 args['cluster_id'], result.get('ok'),
                 len(json.loads(args['commands_json'])))
    return jsonify(result), 200 if result.get('ok') else 400
