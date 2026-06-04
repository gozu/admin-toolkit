"""
AI Compute › Model Audit › LLM audit table
Notebook version of the per-LLM-profile audit table (model currency + pricing).
Source of truth: backend.py:10515 api_llm_audit (llm_audit.classify_llm)  ·  frontend LlmAuditPage.tsx
Run inside a DSS Jupyter notebook (admin-toolkit code env). No API key / host needed.

NOTE: fetches the LiteLLM pricing catalog over the network + scans every
project's LLM profiles — slow on large instances.
"""
import dataiku
dataiku.use_plugin_libs("admin-toolkit")
from adk_notebook import get_client, ui, data

_STATUS = {"current": ("current", "green"), "obsolete": ("obsolete", "bold red"),
           "ripoff": ("ripoff", "yellow"), "unknown": ("unknown", "grey62"),
           "not_applicable": ("n/a", "grey62")}


def fetch():
    client = get_client()
    return data.llm_audit_report(client)


def render(payload):
    ui.header("LLM Model Audit", "AI Compute › Model Audit")
    rows = payload.get("rows") or []
    scan_errors = payload.get("scanErrors") or []
    if scan_errors:
        ui.note(f"{len(scan_errors)} project scan error(s); LLM profile list may be incomplete.", "WARNING")
    if not rows:
        level = "WARNING" if scan_errors else "INFO"
        ui.note("No LLM profiles found across projects.", level)
        return
    # Most-actionable first: obsolete/ripoff, then unknown, then current.
    order = {"obsolete": 0, "ripoff": 1, "unknown": 2, "current": 3, "not_applicable": 4}
    rows = sorted(rows, key=lambda r: (order.get(r.get("status"), 5), r.get("projectKey") or ""))
    table = [
        [r.get("projectKey", ""),
         r.get("friendlyNameShort") or r.get("friendlyName") or r.get("llmId") or "",
         r.get("type", ""), r.get("effectiveModel") or r.get("rawModel") or "—",
         _STATUS.get(r.get("status"), (r.get("status"), "white")),
         r.get("currentModel") or ""]
        for r in rows
    ]
    ui.data_table("LLM profiles", [
        "Project", "Profile", "Type", "Model", "Status", "Current model"], table,
        caption=f"{len(rows)} profiles")


if __name__ != "__skip__":
    render(fetch())
