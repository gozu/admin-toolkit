"""
Connections › Usage › Connection usage (datasets / LLM / filesystem)
Notebook version of the connection-usage summary card.
Source of truth: backend.py:5669 api_connection_usages  ·  frontend ConnectionsUsagePage.tsx
Run inside a DSS Jupyter notebook (admin-toolkit code env). No API key / host needed.

NOTE: scans every project via the SDK — slow on large instances.
"""
import dataiku
dataiku.use_plugin_libs("admin-toolkit")
from adk_notebook import get_client, ui, data


def fetch():
    client = get_client()
    return data.connection_usages(client)


def render(payload):
    ui.header("Connection Usage", "Connections › Usage")
    datasets = payload.get("datasetUsages") or []
    llms = payload.get("llmUsages") or []
    fs = payload.get("localFilesystemUsages") or []
    scan_errors = payload.get("scanErrors") or []
    ui.stat_cards([
        {"label": "Dataset connections", "value": len(datasets), "style": "cyan"},
        {"label": "LLM connections", "value": len(llms), "style": "green"},
        {"label": "Local-FS objects", "value": len(fs), "style": "yellow" if fs else "grey62"},
        {"label": "Scan errors", "value": len(scan_errors), "style": "red" if scan_errors else "grey62"},
    ])
    if scan_errors:
        ui.note(f"{len(scan_errors)} scan error(s); counts may be incomplete.", "WARNING")
    if datasets:
        ui.bar_list("Datasets per connection", [
            {"label": d.get("name", "?"), "value": d.get("datasetCount", 0)} for d in datasets
        ], value_fmt=lambda v: f"{v:.0f}")
    if llms:
        ui.bar_list("LLM recipes per connection", [
            {"label": d.get("name", "?"), "value": d.get("recipeCount", 0), "style": "green"} for d in llms
        ], value_fmt=lambda v: f"{v:.0f}")


if __name__ != "__skip__":
    render(fetch())
