"""
Misc › Logs › Error list
Notebook version of the recent backend.log error blocks (with surrounding context).
Source of truth: backend.py:10287 api_logs_errors (_parse_log_errors)  ·  frontend LogsPage.tsx
Run inside a DSS Jupyter notebook (admin-toolkit code env). No API key / host needed.

Renders the parser's rawLogErrors (the actual log lines) rather than the
webapp's HTML-highlighted formattedLogErrors.
"""
import dataiku
dataiku.use_plugin_libs("admin-toolkit")
from adk_notebook import get_client, ui
from adk_notebook.parse import parse_log_errors


def fetch():
    client = get_client()
    try:
        log_content = client.get_log("backend.log")
    except Exception as exc:
        return {"error": str(exc), "rawLogErrors": []}
    return parse_log_errors(log_content)


def render(payload):
    ui.header("Backend Log — Recent Errors", "Misc › Logs")
    if payload.get("error"):
        ui.note(f"Could not read backend.log: {payload['error']}", "ERROR")
        return
    stats = payload.get("logStats") or {}
    if not stats.get("Unique Errors", 0):
        ui.note("No ERROR/FATAL/SEVERE/WARN patterns found in backend.log.", "SUCCESS")
        return
    errors = payload.get("rawLogErrors") or []
    if not errors:
        ui.note("No ERROR/FATAL/SEVERE/WARN patterns found in backend.log.", "SUCCESS")
        return
    for block in errors:
        lines = block.get("data") or []
        text = "".join(line if line.endswith("\n") else line + "\n" for line in lines).rstrip()
        # cap very long blocks so a cell stays readable
        if len(text) > 6000:
            text = text[:6000] + "\n… (truncated)"
        ui.code_block(f"Error @ {block.get('timestamp', '?')}", text, style="grey85", border="red")


if __name__ != "__skip__":
    render(fetch())
