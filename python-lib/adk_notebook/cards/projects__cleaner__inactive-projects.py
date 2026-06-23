"""
Projects › Cleaner › Inactive projects
Notebook version of the inactive-projects card (no project modifications past a threshold).
Source of truth: backend.py:10000 api_tools_inactive_projects (+ _list_projects_catalog)  ·  frontend ProjectsPage.tsx
Run inside a DSS Jupyter notebook (admin-toolkit code env). No API key / host needed.

NOTE: lastModifiedOn is derived from a per-project git-log walk
(newest non-migration commit timestamp), matching _list_projects_catalog —
NOT list_projects().lastModifiedOn, which does not reflect webapp edits. DSS upgrade
migration commits (author 'no:auth', "Migration to DSS version X") are skipped so a
version upgrade does not look like project activity. The
threshold is the INACTIVE_THRESHOLD_DAYS constant below (default 180) — edit it
in the cell to change which projects are flagged.
"""
from datetime import datetime, timezone
from dateutil import parser as dtparser
import dataiku
dataiku.use_plugin_libs("admin-toolkit")
from adk_notebook import get_client, ui

# Projects untouched longer than this many days are flagged. Override here.
INACTIVE_THRESHOLD_DAYS = 180


def _git_log_last_activity_ms(log):
    """Newest *user*-activity timestamp (ms) from a project git log, skipping DSS
    upgrade migration commits (author 'no:auth', "Migration to DSS version X").
    Matches _git_log_last_activity_ms in adk_backend/clients.py. Falls back to the
    newest commit if every entry is a migration."""
    entries = log["entries"]
    for entry in entries:
        if entry.get("author") == "no:auth" and str(entry.get("message") or "").startswith("Migration to DSS version"):
            continue
        return int(dtparser.isoparse(entry["timestamp"]).timestamp() * 1000)
    return int(dtparser.isoparse(entries[0]["timestamp"]).timestamp() * 1000)


def _list_projects_catalog(client):
    """Sequential port of backend.py _list_projects_catalog: {key, name, owner}
    plus a git-log-derived lastModifiedOn (ms). Replaces the parallel
    ThreadPoolExecutor git-log fetch with an identical per-item walk; sorted by key."""
    out = []
    for project in (client.list_projects() or []):
        if not isinstance(project, dict):
            continue
        key = str(project.get("projectKey") or project.get("key") or project.get("id") or "").strip()
        if not key:
            continue
        entry = {
            "key": key,
            "name": str(project.get("name") or key),
            "owner": str(project.get("ownerLogin") or project.get("owner") or project.get("ownerName") or "Unknown"),
        }
        try:
            log = client.get_project(key).get_project_git().log()
            entry["lastModifiedOn"] = _git_log_last_activity_ms(log)
        except Exception:
            pass
        out.append(entry)
    out.sort(key=lambda item: item.get("key") or "")
    return out


def fetch():
    client = get_client()
    catalog = _list_projects_catalog(client)
    inactive_threshold_days = INACTIVE_THRESHOLD_DAYS
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    results = []
    for entry in catalog:
        last_modified_ms = entry.get("lastModifiedOn")
        if last_modified_ms is None:
            continue
        try:
            days_inactive = (now_ms - int(last_modified_ms)) / (1000 * 60 * 60 * 24)
        except (TypeError, ValueError):
            continue
        if days_inactive < inactive_threshold_days:
            continue
        results.append({
            "projectKey": entry["key"],
            "name": entry.get("name", entry["key"]),
            "owner": entry.get("owner", "Unknown"),
            "daysInactive": round(days_inactive),
        })
    return {"projects": results, "thresholdDays": inactive_threshold_days}


def render(payload):
    ui.header("Inactive Projects", "Projects › Cleaner")
    projects = payload.get("projects") or []
    threshold = payload.get("thresholdDays", INACTIVE_THRESHOLD_DAYS)
    if not projects:
        ui.note(f"No projects inactive longer than {threshold} days.", "SUCCESS")
        return
    rows = [
        [p.get("projectKey", ""), p.get("name", ""), p.get("owner", ""),
         (f"{p.get('daysInactive', 0)} d", "red" if p.get("daysInactive", 0) >= 365 else "yellow")]
        for p in projects
    ]
    ui.data_table("Inactive projects", [
        "Key", "Name", "Owner", {"name": "Inactive", "justify": "right"}], rows,
        caption=f"{len(projects)} projects inactive > {threshold} days")


if __name__ != "__skip__":
    render(fetch())
