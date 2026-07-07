from dataiku.llm.agent_tools import BaseAgentTool

from atk_agent_common import actuator, adapter
from atk_agent_common import actions as actions_registry


class PlanAdminActionTool(BaseAgentTool):
    def set_config(self, config, plugin_config):
        self.plugin_config = plugin_config

    def get_descriptor(self, tool):
        return {
            "description": (
                "Plan an admin action WITHOUT executing it. Gathers the exact target and "
                "blast radius from read-only scans (size, usage, inactivity, backup folder) "
                "and mints a 15-minute confirm_token bound to this exact action/host/target. "
                "This is the mandatory first step for any mutation — show the returned plan "
                "to the user verbatim and wait for explicit approval. Actions: %s. "
                "Target shapes: %s. %s"
                % (', '.join(actuator.ACTIONS), actuator.TARGET_SHAPES,
                   actions_registry.BATCH_NOTE)),
            "inputSchema": {
                "$id": "https://dataiku.com/agents/tools/atk/plan-admin-action/input",
                "title": "Input for the plan-admin-action tool",
                "type": "object",
                "properties": {
                    "host": adapter.HOST_PROPERTY,
                    "action": {"type": "string", "enum": list(actuator.ACTIONS)},
                    "target": {"type": "object", "description": "Action-specific target (see tool description)."},
                    "targets": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Several targets for ONE batched plan (batchable actions only). Pass either target or targets."
                    },
                    "params": {"type": "object", "description": "Optional action-specific extras."}
                },
                "required": ["action"]
            }
        }

    def invoke(self, input, trace):
        args = input.get("input") or {}
        return adapter.run_tool(
            actuator.plan_admin_action, self.plugin_config,
            host=args.get("host", "local"), action=args.get("action"),
            target=args.get("target"), targets=args.get("targets"),
            params=args.get("params"))
