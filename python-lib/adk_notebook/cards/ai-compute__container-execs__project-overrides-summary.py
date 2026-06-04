"""
AI Compute › Container Execs › Project-overrides summary
Notebook version of the project/job override count card.
Source of truth: backend.py:9501 api_container_execs (_cex_scan summary)  ·  frontend ResourcesPage.tsx
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
    ui.header("Container Exec — Override Summary", "AI Compute › Container Execs")
    s = payload.get("summary") or {}
    ui.stat_cards([
        {"label": "Projects scanned", "value": s.get("projectCount", 0), "style": "white"},
        {"label": "Projects w/ overrides", "value": s.get("projectUsageCount", 0),
         "style": "yellow" if s.get("projectUsageCount") else "green"},
        {"label": "Project-level overrides", "value": s.get("projectOverrideRowCount", 0), "style": "cyan"},
        {"label": "Job-level overrides", "value": s.get("jobOverrideCount", 0), "style": "magenta"},
        {"label": "Scan errors", "value": payload.get("scanErrorCount", 0),
         "style": "red" if payload.get("scanErrorCount") else "grey62"},
    ])
    if payload.get("scanErrors"):
        ui.note(f"{len(payload.get('scanErrors') or [])} scan error(s); override summary may be incomplete.", "WARNING")
    nc = payload.get("nonCarrierCounts") or {}
    nonzero = {k: v for k, v in nc.items() if v}
    if nonzero:
        ui.bar_list("Non-carrier objects (no container-exec selection)",
                    [{"label": k, "value": v, "style": "grey62"} for k, v in nonzero.items()],
                    value_fmt=lambda v: f"{v:.0f}")


if __name__ != "__skip__":
    render(fetch())
