import json

from dataiku.llm.python import BaseLLM

from atk_agent_common import actuator, adapter, agent_runtime, agent_tools
from atk_agent_common.errors import ToolkitError

SYSTEM_PROMPT = """You are the Admin Toolkit ops actuator: you carry out administrative actions on \
Dataiku DSS instances with a strict human-in-the-loop protocol.

The protocol — never deviate:
1. UNDERSTAND: use the sensor tools to identify the exact target (never guess names/keys).
2. PLAN: call plan_admin_action. It returns the blast radius and a confirm_token.
3. SHOW: present the returned plan to the user VERBATIM — summary, sizes, warnings, \
projects affected, backup destination. Do not soften warnings.
4. WAIT: ask "Do you confirm?" and STOP. Only an explicit affirmative in the user's NEXT \
message counts as confirmation. Pre-authorization ("just do it for anything") does NOT count \
— each action needs its own confirmation after its own plan.
5. EXECUTE: call execute_admin_action with the exact canonicalTarget, confirm=true and the \
token. Report the outcome AND the auditId.

If a tool returns an error (red-locked, kill-switch off, token rejected/expired), relay its \
message and remediation; never work around a gate. If the token expired because the user took \
time to answer, re-plan and re-confirm.

Batch protocol (messages carrying a list of pre-approved-for-planning action items, e.g. a \
handoff from another agent's checklist): plan EVERY listed item — one plan_admin_action call \
per item, passing the item's item_ref verbatim so plans and audit rows stay traceable to the \
checklist. Present each plan (the UI renders them as cards), then WAIT. The user may approve \
plans individually or in one batch message enumerating several tokens; execute exactly the \
plans whose tokens they approved, one execute_admin_action per plan with its own item_ref, and \
report each outcome + auditId separately. A batch handoff is NOT confirmation — every execution \
still requires the user's explicit approval of that specific plan.
Allowed actions for this agent: {allowed_actions}."""


class OpsActuatorAgent(BaseLLM):
    def __init__(self):
        pass

    def set_config(self, config, plugin_config):
        self.config = config or {}
        self.plugin_config = plugin_config or {}

    async def aprocess_stream(self, query, settings, trace):
        try:
            client = adapter.build_client(self.plugin_config)
            llm_id = (self.config.get('llm_id') or '').strip() or client.settings.get('default_llm_id')
            if not llm_id:
                raise ToolkitError('No LLM configured.',
                                   remediation='Set llm_id on the agent or default_llm_id in the plugin settings.')
            llm = agent_runtime.build_llm(llm_id)
        except ToolkitError as exc:
            yield {'chunk': {'text': 'Cannot start: %s %s' % (exc.message, exc.remediation or '')}}
            return

        allow_execute = bool(self.config.get('allow_red_actions'))
        allowed = [a.strip() for a in (self.config.get('allowed_actions') or '').split(',') if a.strip()]
        allowed = allowed or list(actuator.ACTIONS)

        tools = agent_tools.build_langchain_tools(
            client, names=['list_hosts', 'instance_health', 'storage_footprint',
                           'config_inspect', 'db_health', 'compute_cost'])
        from langchain_core.tools import StructuredTool

        def plan_admin_action(action, target, host='local', params=None, item_ref=None):
            if action not in allowed:
                return json.dumps({'error': {'code': 'action-not-allowed',
                                             'message': 'Action %r is not in this agent\'s allowlist (%s).'
                                                        % (action, ', '.join(allowed))}})
            try:
                result = actuator.plan_admin_action(client, host=host, action=action,
                                                    target=target, params=params)
                # Checklist provenance rides along for the UI; deliberately NOT
                # part of the signed token payload (confirm.py is untouched).
                if item_ref and isinstance(result, dict):
                    result['itemRef'] = item_ref
                return json.dumps(result, default=str)
            except ToolkitError as exc:
                return json.dumps(exc.to_output(), default=str)

        def execute_admin_action(action, target, confirm, confirm_token, host='local', item_ref=None):
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
                    agent_name='ops-actuator', llm_id=llm_id,
                    provenance=item_ref if isinstance(item_ref, dict) else None), default=str)
            except ToolkitError as exc:
                return json.dumps(exc.to_output(), default=str)

        tools.append(StructuredTool.from_function(
            plan_admin_action, name='plan_admin_action',
            description=('Plan an admin action (read-only dry run): blast radius + confirm_token. '
                         'Actions: %s. Targets: project-delete {projectKey}; code-env-delete '
                         '{name, lang}; db-vacuum/db-analyze {connection, table}; image-delete '
                         '{provider, cutoff, images}; plugin-deploy {pluginId, targetHostId}; '
                         'k8s-exec-config-tune {configName, changes:{memRequestMB|memLimitMB|'
                         'cpuRequest|cpuLimit}} (ground in compute_cost/k8s evidence first). '
                         'item_ref {batchId, itemId} (optional): pass through verbatim when the '
                         'request came from an action-item checklist.'
                         % ', '.join(allowed))))
        tools.append(StructuredTool.from_function(
            execute_admin_action, name='execute_admin_action',
            description=('Execute a planned + user-confirmed admin action. Pass the exact '
                         'canonicalTarget from the plan, confirm=true, and the confirm_token; '
                         'pass the same item_ref as the plan when one was given.')))

        prompt = SYSTEM_PROMPT.replace('{allowed_actions}', ', '.join(allowed))
        messages = agent_runtime.messages_from_query(query, prompt)
        async for chunk in agent_runtime.run_tool_loop(llm, tools, messages, trace):
            yield chunk
