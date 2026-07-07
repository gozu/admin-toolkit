from dataiku.llm.agent_tools import BaseAgentTool

from atk_agent_common import adapter, tools_impl


class ConfigInspectTool(BaseAgentTool):
    def set_config(self, config, plugin_config):
        self.plugin_config = plugin_config

    def get_descriptor(self, tool):
        return {
            "description": (
                "Inspect one configuration domain of a DSS host. Domains: 'connections'"
                "(counts by type + names; detail='health' probes each connection), 'code-"
                "envs' (version counts, deprecated-Python envs, unused envs, largest; heavy"
                "scan — may return scan_running), 'plugins' (installed list, dev plugins;"
                "detail='usage' adds projects-using counts, slower), 'llms' (LLM Mesh models"
                "grouped by connection), 'clusters' (attached k8s clusters incl. unavailable"
                "detach candidates; detail='health' adds the reachability sweep), 'users'"
                "(logins, enabled state, groups), 'api-keys' (personal + global, secrets"
                "never shown), and the per-project domains 'scenarios', 'webapps',"
                "'notebooks', 'jobs', 'datasets' (rows carry exposed=true when shared to "
                "other projects; detail='usage' adds flow lineage per dataset — producing/"
                "consuming recipes, webapp/scenario name-references, plus 'unreferenced' "
                "and 'deleteCandidates' rollups for cleanup grounding, slower) — for "
                "those, name_filter is the PROJECT KEY (required)."
                "Use name_filter as a substring filter elsewhere."),
            "inputSchema": {
                "$id": "https://dataiku.com/agents/tools/atk/config-inspect/input",
                "title": "Input for the config-inspect tool",
                "type": "object",
                "properties": {
                    "host": adapter.HOST_PROPERTY,
                    "domain": {
                        "type": "string",
                        "enum": ["connections", "code-envs", "plugins", "llms", "clusters", "scenarios", "webapps", "users", "api-keys", "notebooks", "jobs", "datasets"],
                        "description": "Configuration domain to inspect."
                    },
                    "detail": {
                        "type": "string",
                        "enum": ["health", "usage"],
                        "description": "Optional drill-down: 'health' (connections, clusters), 'usage' (plugins, datasets). Slower."
                    },
                    "name_filter": {
                        "type": "string",
                        "description": "Case-insensitive substring filter on names/ids/types."
                    },
                    "top_n": {"type": "integer", "description": "Rows per list (default 15).", "default": 15}
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
            top_n=int(args.get("top_n", 15)))
