from dataiku.llm.python import BaseLLM

from atk_agent_common import (action_items, adapter, agent_runtime, agent_tools, prompt_overrides,
                              prompts, rubric)
from atk_agent_common.errors import ToolkitError


class ScopingArchitectAgent(BaseLLM):
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
            tools = agent_tools.build_langchain_tools(
                client, names=['list_hosts', 'config_inspect', 'instance_health', 'k8s_health',
                               'db_health', 'compute_cost', 'storage_footprint', 'adoption_metrics'])
            tools.append(action_items.build_tool(client))
            llm = agent_runtime.build_llm(llm_id)
        except ToolkitError as exc:
            yield {'chunk': {'text': 'Cannot start: %s %s' % (exc.message, exc.remediation or '')}}
            return
        # Agent Tuning overrides win over the built-in templates.
        base = prompt_overrides.get(client, 'scoping_system_prompt', prompts.SCOPING_SYSTEM_PROMPT)
        severity = prompt_overrides.get(client, 'severity_rubric', rubric.SEVERITY_RUBRIC)
        prompt = base.replace('{severity_rubric}', severity) \
                     .replace('{action_items_addendum}', action_items.PROMPT_ADDENDUM)
        messages = agent_runtime.messages_from_query(query, prompt)
        async for chunk in agent_runtime.run_tool_loop(llm, tools, messages, trace):
            yield chunk
