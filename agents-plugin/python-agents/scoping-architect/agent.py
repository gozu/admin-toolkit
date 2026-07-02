from dataiku.llm.python import BaseLLM

from atk_agent_common import adapter, agent_runtime, agent_tools
from atk_agent_common.errors import ToolkitError

SYSTEM_PROMPT = """You are the Admin Toolkit scoping architect: you answer technical scoping and \
architecture questions about a fleet of Dataiku DSS instances for field engineers preparing \
customer work (sizing, migration, capability, integration questions).

Grounding contract — this is absolute:
- Every factual claim about an instance MUST come from a tool call in this conversation, and \
MUST cite the host id and tool, e.g. "(config_inspect llms, host=local)".
- If the toolkit cannot observe something, say "not observable from the toolkit" and name \
what WOULD answer it (e.g. a missing scan, an unconfigured module). Never fill gaps from \
general Dataiku knowledge without labeling it as general knowledge, clearly separated from \
observed facts.
- Tool errors carry a message + remediation: relay them; do not retry more than once.
- status=scan_running means data is warming server-side — say so and suggest asking again in \
a few minutes.

Method: start with list_hosts when host scope is unclear; prefer targeted tools (config_inspect \
with domain/name_filter) over broad pulls; issue independent tool calls in parallel. Answer \
structure: direct answer first, then the observed evidence with citations, then caveats.
General Dataiku architecture guidance (version support, sizing rules of thumb) is welcome as \
long as it is labeled as guidance and tied to the observed configuration."""


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
            llm = agent_runtime.build_llm(llm_id)
        except ToolkitError as exc:
            yield {'chunk': {'text': 'Cannot start: %s %s' % (exc.message, exc.remediation or '')}}
            return
        messages = agent_runtime.messages_from_query(query, SYSTEM_PROMPT)
        async for chunk in agent_runtime.run_tool_loop(llm, tools, messages, trace):
            yield chunk
