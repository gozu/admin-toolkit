"""Log routes: parsed backend.log errors + raw log tail."""
import os

from flask import Blueprint, g, jsonify

from adk_backend.caching import _cache_get
from adk_backend.logparse import _coerce_log_text, _parse_log_errors
from adk_backend.settings import _BACKEND_SETTINGS
from adk_backend.sysinfo import _dip_home, _safe_read_text

bp = Blueprint('logs', __name__)


@bp.route('/api/logs/errors')
def api_logs_errors():
    client = g.client
    dip_home = _dip_home()

    def loader():
        log_content = None
        try:
            log_content = client.get_log('backend.log')
        except Exception:
            log_content = _safe_read_text(os.path.join(dip_home, 'run', 'backend.log'))
        return _parse_log_errors(log_content)

    data = _cache_get('log_errors', _BACKEND_SETTINGS['cache_ttl_log_errors'], loader)
    return jsonify(data)


@bp.route('/api/logs/raw-tail')
def api_logs_raw_tail():
    """Return the last 100K characters of backend.log as plain text."""
    max_chars = 100_000
    try:
        client = g.client
        dip_home = _dip_home()
        log_content = None
        try:
            log_content = client.get_log('backend.log')
        except Exception:
            log_content = _safe_read_text(os.path.join(dip_home, 'run', 'backend.log'))
        text = _coerce_log_text(log_content) or ''
        if len(text) > max_chars:
            text = text[-max_chars:]
        return jsonify({'text': text, 'chars': len(text)})
    except Exception as e:
        return jsonify({'error': str(e), 'text': '', 'chars': 0}), 500
