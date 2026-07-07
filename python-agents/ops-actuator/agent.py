import json

from dataiku.llm.python import BaseLLM

from atk_agent_common import (actuator, adapter, agent_runtime, agent_tools, prompt_overrides,
                              prompts, rubric)
from atk_agent_common.errors import ToolkitError


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
                         'cpuRequest|cpuLimit}} (ground in compute_cost/k8s evidence first); '
                         'log-cleanup {roots?, minAgeDays?, maxDeleteGB?} (rotated logs only, '
                         'whitelisted DIP_HOME roots); docker-prune {mode: builder|image, '
                         'keepStorageGB?, filterUntilHours?}; k8s-apply-fix {clusterId, '
                         'commands: [kubectl arg strings], manifestYaml?, execConfigPatch?: '
                         '{configName, changes}, verifyRule?: k8s-insights rule id to re-check '
                         'after execution}; code-env-consolidate {sourceEnvName, targetEnvName, '
                         'language?, projectKeys?, usageTypes?, retireSource?}; settings-set '
                         '{path: dot/index path into DSS general settings, newValue} '
                         '(security/auth/licensing paths are blacklisted). '
                         'item_ref {batchId, itemId} (optional): pass through verbatim when the '
                         'request came from an action-item checklist.'
                         % ', '.join(allowed))))
        tools.append(StructuredTool.from_function(
            execute_admin_action, name='execute_admin_action',
            description=('Execute a planned + user-confirmed admin action. Pass the exact '
                         'canonicalTarget from the plan, confirm=true, and the confirm_token; '
                         'pass the same item_ref as the plan when one was given.')))

        # Agent Tuning overrides win over the built-in templates.
        base = prompt_overrides.get(client, 'actuator_system_prompt', prompts.ACTUATOR_SYSTEM_PROMPT)
        safety = prompt_overrides.get(client, 'action_safety_rubric', rubric.ACTION_SAFETY_RUBRIC)
        prompt = base.replace('{action_safety_rubric}', safety) \
                     .replace('{allowed_actions}', ', '.join(allowed))
        messages = agent_runtime.messages_from_query(query, prompt)
        async for chunk in agent_runtime.run_tool_loop(llm, tools, messages, trace):
            yield chunk
