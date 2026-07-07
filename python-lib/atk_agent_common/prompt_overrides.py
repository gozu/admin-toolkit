"""Runtime prompt overrides — the agents' read side of Agent Tuning.

The webapp's Agent Tuning page appends prompt versions to a Dataiku dataset
(one column per prompt type plus setting columns like the LLM override, one
row per save, latest row wins); the backend serves the latest row at
GET /api/agents/tuning/prompts as {values, settings}. Agents fetch that
through their ToolkitClient at turn start, cached briefly per kernel. ANY
failure falls back to the built-in defaults — a broken or missing tuning
store must never take an agent down.
"""

import logging
import time

logger = logging.getLogger('atk-agents')

_TTL_S = 60
_EMPTY = {'values': {}, 'settings': {}}
_cache = {'ts': 0.0, 'payload': None}


def _payload(client):
    """The latest tuning snapshot {values, settings}, cached briefly."""
    now = time.time()
    if _cache['payload'] is not None and now - _cache['ts'] < _TTL_S:
        return _cache['payload']
    payload = _EMPTY
    try:
        data = client.get('/api/agents/tuning/prompts')
        raw_values = (data or {}).get('values') or {}
        raw_settings = (data or {}).get('settings') or {}
        payload = {
            'values': {k: v for k, v in raw_values.items()
                       if isinstance(v, str) and v.strip()},
            'settings': {k: v.strip() for k, v in raw_settings.items()
                         if isinstance(v, str)},
        }
    except Exception as exc:
        logger.warning('prompt overrides unavailable (%s: %s) — using built-in prompts',
                       type(exc).__name__, str(exc)[:200])
    _cache['ts'] = now
    _cache['payload'] = payload
    return payload


def overrides(client):
    """{prompt_type_key: override_text} for every customized prompt type."""
    return _payload(client)['values']


def get(client, key, default):
    """The tuned prompt for `key`, or `default` when no override exists."""
    return overrides(client).get(key) or default


def llm_override(client):
    """The Agent Tuning model override id, or '' when none is set."""
    return _payload(client)['settings'].get('llm_override') or ''
