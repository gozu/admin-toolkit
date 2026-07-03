from dataiku.llm.agent_tools import BaseAgentTool

from atk_agent_common import actuator, adapter


class ExecuteAdminActionTool(BaseAgentTool):
    def set_config(self, config, plugin_config):
        self.plugin_config = plugin_config

    def get_descriptor(self, tool):
        return {
            "description": (
                "Execute an admin action that plan-admin-action planned AND the user explicitly "
                "approved in this conversation. Requires the plan's confirm_token (15-min TTL, "
                "bound to the exact action/host/target — any drift is rejected) and confirm=true. "
                "Refuses when the plugin's enable_red_actions kill-switch is off. Every call is "
                "written to the agents.agent_actions audit table."),
            "inputSchema": {
                "$id": "https://dataiku.com/agents/tools/atk/execute-admin-action/input",
                "title": "Input for the execute-admin-action tool",
                "type": "object",
                "properties": {
                    "host": adapter.HOST_PROPERTY,
                    "action": {"type": "string", "enum": list(actuator.ACTIONS)},
                    "target": {"type": "object", "description": "EXACTLY the canonicalTarget returned by plan-admin-action."},
                    "confirm": {"type": "boolean", "description": "Must be true; set only after explicit user approval."},
                    "confirm_token": {"type": "string", "description": "The confirm_token from plan-admin-action."}
                },
                "required": ["action", "target", "confirm", "confirm_token"]
            }
        }

    def invoke(self, input, trace):
        args = input.get("input") or {}
        return adapter.run_tool(
            actuator.execute_admin_action, self.plugin_config,
            host=args.get("host", "local"), action=args.get("action"),
            target=args.get("target"), confirm_flag=bool(args.get("confirm", False)),
            confirm_token=args.get("confirm_token"),
            agent_name="tool:execute-admin-action")
