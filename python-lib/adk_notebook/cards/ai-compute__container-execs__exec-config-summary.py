"""
AI Compute › Container Execs › Execution-config summary
Notebook version of the instance container-execution-config card.
Source of truth: backend.py:9501 api_container_execs (_cex_scan)  ·  frontend ResourcesPage.tsx
Run inside a DSS Jupyter notebook (admin-toolkit code env). No API key / host needed.

NOTE: scans every project for overrides — slow on large instances.
"""
import dataiku
dataiku.use_plugin_libs("admin-toolkit")
from adk_notebook import get_client, ui, data


def fetch():
    client = get_client()
    return data.container_execs(client)


def render(payload):
    ui.header("Container Execution Configs", "AI Compute › Container Execs")
    s = payload.get("summary") or {}
    ui.stat_cards([
        {"label": "Configs", "value": s.get("configCount", 0), "style": "cyan"},
        {"label": "Default config", "value": payload.get("globalDefaultConfig") or "—", "style": "green"},
        {"label": "Explicit overrides", "value": s.get("explicitUsageCount", 0),
         "style": "yellow" if s.get("explicitUsageCount") else "grey62"},
        {"label": "Projects w/ overrides", "value": s.get("projectUsageCount", 0), "style": "white"},
        {"label": "Scan errors", "value": payload.get("scanErrorCount", 0),
         "style": "red" if payload.get("scanErrorCount") else "grey62"},
    ])
    if payload.get("scanErrors"):
        ui.note(f"{len(payload.get('scanErrors') or [])} scan error(s); override counts may be incomplete.", "WARNING")
    configs = payload.get("configs") or []
    if not configs:
        ui.note("No execution configs defined on this instance.", "WARNING")
        return
    rows = [
        [c.get("name", ""), c.get("type", ""), c.get("workloadType") or "—",
         c.get("kubernetesNamespace") or "—",
         (c.get("name") == payload.get("globalDefaultConfig")) and ("default", "green") or ("", "grey62")]
        for c in configs
    ]
    ui.data_table("Execution configs", ["Name", "Type", "Workload", "K8s namespace", "Default"], rows)


if __name__ != "__skip__":
    render(fetch())
