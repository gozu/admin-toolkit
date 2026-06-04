"""
Overview › Summary › Info panel
Notebook version of the system/instance info card.
Source of truth: backend.py:5269 api_overview  ·  frontend SummaryPage.tsx
Run inside a DSS Jupyter notebook (admin-toolkit code env). No API key / host needed.
"""
import dataiku
dataiku.use_plugin_libs("admin-toolkit")          # puts python-lib on sys.path
from adk_notebook import get_client, ui, data


def fetch():
    client = get_client()
    return data.overview(client)


def render(payload):
    ui.header("System & Instance Info", "Overview › Summary")
    info = payload.get("instanceInfo") or {}
    ui.kv_panel("Instance", {
        "DSS version": payload.get("dssVersion") or "—",
        "OS": payload.get("osInfo") or "—",
        "CPU": payload.get("cpuCores") or "—",
        "Python": payload.get("pythonVersion") or "—",
        "Spark": payload.get("sparkVersion") or "—",
        "Last restart": payload.get("lastRestartTime") or "—",
    })
    if info:
        https_value = info.get("https") if "https" in info else None
        if https_value is True:
            https_display = ("yes", "green")
        elif https_value is False:
            https_display = ("no", "yellow")
        else:
            https_display = ("unknown", "grey62")
        ui.kv_panel("Node", {
            "Node ID": info.get("nodeId") or "—",
            "Install ID": info.get("installId") or "—",
            "Instance URL": info.get("instanceUrl") or "—",
            "HTTPS": https_display,
            "Port": info.get("port") or "—",
        })


if __name__ != "__skip__":       # auto-runs when the cell is executed
    render(fetch())
