import json

from dataiku.llm.python import BaseLLM

from atk_agent_common import adapter, agent_runtime, agent_tools
from atk_agent_common.errors import ToolkitError
from atk_agent_common.triage import sweep

SYSTEM_PROMPT = """You are the Admin Toolkit health-triage agent for a fleet of Dataiku DSS instances.

Ground rules:
- Answer ONLY from tool output. Never invent metrics, host names, or issues. If a tool \
returns an error payload, relay its message and remediation instead of guessing.
- Cite the host id and the tool that produced each number or claim, e.g. "(instance-health, host=akaos-vm)".
- A tool result with status=scan_running means the data is still warming: say so and \
suggest retrying in a few minutes; do not treat it as a failure or as healthy.

When the user asks for a sweep / triage / fleet check / "how are my instances":
1. Call the triage_sweep tool ONCE — it deterministically scores every host with the same \
0-100 health score the toolkit UI shows and flags hosts under the threshold. Do not \
re-derive or second-guess the ranking.
2. For each flagged host (worst first, at most {max_recommendations}), draft ONE concrete \
recommendation grounded in its topIssues and signals (log errors, sanity check). Structure \
per host: score + status, top 3 issues, your recommendation, the suggested next action, \
and the evidence (issue ids / log signatures you used).
3. Close with a one-paragraph fleet summary.

For ad-hoc questions, use the sensor tools directly and keep the same grounding rules.
Health scores are 0-100 (higher is better); by default <80 is a warning, <50 critical."""


class HealthTriageAgent(BaseLLM):
    def __init__(self):
        pass

    def set_config(self, config, plugin_config):
        self.config = config or {}
        self.plugin_config = plugin_config or {}

    def _build(self):
        client = adapter.build_client(self.plugin_config)
        settings = client.settings
        llm_id = (self.config.get('llm_id') or '').strip() or settings.get('default_llm_id')
        if not llm_id:
            raise ToolkitError('No LLM configured.',
                               remediation='Set llm_id on the agent or default_llm_id in the plugin settings.')
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
        return agent_runtime.build_llm(llm_id), tools

    async def aprocess_stream(self, query, settings, trace):
        try:
            llm, tools = self._build()
        except ToolkitError as exc:
            yield {'chunk': {'text': 'Cannot start: %s %s' % (exc.message, exc.remediation or '')}}
            return
        prompt = SYSTEM_PROMPT.replace('{max_recommendations}',
                                       str(self.config.get('max_recommendations') or 5))
        messages = agent_runtime.messages_from_query(query, prompt)
        async for chunk in agent_runtime.run_tool_loop(llm, tools, messages, trace):
            yield chunk
