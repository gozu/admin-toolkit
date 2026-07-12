"""Messaging routes — channel catalog + the notification-send delivery path.

Mail channels deliver directly via the public send API (probed live on DSS
14.7: /messaging-channels/<id>/actions/send is mail-only). Every other family
is delivered through a reporter on the toolkit's notification-relay scenario
in ADMINTOOLKIT — reporters are the only DSS-supported send path for slack /
msteams / google-chat / webhook / twilio / shell channels. The relay scenario
is toolkit-owned (protected from toolkit-scenario-write) and has no steps and
no triggers: it exists solely so its END_OF_RUN reporter can fire.
"""
import logging

from flask import Blueprint, g, jsonify, request

from adk_backend.clients import _resolve_macro_project
from adk_backend.utils import advanced

bp = Blueprint('messaging_tools', __name__)
_LOGGER = logging.getLogger(__name__)

RELAY_SCENARIO_NAME = 'Agents — Notification relay'

# channel type → scenario-reporter messaging type
_REPORTER_TYPES = {
    'slack': 'slack-scenario',
    'msteams': 'msteams-scenario',
    'google-chat': 'google-chat-scenario',
    'twilio': 'twilio-scenario',
    'webhook': 'webhook-scenario',
    'shell': 'shell-scenario',
}


def _channel_rows(client):
    rows = []
    for item in client.list_messaging_channels() or []:
        raw = item.get_raw() if hasattr(item, 'get_raw') else getattr(item, '_data', {}) or {}
        if not raw.get('id'):
            continue
        rows.append({'id': str(raw.get('id')),
                     'type': str(raw.get('type') or ''),
                     'family': str(raw.get('family') or '') or None,
                     'label': str(raw.get('label') or raw.get('id'))})
    return rows


def _is_mail(row):
    return (row.get('family') or '').lower() == 'mail' or \
        (row.get('type') or '').lower() in ('smtp', 'mail')


@bp.route('/api/tools/messaging/channels')
def api_messaging_channels():
    try:
        return jsonify({'ok': True, 'channels': _channel_rows(g.client)})
    except Exception as exc:
        _LOGGER.error('[messaging] channel listing failed: %s', exc)
        return jsonify({'ok': False, 'error': str(exc)}), 502


def _send_mail(client, project_key, channel_id, recipients, subject, message):
    channel = client.get_messaging_channel(channel_id)
    channel.send(project_key, recipients, subject, message, plain_text=True)
    return {'ok': True, 'deliveredVia': 'direct-send', 'channelId': channel_id,
            'recipients': recipients}


def _send_via_relay(client, project, channel_row, message):
    """Configure + fire the relay scenario's reporter for one send."""
    scenario = None
    for info in project.list_scenarios() or []:
        if info.get('name') == RELAY_SCENARIO_NAME:
            scenario = project.get_scenario(info.get('id'))
            break
    if scenario is None:
        scenario = project.create_scenario(scenario_name=RELAY_SCENARIO_NAME,
                                           type='step_based')
    reporter_type = _REPORTER_TYPES.get((channel_row.get('type') or '').lower(),
                                        '%s-scenario' % channel_row.get('type'))
    settings = scenario.get_settings()
    settings.get_raw()['active'] = False  # manual fire only — never scheduled
    del settings.raw_triggers[:]
    del settings.raw_steps[:]
    del settings.raw_reporters[:]
    settings.raw_reporters.append({
        'active': True,
        'phase': 'END_OF_RUN',
        'messaging': {
            'type': reporter_type,
            'configuration': {
                'channelId': channel_row['id'],
                'message': message,
            },
        },
    })
    settings.save()
    run = scenario.run_and_wait()
    outcome = str(getattr(run, 'outcome', '') or '')
    return {'ok': outcome in ('SUCCESS', 'WARNING'),
            'deliveredVia': 'scenario-reporter',
            'channelId': channel_row['id'],
            'reporterType': reporter_type,
            'scenarioRunOutcome': outcome,
            'note': 'Reporter delivery is asynchronous to the run outcome — a failed '
                    'webhook/token surfaces in the relay scenario run log, not here.'}


@bp.route('/api/tools/messaging/send', methods=['POST'])
@advanced
def api_messaging_send():
    body = request.get_json(force=True, silent=True) or {}
    channel_id = str(body.get('channelId') or '').strip()
    message = str(body.get('message') or '').strip()
    subject = str(body.get('subject') or '').strip() or '[Admin Toolkit] Agent notification'
    recipients = [str(r).strip() for r in (body.get('recipients') or []) if str(r).strip()]
    if not channel_id or not message:
        return jsonify({'ok': False, 'error': 'channelId and message are required'}), 400
    try:
        rows = _channel_rows(g.client)
        row = next((c for c in rows if c['id'] == channel_id), None)
        if row is None:
            return jsonify({'ok': False, 'error': 'channel %r not found' % channel_id}), 404
        project = _resolve_macro_project(g.client)
        if _is_mail(row):
            if not recipients:
                return jsonify({'ok': False,
                                'error': 'mail channel %r needs recipients[]' % channel_id}), 400
            result = _send_mail(g.client, project.project_key, channel_id,
                                recipients, subject, message)
        else:
            result = _send_via_relay(g.client, project, row, message)
    except Exception as exc:
        _LOGGER.error('[messaging] send via %s failed: %s', channel_id, exc)
        return jsonify({'ok': False, 'error': '%s: %s'
                        % (type(exc).__name__, str(exc)[:300])}), 502
    _LOGGER.info('[messaging] send via %s: %s', channel_id, result.get('deliveredVia'))
    return jsonify(result), 200 if result.get('ok') else 502
