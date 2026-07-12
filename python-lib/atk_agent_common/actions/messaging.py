"""Messaging actuator action — compose + send a notification through any DSS
messaging channel.

Mail channels deliver via the public send API (DSSMailMessagingChannel.send —
probed live: the REST endpoint is mail-only). Every other family (slack,
msteams, google-chat, webhook, twilio, shell) is delivered by the backend
through a reporter on the toolkit's notification-relay scenario in
ADMINTOOLKIT, because reporters are the only DSS-supported send path for
non-mail channels.
"""

from ..errors import ToolkitError
from . import _base

_PREVIEW_CHARS = 400
_MAX_MESSAGE_CHARS = 4000


def _channel_row(client, host, channel_id):
    data = client.get('/api/tools/messaging/channels', host=host)
    rows = data.get('channels') or []
    row = next((c for c in rows if c.get('id') == channel_id), None)
    if row is None:
        ids = ', '.join(sorted('%s (%s)' % (c.get('id'), c.get('type')) for c in rows))
        raise ToolkitError(
            'Messaging channel %r not found on host %r. Channels: %s'
            % (channel_id, host, ids or '(none configured)'),
            remediation='Channels are configured under Administration → Settings → '
                        'Notifications on that DSS.')
    return row


def _plan_notification_send(client, host, target, params):
    channel_id = _base.require_str(target, 'channelId', 'notification-send')
    message = str((target or {}).get('message') or '').strip()
    if not message:
        raise ToolkitError('notification-send target needs a non-empty "message".')
    if len(message) > _MAX_MESSAGE_CHARS:
        raise ToolkitError('message exceeds %d characters — shorten it.' % _MAX_MESSAGE_CHARS)
    subject = str((target or {}).get('subject') or '').strip() or '[Admin Toolkit] Agent notification'
    recipients = sorted(str(r).strip() for r in ((target or {}).get('recipients') or [])
                        if str(r).strip())
    row = _channel_row(client, host, channel_id)
    family = str(row.get('family') or '').lower()
    is_mail = family == 'mail' or str(row.get('type') or '').lower() in ('smtp', 'mail')
    if is_mail and not recipients:
        raise ToolkitError('Channel %r is a mail channel — recipients[] is required.' % channel_id)
    canonical = {'channelId': channel_id, 'message': message,
                 'subject': subject, 'recipients': recipients}
    return canonical, {
        'summary': 'Send a notification via channel %s (%s)%s.' % (
            channel_id, row.get('type'),
            ' to %s' % ', '.join(recipients) if recipients else
            " to the channel's configured destination"),
        'channel': {'id': channel_id, 'type': row.get('type'), 'family': family or None},
        'subject': subject,
        'messagePreview': message[:_PREVIEW_CHARS] + ('…' if len(message) > _PREVIEW_CHARS else ''),
        'deliveryPath': 'direct mail send' if is_mail else
                        'reporter on the ADMINTOOLKIT notification-relay scenario',
        'note': 'The message leaves the instance on execute — sends cannot be recalled. '
                'The full text above is exactly what will be delivered.',
    }


def _exec_notification_send(client, host, target):
    result = client.post('/api/tools/messaging/send', host=host, red=True,
                         json={'channelId': target['channelId'],
                               'message': target['message'],
                               'subject': target.get('subject'),
                               'recipients': target.get('recipients') or []})
    if not result.get('ok'):
        raise ToolkitError('notification send failed: %s'
                           % (result.get('error') or result))
    return result


SPECS = [
    _base.spec('notification-send',
               'notification-send {channelId, message, subject?, recipients[]?} '
               '(mail channels need recipients[]; slack/teams/webhook/chat channels '
               'deliver to their configured destination)', 'amber',
               _plan_notification_send, _exec_notification_send),
]
