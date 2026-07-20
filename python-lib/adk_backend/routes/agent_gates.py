"""Agent Settings routes — the per-action enablement gates, plus the
autonomous daily-agent panel (triage sweep permissions, schedule status,
provisioning and the test digest).

One {name: bool} map, stored in the LOCAL plugin config (hidden param
`agent_action_gates`, declared in plugin.json so DSS never prunes it) and
served live to both the Agent Settings page and the agents themselves
(atk_agent_common.action_gates polls GET with a short kernel cache, so an
admin toggle needs no kernel recycle).

Default policy (fail-closed): read-only sensors ON unless unchecked; every
actuator action OFF until an admin enables it. Enforcement lives in
atk_agent_common.actuator (plan + execute) and agent_tools (sensor build) —
this module is only the catalog + storage.
"""
import json
import logging
import threading

from flask import Blueprint, jsonify, request

from adk_backend.clients import _local_thread_client
from adk_backend.utils import advanced, local_only
from atk_agent_common import actuator, tools_impl
from atk_agent_common import actions as actions_registry

bp = Blueprint('agent_gates', __name__)
_LOGGER = logging.getLogger(__name__)

_PLUGIN_ID = 'admin-toolkit'
_PARAM = 'agent_action_gates'

# Serializes the read-merge-write in the update route: it applies a partial
# delta onto the stored map, so two concurrent toggles must not each read the
# same baseline and clobber the other's change.
_write_lock = threading.Lock()


def _read_gates():
    try:
        raw = _local_thread_client().get_plugin(_PLUGIN_ID).get_settings().get_raw()
        config = raw.get('config', {}) if isinstance(raw, dict) else {}
        parsed = json.loads(config.get(_PARAM) or '{}')
        return {str(k): bool(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}
    except Exception as exc:
        _LOGGER.warning('[agent-gates] read failed (%s) — defaults apply', exc)
        return {}


def _write_gates(gates):
    settings = _local_thread_client().get_plugin(_PLUGIN_ID).get_settings()
    settings.get_raw().setdefault('config', {})[_PARAM] = json.dumps(gates, sort_keys=True)
    settings.save()


def _catalog(gates):
    sensors = [{'name': name, 'mode': 'read', 'description': description,
                'enabled': bool(gates.get(name, True))}
               for name, description in tools_impl.SENSOR_DESCRIPTIONS.items()]
    local_only_set = set(actuator._LOCAL_ONLY_ACTIONS)
    actions = [{'action': action,
                'mode': actions_registry.MODES[action],
                'risk': actions_registry.ALL_RISKS[action],
                'shape': actions_registry.SHAPES[action],
                'batchable': action in actions_registry.BATCHABLE,
                'localOnly': action in local_only_set,
                'enabled': bool(gates.get(action, False))}
               for action in actuator.ACTIONS]
    actions.sort(key=lambda row: (row['mode'], row['action']))
    return sensors, actions


@bp.route('/api/agents/action-settings')
@local_only
def api_action_settings():
    gates = _read_gates()
    sensors, actions = _catalog(gates)
    return jsonify({'ok': True, 'sensors': sensors, 'actions': actions, 'gates': gates})


@bp.route('/api/agents/action-settings/update', methods=['POST'])
@advanced
@local_only
def api_action_settings_update():
    body = request.get_json(force=True, silent=True) or {}
    updates = body.get('gates')
    if not isinstance(updates, dict) or not updates:
        return jsonify({'ok': False, 'error': 'gates must be a non-empty {name: bool} map'}), 400
    known = set(actuator.ACTIONS) | set(tools_impl.SENSOR_DESCRIPTIONS)
    rejected = sorted(str(k) for k in updates if str(k) not in known)
    if rejected:
        return jsonify({'ok': False, 'error': 'unknown action(s): %s' % ', '.join(rejected)}), 400
    try:
        with _write_lock:
            gates = _read_gates()
            for key, value in updates.items():
                gates[str(key)] = bool(value)
            _write_gates(gates)
    except Exception as exc:
        _LOGGER.error('[agent-gates] write failed: %s', exc)
        return jsonify({'ok': False, 'error': '%s: %s'
                        % (type(exc).__name__, str(exc)[:200])}), 502
    _LOGGER.info('[agent-gates] updated: %s', json.dumps(updates, sort_keys=True)[:300])
    sensors, actions = _catalog(gates)
    return jsonify({'ok': True, 'sensors': sensors, 'actions': actions, 'gates': gates})


# ── Autonomous daily agent (triage sweep) panel ──────────────────────────────
# One GET powers the whole Permissions-page panel; one POST writes its knobs
# (and couples the main action gate when an action is opted in — autonomous
# execution without the gate would be refused at plan time anyway).

_TRIAGE_KNOBS = ('auto_remediate_enabled', 'auto_remediate_remote_hosts',
                 'auto_remediate_actions', 'auto_remediate_max_gb',
                 'auto_remediate_max_objects')


def _plugin_config():
    raw = _local_thread_client().get_plugin(_PLUGIN_ID).get_settings().get_raw()
    return raw.get('config', {}) if isinstance(raw, dict) else {}


def _truthy(value, default=False):
    if value is None or value == '':
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('true', '1', 'yes')


def _scenario_status(client):
    """Best-effort schedule readout: provisioned? active? hour? last run?"""
    from atk_agent_common.triage import provision
    out = {'provisioned': False, 'active': False, 'hour': None, 'lastRun': None}
    try:
        project = client.get_project(provision.MACRO_PROJECT_KEY)
        info = next((s for s in project.list_scenarios() or []
                     if s.get('name') == provision.SCENARIO_NAME), None)
        if not info:
            return out
        scenario = project.get_scenario(info.get('id'))
        raw = scenario.get_settings().get_raw()
        out['provisioned'] = True
        out['active'] = bool(raw.get('active'))
        for trigger in raw.get('triggers') or []:
            params = trigger.get('params') or {}
            if 'hour' in params:
                out['hour'] = params.get('hour')
                break
        runs = scenario.get_last_runs(limit=1, only_finished_runs=True) or []
        if runs:
            run = getattr(runs[0], 'run', None) or {}
            result = run.get('result') or {}
            out['lastRun'] = {
                'outcome': result.get('outcome'),
                'start': run.get('start'),
                'end': run.get('end'),
            }
    except Exception as exc:
        _LOGGER.info('[triage-panel] scenario status unavailable: %s', exc)
    return out


def _triage_payload(client, config):
    from atk_agent_common.remediation_map import auto_catalog
    gates = _read_gates()
    opted = {a.strip() for a in str(config.get('auto_remediate_actions') or '').split(',')
             if a.strip()}
    local_only_set = set(actuator._LOCAL_ONLY_ACTIONS)
    actions = [dict(row,
                    optedIn=row['action'] in opted,
                    gateEnabled=bool(gates.get(row['action'], False)),
                    localOnly=row['action'] in local_only_set,
                    batchable=row['action'] in actions_registry.BATCHABLE)
               for row in auto_catalog()]
    return {
        'ok': True,
        'enabled': _truthy(config.get('auto_remediate_enabled'), default=True),
        'remoteHosts': _truthy(config.get('auto_remediate_remote_hosts'), default=False),
        'actions': actions,
        'caps': {
            'maxGb': int(config.get('auto_remediate_max_gb') or 20),
            'maxObjects': int(config.get('auto_remediate_max_objects') or 25),
            'logMinAgeDays': int(config.get('log_cleanup_min_age_days') or 3),
        },
        'delivery': {
            'recipient': str(config.get('triage_recipient') or ''),
            'mailChannel': str(config.get('triage_mail_channel') or ''),
            'threshold': int(config.get('triage_score_threshold') or 75),
            'llmConfigured': bool(str(config.get('default_llm_id') or '').strip()),
        },
        'killSwitch': _truthy(config.get('enable_red_actions')),
        'masterPassword': bool(str(config.get('master_password') or '').strip()),
        'scenario': _scenario_status(client),
    }


@bp.route('/api/agents/triage-settings')
@local_only
def api_triage_settings():
    client = _local_thread_client()
    return jsonify(_triage_payload(client, _plugin_config()))


@bp.route('/api/agents/triage-settings/update', methods=['POST'])
@advanced
@local_only
def api_triage_settings_update():
    from atk_agent_common.remediation_map import auto_catalog
    body = request.get_json(force=True, silent=True) or {}
    eligible = [row['action'] for row in auto_catalog()]
    updates = {}
    if 'enabled' in body:
        updates['auto_remediate_enabled'] = bool(body['enabled'])
    if 'remoteHosts' in body:
        updates['auto_remediate_remote_hosts'] = bool(body['remoteHosts'])
    if 'maxGb' in body:
        try:
            updates['auto_remediate_max_gb'] = max(1, int(body['maxGb']))
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'maxGb must be a number'}), 400
    if 'maxObjects' in body:
        try:
            updates['auto_remediate_max_objects'] = max(1, int(body['maxObjects']))
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'maxObjects must be a number'}), 400
    opt_in = body.get('optIn')
    gates_to_enable = []
    if opt_in is not None:
        if not isinstance(opt_in, dict):
            return jsonify({'ok': False, 'error': 'optIn must be a {action: bool} map'}), 400
        bad = sorted(str(a) for a in opt_in if str(a) not in eligible)
        if bad:
            return jsonify({'ok': False,
                            'error': 'not auto-eligible: %s' % ', '.join(bad)}), 400
    if not updates and opt_in is None:
        return jsonify({'ok': False, 'error': 'nothing to update'}), 400

    try:
        with _write_lock:
            settings = _local_thread_client().get_plugin(_PLUGIN_ID).get_settings()
            config = settings.get_raw().setdefault('config', {})
            if opt_in is not None:
                current = {a.strip() for a in str(config.get('auto_remediate_actions') or '')
                           .split(',') if a.strip()}
                for action, value in opt_in.items():
                    if bool(value):
                        current.add(str(action))
                        gates_to_enable.append(str(action))
                    else:
                        current.discard(str(action))
                updates['auto_remediate_actions'] = ','.join(
                    a for a in eligible if a in current)
            config.update(updates)
            if gates_to_enable:
                # Opting into autonomous execution is consent for the action to
                # exist at all — flip the main gate on too (never off: the chat
                # agent may still use an action the sweep no longer runs).
                gates = _read_gates()
                missing = [a for a in gates_to_enable if not gates.get(a, False)]
                if missing:
                    for action in missing:
                        gates[action] = True
                    config[_PARAM] = json.dumps(gates, sort_keys=True)
            settings.save()
            config = settings.get_raw().get('config', {})
    except Exception as exc:
        _LOGGER.error('[triage-panel] write failed: %s', exc)
        return jsonify({'ok': False, 'error': '%s: %s'
                        % (type(exc).__name__, str(exc)[:200])}), 502
    _LOGGER.info('[triage-panel] updated: %s (gates enabled: %s)',
                 json.dumps({k: v for k, v in updates.items()}, sort_keys=True)[:300],
                 gates_to_enable or 'none')
    return jsonify(_triage_payload(_local_thread_client(), config))


@bp.route('/api/agents/triage-provision', methods=['POST'])
@advanced
@local_only
def api_triage_provision():
    """Ensure-or-repair the daily scenario (project, trigger, failure reporter)
    from the UI — the CLI provisioner's exact flow, no shell needed."""
    from atk_agent_common import config as config_mod
    from atk_agent_common.triage import provision
    body = request.get_json(force=True, silent=True) or {}
    settings = config_mod.resolve(_plugin_config())
    hour = body.get('hour')
    try:
        hour = int(hour) if hour is not None else 7
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'hour must be 0-23'}), 400
    if not 0 <= hour <= 23:
        return jsonify({'ok': False, 'error': 'hour must be 0-23'}), 400
    try:
        result = provision.provision_all(_local_thread_client(), settings, hour=hour)
    except Exception as exc:
        _LOGGER.error('[triage-panel] provision failed: %s', exc)
        return jsonify({'ok': False, 'error': '%s: %s'
                        % (type(exc).__name__, str(exc)[:300])}), 502
    result['scenario'] = _scenario_status(_local_thread_client())
    return jsonify(result)


@bp.route('/api/agents/triage-digest-test', methods=['POST'])
@advanced
@local_only
def api_triage_digest_test():
    """Send the branded digest email with representative sample data — lets an
    admin see exactly what tomorrow's report will look like (and demo it)
    without waiting for the 7am sweep."""
    from atk_agent_common.triage import digest, provision
    body = request.get_json(force=True, silent=True) or {}
    config = _plugin_config()
    recipient = str(body.get('recipient') or config.get('triage_recipient') or '').strip()
    if not recipient:
        return jsonify({'ok': False, 'error': 'No recipient: set the triage digest '
                        'recipient in Settings → Agents & Outreach first.'}), 400
    client = _local_thread_client()
    try:
        channel_id = provision.resolve_mail_channel(
            client, str(config.get('triage_mail_channel') or ''))
        channel = client.get_messaging_channel(channel_id)
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)[:300]}), 502
    ctx = digest.sample_context()
    ctx['toolkitUrl'] = str(body.get('toolkitUrl') or '').strip() or None
    html = digest.render_digest_html(ctx)
    subject = digest.build_subject(ctx) + ' — test preview'
    try:
        channel.send(provision.MACRO_PROJECT_KEY, [recipient], subject, html,
                     plain_text=False)
    except TypeError:
        # Older dataikuapi without the plain_text kwarg — body is HTML either way.
        channel.send(provision.MACRO_PROJECT_KEY, [recipient], subject, html)
    except Exception as exc:
        return jsonify({'ok': False, 'error': '%s: %s'
                        % (type(exc).__name__, str(exc)[:300])}), 502
    _LOGGER.info('[triage-panel] test digest sent to %s via %s', recipient, channel_id)
    return jsonify({'ok': True, 'recipient': recipient, 'channel': channel_id})
