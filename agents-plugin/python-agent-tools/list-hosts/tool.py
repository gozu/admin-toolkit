from dataiku.llm.agent_tools import BaseAgentTool

from atk_agent_common import adapter, tools_impl


class ListHostsTool(BaseAgentTool):
    def set_config(self, config, plugin_config):
        self.plugin_config = plugin_config

    def get_descriptor(self, tool):
        return {
            "description": (
                "List the DSS hosts this toolkit is connected to (id, label, url). "
                "Call this first when the user mentions an instance by name, or before "
                "any tool call targeting a non-local host. With probe=true, also checks "
                "each host's reachability and Admin Toolkit install state."),
            "inputSchema": {
                "$id": "https://dataiku.com/agents/tools/atk/list-hosts/input",
                "title": "Input for the list-hosts tool",
                "type": "object",
                "properties": {
                    "probe": {
                        "type": "boolean",
                        "description": "Also probe each host: reachable? toolkit plugin installed? (slower)",
                        "default": False
                    }
                }
            }
        }

    def invoke(self, input, trace):
        args = input.get("input") or {}
        return adapter.run_tool(tools_impl.list_hosts, self.plugin_config,
                                probe=bool(args.get("probe", False)))
