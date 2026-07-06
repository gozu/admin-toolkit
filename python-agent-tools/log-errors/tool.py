from dataiku.llm.agent_tools import BaseAgentTool

from atk_agent_common import adapter, tools_impl


class LogErrorsTool(BaseAgentTool):
    def set_config(self, config, plugin_config):
        self.plugin_config = plugin_config

    def get_descriptor(self, tool):
        return {
            "description": (
                "Backend.log diagnostics for a DSS host: grouped error signatures with "
                "timestamps and stats. Pass `pattern` (regex) to grep the recent raw log "
                "tail (last ~100K chars, up to 80 matching lines) — use that to chase a "
                "specific exception or component."),
            "inputSchema": {
                "$id": "https://dataiku.com/agents/tools/atk/log-errors/input",
                "title": "Input for the log-errors tool",
                "type": "object",
                "properties": {
                    "host": adapter.HOST_PROPERTY,
                    "top_n": {"type": "integer", "description": "Max error groups (default 10).", "default": 10},
                    "pattern": {"type": "string", "description": "Optional case-insensitive regex to grep the raw tail."},
                    "raw": {"type": "boolean", "description": "Return the last 80 raw tail lines instead of parsed groups.", "default": False}
                }
            }
        }

    def invoke(self, input, trace):
        args = input.get("input") or {}
        return adapter.run_tool(
            tools_impl.log_errors, self.plugin_config,
            host=args.get("host", "local"), top_n=int(args.get("top_n", 10)),
            pattern=args.get("pattern"), raw=bool(args.get("raw", False)))
