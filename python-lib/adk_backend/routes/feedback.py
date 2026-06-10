"""In-app feedback route (EAP) — emails feedback + attachments via the same
DSS mail channel the outreach campaigns use (adk_backend.mail)."""
import logging
import os
import re
import tempfile
import time
from typing import Any, Dict, List

from flask import Blueprint, g, jsonify, request

from adk_backend.clients import MACRO_PROJECT_KEY
from adk_backend.mail import (
    _get_configured_mail_channel, _get_mail_channel, _list_mail_channels,
)
from adk_backend.utils import local_only

bp = Blueprint('feedback', __name__)
_LOGGER = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# In-app feedback (EAP)
#
# Admins have no other channel to report bugs / ideas while the toolkit is in
# Early Access Preview, and the repo is private (a client-only GitHub-issue
# link 404s for non-collaborators). So the backend emails feedback — with
# optional file/image attachments — to a fixed recipient via the same DSS mail
# channel the outreach campaigns already use. The endpoint is public (no auth),
# so honeypot + per-worker rate limit + strict caps are mandatory.
# ─────────────────────────────────────────────────────────────────────────
FEEDBACK_RECIPIENT = 'alex.kaos@dataiku.com'
_FEEDBACK_MAX_MSG_LEN = 5000
_FEEDBACK_MAX_FILES = 5
_FEEDBACK_MAX_FILE_BYTES = 8 * 1024 * 1024  # 8 MB per file
_FEEDBACK_ALLOWED_EXT = {
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg',
    '.pdf', '.txt', '.log',
}
_FEEDBACK_RATE_MAX = 5            # max submissions …
_FEEDBACK_RATE_WINDOW_S = 600     # … per 10 minutes, per (host, client IP)
# Per-gunicorn-worker (not global) — acceptable for EAP volume.
_FEEDBACK_RATE: Dict[str, List[float]] = {}


def _feedback_safe_name(name: str) -> str:
    """Sanitize an uploaded filename to a safe basename so the emailed
    attachment keeps a meaningful name instead of a random temp name."""
    base = os.path.basename((name or '').replace('\\', '/')).strip()
    base = re.sub(r'[^A-Za-z0-9._-]', '_', base).lstrip('.')
    return (base or 'attachment')[:120]


@bp.route('/api/feedback', methods=['POST'])
@local_only
def api_feedback():
    """Email in-app feedback (+ optional attachments) to a fixed recipient.

    @local_only: the mail channel and plugin config live on the LOCAL DSS, so a
    remote-host view must not break feedback — g.client is the local client."""
    # Honeypot: a real user never sees the `website` field; bots fill it.
    # Silently accept + drop so the bot can't tell it was rejected.
    if (request.form.get('website') or '').strip():
        return jsonify({'ok': True})

    # Rate limit on host + client IP (per worker — fine for EAP volume).
    rate_key = f"{getattr(g, 'host_id', 'local')}|{request.remote_addr or '?'}"
    now = time.time()
    recent = [t for t in _FEEDBACK_RATE.get(rate_key, []) if now - t < _FEEDBACK_RATE_WINDOW_S]
    if len(recent) >= _FEEDBACK_RATE_MAX:
        _FEEDBACK_RATE[rate_key] = recent
        return jsonify({
            'error': 'rate-limited',
            'message': 'Please wait a moment before sending more feedback.',
        }), 429

    fb_type = (request.form.get('type') or '').strip().lower()
    if fb_type not in ('bug', 'idea', 'other'):
        return jsonify({'error': 'Invalid feedback type'}), 400

    message = (request.form.get('message') or '').strip()
    if not message:
        return jsonify({'error': 'Message is required'}), 400
    if len(message) > _FEEDBACK_MAX_MSG_LEN:
        return jsonify({'error': f'Message exceeds {_FEEDBACK_MAX_MSG_LEN} characters'}), 400

    reply_email = re.sub(r'[\r\n]', '', (request.form.get('email') or '').strip())
    if reply_email and not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', reply_email):
        return jsonify({'error': 'Invalid reply email address'}), 400

    diagnostics = (request.form.get('diagnostics') or '').strip()

    # Validate attachments before touching the filesystem or the mail channel.
    uploads = [u for u in request.files.getlist('attachments') if u and u.filename]
    if len(uploads) > _FEEDBACK_MAX_FILES:
        return jsonify({'error': f'Too many files (max {_FEEDBACK_MAX_FILES})'}), 400
    for up in uploads:
        ext = os.path.splitext(up.filename)[1].lower()
        if ext not in _FEEDBACK_ALLOWED_EXT:
            return jsonify({'error': f'File type not allowed: {up.filename}'}), 400
        try:
            up.stream.seek(0, os.SEEK_END)
            size = up.stream.tell()
            up.stream.seek(0)
        except (OSError, ValueError):
            size = 0
        if size > _FEEDBACK_MAX_FILE_BYTES:
            return jsonify({
                'error': f'File too large (max {_FEEDBACK_MAX_FILE_BYTES // (1024 * 1024)} MB): {up.filename}',
            }), 400

    client = g.client
    if client is None:
        return jsonify({'error': 'No DSS mail channel configured'}), 400
    channels = _list_mail_channels(client)
    if not channels:
        _LOGGER.warning("[feedback] send failed: no DSS mail channel configured")
        return jsonify({'error': 'No DSS mail channel configured'}), 400
    selected = _get_configured_mail_channel() or channels[0]['id']
    channel_obj = _get_mail_channel(client, selected)
    if channel_obj is None:
        _LOGGER.warning("[feedback] send failed: cannot resolve mail channel %s", selected)
        return jsonify({'error': 'No DSS mail channel configured'}), 400

    # Must use the macro-project fallback, NOT the empty-string self-reject path.
    project_key = os.environ.get('DKU_CURRENT_PROJECT_KEY') or MACRO_PROJECT_KEY

    subject = re.sub(r'[\r\n]', '', f'[admin-toolkit feedback] {fb_type}')
    body_lines = [message, '']
    if reply_email:
        body_lines.append(f'Reply-to: {reply_email}')
        body_lines.append('')
    if diagnostics:
        body_lines.append('Diagnostics:')
        body_lines.append(diagnostics)
    body = '\n'.join(body_lines)

    # send() wants list[BufferedReader] (real open file objects). werkzeug's
    # FileStorage.stream (a SpooledTemporaryFile) isn't guaranteed compatible,
    # so stage each upload to its own temp dir under a sanitized original name
    # and hand over open 'rb' handles; close + delete them in finally.
    handles: List[Any] = []
    temp_paths: List[str] = []
    temp_dirs: List[str] = []
    try:
        for up in uploads:
            tmpdir = tempfile.mkdtemp(prefix='admin-toolkit-feedback-')
            temp_dirs.append(tmpdir)
            dest = os.path.join(tmpdir, _feedback_safe_name(up.filename))
            up.save(dest)
            temp_paths.append(dest)
            handles.append(open(dest, 'rb'))
        channel_obj.send(
            project_key, [FEEDBACK_RECIPIENT], subject, body,
            attachments=handles or None, plain_text=True,
        )
    except Exception as exc:
        _LOGGER.warning("[feedback] send failed: %s", exc)
        return jsonify({'error': f'Failed to send feedback: {exc}'}), 502
    finally:
        for h in handles:
            try:
                h.close()
            except Exception:
                pass
        for p in temp_paths:
            try:
                os.unlink(p)
            except Exception:
                pass
        for d in temp_dirs:
            try:
                os.rmdir(d)
            except Exception:
                pass

    _FEEDBACK_RATE[rate_key] = recent + [now]
    _LOGGER.info(
        "[feedback] sent type=%s files=%d host=%s",
        fb_type, len(uploads), getattr(g, 'host_id', 'local'),
    )
    return jsonify({'ok': True})
