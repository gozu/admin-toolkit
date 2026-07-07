import json

from dataiku.llm.python import BaseLLM

from atk_agent_common import (action_items, adapter, agent_runtime, agent_tools, prompt_overrides,
                              prompts, remediation_map, rubric)
from atk_agent_common.errors import ToolkitError
from atk_agent_common.triage import sweep


class HealthTriageAgent(BaseLLM):
    def __init__(self):
        pass

    def set_config(self, config, plugin_config):
        self.config = config or {}
        self.plugin_config = plugin_config or {}

    def _build(self):
        client = adapter.build_client(self.plugin_config)
        # Agent Tuning override > per-agent llm_id > plugin default_llm_id.
        llm_id = agent_runtime.resolve_llm_id(client, self.config)
        hosts = [h.strip() for h in (self.config.get('hosts') or '').split(',') if h.strip()] or None
        threshold = int(self.config.get('score_threshold') or 75)

        tools = agent_tools.build_langchain_tools(client)

        def triage_sweep():
            try:
                return json.dumps(sweep.sweep_fleet(client, hosts=hosts, score_threshold=threshold),
                                  default=str)
            except ToolkitError as exc:
                return json.dumps(exc.to_output(), default=str)

        from langchain_core.tools import StructuredTool
        tools.append(StructuredTool.from_function(
            triage_sweep, name='triage_sweep',
            description=('Deterministic fleet triage: scores every configured host with the UI '
                         'health score, ranks worst-first, flags hosts under the threshold and '
                         'attaches supporting signals. Call once for any sweep/fleet-check request; '
                         'takes no arguments.')))
        tools.append(action_items.build_tool(client))
        return client, agent_runtime.build_llm(llm_id), tools

    async def aprocess_stream(self, query, settings, trace):
        try:
            client, llm, tools = self._build()
        except ToolkitError as exc:
            yield {'chunk': {'text': 'Cannot start: %s %s' % (exc.message, exc.remediation or '')}}
            return
        # Agent Tuning overrides (versioned dataset via the backend) win over
        # the built-in templates; placeholders are substituted either way.
        base = prompt_overrides.get(client, 'triage_system_prompt', prompts.TRIAGE_SYSTEM_PROMPT)
        severity = prompt_overrides.get(client, 'severity_rubric', rubric.SEVERITY_RUBRIC)
        prompt = base.replace('{max_recommendations}',
                              str(self.config.get('max_recommendations') or 5)) \
                     .replace('{remediation_map}', remediation_map.prompt_table()) \
                     .replace('{severity_rubric}', severity) \
                     .replace('{action_items_addendum}', action_items.PROMPT_ADDENDUM)
        messages = agent_runtime.messages_from_query(query, prompt)
        async for chunk in agent_runtime.run_tool_loop(llm, tools, messages, trace):
            yield chunk
