from dataiku.llm.agent_tools import BaseAgentTool

from atk_agent_common import adapter, tools_impl


class ComputeCostTool(BaseAgentTool):
    def set_config(self, config, plugin_config):
        self.plugin_config = plugin_config

    def get_descriptor(self, tool):
        return {
            "description": (
                "Compute and LLM cost for one DSS host from its Compute Resource Usage audit "
                "records: totals (CPU-hours, memory GB-hours, LLM USD), the covered time span, "
                "and top consumers grouped by project, user, or context type. Coverage is "
                "limited to the instance's rolling audit-log retention — the span field says "
                "exactly what period the numbers describe."),
            "inputSchema": {
                "$id": "https://dataiku.com/agents/tools/atk/compute-cost/input",
                "title": "Input for the compute-cost tool",
                "type": "object",
                "properties": {
                    "host": adapter.HOST_PROPERTY,
                    "group_by": {
                        "type": "string",
                        "enum": ["project", "user", "context_type"],
                        "description": "Dimension for the top-consumers rows (default project).",
                        "default": "project"
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "Rows returned (default 10).",
                        "default": 10
                    }
                }
            }
        }

    def invoke(self, input, trace):
        args = input.get("input") or {}
        return adapter.run_tool(
            tools_impl.compute_cost, self.plugin_config,
            host=args.get("host", "local"),
            group_by=args.get("group_by", "project"),
            top_n=int(args.get("top_n", 10)))
