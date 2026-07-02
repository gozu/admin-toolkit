from dataiku.llm.agent_tools import BaseAgentTool

from atk_agent_common import adapter, tools_impl


class InstanceHealthTool(BaseAgentTool):
    def set_config(self, config, plugin_config):
        self.plugin_config = plugin_config

    def get_descriptor(self, tool):
        return {
            "description": (
                "Health snapshot of one DSS host: version/OS/memory/filesystems, DSS "
                "sanity-check errors and warnings, Java component heap sizes, and a "
                "ranked topIssues list derived from the instance's own thresholds. "
                "Use `sections` to fetch only what you need."),
            "inputSchema": {
                "$id": "https://dataiku.com/agents/tools/atk/instance-health/input",
                "title": "Input for the instance-health tool",
                "type": "object",
                "properties": {
                    "host": adapter.HOST_PROPERTY,
                    "sections": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["system", "sanity", "java", "issues", "score"]},
                        "description": "Which sections to include (default: system/sanity/java/issues)."
                    },
                    "include_score": {
                        "type": "boolean",
                        "description": ("Compute the 0-100 UI health score (6 weighted categories). "
                                        "Slower: forces the heavy code-envs + project-footprint scans; "
                                        "may return status=scan_running on a cold cache — re-invoke later."),
                        "default": False
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "Max issues returned (default 20).",
                        "default": 20
                    }
                }
            }
        }

    def invoke(self, input, trace):
        args = input.get("input") or {}
        return adapter.run_tool(
            tools_impl.instance_health, self.plugin_config,
            host=args.get("host", "local"),
            sections=args.get("sections"),
            top_n=int(args.get("top_n", 20)),
            include_score=bool(args.get("include_score", False)))
