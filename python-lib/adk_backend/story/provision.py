"""Story hub provisioning — the 'Story: collect analytics' scenario.

Idempotent ensure-or-repair (Pulse's proven pattern): find the scenario by
name, create it when missing, then unconditionally clear and rebuild triggers,
steps and reporters, and save. Re-provisioning always converges to exactly one
scenario with one step, one daily trigger and one failure reporter.

The failure email is a hard requirement, and reporter JSON varies across DSS
versions — so every provision run does a save → re-fetch → verify pass. If
the primary shape (CUSTOM outcome expression) comes back mangled, we retry
once with the fallback shape (status-list on FAILED/ABORTED) and surface
{reporterVerified, reporterShape} to the Setup page. Because the single step
uses proceedOnFailure=False and the macro raises on any host/source failure,
one END_OF_RUN reporter covers "failure at any level".

Flask-free: callable from routes today, from a bootstrap macro tomorrow.
"""
import logging
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)

MACRO_PROJECT_KEY = 'ADMINTOOLKIT'
MACRO_PROJECT_NAME = 'Admin Toolkit'
SCENARIO_NAME = 'Story — Collect analytics'
MACRO_TYPE = 'pyrunnable_admin-toolkit_story-collect'

REPORTER_SUBJECT = '[Admin Toolkit / Story] Collection FAILED on ${scenarioProjectKey}'
REPORTER_MESSAGE = (
    'Story analytics collection failed.\n\n'
    'Scenario: ${scenarioName}\n'
    'Outcome: ${outcome}\n\n'
    'Per-host / per-source detail is durable in story.ingest_runs — open the '
    "toolkit's Story Setup page (or query the table) to see which host and "
    'source failed and why.'
)


class MailChannelMissing(Exception):
    """No mail channel available on the hub — the failure email cannot be wired."""


def build_step() -> Dict[str, Any]:
    return {
        'type': 'runnable',
        'name': 'story_collect',
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


def build_reporter(channel_id: str, recipient: str) -> Dict[str, Any]:
    """Primary shape: CUSTOM run condition on the scenario outcome."""
    return {
        'active': True,
        'phase': 'END_OF_RUN',
        'runConditionType': 'CUSTOM',
        'runConditionExpression': "outcome != 'SUCCESS'",
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


def build_reporter_fallback(channel_id: str, recipient: str) -> Dict[str, Any]:
    """Fallback shape for DSS versions that mangle the CUSTOM expression."""
    return {
        'active': True,
        'phase': 'END_OF_RUN',
        'runConditionType': 'RUN_IF_STATUS_MATCH',
        'runConditionStatuses': ['FAILED', 'ABORTED'],
        'runConditionExpression': '',
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


def resolve_mail_channel(client: Any, preferred_id: str = '') -> str:
    """Channel id for the failure reporter: the story_mail_channel setting when
    it matches an existing mail channel, else the first available one."""
    # Lazy import: adk_backend.mail pulls in flask via clients.py; this
    # function only runs from the webapp backend where flask exists.
    from adk_backend.mail import _list_mail_channels
    channels = _list_mail_channels(client)
    if not channels:
        raise MailChannelMissing(
            'No mail channel is configured on this DSS — the Story failure '
            'email cannot be delivered. Configure one under Administration → '
            'Settings → Notifications & Integrations.')
    if preferred_id:
        for channel in channels:
            if channel['id'] == preferred_id:
                return channel['id']
    return channels[0]['id']


def _reporter_matches(entry: Any, recipient: str) -> bool:
    """A saved reporter counts as verified iff it fires at end of run, targets
    our recipient, and has SOME failure condition (either style)."""
    if not isinstance(entry, dict):
        return False
    if str(entry.get('phase') or '') != 'END_OF_RUN':
        return False
    messaging = entry.get('messaging') or {}
    configuration = messaging.get('configuration') or {}
    if recipient not in str(configuration.get('recipient') or ''):
        return False
    if 'outcome' in str(entry.get('runConditionExpression') or ''):
        return True
    statuses = [str(s).upper() for s in (entry.get('runConditionStatuses') or [])]
    return 'FAILED' in statuses


def verify_reporter(scenario: Any, recipient: str) -> bool:
    """Re-fetch the saved settings and check the reporter survived the save."""
    settings = scenario.get_settings()
    reporters = list(getattr(settings, 'raw_reporters', None) or [])
    return any(_reporter_matches(entry, recipient) for entry in reporters)


def ensure_story_scenario(project: Any, cfg: Any, channel_id: str,
                          reporter: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Create-or-repair the scenario; returns {'scenario', 'status'}."""
    existing_id = None
    for scenario_info in project.list_scenarios() or []:
        if scenario_info.get('name') == SCENARIO_NAME:
            existing_id = scenario_info.get('id')
            break

    if existing_id:
        scenario = project.get_scenario(existing_id)
        status = 'repaired'
    else:
        scenario = project.create_scenario(scenario_name=SCENARIO_NAME, type='step_based')
        status = 'created'

    settings = scenario.get_settings()
    raw = settings.get_raw()
    raw['active'] = True
    run_as = (getattr(cfg, 'run_as_user', '') or '').strip()
    if run_as:
        raw['runAsUser'] = run_as
    else:
        raw.pop('runAsUser', None)

    del settings.raw_triggers[:]
    settings.add_daily_trigger(hour=int(getattr(cfg, 'collect_hour', 2)), minute=0,
                               timezone='SERVER')

    del settings.raw_steps[:]
    settings.raw_steps.append(build_step())

    del settings.raw_reporters[:]
    settings.raw_reporters.append(
        reporter or build_reporter(channel_id, getattr(cfg, 'alert_email', '')))

    settings.save()
    return {'scenario': scenario, 'status': status}


def _swap_reporter(scenario: Any, reporter: Dict[str, Any]) -> None:
    settings = scenario.get_settings()
    del settings.raw_reporters[:]
    settings.raw_reporters.append(reporter)
    settings.save()


def ensure_macro_project(client: Any) -> Dict[str, Any]:
    """ADMINTOOLKIT on the hub — same key the whole toolkit uses for macros."""
    try:
        project = client.get_project(MACRO_PROJECT_KEY)
        project.get_summary()
        return {'project': project, 'status': 'already_exists'}
    except Exception:
        project = client.create_project(MACRO_PROJECT_KEY, MACRO_PROJECT_NAME, 'admin')
        return {'project': project, 'status': 'created'}


def provision_all(client: Any, cfg: Any) -> Dict[str, Any]:
    """Idempotent full provision: project → mail channel → scenario → verified
    reporter. Returns {'ok', 'steps': [...], 'reporterVerified', 'reporterShape'}."""
    steps: List[Dict[str, Any]] = []

    if not getattr(cfg, 'connection_name', None):
        steps.append({'step': 'config', 'status': 'error',
                      'message': "No Story PostgreSQL connection selected in plugin settings."})
        return {'ok': False, 'steps': steps, 'reporterVerified': False, 'reporterShape': None}
    steps.append({'step': 'config', 'status': 'ok',
                  'message': 'connection=%s' % cfg.connection_name})

    try:
        project_result = ensure_macro_project(client)
        steps.append({'step': 'project:%s' % MACRO_PROJECT_KEY,
                      'status': project_result['status']})
    except Exception as exc:
        steps.append({'step': 'project:%s' % MACRO_PROJECT_KEY, 'status': 'error',
                      'message': '%s: %s' % (type(exc).__name__, str(exc)[:300])})
        return {'ok': False, 'steps': steps, 'reporterVerified': False, 'reporterShape': None}

    try:
        channel_id = resolve_mail_channel(client, getattr(cfg, 'mail_channel', ''))
        steps.append({'step': 'mail-channel', 'status': 'ok', 'message': channel_id})
    except Exception as exc:
        steps.append({'step': 'mail-channel', 'status': 'error',
                      'message': '%s: %s' % (type(exc).__name__, str(exc)[:300])})
        return {'ok': False, 'steps': steps, 'reporterVerified': False, 'reporterShape': None}

    recipient = getattr(cfg, 'alert_email', '')
    try:
        scenario_result = ensure_story_scenario(project_result['project'], cfg, channel_id)
        steps.append({'step': 'scenario:%s' % SCENARIO_NAME,
                      'status': scenario_result['status']})
    except Exception as exc:
        steps.append({'step': 'scenario:%s' % SCENARIO_NAME, 'status': 'error',
                      'message': '%s: %s' % (type(exc).__name__, str(exc)[:300])})
        return {'ok': False, 'steps': steps, 'reporterVerified': False, 'reporterShape': None}

    scenario = scenario_result['scenario']
    verified = verify_reporter(scenario, recipient)
    shape = 'primary'
    if not verified:
        # Primary shape was mangled by this DSS version's save path — retry
        # once with the status-list fallback shape.
        try:
            _swap_reporter(scenario, build_reporter_fallback(channel_id, recipient))
            verified = verify_reporter(scenario, recipient)
            shape = 'fallback'
        except Exception as exc:
            steps.append({'step': 'reporter', 'status': 'error',
                          'message': '%s: %s' % (type(exc).__name__, str(exc)[:300])})
            return {'ok': False, 'steps': steps, 'reporterVerified': False, 'reporterShape': None}

    steps.append({
        'step': 'reporter',
        'status': 'verified' if verified else 'unverified',
        'message': 'shape=%s recipient=%s' % (shape, recipient),
    })
    return {'ok': verified, 'steps': steps,
            'reporterVerified': verified, 'reporterShape': shape}
