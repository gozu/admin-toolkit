"""
AI Compute › Model Audit › LLM status summary
Notebook version of the model-audit status counters.
Source of truth: backend.py:10515 api_llm_audit (llm_audit.summarize_rows)  ·  frontend LlmAuditPage.tsx
Run inside a DSS Jupyter notebook (admin-toolkit code env). No API key / host needed.

NOTE: fetches the LiteLLM pricing catalog over the network + scans every
project's LLM profiles — slow on large instances.
"""
import dataiku
dataiku.use_plugin_libs("admin-toolkit")
from adk_notebook import get_client, ui, data

_STATUS_STYLE = {"current": "green", "obsolete": "red", "ripoff": "yellow",
                 "unknown": "grey62", "not_applicable": "grey62"}


def fetch():
    client = get_client()
    return data.llm_audit_report(client)


def render(payload):
    ui.header("LLM Model Audit — Summary", "AI Compute › Model Audit")
    summary = payload.get("summary") or {}
    counts = summary.get("countsByStatus") or {}
    scan_errors = payload.get("scanErrors") or []
    ui.stat_cards([
        {"label": "LLM profiles", "value": summary.get("llmsTotal", 0), "style": "cyan"},
        {"label": "Current", "value": counts.get("current", 0), "style": "green"},
        {"label": "Obsolete", "value": counts.get("obsolete", 0),
         "style": "red" if counts.get("obsolete") else "grey62"},
        {"label": "Unknown", "value": counts.get("unknown", 0), "style": "grey62"},
        {"label": "Scan errors", "value": len(scan_errors), "style": "red" if scan_errors else "grey62"},
    ])
    if scan_errors:
        ui.note(f"{len(scan_errors)} project scan error(s); summary may be incomplete.", "WARNING")
    if counts:
        ui.bar_list("LLM profiles by status", [
            {"label": k, "value": v, "style": _STATUS_STYLE.get(k, "white")}
            for k, v in counts.items() if v
        ], value_fmt=lambda v: f"{v:.0f}")
    ui.note(f"Pricing catalog fetched at {payload.get('pricingFetchedAt') or '—'}", "NEUTRAL")


if __name__ != "__skip__":
    render(fetch())
