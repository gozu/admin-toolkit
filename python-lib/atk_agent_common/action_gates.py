"""Per-action enablement gates — the agents' read side of Agent Settings.

The webapp's Agent Settings page persists one {name: bool} map in the plugin
config (`agent_action_gates`, hidden param) and serves it live at
GET /api/agents/action-settings. Agents fetch that through their
ToolkitClient, cached briefly per kernel, so an admin toggle takes effect
without a kernel recycle. When the backend is unreachable the kernel's own
plugin-config snapshot is the fallback.

Default policy (fail-closed): read-only sensor tools are enabled unless
explicitly disabled; every actuator action is DISABLED until an admin enables
it in Agent Settings.
"""

import logging
import time

logger = logging.getLogger('atk-agents')

_TTL_S = 30
_cache = {'ts': 0.0, 'gates': None}


def _config_gates(client):
    """The kernel-start snapshot from plugin config (config.resolve)."""
    gates = client.settings.get('agent_action_gates')
    return gates if isinstance(gates, dict) else {}


def gates(client):
    """The live {name: bool} gate map, cached briefly."""
    now = time.time()
    if _cache['gates'] is not None and now - _cache['ts'] < _TTL_S:
        return _cache['gates']
    resolved = None
    try:
        data = client.get('/api/agents/action-settings')
        raw = (data or {}).get('gates')
        if isinstance(raw, dict):
            resolved = {str(k): bool(v) for k, v in raw.items()}
    except Exception as exc:
        logger.warning('action gates unavailable (%s: %s) — using plugin-config snapshot',
                       type(exc).__name__, str(exc)[:200])
    if resolved is None:
        resolved = {str(k): bool(v) for k, v in _config_gates(client).items()}
    _cache['ts'] = now
    _cache['gates'] = resolved
    return resolved


def sensor_enabled(client, name):
    """Read-only sensor tools default ON."""
    return bool(gates(client).get(name, True))


def action_enabled(client, action):
    """Actuator actions default OFF — an admin must enable each one."""
    return bool(gates(client).get(action, False))


def disabled_error(action):
    return {'error': {
        'code': 'action-disabled',
        'message': 'Action %r is disabled in Agent Settings — every non-read action is '
                   'off until an administrator enables it.' % action,
        'remediation': 'An administrator can enable it in the webapp under '
                       'Agents → Agent Settings. Relay this to the user; agents cannot '
                       'change their own gates.',
    }}
