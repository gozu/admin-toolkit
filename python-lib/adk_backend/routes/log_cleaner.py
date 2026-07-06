"""Log Cleaner routes — rotated-log scan/delete via the log-cleaner macro.

Host-bound filesystem work, so everything goes through the macro (multi-
instance rule); the rotated-log whitelist + min-age policy live INSIDE the
macro script, not here.
"""
import logging

from flask import Blueprint, g, jsonify, request

from adk_backend.macros import _log_cleaner_macro
from adk_backend.utils import advanced

bp = Blueprint('log_cleaner', __name__)
_LOGGER = logging.getLogger(__name__)


@bp.route('/api/tools/log-cleaner/scan')
def api_log_cleaner_scan():
    roots = (request.args.get('roots') or '').strip()
    min_age_days = request.args.get('minAgeDays', type=int)
    try:
        result = _log_cleaner_macro(g.client, 'scan', roots=roots or None,
                                    min_age_days=min_age_days)
    except Exception as exc:
        _LOGGER.error('[log-cleaner] scan macro failed: %s', exc)
        return jsonify({'ok': False, 'error': str(exc)}), 502
    return jsonify(result), 200 if result.get('ok') else 400


@bp.route('/api/tools/log-cleaner/delete', methods=['POST'])
@advanced
def api_log_cleaner_delete():
    body = request.get_json(force=True, silent=True) or {}
    roots = ','.join(body.get('roots') or []) if isinstance(body.get('roots'), list) \
        else (body.get('roots') or '')
    dry_run = bool(body.get('dryRun', True))
    try:
        result = _log_cleaner_macro(
            g.client, 'delete',
            roots=roots or None,
            min_age_days=body.get('minAgeDays'),
            max_delete_gb=body.get('maxDeleteGB'),
            dry_run=dry_run)
    except Exception as exc:
        _LOGGER.error('[log-cleaner] delete macro failed: %s', exc)
        return jsonify({'ok': False, 'error': str(exc)}), 502
    _LOGGER.info('[log-cleaner] delete dryRun=%s deleted=%s reclaimedGB=%s',
                 dry_run, result.get('totalDeletedFiles'), result.get('totalReclaimedGB'))
    return jsonify(result), 200 if result.get('ok') else 400
