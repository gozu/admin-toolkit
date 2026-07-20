"""Per-action enablement + autonomy gates — the agents' read side of the
Agent Permissions page.

The webapp persists TWO {name: bool} maps in the plugin config (hidden params
`agent_action_gates` and `agent_autonomous_gates`) and serves both live at
GET /api/agents/action-settings. Agents fetch that through their
ToolkitClient, cached briefly per kernel, so an admin toggle takes effect
without a kernel recycle. When the backend is unreachable the kernel's own
plugin-config snapshot is the fallback (config.resolve parses both params,
seeding autonomy from the legacy auto_remediate_actions CSV when the new
param has never been written).

Default policy (fail-closed): read-only sensor tools are enabled unless
explicitly disabled (and autonomous by default — reading is side-effect
free); every actuator action is DISABLED and NON-autonomous until an admin
enables it in Agent Permissions. python-run can NEVER be autonomous — the
AUTO_EXCLUDED floor here holds even if the stored map is hand-edited.
"""

import logging
import time

from .remediation_map import AUTO_EXCLUDED

logger = logging.getLogger('atk-agents')

_TTL_S = 30
_cache = {'ts': 0.0, 'gates': None, 'autonomous': None}


def _config_map(client, key):
    """A kernel-start snapshot from plugin config (config.resolve)."""
    value = client.settings.get(key)
    return value if isinstance(value, dict) else {}


def _maps(client):
    """The live gate + autonomous maps, cached briefly — one fetch fills both."""
    now = time.time()
    if _cache['gates'] is not None and now - _cache['ts'] < _TTL_S:
        return _cache['gates'], _cache['autonomous']
    gates_map = auto_map = None
    try:
        data = client.get('/api/agents/action-settings') or {}
        raw = data.get('gates')
        if isinstance(raw, dict):
            gates_map = {str(k): bool(v) for k, v in raw.items()}
        raw = data.get('autonomous')
        if isinstance(raw, dict):
            auto_map = {str(k): bool(v) for k, v in raw.items()}
    except Exception as exc:
        logger.warning('action gates unavailable (%s: %s) — using plugin-config snapshot',
                       type(exc).__name__, str(exc)[:200])
    if gates_map is None:
        gates_map = {str(k): bool(v) for k, v in
                     _config_map(client, 'agent_action_gates').items()}
    if auto_map is None:
        auto_map = {str(k): bool(v) for k, v in
                    _config_map(client, 'agent_autonomous_gates').items()}
    _cache['ts'] = now
    _cache['gates'] = gates_map
    _cache['autonomous'] = auto_map
    return gates_map, auto_map


def gates(client):
    """The live {name: bool} enablement map."""
    return _maps(client)[0]


def autonomous(client):
    """The live {name: bool} autonomy map."""
    return _maps(client)[1]


def sensor_enabled(client, name):
    """Read-only sensor tools default ON."""
    return bool(gates(client).get(name, True))


def action_enabled(client, action):
    """Actuator actions default OFF — an admin must enable each one."""
    return bool(gates(client).get(action, False))


def sensor_autonomous(client, name):
    """Sensors default autonomous ON (they are side-effect free)."""
    return bool(autonomous(client).get(name, True))


def action_autonomous(client, action):
    """Actions default autonomous OFF; AUTO_EXCLUDED (python-run) is a hard
    floor — False even if the stored map was hand-edited to say otherwise."""
    if action in AUTO_EXCLUDED:
        return False
    return bool(autonomous(client).get(action, False))


def disabled_error(action):
    return {'error': {
        'code': 'action-disabled',
        'message': 'Action %r is disabled in Agent Settings — every non-read action is '
                   'off until an administrator enables it.' % action,
        'remediation': 'An administrator can enable it in the webapp under '
                       'Agents → Agent Settings. Relay this to the user; agents cannot '
                       'change their own gates.',
        'link': {'page': 'agent-settings', 'label': 'Enable in Agents → Permissions'},
    }}


def autonomy_error(action):
    return {'error': {
        'code': 'action-not-autonomous',
        'message': 'Action %r is not granted autonomous execution — the nightly agent '
                   'may only run actions whose Autonomous box an administrator ticked '
                   'in Agents → Permissions.' % action,
        'remediation': 'An administrator can grant it in the webapp under Agents → '
                       'Permissions (the Auto column). python-run can never be '
                       'autonomous.',
        'link': {'page': 'agent-settings', 'label': 'Review in Agents → Permissions'},
    }}
