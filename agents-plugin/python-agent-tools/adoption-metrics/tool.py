from dataiku.llm.agent_tools import BaseAgentTool

from atk_agent_common import adapter, tools_impl


class AdoptionMetricsTool(BaseAgentTool):
    def set_config(self, config, plugin_config):
        self.plugin_config = plugin_config

    def get_descriptor(self, tool):
        return {
            "description": (
                "Adoption and engagement metrics for one DSS host, built from persistent "
                "project git history (not the short-lived audit log): monthly active-builder "
                "and commit trend with latest-month deltas, totals (projects, active projects, "
                "builders, people-per-project), repeat-builder split, top builders, top groups, "
                "and new-user cohorts."),
            "inputSchema": {
                "$id": "https://dataiku.com/agents/tools/atk/adoption-metrics/input",
                "title": "Input for the adoption-metrics tool",
                "type": "object",
                "properties": {
                    "host": adapter.HOST_PROPERTY,
                    "window_months": {
                        "type": "integer",
                        "description": "Months of trend/cohort history to return (default 12).",
                        "default": 12
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "Rows in top-builders/top-groups lists (default 10).",
                        "default": 10
                    }
                }
            }
        }

    def invoke(self, input, trace):
        args = input.get("input") or {}
        return adapter.run_tool(
            tools_impl.adoption_metrics, self.plugin_config,
            host=args.get("host", "local"),
            window_months=int(args.get("window_months", 12)),
            top_n=int(args.get("top_n", 10)))
