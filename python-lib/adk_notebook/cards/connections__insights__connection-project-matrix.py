"""
Connections › Insights › Connection-project matrix
Notebook version of the connection × project usage card.
Source of truth: backend.py:5669 api_connection_usages  ·  frontend ConnectionsInsightsPage.tsx
Run inside a DSS Jupyter notebook (admin-toolkit code env). No API key / host needed.

NOTE: scans every project's datasets + LLM recipes via the SDK — slow on large
instances (the same work the webapp streams behind a progress bar).
"""
import dataiku
dataiku.use_plugin_libs("admin-toolkit")
from adk_notebook import get_client, ui, data


def fetch():
    client = get_client()
    return data.connection_usages(client)


def render(payload):
    ui.header("Connection ↔ Project Usage", "Connections › Insights")
    datasets = payload.get("datasetUsages") or []
    llms = payload.get("llmUsages") or []
    scan_errors = payload.get("scanErrors") or []
    if scan_errors:
        ui.note(f"{len(scan_errors)} scan error(s); connection usage may be incomplete.", "WARNING")

    if datasets:
        rows = [[d.get("name", ""), d.get("type", ""), d.get("projectCount", 0), d.get("datasetCount", 0)]
                for d in datasets]
        ui.data_table("Dataset connections", [
            "Connection", "Type", {"name": "Projects", "justify": "right"},
            {"name": "Datasets", "justify": "right"}], rows)
    else:
        level = "WARNING" if scan_errors else "INFO"
        ui.note("No dataset-connection usages found.", level)

    if llms:
        rows = [[d.get("name", ""), d.get("type", ""), d.get("projectCount", 0), d.get("recipeCount", 0)]
                for d in llms]
        ui.data_table("LLM-recipe connections", [
            "Connection", "Type", {"name": "Projects", "justify": "right"},
            {"name": "Recipes", "justify": "right"}], rows)


if __name__ != "__skip__":
    render(fetch())
