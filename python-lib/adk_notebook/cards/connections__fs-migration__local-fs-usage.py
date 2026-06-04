"""
Connections › FS Migration › Local filesystem usage
Notebook version of the local-FS objects card (migration candidates).
Source of truth: backend.py:5669 api_connection_usages  ·  frontend ConnectionsFsMigrationPage.tsx
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
    ui.header("Local Filesystem Usage", "Connections › FS Migration")
    fs = payload.get("localFilesystemUsages") or []
    scan_errors = payload.get("scanErrors") or []
    if scan_errors:
        ui.note(f"{len(scan_errors)} scan error(s); migration list may be incomplete.", "WARNING")
    if not fs:
        level = "WARNING" if scan_errors else "SUCCESS"
        ui.note("No objects on local-filesystem connections — nothing to migrate.", level)
        return
    rows = [
        [u.get("owner", ""), u.get("projectKey", ""),
         (u.get("objectType", ""), "cyan" if u.get("objectType") == "dataset" else "yellow"),
         u.get("objectName", ""), u.get("connection", ""), u.get("path", "")]
        for u in fs
    ]
    ui.data_table("Objects on local-FS connections", [
        "Owner", "Project", "Type", "Object", "Connection", {"name": "Path", "max_width": 40},
    ], rows, caption=f"{len(fs)} objects — migrate off local filesystem before scaling out")


if __name__ != "__skip__":
    render(fetch())
