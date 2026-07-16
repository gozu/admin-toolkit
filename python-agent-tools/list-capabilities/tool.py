from dataiku.llm.agent_tools import BaseAgentTool

from atk_agent_common import adapter, tools_impl


class ListCapabilitiesTool(BaseAgentTool):
    def set_config(self, config, plugin_config):
        self.plugin_config = plugin_config

    def get_descriptor(self, tool):
        return {
            "description": (
                "Ground-truth capability map of the Admin Toolkit agents: every sensor and "
                "admin action with its LIVE enablement gate state, the master kill-switch, "
                "and a map of every toolkit webapp page. Answer \"can you X?\" from this — "
                "never claim a capability is missing without checking it first."),
            "inputSchema": {
                "$id": "https://dataiku.com/agents/tools/atk/list-capabilities/input",
                "title": "Input for the list-capabilities tool",
                "type": "object",
                "properties": {}
            }
        }

    def invoke(self, input, trace):
        return adapter.run_tool(tools_impl.list_capabilities, self.plugin_config)
