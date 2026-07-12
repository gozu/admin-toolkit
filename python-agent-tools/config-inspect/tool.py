from dataiku.llm.agent_tools import BaseAgentTool

from atk_agent_common import adapter, domain_registry, tools_impl

# Domain enumeration + description are GENERATED from the registry — this
# surface can never drift from the in-agent LangChain tool or the registry.
_DOMAIN_NAMES = [row['name'] for row in domain_registry.DOMAINS]
_DETAIL_MODES = sorted({mode for row in domain_registry.DOMAINS
                        for mode in row['detail_modes']})


class ConfigInspectTool(BaseAgentTool):
    def set_config(self, config, plugin_config):
        self.plugin_config = plugin_config

    def get_descriptor(self, tool):
        return {
            "description": tools_impl.SENSOR_DESCRIPTIONS['config_inspect'],
            "inputSchema": {
                "$id": "https://dataiku.com/agents/tools/atk/config-inspect/input",
                "title": "Input for the config-inspect tool",
                "type": "object",
                "properties": {
                    "host": adapter.HOST_PROPERTY,
                    "domain": {
                        "type": "string",
                        "enum": ["list"] + _DOMAIN_NAMES,
                        "description": "Domain to inspect, or 'list' for the cheap "
                                       "manifest of every domain."
                    },
                    "detail": {
                        "type": "string",
                        "enum": _DETAIL_MODES,
                        "description": "Optional drill-down; each domain's manifest "
                                       "entry lists its detail modes. Slower."
                    },
                    "name_filter": {
                        "type": "string",
                        "description": "Case-insensitive substring filter on names/ids/"
                                       "types; the PROJECT KEY for project-scoped domains."
                    },
                    "top_n": {"type": "integer", "description": "Rows per list (default 15).", "default": 15},
                    "page": {"type": "integer", "description": "Page through long listings (default 1).", "default": 1},
                    "fields": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Keep only these top-level output keys (see the manifest's fields)."
                    }
                },
                "required": ["domain"]
            }
        }

    def invoke(self, input, trace):
        args = input.get("input") or {}
        return adapter.run_tool(
            tools_impl.config_inspect, self.plugin_config,
            host=args.get("host", "local"), domain=args.get("domain", "connections"),
            detail=args.get("detail"), name_filter=args.get("name_filter"),
            top_n=int(args.get("top_n", 15)), page=int(args.get("page", 1)),
            fields=args.get("fields"))
