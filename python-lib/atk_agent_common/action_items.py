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

from . import actions as actions_registry
from . import actuator

MAX_ITEMS = 10
_RISKS = ('red', 'amber', 'green')

# Single source: generated from the actions registry (legacy + domains).
_TARGET_SHAPES = actuator.TARGET_SHAPES

TOOL_DESCRIPTION = (
    'Propose up to %d structured admin action items derived from your findings. Each item: '
    '{title (<=120 chars), why (<=500), host (default local), risk: red|amber|green, '
    'action?: exact actuator action name, target?: the action\'s target dict, '
    'targets?: [target dicts] for several objects under ONE item (batchable actions only), '
    'evidence: [strings]}. '
    'Set action ONLY when it maps exactly to one of: %s — target shapes: %s. %s '
    'Items with no valid action become advisory (still shown, not executable). Call ONCE, '
    'as the LAST element of your reply — write any analysis first; after this returns, end '
    'the turn with no further text.'
    % (MAX_ITEMS, ', '.join(actuator.ACTIONS), _TARGET_SHAPES,
       actions_registry.BATCH_NOTE))

# Appended to the sensor agents' system prompts.
PROMPT_ADDENDUM = """
When your findings imply concrete admin work (cleanup, maintenance, tuning, deletions, \
deploys), finish the investigation by calling propose_action_items ONCE with every piece of \
work you identified (most important first, max {max_items}). Rules:
- Propose only items at MEDIUM severity or higher (the severity rubric's digest floor). \
Whitelist-suppressed findings never reach you — do not hedge live findings; every finding \
you see is live.
- Set `action` + `target` ONLY when they map exactly to the actuator catalog ({actions}); \
anything else stays advisory (title/why/evidence only, no action).
- FIX DISCIPLINE (every mapped finding): GATE first — is the mapped fix even right for \
this instance? GROUND on keys/paths/names observed via sensors, never guessed. DERIVE \
targets from actual usage. Propose the smallest reversible mutation, and name the \
verification that will prove the finding cleared. When the finding lacks a required \
target key, the remediation map's drill steps name the read that supplies it — do that \
read and propose concrete values, or stay advisory and say exactly what is missing.
- Several objects needing the SAME action (e.g. six unused code envs) = ONE item with \
`targets: [dict, ...]` — never burn one item slot per object. Batchable: {batchable}.
- risk: 'red' for anything destructive or settings-mutating (deletions, config/settings \
changes — all require backup-first / prior-value recording downstream), 'amber' for \
locking/maintenance operations, 'green' for safe low-impact work. Never soften a risk color.
- Do NOT add a separate backup/export item before a destructive delete. project-delete, \
code-env-delete, connection-delete and cluster-detach ALL back up to block storage \
automatically at plan time (the plan shows the destination) — a standalone project-export \
"export before deleting" item is redundant and wastes an item slot. Propose the delete itself \
(honest red risk); the backup is built in. (project-export stands alone only to archive \
projects you are KEEPING, or a migration bundle — never as a pre-delete safety copy.)
- Every item needs concrete `evidence` entries citing tool + host + the numbers that justify it.
The items render as a checklist; the USER decides what is handed to the ops-actuator for \
planning and approval. Never plan, never execute, never promise execution yourself.
PLACEMENT — the checklist is always the LAST element of your reply: write your full \
analysis/summary BEFORE calling propose_action_items, then call it as your final act. After \
the tool returns, END YOUR TURN IMMEDIATELY with no further text — no recap, no sign-off, no \
"let me know" (the checklist speaks for itself; trailing prose wastes tokens and pushes it \
off the bottom). Sole exception: if the tool result reports dropped or downgraded items, add \
ONE short line naming the most important of them, nothing else.""".format(
    max_items=MAX_ITEMS, actions=', '.join(actuator.ACTIONS),
    batchable=', '.join(sorted(actuator.BATCHABLE_ACTIONS)))


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


def _normalize_item_targets(raw, action, notes):
    """One list of target dicts from an item's target/targets pair.
    Multi-target on a non-batchable action keeps only the first target
    (noted, never silently)."""
    targets = []
    raw_targets = raw.get('targets')
    if isinstance(raw_targets, list):
        targets = [t for t in raw_targets if isinstance(t, dict)]
        if len(targets) != len(raw_targets):
            notes.append('non-dict entries in targets were dropped')
    if not targets and isinstance(raw.get('target'), dict):
        targets = [raw['target']]
    if action and len(targets) > 1 and action not in actuator.BATCHABLE_ACTIONS:
        notes.append('action %r is not batchable — kept the first target only '
                     '(batchable: %s)' % (action, ', '.join(sorted(actuator.BATCHABLE_ACTIONS))))
        targets = targets[:1]
    return targets


def propose_action_items(client, items):
    """Normalize a batch of proposed action items (cap MAX_ITEMS, server ids).

    Item in: {title, why, host?, risk?, action?, target?, targets?, evidence?}
    Item out adds: id ('ai-<8hex>'), actionable (bool), targets/targetCount,
    validation (note when the proposal was downgraded to advisory instead of
    dropped). `target` mirrors targets[0] for back-compat consumers.
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
        if action and action not in actuator.ACTIONS:
            notes.append('action %r is not in the actuator catalog (%s) — downgraded to advisory'
                         % (action, ', '.join(actuator.ACTIONS)))
            action = None
        targets = _normalize_item_targets(raw, action, notes) if action else []
        if action and not targets:
            notes.append('action %r proposed without any target dict — downgraded to advisory'
                         % action)
            action = None
        if action and targets:
            # Same presence semantics as the planners (require_str /
            # `'newValue' not in target`): an item the actuator could never
            # plan must not reach the checklist as actionable.
            required = actions_registry.REQUIRED_TARGET_KEYS.get(action) or frozenset()
            missing = sorted({key for t in targets for key in required if key not in t})
            if missing:
                notes.append('action %r target(s) missing required key(s) %s — downgraded to '
                             'advisory. Emit this action only when the evidence supplies a '
                             'concrete value for every required key; never guess one.'
                             % (action, ', '.join(missing)))
                action = None
                targets = []
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
            'target': targets[0] if targets else None,
            'targets': targets or None,
            'targetCount': len(targets),
            'evidence': evidence[:6],
            'actionable': action is not None and len(targets) >= 1,
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
                     'themselves. END YOUR TURN NOW with no further text: the checklist is the '
                     'final element of your reply and anything after it wastes tokens.'),
    }
    if dropped:
        result['droppedCount'] = dropped
        result['nextStep'] += (' Sole exception: %d item(s) beyond the %d-item cap were dropped — '
                               'add ONE short line naming the most important dropped item(s), '
                               'then stop.' % (dropped, MAX_ITEMS))
    return result
