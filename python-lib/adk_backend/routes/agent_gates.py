"""Agent Settings routes — the per-action enablement gates.

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

from flask import Blueprint, jsonify, request

from adk_backend.clients import _local_thread_client
from adk_backend.utils import advanced, local_only
from atk_agent_common import actuator, tools_impl
from atk_agent_common import actions as actions_registry

bp = Blueprint('agent_gates', __name__)
_LOGGER = logging.getLogger(__name__)

_PLUGIN_ID = 'admin-toolkit'
_PARAM = 'agent_action_gates'


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
    gates = _read_gates()
    for key, value in updates.items():
        gates[str(key)] = bool(value)
    try:
        _write_gates(gates)
    except Exception as exc:
        _LOGGER.error('[agent-gates] write failed: %s', exc)
        return jsonify({'ok': False, 'error': '%s: %s'
                        % (type(exc).__name__, str(exc)[:200])}), 502
    _LOGGER.info('[agent-gates] updated: %s', json.dumps(updates, sort_keys=True)[:300])
    sensors, actions = _catalog(gates)
    return jsonify({'ok': True, 'sensors': sensors, 'actions': actions, 'gates': gates})
