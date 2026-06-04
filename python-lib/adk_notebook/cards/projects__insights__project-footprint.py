"""
Projects › Insights › Project footprint
Notebook version of the per-project disk footprint card.
Source of truth: backend.py:7868 api_project_footprint  ·  frontend ProjectsPage.tsx
Run inside a DSS Jupyter notebook (admin-toolkit code env). No API key / host needed.

NOTE: walks every project's footprint via the SDK — slow on large instances
(the same heavy work the webapp parallelises behind a progress bar).
"""
import dataiku
dataiku.use_plugin_libs("admin-toolkit")
from adk_notebook import get_client, ui, data
from adk_notebook.parse import format_size_human


def fetch():
    client = get_client()
    return data.project_footprint(client)


_HEALTH_STYLE = {"green": "green", "yellow": "yellow", "orange": "yellow",
                 "red": "red", "angry-red": "bold red"}


def render(payload):
    ui.header("Project Footprint", "Projects › Insights")
    summary = payload.get("summary") or {}
    projects = payload.get("projects") or []
    scan_errors = payload.get("scanErrors") or []
    ui.stat_cards([
        {"label": "Projects", "value": summary.get("projectCount", len(projects)), "style": "cyan"},
        {"label": "Avg size", "value": f"{summary.get('instanceAvgProjectGB', 0):.2f} GB", "style": "white"},
        {"label": "Avg risk", "value": f"{summary.get('instanceProjectRiskAvg', 0):.2f}", "style": "yellow"},
        {"label": "Footprint errors", "value": len(scan_errors), "style": "red" if scan_errors else "grey62"},
    ])
    if scan_errors:
        ui.note(f"{len(scan_errors)} project footprint error(s); project table may be incomplete.", "WARNING")
    if not projects:
        ui.note("No project footprint data (footprint API unavailable?).", "WARNING")
        return
    ui.bar_list("Top projects by size", [
        {"label": p.get("name", p.get("projectKey", "?")), "value": p.get("totalBytes", 0)}
        for p in projects[:15]
    ], value_fmt=lambda v: format_size_human(int(v)))
    rows = [
        [p.get("name", ""), p.get("owner", ""),
         format_size_human(int(p.get("totalBytes", 0))),
         (p.get("codeEnvCount", 0), _HEALTH_STYLE.get(p.get("codeEnvHealth"), "white")),
         f"{p.get('projectRisk', 0):.2f}",
         (p.get("projectSizeHealth", ""), _HEALTH_STYLE.get(p.get("projectSizeHealth"), "white"))]
        for p in projects
    ]
    ui.data_table("Projects", [
        "Project", "Owner", {"name": "Total size", "justify": "right"},
        {"name": "Code envs", "justify": "right"}, {"name": "Risk", "justify": "right"},
        "Size health",
    ], rows)


if __name__ != "__skip__":
    render(fetch())
