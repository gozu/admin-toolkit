from dataiku.llm.python import BaseLLM

from atk_agent_common import adapter, agent_runtime, generalist
from atk_agent_common.errors import ToolkitError


class AdminGeneralistAgent(BaseLLM):
    """The single Admin Toolkit agent (Tier 4c): every sensor + triage_sweep +
    propose_action_items + the plan/execute write protocol in one kernel, one
    thread. Task-mode behavior comes from GENERALIST_SYSTEM_PROMPT.

    Toolset and prompt assembly are shared with the in-process native runtime
    (atk_agent_common/generalist.py) — this component only hosts the loop
    inside a Dataiku agent kernel.
    """

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

        behavior = generalist.agent_behavior(self.config)
        tools = generalist.build_toolset(client, behavior, llm_id)
        prompt = generalist.build_system_prompt(client, behavior, tools)
        messages = agent_runtime.messages_from_query(query, prompt)
        async for chunk in agent_runtime.run_tool_loop(llm, tools, messages, trace):
            yield chunk
