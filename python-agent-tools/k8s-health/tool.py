from dataiku.llm.agent_tools import BaseAgentTool

from atk_agent_common import adapter, tools_impl


class K8sHealthTool(BaseAgentTool):
    def set_config(self, config, plugin_config):
        self.plugin_config = plugin_config

    def get_descriptor(self, tool):
        return {
            "description": (
                "Kubernetes insight for a DSS host: lists DSS-known clusters with state, "
                "probes reachability of each (kubectl version sweep), and — when `cluster` "
                "is given — runs the full audit of that cluster (node pressure, unhealthy "
                "pods, rule findings). The deep audit can take a minute or more."),
            "inputSchema": {
                "$id": "https://dataiku.com/agents/tools/atk/k8s-health/input",
                "title": "Input for the k8s-health tool",
                "type": "object",
                "properties": {
                    "host": adapter.HOST_PROPERTY,
                    "cluster": {"type": "string", "description": "Cluster id for a deep audit (from the clusters list)."},
                    "top_n": {"type": "integer", "description": "Rows per list (default 10).", "default": 10}
                }
            }
        }

    def invoke(self, input, trace):
        args = input.get("input") or {}
        return adapter.run_tool(
            tools_impl.k8s_health, self.plugin_config,
            host=args.get("host", "local"), cluster=args.get("cluster"),
            top_n=int(args.get("top_n", 10)))
