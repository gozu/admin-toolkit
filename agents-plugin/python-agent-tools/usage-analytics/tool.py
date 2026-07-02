from dataiku.llm.agent_tools import BaseAgentTool

from atk_agent_common import adapter, tools_impl


class UsageAnalyticsTool(BaseAgentTool):
    def set_config(self, config, plugin_config):
        self.plugin_config = plugin_config

    def get_descriptor(self, tool):
        return {
            "description": (
                "Persistent (Postgres-backed) usage analytics from the toolkit's Story "
                "pipeline: 'user-activity' (daily active/developing users), 'event-counts' "
                "(audit event volumes by type), 'licenses', 'inventory'. Unlike compute-cost, "
                "this survives audit-log rotation — use it for trends. `host` filters by "
                "instance id (data for the whole fleet lives on the hub)."),
            "inputSchema": {
                "$id": "https://dataiku.com/agents/tools/atk/usage-analytics/input",
                "title": "Input for the usage-analytics tool",
                "type": "object",
                "properties": {
                    "metric": {
                        "type": "string",
                        "enum": ["user-activity", "event-counts", "licenses", "inventory"],
                        "description": "Which analytics series to fetch."
                    },
                    "host": {
                        "type": "string",
                        "description": "Optional instance id filter (Story stores all instances on the hub)."
                    },
                    "days": {"type": "integer", "description": "Lookback window in days (default 30).", "default": 30}
                },
                "required": ["metric"]
            }
        }

    def invoke(self, input, trace):
        args = input.get("input") or {}
        return adapter.run_tool(
            tools_impl.usage_analytics, self.plugin_config,
            metric=args.get("metric", "user-activity"),
            host=args.get("host"), days=int(args.get("days", 30)))
