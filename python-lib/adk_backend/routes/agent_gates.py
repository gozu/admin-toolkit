"""Agent Permissions routes — the per-capability Enabled + Autonomous gates,
plus the autonomous daily-agent panel (triage sweep status, schedule,
provisioning and the test digest).

TWO {name: bool} maps, stored in the LOCAL plugin config (hidden params
`agent_action_gates` + `agent_autonomous_gates`, declared in plugin.json so
DSS never prunes them) and served live to both the Agent Permissions page and
the agents themselves (atk_agent_common.action_gates polls GET with a short
kernel cache, so an admin toggle needs no kernel recycle).

Default policy (fail-closed): read-only sensors ON + autonomous unless
unchecked; every actuator action OFF and NON-autonomous until an admin
enables it. Invariants (enforced in apply_capability_updates): autonomous ⇒
enabled, and python-run can never be autonomous. Enforcement lives in
atk_agent_common.actuator (plan + execute), agent_tools (sensor build) and
triage/auto_agent (the nightly planner) — this module is only the catalog +
storage.

Migration: agent_autonomous_gates seeds ONCE from the legacy
auto_remediate_actions CSV — only while its raw value has never been written
(a persisted '{}' means "admin revoked everything", never re-seed). The CSV
is read-only from here on, never written.
"""
import json
import logging
import threading

from flask import Blueprint, jsonify, request

from adk_backend.clients import _local_thread_client
from adk_backend.utils import advanced, local_only
from atk_agent_common import actuator, tools_impl
from atk_agent_common import actions as actions_registry
from atk_agent_common.remediation_map import AUTO_EXCLUDED

bp = Blueprint('agent_gates', __name__)
_LOGGER = logging.getLogger(__name__)

_PLUGIN_ID = 'admin-toolkit'
_PARAM = 'agent_action_gates'
_PARAM_AUTO = 'agent_autonomous_gates'

# Serializes the read-merge-write in the update route: it applies a partial
# delta onto the stored maps, so two concurrent toggles must not each read the
# same baseline and clobber the other's change.
_write_lock = threading.Lock()


def _parse_map(raw):
    parsed = json.loads(raw or '{}')
    return {str(k): bool(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}


def _read_gates(config=None):
    try:
        if config is None:
            config = _plugin_config()
        return _parse_map(config.get(_PARAM))
    except Exception as exc:
        _LOGGER.warning('[agent-gates] read failed (%s) — defaults apply', exc)
        return {}


def _read_autonomous(config=None):
    """The stored autonomy map; while the raw param has never been written,
    the in-memory seed from the legacy CSV (pure read — persistence happens
    on the first successful POST, which always writes both maps)."""
    try:
        if config is None:
            config = _plugin_config()
        raw = config.get(_PARAM_AUTO)
        if raw is None or str(raw).strip() == '':
            legacy = {a.strip() for a in str(config.get('auto_remediate_actions') or '')
                      .split(',') if a.strip()}
            return {a: True for a in sorted(legacy - AUTO_EXCLUDED)}
        return _parse_map(raw)
    except Exception as exc:
        _LOGGER.warning('[agent-gates] autonomous read failed (%s) — defaults apply', exc)
        return {}


def _write_maps(gates, autonomous):
    settings = _local_thread_client().get_plugin(_PLUGIN_ID).get_settings()
    config = settings.get_raw().setdefault('config', {})
    config[_PARAM] = json.dumps(gates, sort_keys=True)
    config[_PARAM_AUTO] = json.dumps(autonomous, sort_keys=True)
    settings.save()


def apply_capability_updates(gates, autonomous, gate_updates=None, auto_updates=None,
                             known=None, sensors=None):
    """Merge partial Enabled/Autonomous deltas onto the stored maps, enforcing
    the invariants. Pure (Flask-free) for tests. Raises ValueError with a
    user-facing message on invalid input; returns (gates, autonomous) as new
    dicts.

      • unknown capability names are rejected (when `known` is given);
      • AUTO_EXCLUDED actions (python-run) can never be set autonomous;
      • autonomous:true forces the gate ON — it wins over a contradictory
        gates delta in the same body;
      • any gate that ends up OFF clears its autonomous flag, so
        autonomous ⇒ enabled always holds (`sensors` names the capabilities
        whose autonomy DEFAULTS to true, so revocation is recorded
        explicitly for them).
    """
    gates = dict(gates or {})
    autonomous = dict(autonomous or {})
    gate_updates = {str(k): bool(v) for k, v in (gate_updates or {}).items()}
    auto_updates = {str(k): bool(v) for k, v in (auto_updates or {}).items()}
    if not gate_updates and not auto_updates:
        raise ValueError('nothing to update: pass gates and/or autonomous '
                         'as a non-empty {name: bool} map')
    if known is not None:
        rejected = sorted({k for k in list(gate_updates) + list(auto_updates)
                           if k not in known})
        if rejected:
            raise ValueError('unknown action(s): %s' % ', '.join(rejected))
    blocked = sorted(a for a, v in auto_updates.items() if v and a in AUTO_EXCLUDED)
    if blocked:
        raise ValueError('%s can never run autonomously — every run requires the '
                         'per-run human code acknowledgment' % ', '.join(blocked))
    gates.update(gate_updates)
    for name, value in auto_updates.items():
        autonomous[name] = value
        if value:
            gates[name] = True
    sensors = set(sensors or ())
    for name, enabled in gates.items():
        if not enabled and autonomous.get(name, name in sensors):
            autonomous[name] = False
    return gates, autonomous


def _catalog(gates, autonomous):
    """Contract rows for the Permissions page: every capability with its
    Enabled + effective Autonomous state (AND-ed with enabled, and with
    autoCapable for actions, so a row never claims an autonomy that could
    not actually run)."""
    sensors = [{'name': name, 'mode': 'read', 'description': description,
                'enabled': bool(gates.get(name, True)),
                'autonomous': bool(gates.get(name, True))
                and bool(autonomous.get(name, True))}
               for name, description in tools_impl.SENSOR_DESCRIPTIONS.items()]
    local_only_set = set(actuator._LOCAL_ONLY_ACTIONS)
    actions = []
    for action in actuator.ACTIONS:
        enabled = bool(gates.get(action, False))
        auto_capable = action not in AUTO_EXCLUDED
        actions.append({'action': action,
                        'mode': actions_registry.MODES[action],
                        'risk': actions_registry.ALL_RISKS[action],
                        'shape': actions_registry.SHAPES[action],
                        'batchable': action in actions_registry.BATCHABLE,
                        'localOnly': action in local_only_set,
                        'enabled': enabled,
                        'autoCapable': auto_capable,
                        'autonomous': enabled and auto_capable
                        and bool(autonomous.get(action, False))})
    actions.sort(key=lambda row: (row['mode'], row['action']))
    return sensors, actions


def _settings_payload(gates, autonomous):
    sensors, actions = _catalog(gates, autonomous)
    return {'ok': True, 'sensors': sensors, 'actions': actions,
            'gates': gates, 'autonomous': autonomous}


@bp.route('/api/agents/action-settings')
@local_only
def api_action_settings():
    config = _plugin_config()
    return jsonify(_settings_payload(_read_gates(config), _read_autonomous(config)))


@bp.route('/api/agents/action-settings/update', methods=['POST'])
@advanced
@local_only
def api_action_settings_update():
    body = request.get_json(force=True, silent=True) or {}
    gate_updates = body.get('gates')
    auto_updates = body.get('autonomous')
    for name, updates in (('gates', gate_updates), ('autonomous', auto_updates)):
        if updates is not None and not isinstance(updates, dict):
            return jsonify({'ok': False,
                            'error': '%s must be a {name: bool} map' % name}), 400
    known = set(actuator.ACTIONS) | set(tools_impl.SENSOR_DESCRIPTIONS)
    try:
        with _write_lock:
            config = _plugin_config()
            gates, autonomous = apply_capability_updates(
                _read_gates(config), _read_autonomous(config),
                gate_updates, auto_updates,
                known=known, sensors=set(tools_impl.SENSOR_DESCRIPTIONS))
            _write_maps(gates, autonomous)
    except ValueError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    except Exception as exc:
        _LOGGER.error('[agent-gates] write failed: %s', exc)
        return jsonify({'ok': False, 'error': '%s: %s'
                        % (type(exc).__name__, str(exc)[:200])}), 502
    _LOGGER.info('[agent-gates] updated: gates=%s autonomous=%s',
                 json.dumps(gate_updates or {}, sort_keys=True)[:300],
                 json.dumps(auto_updates or {}, sort_keys=True)[:300])
    return jsonify(_settings_payload(gates, autonomous))


# ── Autonomous daily agent (triage sweep) panel ──────────────────────────────
# One GET powers the whole Permissions-page panel; one POST writes its knobs.
# Per-action autonomy moved to the main capability list (the Auto column) —
# the panel only shows the allowed/total count and links down to it.

_TRIAGE_KNOBS = ('auto_remediate_enabled', 'auto_remediate_remote_hosts',
                 'auto_remediate_max_gb', 'auto_remediate_max_objects')


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
    _sensors, action_rows = _catalog(_read_gates(config), _read_autonomous(config))
    return {
        'ok': True,
        'enabled': _truthy(config.get('auto_remediate_enabled'), default=True),
        'remoteHosts': _truthy(config.get('auto_remediate_remote_hosts'), default=False),
        # Actions only (python-run excluded from the denominator): the panel
        # count links down to the Auto column in the capability list.
        'autonomousCounts': {
            'allowed': sum(1 for row in action_rows if row['autonomous']),
            'total': sum(1 for row in action_rows if row['autoCapable']),
        },
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
        'killSwitch': _truthy(config.get('enable_red_actions'), default=True),
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
    body = request.get_json(force=True, silent=True) or {}
    if 'optIn' in body:
        return jsonify({'ok': False, 'error': 'optIn was removed — per-action autonomy '
                        'lives on the capability list now (POST '
                        '/api/agents/action-settings/update with an autonomous map)'}), 400
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
    if not updates:
        return jsonify({'ok': False, 'error': 'nothing to update'}), 400

    try:
        with _write_lock:
            settings = _local_thread_client().get_plugin(_PLUGIN_ID).get_settings()
            config = settings.get_raw().setdefault('config', {})
            config.update(updates)
            settings.save()
            config = settings.get_raw().get('config', {})
    except Exception as exc:
        _LOGGER.error('[triage-panel] write failed: %s', exc)
        return jsonify({'ok': False, 'error': '%s: %s'
                        % (type(exc).__name__, str(exc)[:200])}), 502
    _LOGGER.info('[triage-panel] updated: %s',
                 json.dumps({k: v for k, v in updates.items()}, sort_keys=True)[:300])
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
