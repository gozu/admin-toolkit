"""
AI Compute › CS Templates › Code Studio template usage
Notebook version of the code-studio-template-per-project card.
Source of truth: backend.py:12928 api_cs_template_projects  ·  frontend ResourcesPage.tsx
Run inside a DSS Jupyter notebook (admin-toolkit code env). No API key / host needed.

NOTE: enriches each code studio via get_settings()/get_status() — slow on
instances with many code studios.
"""
import dataiku
dataiku.use_plugin_libs("admin-toolkit")
from adk_notebook import get_client, ui

INCLUDE_STATE = True


def _template_index(client):
    try:
        items = client.list_code_studio_templates(as_type="listitems")
    except Exception:
        return {}
    out = {}
    for item in items:
        raw = getattr(item, "_data", {}) or {}
        tid = str(raw.get("id") or "")
        if not tid:
            continue
        desc = raw.get("desc") or {}
        out[tid] = {"id": tid, "label": str(raw.get("label") or desc.get("label") or tid)}
    return out


def _list_one_project(client, project_key, template_index):
    project = client.get_project(project_key)
    studios = []
    for item in project.list_code_studios(as_type="listitems"):
        raw = getattr(item, "_data", {}) or {}
        tid = str(raw.get("templateId") or "")
        cs_id = str(raw.get("id") or "")
        entry = {"id": cs_id, "name": str(raw.get("name") or cs_id), "owner": str(raw.get("owner") or ""),
                 "templateId": tid, "templateLabel": (template_index.get(tid) or {}).get("label") or tid,
                 "state": None}
        if cs_id and INCLUDE_STATE:
            try:
                entry["state"] = client.get_project(project_key).get_code_studio(cs_id).get_status().state
            except Exception:
                pass
        studios.append(entry)
    return studios


def fetch():
    client = get_client()
    template_index = _template_index(client)
    result = []
    for p in (client.list_projects() or []):
        pk = str(p.get("projectKey") or "")
        if not pk:
            continue
        try:
            studios = _list_one_project(client, pk, template_index)
        except Exception:
            studios = []
        if studios:
            result.append({"projectKey": pk, "codeStudios": studios})
    result.sort(key=lambda r: r["projectKey"])
    return {"projects": result, "templates": list(template_index.values())}


def render(payload):
    ui.header("Code Studio Template Usage", "AI Compute › CS Templates")
    projects = payload.get("projects") or []
    templates = payload.get("templates") or []
    ui.note(f"{len(templates)} templates defined · {len(projects)} projects use code studios.", "INFO")
    if not projects:
        ui.note("No code studios in use.", "INFO")
        return
    rows = []
    for p in projects:
        for cs in p.get("codeStudios") or []:
            rows.append([p.get("projectKey", ""), cs.get("name", ""), cs.get("owner") or "—",
                         cs.get("templateLabel", ""),
                         (cs.get("state") or "—", "green" if cs.get("state") == "RUNNING" else "grey62")])
    ui.data_table("Code studios", ["Project", "Code Studio", "Owner", "Template", "State"], rows,
                  caption=f"{len(rows)} code studios")


if __name__ != "__skip__":
    render(fetch())
