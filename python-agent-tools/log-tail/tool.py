from dataiku.llm.agent_tools import BaseAgentTool

from atk_agent_common import adapter, tools_impl


class LogTailTool(BaseAgentTool):
    def set_config(self, config, plugin_config):
        self.plugin_config = plugin_config

    def get_descriptor(self, tool):
        return {
            "description": (
                "Raw backend.log tail for a DSS host: the last N lines verbatim (window = the "
                "log's last ~100K characters), or — with `pattern` — the matching lines only. "
                "v1 serves backend.log only. For grouped/deduplicated error signatures use the "
                "log-errors tool instead."),
            "inputSchema": {
                "$id": "https://dataiku.com/agents/tools/atk/log-tail/input",
                "title": "Input for the log-tail tool",
                "type": "object",
                "properties": {
                    "host": adapter.HOST_PROPERTY,
                    "lines": {"type": "integer", "description": "Lines returned (default 200, max 1000).", "default": 200},
                    "pattern": {"type": "string", "description": "Optional case-insensitive regex — return matching lines only."},
                    "log": {"type": "string", "description": "Log name (v1: only 'backend.log').", "default": "backend.log"}
                }
            }
        }

    def invoke(self, input, trace):
        args = input.get("input") or {}
        return adapter.run_tool(
            tools_impl.log_tail, self.plugin_config,
            host=args.get("host", "local"), lines=int(args.get("lines", 200)),
            pattern=args.get("pattern"), log=args.get("log", "backend.log"))
