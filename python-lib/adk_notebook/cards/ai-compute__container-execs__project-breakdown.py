"""
AI Compute › Container Execs › Per-project override breakdown
Notebook version of the per-project explicit-override list.
Source of truth: backend.py:9501 api_container_execs (_cex_scan projectRows)  ·  frontend ResourcesPage.tsx
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
    ui.header("Container Exec — Per-Project Overrides", "AI Compute › Container Execs")
    project_rows = payload.get("projectRows") or []
    scan_errors = payload.get("scanErrors") or []
    if scan_errors:
        ui.note(f"{len(scan_errors)} scan error(s); per-project list may be incomplete.", "WARNING")
    if not project_rows:
        ui.note("No projects override the instance container-exec defaults.", "WARNING" if scan_errors else "SUCCESS")
        return
    rows = []
    for p in project_rows:
        for o in (p.get("projectOverrides") or []) + (p.get("jobOverrides") or []):
            rows.append([
                p.get("projectKey", ""), o.get("objectType", ""), o.get("objectName", ""),
                (o.get("overrideLevel", ""), "yellow" if o.get("overrideLevel") == "job" else "cyan"),
                o.get("effectiveContainerConf") or o.get("containerConf") or "—",
            ])
    ui.data_table("Explicit overrides", [
        "Project", "Object type", "Object", "Level", "Container config"], rows,
        caption=f"{len(project_rows)} projects override defaults")


if __name__ != "__skip__":
    render(fetch())
