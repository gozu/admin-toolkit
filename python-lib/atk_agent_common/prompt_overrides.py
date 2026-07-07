"""Runtime prompt overrides — the agents' read side of Agent Tuning.

The webapp's Agent Tuning page appends prompt versions to a Dataiku dataset
(one column per prompt type, one row per save, latest row wins); the backend
serves the latest row's non-empty cells at GET /api/agents/tuning/prompts.
Agents fetch that through their ToolkitClient at turn start, cached briefly
per kernel. ANY failure falls back to the built-in defaults — a broken or
missing tuning store must never take an agent down.
"""

import logging
import time

logger = logging.getLogger('atk-agents')

_TTL_S = 60
_cache = {'ts': 0.0, 'values': None}


def overrides(client):
    """{prompt_type_key: override_text} for every customized prompt type."""
    now = time.time()
    if _cache['values'] is not None and now - _cache['ts'] < _TTL_S:
        return _cache['values']
    values = {}
    try:
        data = client.get('/api/agents/tuning/prompts')
        raw = (data or {}).get('values') or {}
        values = {k: v for k, v in raw.items() if isinstance(v, str) and v.strip()}
    except Exception as exc:
        logger.warning('prompt overrides unavailable (%s: %s) — using built-in prompts',
                       type(exc).__name__, str(exc)[:200])
    _cache['ts'] = now
    _cache['values'] = values
    return values


def get(client, key, default):
    """The tuned prompt for `key`, or `default` when no override exists."""
    return overrides(client).get(key) or default
