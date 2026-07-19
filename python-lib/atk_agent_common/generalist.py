"""Shared assembly for the Admin Toolkit generalist agent — ONE definition of
its toolset and system prompt, consumed by BOTH runtimes:

  • the Dataiku plugin-agent kernel (python-agents/admin-generalist/agent.py),
  • the in-process native runtime (adk_backend/agent_native.py).

Extracted from the kernel component so the two runtimes cannot drift: any tool
added or prompt clause changed here reaches both surfaces on the next deploy
(the kernel still needs its usual recycle; the native runtime picks it up on
backend restart, i.e. immediately after deploy).

Everything here is flask-free and DSS-import-free: `client` is always the
ToolkitClient (HTTP onto the toolkit backend), so this module stays runnable
from kernels, the webapp backend, and the scripts/agents drills alike.
"""

import json

from langchain_core.tools import StructuredTool

from . import action_items, actuator, agent_tools, prompt_overrides, prompts, remediation_map, rubric
from . import actions as actions_registry
from .errors import ToolkitError
from .triage import sweep

AGENT_NAME = 'admin-generalist'


def agent_behavior(config):
    """The kernel-config-derived behavior knobs, defaulted exactly like the
    plugin agent component does. `config` is the agent instance's
    pluginAgentConfig (may be {} for the native runtime's virtual agent)."""
    config = config or {}
    hosts = [h.strip() for h in (config.get('hosts') or '').split(',') if h.strip()] or None
    allowed = [a.strip() for a in (config.get('allowed_actions') or '').split(',') if a.strip()]
    return {
        'hosts': hosts,
        'score_threshold': int(config.get('score_threshold') or 75),
        'allow_execute': bool(config.get('allow_red_actions')),
        'allowed': allowed or list(actuator.ACTIONS),
        'max_recommendations': int(config.get('max_recommendations') or 5),
    }


def build_toolset(client, behavior, llm_id, agent_name=AGENT_NAME):
    """The generalist's full tool list: every enabled sensor + triage_sweep +
    propose_action_items + the plan/execute write protocol. Returns LangChain
    StructuredTools ready for bind_tools()."""
    hosts = behavior['hosts']
    threshold = behavior['score_threshold']
    allow_execute = behavior['allow_execute']
    allowed = behavior['allowed']

    # Full sensor set (post Agent Settings gating).
    tools = agent_tools.build_langchain_tools(client)

    def triage_sweep():
        try:
            return json.dumps(sweep.sweep_fleet(client, hosts=hosts, score_threshold=threshold),
                              default=str)
        except ToolkitError as exc:
            return json.dumps(exc.to_output(), default=str)

    tools.append(StructuredTool.from_function(
        triage_sweep, name='triage_sweep',
        description=('Deterministic fleet triage: scores every configured host with the UI '
                     'health score, ranks worst-first, flags hosts under the threshold and '
                     'attaches supporting signals. Call once for any sweep/fleet-check request; '
                     'takes no arguments.')))
    tools.append(action_items.build_tool(client))

    def plan_admin_action(action, target=None, targets=None, host='local', params=None,
                          item_ref=None):
        if action not in allowed:
            return json.dumps({'error': {'code': 'action-not-allowed',
                                         'message': 'Action %r is not in this agent\'s allowlist (%s).'
                                                    % (action, ', '.join(allowed))}})
        try:
            result = actuator.plan_admin_action(client, host=host, action=action,
                                                target=target, targets=targets,
                                                params=params)
            # Checklist provenance rides along for the UI; deliberately NOT
            # part of the signed token payload (confirm.py is untouched).
            if item_ref and isinstance(result, dict):
                result['itemRef'] = item_ref
            return json.dumps(result, default=str)
        except ToolkitError as exc:
            return json.dumps(exc.to_output(), default=str)

    def execute_admin_action(action, target, confirm, confirm_token, host='local', item_ref=None):
        # No `link` here: this code has its own frontend card (GateHint)
        # deep-linking to the DSS agent-config page, which no internal
        # toolkit page can replace.
        if not allow_execute:
            return json.dumps({'error': {'code': 'agent-execution-disabled',
                                         'message': 'This agent instance has allow_red_actions=false: '
                                                    'it may plan but never execute. An admin must enable it.'}})
        if action not in allowed:
            return json.dumps({'error': {'code': 'action-not-allowed',
                                         'message': 'Action %r is not in this agent\'s allowlist.' % action}})
        try:
            return json.dumps(actuator.execute_admin_action(
                client, host=host, action=action, target=target,
                confirm_flag=bool(confirm), confirm_token=confirm_token,
                agent_name=agent_name, llm_id=llm_id,
                provenance=item_ref if isinstance(item_ref, dict) else None), default=str)
        except ToolkitError as exc:
            return json.dumps(exc.to_output(), default=str)

    tools.append(StructuredTool.from_function(
        plan_admin_action, name='plan_admin_action',
        description=('Plan an admin action (read-only dry run): blast radius + confirm_token. '
                     'Actions: %s. Target shapes: %s. %s '
                     'item_ref {batchId, itemId} (optional): pass through verbatim when the '
                     'request came from an action-item checklist.'
                     % (', '.join(allowed), actuator.TARGET_SHAPES,
                        actions_registry.BATCH_NOTE))))
    tools.append(StructuredTool.from_function(
        execute_admin_action, name='execute_admin_action',
        description=('Execute a planned + user-confirmed admin action. Pass the exact '
                     'canonicalTarget from the plan, confirm=true, and the confirm_token; '
                     'pass the same item_ref as the plan when one was given. For python-run '
                     'you may pass {code, purpose} instead — the code must be EXACTLY the '
                     'planned code (it is hash-checked against the token).')))
    return tools


def build_system_prompt(client, behavior, tools):
    """The generalist system prompt with Agent Tuning overrides applied and
    every template slot filled from the tools ACTUALLY bound this turn."""
    base = prompt_overrides.get(client, 'generalist_system_prompt',
                                prompts.GENERALIST_SYSTEM_PROMPT)
    severity = prompt_overrides.get(client, 'severity_rubric', rubric.SEVERITY_RUBRIC)
    safety = prompt_overrides.get(client, 'action_safety_rubric', rubric.ACTION_SAFETY_RUBRIC)
    return base.replace('{max_recommendations}', str(behavior['max_recommendations'])) \
               .replace('{remediation_map}', remediation_map.prompt_table()) \
               .replace('{severity_rubric}', severity) \
               .replace('{action_safety_rubric}', safety) \
               .replace('{allowed_actions}', ', '.join(behavior['allowed'])) \
               .replace('{action_items_addendum}', action_items.PROMPT_ADDENDUM) \
               .replace('{sensor_manifest}', agent_tools.sensor_manifest(tools))
