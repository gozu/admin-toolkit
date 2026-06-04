"""
Projects › Compute › SQL pushdown audit
Notebook version of the SQL-pushdown card: visual recipes on the DSS engine
whose I/O is all on one SQL connection (candidates to push down in-database).
Source of truth: backend.py:6005 api_sql_pushdown_audit  ·  frontend ProjectComputePage.tsx
Run inside a DSS Jupyter notebook (admin-toolkit code env). No API key / host needed.

NOTE: introspects every visual recipe's engine via the SDK — slow on large
instances (the same per-recipe work the webapp streams).
"""
import ast
import json
import dataiku
dataiku.use_plugin_libs("admin-toolkit")
from adk_notebook import get_client, ui

_SQL_CONNECTION_TYPES = {
    'PostgreSQL', 'Greenplum', 'MySQL', 'MariaDB', 'SQLServer', 'Oracle',
    'Snowflake', 'BigQuery', 'Redshift', 'Teradata', 'Vertica', 'SAPHANA',
    'Synapse', 'Databricks', 'Athena', 'Trino', 'Presto', 'Exasol',
    'Netezza', 'DB2', 'SQLite',
}
_CODE_RECIPE_TYPES = {'python', 'r', 'pyspark', 'spark_scala', 'scala', 'sql_query', 'sql_script'}
_NO_SQL_ENGINE_TYPES = {'clustering_cluster', 'clustering_scoring', 'fuzzyjoin', 'download'}
_SAMPLING_PUSHDOWNABLE = {'FULL', 'HEAD_SEQUENTIAL', 'RANDOM_FIXED_NB'}


def _dataset_map(proj):
    out = {}
    try:
        datasets = proj.list_datasets()
    except Exception:
        return out
    for ds in datasets:
        name = ds.get("name") or ""
        if not name:
            continue
        params = ds.get("params", {})
        if isinstance(params, str):
            try:
                params = ast.literal_eval(params)
            except Exception:
                params = {}
        conn = params.get("connection") if isinstance(params, dict) else None
        out[name] = {"type": ds.get("type", "") or "", "connection": conn or ""}
    return out


def _strip_prefix(ref, project_key):
    if "." in ref:
        prefix, name = ref.split(".", 1)
        return (name, False) if prefix == project_key else (ref, True)
    return ref, False


def _scan_project(client, project_key):
    proj = client.get_project(project_key)
    findings = []
    errors = []
    ds_map = _dataset_map(proj)
    try:
        recipes = proj.list_recipes() or []
    except Exception as exc:
        errors.append({"projectKey": project_key, "area": "recipes",
                       "error": str(exc)[:240]})
        return {"projectKey": project_key, "findings": findings, "errors": errors}
    for r in recipes:
        if not isinstance(r, dict):
            continue
        rtype = r.get("type", "") or ""
        if not rtype or rtype in _CODE_RECIPE_TYPES or rtype in _NO_SQL_ENGINE_TYPES:
            continue
        rname = r.get("name") or ""
        if not rname:
            continue
        try:
            recipe = proj.get_recipe(rname)
            settings = recipe.get_settings()
            if rtype == "sampling":
                payload = settings.get_json_payload() if hasattr(settings, "get_json_payload") else None
                if not payload:
                    raw_str = settings.get_payload() if hasattr(settings, "get_payload") else ""
                    try:
                        payload = json.loads(raw_str) if raw_str else {}
                    except Exception:
                        payload = {}
                sel = (payload or {}).get("selection") or {}
                sp = sel.get("samplingMethod") or sel.get("samplingMethodObj") or ""
                if sp and sp not in _SAMPLING_PUSHDOWNABLE:
                    continue
                if sp == "RANDOM_FIXED_NB" and bool(sel.get("useRandomSeed")):
                    continue
            inputs = list(settings.get_flat_input_refs() or [])
            outputs = list(settings.get_flat_output_refs() or [])
            if not inputs or not outputs:
                continue

            def _resolve(refs):
                resolved = []
                for ref in refs:
                    local, foreign = _strip_prefix(ref, project_key)
                    if foreign:
                        return None
                    info = ds_map.get(local)
                    if info is None:
                        return None
                    resolved.append((local, info))
                return resolved

            in_resolved = _resolve(inputs)
            out_resolved = _resolve(outputs)
            if in_resolved is None or out_resolved is None:
                continue
            all_infos = [info for _, info in in_resolved + out_resolved]
            if not all(info.get("type") in _SQL_CONNECTION_TYPES for info in all_infos):
                continue
            connections = {info.get("connection") for info in all_infos}
            if len(connections) != 1:
                continue
            connection = next(iter(connections))
            if not connection:
                continue
            status = recipe.get_status()
            engine_details = status.get_selected_engine_details() if status else None
            if not isinstance(engine_details, dict) or engine_details.get("type") != "DSS":
                continue
            engines = status.get_engines_details() if status else None
            if not isinstance(engines, list):
                continue
            sql_engine = next((e for e in engines if isinstance(e, dict) and e.get("type") == "SQL"), None)
            if not sql_engine or not sql_engine.get("isSelectable"):
                continue
            findings.append({"recipeName": rname, "recipeType": rtype, "connection": connection,
                             "inputs": [local for local, _ in in_resolved],
                             "outputs": [local for local, _ in out_resolved]})
        except Exception as exc:
            errors.append({"projectKey": project_key, "area": "recipe",
                           "error": str(exc)[:240]})
    return {"projectKey": project_key, "findings": findings, "errors": errors}


def fetch():
    client = get_client()
    catalog = client.list_projects() or []
    project_names = {}
    project_owners = {}
    for p in catalog:
        key = p.get("projectKey") or p.get("key") or p.get("id")
        if key:
            project_names[key] = p.get("name", key)
            project_owners[key] = p.get("ownerLogin") or p.get("owner") or p.get("ownerName") or "Unknown"
    users = client.list_users() or []
    user_map = {}
    for u in users:
        login = u.get("login") or ""
        if not login:
            continue
        user_map[login] = {"displayName": u.get("displayName") or login,
                           "email": u.get("email") or None}
    per_project = {}
    scan_errors = []
    for pk in project_names:
        try:
            result = _scan_project(client, pk)
        except Exception as exc:
            result = {"projectKey": pk, "findings": [], "errors": [
                {"projectKey": pk, "area": "scan", "error": str(exc)[:240]}
            ]}
        for err in result.get("errors") or []:
            if isinstance(err, dict):
                scan_errors.append(err)
        if result.get("findings"):
            per_project[pk] = result["findings"]
    owner_buckets = {}
    for pk, findings in per_project.items():
        owner = project_owners.get(pk) or "Unknown"
        info = user_map.get(owner, {})
        bucket = owner_buckets.setdefault(owner, {
            "ownerLogin": owner,
            "ownerDisplayName": info.get("displayName") or owner,
            "ownerEmail": info.get("email"),
            "projects": [],
            "totalRecipes": 0,
        })
        bucket["projects"].append({"projectKey": pk, "projectName": project_names.get(pk, pk),
                                   "recipes": sorted(findings, key=lambda f: (f.get("recipeName") or "").lower())})
        bucket["totalRecipes"] += len(findings)
    for bucket in owner_buckets.values():
        bucket["projects"].sort(key=lambda p: (-len(p["recipes"]), (p.get("projectName") or "").lower()))
    owner_groups = sorted(owner_buckets.values(),
                          key=lambda b: (-b["totalRecipes"], (b.get("ownerDisplayName") or "").lower()))
    return {"ownerGroups": owner_groups, "scanErrors": scan_errors,
            "failedProjectCount": len({e.get("projectKey") for e in scan_errors if e.get("projectKey")}),
            "scannedProjectCount": len(project_names)}


def render(payload):
    ui.header("SQL Pushdown Candidates", "Projects › Compute")
    groups = payload.get("ownerGroups") or []
    scan_errors = payload.get("scanErrors") or []
    if scan_errors:
        ui.note(f"{len(scan_errors)} scan error(s); SQL-pushdown candidates may be incomplete.", "WARNING")
    if not groups:
        level = "WARNING" if scan_errors else "SUCCESS"
        ui.note("No visual recipes qualify for SQL pushdown — all already SQL or DSS-only.", level)
        return
    total = sum(g.get("totalRecipes", 0) for g in groups)
    ui.note(f"{total} recipe(s) across {len(groups)} owner(s) could be pushed in-database.", "WARNING")
    rows = []
    for g in groups:
        for proj in g.get("projects", []):
            for r in proj.get("recipes", []):
                rows.append([g.get("ownerLogin", ""), proj.get("projectName", ""),
                             r.get("recipeName", ""), r.get("recipeType", ""), r.get("connection", "")])
    ui.data_table("Pushdown candidates", ["Owner", "Project", "Recipe", "Type", "Connection"], rows)


if __name__ != "__skip__":
    render(fetch())
