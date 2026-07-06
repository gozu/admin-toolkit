from dataiku.llm.agent_tools import BaseAgentTool

from atk_agent_common import adapter, tools_impl


class DbHealthTool(BaseAgentTool):
    def set_config(self, config, plugin_config):
        self.plugin_config = plugin_config

    def get_descriptor(self, tool):
        return {
            "description": (
                "PostgreSQL runtime-database health for a DSS host. Views: 'overview' "
                "(size, version, dead/live tuples, writability), 'tables' (worst tables by "
                "dead tuples — vacuum candidates), 'per-project' (usage attribution). "
                "Defaults to the configured RuntimeDB connection; pass `connection` to "
                "inspect another PostgreSQL connection."),
            "inputSchema": {
                "$id": "https://dataiku.com/agents/tools/atk/db-health/input",
                "title": "Input for the db-health tool",
                "type": "object",
                "properties": {
                    "host": adapter.HOST_PROPERTY,
                    "view": {
                        "type": "string",
                        "enum": ["overview", "tables", "per-project"],
                        "description": "Which report to fetch (default overview).",
                        "default": "overview"
                    },
                    "connection": {"type": "string", "description": "PostgreSQL connection name (default: the configured RuntimeDB)."},
                    "top_n": {"type": "integer", "description": "Rows per list (default 10).", "default": 10}
                }
            }
        }

    def invoke(self, input, trace):
        args = input.get("input") or {}
        return adapter.run_tool(
            tools_impl.db_health, self.plugin_config,
            host=args.get("host", "local"), view=args.get("view", "overview"),
            connection=args.get("connection"), top_n=int(args.get("top_n", 10)))
