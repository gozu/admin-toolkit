from dataiku.llm.agent_tools import BaseAgentTool

from atk_agent_common import adapter, tools_impl


class StorageFootprintTool(BaseAgentTool):
    def set_config(self, config, plugin_config):
        self.plugin_config = plugin_config

    def get_descriptor(self, tool):
        return {
            "description": (
                "Storage footprint of a DSS host: total/average project size, the largest "
                "projects (with what makes them big), and cleanup candidates that are both "
                "inactive and large. Heavy scan — a cold cache may return status=scan_running "
                "with progress; re-invoke in a few minutes and it will hit the warm cache."),
            "inputSchema": {
                "$id": "https://dataiku.com/agents/tools/atk/storage-footprint/input",
                "title": "Input for the storage-footprint tool",
                "type": "object",
                "properties": {
                    "host": adapter.HOST_PROPERTY,
                    "top_n": {"type": "integer", "description": "Rows per list (default 10).", "default": 10},
                    "min_size_gb": {"type": "number", "description": "Only consider projects at least this big (GB).", "default": 0}
                }
            }
        }

    def invoke(self, input, trace):
        args = input.get("input") or {}
        return adapter.run_tool(
            tools_impl.storage_footprint, self.plugin_config,
            host=args.get("host", "local"), top_n=int(args.get("top_n", 10)),
            min_size_gb=float(args.get("min_size_gb", 0)))
