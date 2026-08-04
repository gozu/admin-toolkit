"""In-app feedback route (EAP) — emails feedback + attachments via the same
DSS mail channel the outreach campaigns use (adk_backend.mail)."""
import inspect
import logging
import mimetypes
import os
import re
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, g, jsonify, request

from adk_backend.clients import MACRO_PROJECT_KEY, _local_thread_client
from adk_backend.mail import (
    _get_configured_mail_channel, _get_mail_channel, _list_mail_channels,
)
from adk_backend.utils import advanced, local_only

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
# 5 user attachments + 1 auto-generated diagnostic bundle.
_FEEDBACK_MAX_FILES = 6
_FEEDBACK_MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MB per file (SMTP attachment ceiling)
_FEEDBACK_ALLOWED_EXT = {
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg',
    '.pdf', '.txt', '.log', '.zip',
}
_FEEDBACK_RATE_MAX = 5            # max submissions …
_FEEDBACK_RATE_WINDOW_S = 600     # … per 10 minutes, per (host, client IP)
# Per-gunicorn-worker (not global) — acceptable for EAP volume.
_FEEDBACK_RATE: Dict[str, List[float]] = {}

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

# ─────────────────────────────────────────────────────────────────────────
# Sender address
#
# The recipient above is fixed (feedback reaches the toolkit author); the
# SENDER is the admin who is using the toolkit. DSS resolves the browsing
# user from the browser's own session headers, so the default sender is that
# user's DSS email. Instances whose SMTP relay only accepts one envelope
# sender (a no-reply/service mailbox) override it with this plugin param,
# managed from webapp Settings → Messaging. Declared hidden in plugin.json so
# DSS does not prune the saved value on plugin update.
# Empty sender ⇒ send() keeps the mail channel's own configured sender.
# ─────────────────────────────────────────────────────────────────────────
FEEDBACK_SENDER_PARAM = 'feedback_sender_email'
_PLUGIN_ID = 'admin-toolkit'


def _feedback_sender_override() -> str:
    """The configured sender override ('' when unset). Plugin settings live on
    the LOCAL instance — always the local client, never g.client."""
    try:
        raw = _local_thread_client().get_plugin(_PLUGIN_ID).get_settings().get_raw()
        config = raw.get('config', {}) if isinstance(raw, dict) else {}
        return (config.get(FEEDBACK_SENDER_PARAM) or '').strip()
    except Exception as exc:
        _LOGGER.debug('[feedback] sender override read failed: %s', exc)
        return ''


def _browsing_user() -> Tuple[str, str]:
    """(login, email) of the DSS user whose browser made this request.

    Same mechanism as chat identity: the webapp is served same-origin with
    DSS, so the browser's session headers reach us and the LOCAL client can
    resolve them. Returns ('', '') whenever that fails (no session, API key
    identity with no matching user, older DSS) — the caller degrades to the
    channel's own sender rather than erroring."""
    try:
        client = _local_thread_client()
        auth = client.get_auth_info_from_browser_headers(dict(request.headers))
        login = str((auth or {}).get('authIdentifier') or '').strip()
        if not login:
            return '', ''
        raw = client.get_user(login).get_settings().get_raw()
        email = str((raw or {}).get('email') or '').strip()
        return login, (email if _EMAIL_RE.match(email) else '')
    except Exception as exc:
        _LOGGER.debug('[feedback] browsing-user lookup failed: %s', exc)
        return '', ''


def _resolve_sender() -> Dict[str, str]:
    """Resolve the envelope sender for this request.

    Order: configured override → browsing admin's DSS email → channel default
    (an empty sender, which send() reads as "use the channel's sender")."""
    override = _feedback_sender_override()
    login, email = _browsing_user()
    if override:
        source = 'override'
        sender = override
    elif email:
        source = 'user'
        sender = email
    else:
        source = 'channel'
        sender = ''
    return {'sender': sender, 'source': source, 'override': override,
            'currentUser': login, 'currentUserEmail': email}


def _send_with_sender(channel_obj: Any, sender: str, *args: Any, **kwargs: Any) -> Optional[str]:
    """channel.send(...), passing `sender` only when this DSS build accepts it.

    The sender kwarg is not in every DSS version's messaging-channel client,
    and the toolkit runs against whatever dataikuapi the host DSS ships.
    Returns the sender actually applied ('' = the channel's own)."""
    if sender:
        try:
            supported = 'sender' in inspect.signature(channel_obj.send).parameters
        except (TypeError, ValueError):
            supported = False
        if supported:
            channel_obj.send(*args, sender=sender, **kwargs)
            return sender
        _LOGGER.warning(
            '[feedback] this DSS build ignores a custom sender — sending as the '
            'mail channel default instead of %s', sender,
        )
    channel_obj.send(*args, **kwargs)
    return ''


@bp.route('/api/feedback/sender', methods=['GET'])
@local_only
def api_feedback_sender_get():
    """Who this instance's feedback will be sent as (and the override, if set)."""
    return jsonify({'ok': True, **_resolve_sender()})


@bp.route('/api/feedback/sender', methods=['POST'])
@advanced
@local_only
def api_feedback_sender_set():
    """Set (or clear, with an empty value) the feedback sender override."""
    body = request.get_json(force=True, silent=True) or {}
    email = re.sub(r'[\r\n]', '', str(body.get('email') or '')).strip()
    if email and not _EMAIL_RE.match(email):
        return jsonify({'ok': False, 'error': 'Invalid sender email address'}), 400
    try:
        settings = _local_thread_client().get_plugin(_PLUGIN_ID).get_settings()
        settings.get_raw().setdefault('config', {})[FEEDBACK_SENDER_PARAM] = email
        settings.save()
    except Exception as exc:
        _LOGGER.warning('[feedback] sender override save failed: %s', exc)
        return jsonify({'ok': False, 'error': f'Could not save: {exc}'}), 502
    return jsonify({'ok': True, **_resolve_sender()})


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
    if reply_email and not _EMAIL_RE.match(reply_email):
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
    # Channel resolution: an explicit per-request choice from the UI wins (lets
    # the user pick which mail channel sends the feedback, mirroring the email
    # outreach tools); otherwise fall back to the configured channel, then the
    # first available one. Ignore an explicit id that isn't a real channel.
    requested_channel = (request.form.get('mailChannel') or '').strip()
    valid_ids = {c['id'] for c in channels}
    selected = (
        (requested_channel if requested_channel in valid_ids else '')
        or _get_configured_mail_channel()
        or channels[0]['id']
    )
    channel_obj = _get_mail_channel(client, selected)
    if channel_obj is None:
        _LOGGER.warning("[feedback] send failed: cannot resolve mail channel %s", selected)
        return jsonify({'error': 'No DSS mail channel configured'}), 400

    # Must use the macro-project fallback, NOT the empty-string self-reject path.
    project_key = os.environ.get('DKU_CURRENT_PROJECT_KEY') or MACRO_PROJECT_KEY

    # Sent AS the admin using the toolkit (or the configured override) so the
    # reply lands with them and the relay sees one of its own addresses.
    sender_info = _resolve_sender()
    sender = sender_info['sender']

    subject = re.sub(r'[\r\n]', '', f'[admin-toolkit feedback] {fb_type}')
    body_lines = [message, '']
    if reply_email:
        body_lines.append(f'Reply-to: {reply_email}')
        body_lines.append('')
    elif sender:
        body_lines.append(f'From: {sender}')
        body_lines.append('')
    if diagnostics:
        body_lines.append('Diagnostics:')
        body_lines.append(diagnostics)
    body = '\n'.join(body_lines)

    # send() forwards each attachment into a `requests` multipart POST. A bare
    # file handle can land without a Content-Type, and DSS's Java mail layer then
    # fails with "Content-Type <null>, expected MIME type". So hand over explicit
    # (filename, handle, content_type) 3-tuples with a guaranteed MIME type.
    # werkzeug's FileStorage.stream (a SpooledTemporaryFile) isn't guaranteed
    # compatible, so stage each upload to its own temp dir under a sanitized
    # original name; close + delete the handles in finally.
    handles: List[Any] = []
    attachments: List[Any] = []
    temp_paths: List[str] = []
    temp_dirs: List[str] = []
    try:
        for up in uploads:
            tmpdir = tempfile.mkdtemp(prefix='admin-toolkit-feedback-')
            temp_dirs.append(tmpdir)
            safe_name = _feedback_safe_name(up.filename)
            dest = os.path.join(tmpdir, safe_name)
            up.save(dest)
            temp_paths.append(dest)
            handle = open(dest, 'rb')
            handles.append(handle)
            content_type = mimetypes.guess_type(safe_name)[0] or 'application/octet-stream'
            attachments.append((safe_name, handle, content_type))
        sender = _send_with_sender(
            channel_obj, sender,
            project_key, [FEEDBACK_RECIPIENT], subject, body,
            attachments=attachments or None, plain_text=True,
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
        "[feedback] sent type=%s files=%d host=%s channel=%s sender=%s (%s)",
        fb_type, len(uploads), getattr(g, 'host_id', 'local'), selected,
        sender or '<channel default>', sender_info['source'],
    )
    return jsonify({'ok': True, 'sender': sender})
