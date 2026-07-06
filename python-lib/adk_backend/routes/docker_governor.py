"""Docker Cache Governor routes — usage inspection + gated prunes via the
docker-governor macro (fixed argv, docker-group only, no sudo). daemon.json
limits are only ever emitted as a manual sudo script, never executed.
"""
import logging

from flask import Blueprint, g, jsonify, request

from adk_backend.macros import _docker_governor_macro
from adk_backend.utils import advanced

bp = Blueprint('docker_governor', __name__)
_LOGGER = logging.getLogger(__name__)

_PRUNE_MODES = {'builder': 'builder-prune', 'image': 'image-prune'}


@bp.route('/api/tools/docker/usage')
def api_docker_usage():
    try:
        result = _docker_governor_macro(g.client, 'usage-scan')
    except Exception as exc:
        _LOGGER.error('[docker-governor] usage macro failed: %s', exc)
        return jsonify({'ok': False, 'error': str(exc)}), 502
    return jsonify(result), 200 if result.get('ok') else 400


@bp.route('/api/tools/docker/prune', methods=['POST'])
@advanced
def api_docker_prune():
    body = request.get_json(force=True, silent=True) or {}
    operation = _PRUNE_MODES.get(str(body.get('mode') or '').strip().lower())
    if not operation:
        return jsonify({'ok': False,
                        'error': "mode must be 'builder' or 'image'"}), 400
    dry_run = bool(body.get('dryRun', True))
    try:
        result = _docker_governor_macro(
            g.client, operation,
            keep_storage_gb=body.get('keepStorageGB'),
            filter_until_hours=body.get('filterUntilHours'),
            dry_run=dry_run)
    except Exception as exc:
        _LOGGER.error('[docker-governor] prune macro failed: %s', exc)
        return jsonify({'ok': False, 'error': str(exc)}), 502
    _LOGGER.info('[docker-governor] %s dryRun=%s reclaimed=%s',
                 operation, dry_run, result.get('totalReclaimed'))
    return jsonify(result), 200 if result.get('ok') else 400


@bp.route('/api/tools/docker/daemon-script')
def api_docker_daemon_script():
    keep = request.args.get('keepStorageGB', type=int)
    try:
        result = _docker_governor_macro(g.client, 'daemon-config-script',
                                        keep_storage_gb=keep)
    except Exception as exc:
        _LOGGER.error('[docker-governor] daemon-script macro failed: %s', exc)
        return jsonify({'ok': False, 'error': str(exc)}), 502
    return jsonify(result), 200 if result.get('ok') else 400
