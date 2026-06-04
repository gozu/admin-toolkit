"""
AI Compute › Container Execs › Mode breakdown tables
Notebook version of the by-mode / by-object-type / by-config breakdown.
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
    ui.header("Container Exec — Override Breakdown", "AI Compute › Container Execs")
    s = payload.get("summary") or {}
    by_mode = s.get("byMode") or {}
    by_type = s.get("byObjectType") or {}
    by_config = s.get("byConfig") or {}
    scan_errors = payload.get("scanErrors") or []
    if scan_errors:
        ui.note(f"{len(scan_errors)} scan error(s); breakdown may be incomplete.", "WARNING")
    if not (by_mode or by_type or by_config):
        ui.note("No explicit container-exec overrides found.", "WARNING" if scan_errors else "SUCCESS")
        return
    if by_type:
        ui.bar_list("Overrides by object type", [{"label": k, "value": v} for k, v in by_type.items()],
                    value_fmt=lambda v: f"{v:.0f}")
    if by_config:
        ui.bar_list("Overrides by execution config", [{"label": k, "value": v} for k, v in by_config.items()],
                    value_fmt=lambda v: f"{v:.0f}")
    if by_mode:
        ui.data_table("By container mode", ["Mode", {"name": "Count", "justify": "right"}],
                      [[k, v] for k, v in sorted(by_mode.items(), key=lambda kv: kv[1], reverse=True)])


if __name__ != "__skip__":
    render(fetch())
