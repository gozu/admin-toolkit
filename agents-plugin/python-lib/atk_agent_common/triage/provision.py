"""Daily triage scenario provisioning — 'Agents — Daily health triage'.

Idempotent ensure-or-repair (the pattern proven by the toolkit's removed
Story feature): one runnable step with proceedOnFailure=False, one daily
trigger, one END_OF_RUN failure reporter, save → re-fetch → verify with a
fallback reporter shape.
"""

import logging

logger = logging.getLogger('atk-agents')

MACRO_PROJECT_KEY = 'ADMINTOOLKIT'
MACRO_PROJECT_NAME = 'Admin Toolkit'
SCENARIO_NAME = 'Agents — Daily health triage'
MACRO_TYPE = 'pyrunnable_admin-toolkit-agents_agent-triage-sweep'

REPORTER_SUBJECT = '[Admin Toolkit / Agents] Daily triage FAILED on ${scenarioProjectKey}'
REPORTER_MESSAGE = (
    'The daily agent health triage failed.\n\n'
    'Scenario: ${scenarioName}\nOutcome: ${outcome}\n\n'
    'Check the scenario run log; per-host rows (when written) are in '
    'agents.agent_triage_daily.'
)


class MailChannelMissing(Exception):
    pass


def build_step():
    return {
        'type': 'runnable',
        'name': 'agent_triage_sweep',
        'enabled': True,
        'alwaysShowComment': False,
        'runConditionType': 'RUN_IF_STATUS_MATCH',
        'runConditionStatuses': ['SUCCESS', 'WARNING'],
        'runConditionExpression': '',
        'resetScenarioStatus': False,
        'delayBetweenRetries': 10,
        'maxRetriesOnFail': 0,
        'params': {
            'runnableType': MACRO_TYPE,
            'config': {},
            'adminConfig': {},
            'proceedOnFailure': False,
        },
    }


def _reporter(channel_id, recipient, fallback=False):
    base = {
        'active': True,
        'phase': 'END_OF_RUN',
        'messaging': {
            'type': 'mail-scenario',
            'configuration': {
                'channelId': channel_id,
                'recipient': recipient,
                'subject': REPORTER_SUBJECT,
                'message': REPORTER_MESSAGE,
            },
        },
    }
    # DSS 14.7 persists exactly these two fields (probed live on akaos); it
    # silently drops runConditionType/runConditionStatuses/runConditionExpression
    # and the phase key. Both shapes are identical now; the fallback branch in
    # provision_all is kept as a retry for other DSS versions.
    base.update({'runConditionEnabled': True,
                 'runCondition': "outcome != 'SUCCESS'"})
    return base


def resolve_mail_channel(client, preferred_id=''):
    channels = [c for c in (client.list_messaging_channels() or [])
                if 'mail' in (str(getattr(c, 'family', '') or '') +
                              str(getattr(c, 'type', '') or '')).lower()]
    ids = [getattr(c, 'id', None) for c in channels if getattr(c, 'id', None)]
    if not ids:
        raise MailChannelMissing(
            'No mail channel configured on this DSS — the triage digest/failure email cannot '
            'be delivered. Configure one under Administration → Settings → Notifications.')
    if preferred_id and preferred_id in ids:
        return preferred_id
    return ids[0]


def _reporter_matches(entry, recipient):
    # DSS strips the phase key on save; missing means END_OF_RUN.
    if not isinstance(entry, dict) or str(entry.get('phase') or 'END_OF_RUN') != 'END_OF_RUN':
        return False
    configuration = (entry.get('messaging') or {}).get('configuration') or {}
    if recipient not in str(configuration.get('recipient') or ''):
        return False
    if entry.get('runConditionEnabled') and 'outcome' in str(entry.get('runCondition') or ''):
        return True
    if 'outcome' in str(entry.get('runConditionExpression') or ''):
        return True
    statuses = [str(s).upper() for s in (entry.get('runConditionStatuses') or [])]
    return 'FAILED' in statuses


def verify_reporter(scenario, recipient):
    settings = scenario.get_settings()
    return any(_reporter_matches(e, recipient)
               for e in (getattr(settings, 'raw_reporters', None) or []))


def ensure_macro_project(client):
    try:
        project = client.get_project(MACRO_PROJECT_KEY)
        project.get_summary()
        return {'project': project, 'status': 'already_exists'}
    except Exception:
        project = client.create_project(MACRO_PROJECT_KEY, MACRO_PROJECT_NAME, 'admin')
        return {'project': project, 'status': 'created'}


def ensure_triage_scenario(project, channel_id, recipient, hour=7, reporter=None):
    existing_id = None
    for info in project.list_scenarios() or []:
        if info.get('name') == SCENARIO_NAME:
            existing_id = info.get('id')
            break
    if existing_id:
        scenario = project.get_scenario(existing_id)
        status = 'repaired'
    else:
        scenario = project.create_scenario(scenario_name=SCENARIO_NAME, type='step_based')
        status = 'created'

    settings = scenario.get_settings()
    settings.get_raw()['active'] = True
    del settings.raw_triggers[:]
    settings.add_daily_trigger(hour=int(hour), minute=0, timezone='SERVER')
    del settings.raw_steps[:]
    settings.raw_steps.append(build_step())
    del settings.raw_reporters[:]
    settings.raw_reporters.append(reporter or _reporter(channel_id, recipient))
    settings.save()
    return {'scenario': scenario, 'status': status}


def provision_all(client, settings, hour=7):
    """settings = resolved plugin settings dict (config.resolve). Returns the
    same {'ok', 'steps', 'reporterVerified', 'reporterShape'} contract as before."""
    steps = []
    if not settings.get('triage_connection'):
        steps.append({'step': 'config', 'status': 'error',
                      'message': 'No triage PostgreSQL connection in plugin settings.'})
        return {'ok': False, 'steps': steps, 'reporterVerified': False, 'reporterShape': None}
    recipient = settings.get('triage_recipient') or ''
    if not recipient:
        steps.append({'step': 'config', 'status': 'error',
                      'message': 'No triage digest recipient in plugin settings.'})
        return {'ok': False, 'steps': steps, 'reporterVerified': False, 'reporterShape': None}
    steps.append({'step': 'config', 'status': 'ok'})

    try:
        project_result = ensure_macro_project(client)
        steps.append({'step': 'project:%s' % MACRO_PROJECT_KEY, 'status': project_result['status']})
    except Exception as exc:
        steps.append({'step': 'project:%s' % MACRO_PROJECT_KEY, 'status': 'error',
                      'message': '%s: %s' % (type(exc).__name__, str(exc)[:300])})
        return {'ok': False, 'steps': steps, 'reporterVerified': False, 'reporterShape': None}

    try:
        channel_id = resolve_mail_channel(client, settings.get('triage_mail_channel') or '')
        steps.append({'step': 'mail-channel', 'status': 'ok', 'message': channel_id})
    except Exception as exc:
        steps.append({'step': 'mail-channel', 'status': 'error',
                      'message': '%s: %s' % (type(exc).__name__, str(exc)[:300])})
        return {'ok': False, 'steps': steps, 'reporterVerified': False, 'reporterShape': None}

    try:
        result = ensure_triage_scenario(project_result['project'], channel_id, recipient, hour=hour)
        steps.append({'step': 'scenario:%s' % SCENARIO_NAME, 'status': result['status']})
    except Exception as exc:
        steps.append({'step': 'scenario:%s' % SCENARIO_NAME, 'status': 'error',
                      'message': '%s: %s' % (type(exc).__name__, str(exc)[:300])})
        return {'ok': False, 'steps': steps, 'reporterVerified': False, 'reporterShape': None}

    scenario = result['scenario']
    verified = verify_reporter(scenario, recipient)
    shape = 'primary'
    if not verified:
        try:
            settings_obj = scenario.get_settings()
            del settings_obj.raw_reporters[:]
            settings_obj.raw_reporters.append(_reporter(channel_id, recipient, fallback=True))
            settings_obj.save()
            verified = verify_reporter(scenario, recipient)
            shape = 'fallback'
        except Exception as exc:
            steps.append({'step': 'reporter', 'status': 'error',
                          'message': '%s: %s' % (type(exc).__name__, str(exc)[:300])})
            return {'ok': False, 'steps': steps, 'reporterVerified': False, 'reporterShape': None}
    steps.append({'step': 'reporter', 'status': 'verified' if verified else 'unverified',
                  'message': 'shape=%s recipient=%s' % (shape, recipient)})
    return {'ok': verified, 'steps': steps, 'reporterVerified': verified, 'reporterShape': shape}
