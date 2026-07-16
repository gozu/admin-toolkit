import json

from dataiku.llm.python import BaseLLM

from atk_agent_common import (action_items, actuator, adapter, agent_runtime, agent_tools,
                              prompt_overrides, prompts, remediation_map, rubric)
from atk_agent_common import actions as actions_registry
from atk_agent_common.errors import ToolkitError
from atk_agent_common.triage import sweep


class AdminGeneralistAgent(BaseLLM):
    """The single Admin Toolkit agent (Tier 4c): every sensor + triage_sweep +
    propose_action_items + the plan/execute write protocol in one kernel, one
    thread. Task-mode behavior comes from GENERALIST_SYSTEM_PROMPT."""

    def __init__(self):
        pass

    def set_config(self, config, plugin_config):
        self.config = config or {}
        self.plugin_config = plugin_config or {}

    async def aprocess_stream(self, query, settings, trace):
        try:
            client = adapter.build_client(self.plugin_config)
            # Agent Tuning override > per-agent llm_id > plugin default_llm_id.
            llm_id = agent_runtime.resolve_llm_id(client, self.config)
            llm = agent_runtime.build_llm(llm_id)
        except ToolkitError as exc:
            yield {'chunk': {'text': 'Cannot start: %s %s' % (exc.message, exc.remediation or '')}}
            return

        hosts = [h.strip() for h in (self.config.get('hosts') or '').split(',') if h.strip()] or None
        threshold = int(self.config.get('score_threshold') or 75)
        allow_execute = bool(self.config.get('allow_red_actions'))
        allowed = [a.strip() for a in (self.config.get('allowed_actions') or '').split(',') if a.strip()]
        allowed = allowed or list(actuator.ACTIONS)

        # Full sensor set (post Agent Settings gating).
        tools = agent_tools.build_langchain_tools(client)
        from langchain_core.tools import StructuredTool

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
                    agent_name='admin-generalist', llm_id=llm_id,
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

        # Agent Tuning overrides win over the built-in templates.
        base = prompt_overrides.get(client, 'generalist_system_prompt',
                                    prompts.GENERALIST_SYSTEM_PROMPT)
        severity = prompt_overrides.get(client, 'severity_rubric', rubric.SEVERITY_RUBRIC)
        safety = prompt_overrides.get(client, 'action_safety_rubric', rubric.ACTION_SAFETY_RUBRIC)
        prompt = base.replace('{max_recommendations}',
                              str(self.config.get('max_recommendations') or 5)) \
                     .replace('{remediation_map}', remediation_map.prompt_table()) \
                     .replace('{severity_rubric}', severity) \
                     .replace('{action_safety_rubric}', safety) \
                     .replace('{allowed_actions}', ', '.join(allowed)) \
                     .replace('{action_items_addendum}', action_items.PROMPT_ADDENDUM) \
                     .replace('{sensor_manifest}', agent_tools.sensor_manifest(tools))
        messages = agent_runtime.messages_from_query(query, prompt)
        async for chunk in agent_runtime.run_tool_loop(llm, tools, messages, trace):
            yield chunk
