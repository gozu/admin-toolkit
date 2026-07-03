"""propose_action_items: structured admin-work proposals from the sensor agents.

Sensor agents (health-triage / scoping-architect) end an investigation by
calling this tool with the admin work their findings imply. It is pure
validation/normalization — NO tokens, NO planning, NO side effects. The
normalized result is surfaced to the UI as an `action_items` event; the human
checks items and the webapp hands them to the ops-actuator, which plans each
one fresh (blast radius + confirm token minted at approval time, not here).
"""

import json
import uuid

from . import actuator

MAX_ITEMS = 10
_RISKS = ('red', 'amber', 'green')

_TARGET_SHAPES = ('project-delete {projectKey}; code-env-delete {name, lang}; '
                  'db-vacuum/db-analyze {connection, table}; image-delete {provider, cutoff, images}; '
                  'plugin-deploy {pluginId, targetHostId}; k8s-exec-config-tune {configName, changes}')

TOOL_DESCRIPTION = (
    'Propose up to %d structured admin action items derived from your findings. Each item: '
    '{title (<=120 chars), why (<=500), host (default local), risk: red|amber|green, '
    'action?: exact actuator action name, target?: the action\'s target dict, evidence: [strings]}. '
    'Set action ONLY when it maps exactly to one of: %s — target shapes: %s. Items with no valid '
    'action become advisory (still shown, not executable). Call ONCE, at the end of the '
    'investigation.' % (MAX_ITEMS, ', '.join(actuator.ACTIONS), _TARGET_SHAPES))

# Appended to the sensor agents' system prompts.
PROMPT_ADDENDUM = """
When your findings imply concrete admin work (cleanup, maintenance, tuning, deletions, \
deploys), finish the investigation by calling propose_action_items ONCE with every piece of \
work you identified (most important first, max {max_items}). Rules:
- Set `action` + `target` ONLY when they map exactly to the actuator catalog ({actions}); \
anything else stays advisory (title/why/evidence only, no action).
- risk: 'red' for deletions and settings changes, 'amber' for locking/maintenance operations, \
'green' for safe low-impact work.
- Every item needs concrete `evidence` entries citing tool + host + the numbers that justify it.
The items render as a checklist; the USER decides what is handed to the ops-actuator for \
planning and approval. Never plan, never execute, never promise execution yourself.""".format(
    max_items=MAX_ITEMS, actions=', '.join(actuator.ACTIONS))


def build_tool(client):
    """LangChain StructuredTool for the sensor agents (same _wrap idiom as
    agent_tools, but bound to this module's pure function)."""
    from langchain_core.tools import StructuredTool

    def run(items: list):
        try:
            return json.dumps(propose_action_items(client, items), default=str)
        except Exception as exc:
            return json.dumps({'error': {'code': 'internal-error',
                                         'message': '%s: %s' % (type(exc).__name__, str(exc)[:200])}})

    return StructuredTool.from_function(run, name='propose_action_items',
                                        description=TOOL_DESCRIPTION)


def _clip(value, limit):
    text = str(value or '').strip()
    return text[:limit]


def propose_action_items(client, items):
    """Normalize a batch of proposed action items (cap MAX_ITEMS, server ids).

    Item in: {title, why, host?, risk?, action?, target?, evidence?}
    Item out adds: id ('ai-<8hex>'), actionable (bool), validation (note when
    the proposal was downgraded to advisory instead of dropped).
    """
    if not isinstance(items, list) or not items:
        return {'error': {'code': 'bad-input',
                          'message': 'items must be a non-empty list of action-item dicts.'}}
    dropped = max(0, len(items) - MAX_ITEMS)
    out = []
    for raw in items[:MAX_ITEMS]:
        if not isinstance(raw, dict):
            continue
        notes = []
        title = _clip(raw.get('title'), 120)
        if not title:
            continue
        risk = str(raw.get('risk') or '').strip().lower()
        if risk not in _RISKS:
            if risk:
                notes.append('risk %r is not one of %s — defaulted to amber' % (risk, '/'.join(_RISKS)))
            risk = 'amber'
        action = str(raw.get('action') or '').strip() or None
        target = raw.get('target') if isinstance(raw.get('target'), dict) else None
        if action and action not in actuator.ACTIONS:
            notes.append('action %r is not in the actuator catalog (%s) — downgraded to advisory'
                         % (action, ', '.join(actuator.ACTIONS)))
            action, target = None, None
        if action and target is None:
            notes.append('action %r proposed without a target dict — downgraded to advisory' % action)
            action = None
        evidence = raw.get('evidence')
        evidence = [_clip(e, 300) for e in evidence if str(e or '').strip()] \
            if isinstance(evidence, list) else []
        out.append({
            'id': 'ai-%s' % uuid.uuid4().hex[:8],
            'title': title,
            'why': _clip(raw.get('why'), 500),
            'host': str(raw.get('host') or 'local').strip() or 'local',
            'risk': risk,
            'action': action,
            'target': target,
            'evidence': evidence[:6],
            'actionable': action is not None,
            'validation': '; '.join(notes) or None,
        })
    if not out:
        return {'error': {'code': 'bad-input',
                          'message': 'No valid items — every item needs at least a title.'}}
    result = {
        'batchId': 'aib-%s' % uuid.uuid4().hex[:8],
        'items': out,
        'count': len(out),
        'nextStep': ('The items are now displayed to the user as a checklist. Do NOT plan or '
                     'execute anything — the user will hand checked items to the ops-actuator '
                     'themselves. Close with a short summary of what you proposed.'),
    }
    if dropped:
        result['droppedCount'] = dropped
        result['nextStep'] += (' NOTE: %d item(s) beyond the %d-item cap were dropped — mention '
                               'the ones you consider most important among the dropped.' % (dropped, MAX_ITEMS))
    return result
