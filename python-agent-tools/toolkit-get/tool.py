from dataiku.llm.agent_tools import BaseAgentTool

from atk_agent_common import adapter, read_registry, tools_impl

# Endpoint enumeration + description are GENERATED from the registry — this
# surface can never drift from the in-agent LangChain tool or the registry.
_ENDPOINT_NAMES = [row["name"] for row in read_registry.ENDPOINTS]


class ToolkitGetTool(BaseAgentTool):
    def set_config(self, config, plugin_config):
        self.plugin_config = plugin_config

    def get_descriptor(self, tool):
        return {
            "description": read_registry.tool_description(),
            "inputSchema": {
                "$id": "https://dataiku.com/agents/tools/atk/toolkit-get/input",
                "title": "Input for the toolkit-get tool",
                "type": "object",
                "properties": {
                    "host": adapter.HOST_PROPERTY,
                    "endpoint": {
                        "type": "string",
                        "enum": ["list"] + _ENDPOINT_NAMES,
                        "description": "Registry endpoint name; 'list' returns the manifest.",
                        "default": "list"
                    },
                    "params": {
                        "type": "object",
                        "description": "Endpoint query params — only the manifest's allowed params are accepted."
                    },
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Keep only these top-level output keys."
                    },
                    "top_n": {"type": "integer", "description": "List window size (default 15, max 100).", "default": 15},
                    "page": {"type": "integer", "description": "List window page (1-based).", "default": 1}
                }
            }
        }

    def invoke(self, input, trace):
        args = input.get("input") or {}
        return adapter.run_tool(
            tools_impl.toolkit_get, self.plugin_config,
            host=args.get("host", "local"), endpoint=args.get("endpoint", "list"),
            params=args.get("params"), fields=args.get("fields"),
            top_n=int(args.get("top_n", 15)), page=int(args.get("page", 1)))
