"""Projects + users routes: inventory, SQL-pushdown audit (SSE), inactive-project cleaner.

`/api/projects/sql_pushdown_audit` reads `g.client` inside its generator, so it
wraps with `stream_with_context`. Worker threads use the host-context-propagating
`ThreadPoolExecutor` from adk_backend.clients (NOT concurrent.futures'), so
`_thread_client()` keeps targeting the selected host.
"""

import json
import logging
import re
import time
from concurrent.futures import as_completed
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, g, jsonify, request

from adk_backend.caching import _cache_get, _cache_pop
from adk_backend.clients import (
    ThreadPoolExecutor,
    _active_support_project,
    _list_projects_catalog,
    _list_projects_catalog_cheap,
    _sdk_fetch,
    _thread_client,
)
from adk_backend.settings import _BACKEND_SETTINGS, _outreach_thresholds
from adk_backend.utils import _SENTINEL, _resolve_nested_path, _sse_response, advanced

bp = Blueprint('projects', __name__)

_LOGGER = logging.getLogger(__name__)


# SQL connection types used by the SQL-pushdown scan to identify compatible connections.
_SQL_CONNECTION_TYPES = {
    'PostgreSQL', 'Greenplum', 'MySQL', 'MariaDB', 'SQLServer', 'Oracle',
    'Snowflake', 'BigQuery', 'Redshift', 'Teradata', 'Vertica', 'SAPHANA',
    'Synapse', 'Databricks', 'Athena', 'Trino', 'Presto', 'Exasol',
    'Netezza', 'DB2', 'SQLite',
}


_SQL_PUSHDOWN_CODE_RECIPE_TYPES = frozenset({
    'python', 'r', 'pyspark', 'spark_scala', 'scala', 'sql_query', 'sql_script',
})

# Visual recipes that have no SQL engine option at all — always skip.
_SQL_PUSHDOWN_NO_SQL_ENGINE_TYPES = frozenset({
    'clustering_cluster',   # ML clustering scoring — no SQL engine
    'clustering_scoring',   # alt name used by some DSS versions
    'fuzzyjoin',            # docs: "Only DSS engine is supported"
    'download',             # downloads files, never in-database
})

# Sampling-recipe methods that CAN be pushed to Snowflake SQL.
# FULL = no sampling (all data); HEAD_SEQUENTIAL = first N rows → LIMIT N;
# RANDOM_FIXED_NB = fixed random N → SAMPLE (only without seed).
# Everything else (class rebalance, column subset, stratified, sorted,
# random-approx, last records, etc.) requires a 2-pass / full-sort that
# does not translate to a Snowflake SELECT — leave it off the allowlist.
_SAMPLING_METHODS_PUSHDOWNABLE = frozenset({
    'FULL',
    'HEAD_SEQUENTIAL',
    'RANDOM_FIXED_NB',
})


@bp.route('/api/projects/sql_pushdown_audit')
def api_sql_pushdown_audit():
    """Stream visual recipes running on DSS engine that qualify for SQL pushdown.

    A recipe is reported when: (1) it is a visual (non-code) recipe, (2) all inputs
    and outputs are SQL-type datasets sharing the same connection, and (3) the
    selected engine is DSS (i.e., not already SQL). Grouped by project owner.
    """
    _LOGGER.info("[sql_pushdown] endpoint hit")

    def _dataset_map_for(proj) -> Dict[str, Dict[str, str]]:
        out: Dict[str, Dict[str, str]] = {}
        try:
            datasets = proj.list_datasets()
        except Exception as e:
            _LOGGER.debug("[sql_pushdown] list_datasets failed: %s", e)
            return out
        for ds in datasets:
            name = ds.get('name') or ''
            if not name:
                continue
            params = ds.get('params', {})
            if isinstance(params, str):
                try:
                    import ast
                    params = ast.literal_eval(params)
                except Exception:
                    params = {}
            conn = params.get('connection') if isinstance(params, dict) else None
            out[name] = {
                'type': ds.get('type', '') or '',
                'connection': conn or '',
            }
        return out

    def _strip_project_prefix(ref: str, project_key: str) -> Tuple[str, bool]:
        """Return (localName, isForeign). Foreign refs are disqualifiers."""
        if '.' in ref:
            prefix, name = ref.split('.', 1)
            if prefix == project_key:
                return name, False
            return ref, True
        return ref, False

    def _scan_project_sql_pushdown(project_key: str) -> Dict[str, Any]:
        client = _thread_client()
        proj = client.get_project(project_key)
        findings: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []

        ds_map = _dataset_map_for(proj)

        try:
            recipes = proj.list_recipes() or []
        except Exception as e:
            _LOGGER.debug("[sql_pushdown] list_recipes failed for %s: %s", project_key, e)
            errors.append({'projectKey': project_key, 'area': 'recipes', 'error': str(e)[:240]})
            return {'projectKey': project_key, 'findings': findings, 'errors': errors}

        for r in recipes:
            if not isinstance(r, dict):
                continue
            rtype = r.get('type', '') or ''
            if not rtype or rtype in _SQL_PUSHDOWN_CODE_RECIPE_TYPES:
                continue
            if rtype in _SQL_PUSHDOWN_NO_SQL_ENGINE_TYPES:
                continue
            rname = r.get('name') or ''
            if not rname:
                continue
            try:
                recipe = proj.get_recipe(rname)
                settings = recipe.get_settings()
                if rtype == 'sampling':
                    payload = settings.get_json_payload() if hasattr(settings, 'get_json_payload') else None
                    if not payload:
                        raw_str = settings.get_payload() if hasattr(settings, 'get_payload') else ''
                        try:
                            payload = json.loads(raw_str) if raw_str else {}
                        except Exception:
                            payload = {}
                    sel = (payload or {}).get('selection') or {}
                    sp = sel.get('samplingMethod') or sel.get('samplingMethodObj') or ''
                    # If samplingMethod is absent, recipe is filter-only (pushdownable as WHERE).
                    # If present, only the allowlist translates to Snowflake SQL.
                    if sp and sp not in _SAMPLING_METHODS_PUSHDOWNABLE:
                        continue
                    # RANDOM_FIXED_NB only translates if no random seed is set
                    if sp == 'RANDOM_FIXED_NB' and bool(sel.get('useRandomSeed')):
                        continue
                inputs = list(settings.get_flat_input_refs() or [])
                outputs = list(settings.get_flat_output_refs() or [])
                if not inputs or not outputs:
                    continue

                def _resolve(refs):
                    resolved: List[Tuple[str, Dict[str, str]]] = []
                    for ref in refs:
                        local, foreign = _strip_project_prefix(ref, project_key)
                        if foreign:
                            return None
                        info = ds_map.get(local)
                        if info is None:
                            return None
                        resolved.append((local, info))
                    return resolved

                in_resolved = _resolve(inputs)
                if in_resolved is None:
                    continue
                out_resolved = _resolve(outputs)
                if out_resolved is None:
                    continue

                all_infos = [info for _, info in in_resolved + out_resolved]
                if not all(info.get('type') in _SQL_CONNECTION_TYPES for info in all_infos):
                    continue
                connections = {info.get('connection') for info in all_infos}
                if len(connections) != 1:
                    continue
                connection = next(iter(connections))
                if not connection:
                    continue

                status = recipe.get_status()
                engine_details = status.get_selected_engine_details() if status else None
                if not isinstance(engine_details, dict):
                    continue
                if engine_details.get('type') != 'DSS':
                    continue

                # Ask the recipe whether it CAN run in-database. DSS exposes one
                # dict per candidate engine; the SQL (in-database) engine reports
                # isSelectable=False with an explanatory statusMessage when the
                # recipe can't be pushed down — e.g. a prepare step like the
                # UpDownFiller processor ("Not translatable to SQL"). Without this
                # check we false-positive those recipes as "should run in SQL".
                engines = status.get_engines_details() if status else None
                if not isinstance(engines, list):
                    continue
                sql_engine = next(
                    (e for e in engines if isinstance(e, dict) and e.get('type') == 'SQL'),
                    None,
                )
                if not sql_engine or not sql_engine.get('isSelectable'):
                    continue

                findings.append({
                    'recipeName': rname,
                    'recipeType': rtype,
                    'connection': connection,
                    'inputs': [local for local, _ in in_resolved],
                    'outputs': [local for local, _ in out_resolved],
                })
            except Exception as e:
                _LOGGER.debug("[sql_pushdown] recipe %s/%s failed: %s", project_key, rname, e)
                errors.append({'projectKey': project_key, 'area': 'recipe', 'error': str(e)[:240]})

        return {'projectKey': project_key, 'findings': findings, 'errors': errors}

    def generate():
        t0 = time.time()
        try:
            client = g.client
            projects_catalog = _list_projects_catalog_cheap(client)
            project_names = {p['key']: p.get('name', p['key']) for p in projects_catalog}
            project_owners = {p['key']: p.get('owner') or '' for p in projects_catalog}
            project_keys = list(project_names.keys())

            users = _sdk_fetch(
                'list_users',
                _BACKEND_SETTINGS['cache_ttl_users'],
                lambda: client.list_users(),
            ) or []
            user_map: Dict[str, Dict[str, Any]] = {}
            for u in users:
                login = u.get('login') or ''
                if not login:
                    continue
                user_map[login] = {
                    'displayName': u.get('displayName') or login,
                    'email': u.get('email') or None,
                }
        except Exception as e:
            _LOGGER.exception("[sql_pushdown] setup failed exc_type=%s", type(e).__name__)
            yield "event: error\ndata: %s\n\n" % json.dumps({'error': f"{type(e).__name__}: {str(e)[:200]}"})
            return

        _LOGGER.info("[sql_pushdown] scan start projects=%d users=%d", len(project_keys), len(user_map))
        yield "event: init\ndata: %s\n\n" % json.dumps({'total': len(project_keys)})

        per_project: Dict[str, List[Dict[str, Any]]] = {}
        scan_errors = []
        scanned = 0

        workers = min(8, max(1, len(project_keys)))
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            futures = {pool.submit(_scan_project_sql_pushdown, pk): pk for pk in project_keys}
            for future in as_completed(futures):
                pk = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {'projectKey': pk, 'findings': []}
                    scan_errors.append({'projectKey': pk, 'area': 'scan', 'error': str(exc)[:240]})
                if result.get('findings'):
                    per_project[pk] = result['findings']
                scan_errors.extend(result.get('errors', []) or [])
                scanned += 1
                if scanned % 20 == 0 or scanned == len(project_keys):
                    yield "event: progress\ndata: %s\n\n" % json.dumps({'scanned': scanned})
        except GeneratorExit:
            pool.shutdown(wait=False, cancel_futures=True)
            return
        finally:
            pool.shutdown(wait=False)

        # Group by owner
        owner_buckets: Dict[str, Dict[str, Any]] = {}
        for pk, findings in per_project.items():
            owner_login = project_owners.get(pk) or 'Unknown'
            info = user_map.get(owner_login, {})
            bucket = owner_buckets.setdefault(owner_login, {
                'ownerLogin': owner_login,
                'ownerDisplayName': info.get('displayName') or owner_login,
                'ownerEmail': info.get('email'),
                'projects': [],
                'totalRecipes': 0,
            })
            sorted_findings = sorted(findings, key=lambda f: (f.get('recipeName') or '').lower())
            bucket['projects'].append({
                'projectKey': pk,
                'projectName': project_names.get(pk, pk),
                'recipes': sorted_findings,
            })
            bucket['totalRecipes'] += len(sorted_findings)

        # Sort projects within each owner by recipe count desc, then name asc
        for bucket in owner_buckets.values():
            bucket['projects'].sort(
                key=lambda p: (-len(p['recipes']), (p.get('projectName') or '').lower()),
            )

        # Sort owners by totalRecipes desc, then displayName asc
        owner_groups = sorted(
            owner_buckets.values(),
            key=lambda b: (-b['totalRecipes'], (b.get('ownerDisplayName') or '').lower()),
        )

        total_ms = int((time.time() - t0) * 1000)
        yield "event: done\ndata: %s\n\n" % json.dumps({
            'total_ms': total_ms,
            'ownerGroups': owner_groups,
            'scanErrors': scan_errors,
            'failedProjectCount': len({e['projectKey'] for e in scan_errors}),
            'scannedProjectCount': len(project_keys),
        })

    return _sse_response(generate)


@bp.route('/api/users')
def api_users():
    client = g.client

    def loader():
        users = _sdk_fetch(
            'list_users',
            _BACKEND_SETTINGS['cache_ttl_users'],
            lambda: client.list_users(),
        )
        groups = _sdk_fetch(
            'list_groups',
            _BACKEND_SETTINGS['cache_ttl_users'],
            lambda: client.list_groups(),
        )

        enabled_users = [u for u in users if u.get('enabled') is True]
        user_stats: Dict[str, Any] = {
            'Total Users': len(users),
            'Enabled Users': len(enabled_users),
        }

        profile_counts: Dict[str, int] = {}
        for user in enabled_users:
            profile = user.get('userProfile')
            if profile:
                profile_counts[profile] = profile_counts.get(profile, 0) + 1
        user_stats.update(profile_counts)

        if groups:
            user_stats['Total Groups'] = len(groups)

        return {
            'userStats': user_stats,
            'users': [
                {
                    'login': u.get('login') or '',
                    'displayName': u.get('displayName'),
                    'email': u.get('email'),
                    'enabled': u.get('enabled'),
                    'userProfile': u.get('userProfile'),
                    'groups': u.get('groups') or [],
                }
                for u in users
            ],
        }

    data = _cache_get('users', _BACKEND_SETTINGS['cache_ttl_users'], loader)
    return jsonify(data)


def _extract_nested_int(payload: Any, *paths: str) -> Optional[int]:
    if not isinstance(payload, dict):
        return None
    for path in paths:
        value = _resolve_nested_path(payload, path)
        if value is _SENTINEL:
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            text = value.strip()
            if text.isdigit():
                return int(text)
    return None


def _normalize_project_permissions(perms_raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(perms_raw, dict):
        return []

    entries = perms_raw.get('permissions')
    if not isinstance(entries, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for perm in entries:
        if not isinstance(perm, dict):
            continue
        name = perm.get('group') or perm.get('user') or 'Unknown'
        entry = {
            'type': 'Group' if perm.get('group') else 'User',
            'name': name,
            'permissions': {},
        }
        for perm_key, perm_val in perm.items():
            if perm_key in ('group', 'user'):
                continue
            entry['permissions'][perm_key] = perm_val
        normalized.append(entry)
    return normalized


def _extract_project_version_number(listing: Dict[str, Any], summary: Dict[str, Any], settings: Dict[str, Any]) -> int:
    value = summary.get('versionTag', {}).get('versionNumber')
    if isinstance(value, (int, float)):
        return int(value)
    return 0


@bp.route('/api/projects')
def api_projects():
    client = g.client

    def loader():
        started = time.time()
        projects = []
        raw_projects = _sdk_fetch(
            'list_projects',
            _BACKEND_SETTINGS['cache_ttl_projects'],
            lambda: client.list_projects() or [],
        )
        total = len(raw_projects)
        _LOGGER.info("[projects] start total=%s", total)
        for idx, project in enumerate(raw_projects, 1):
            key = project.get('projectKey') or project.get('key') or project.get('id')
            name = project.get('name') or key
            owner = project.get('ownerLogin') or project.get('owner') or project.get('ownerName') or 'Unknown'

            perms_raw: Any = None

            try:
                project_obj = client.get_project(key)
            except Exception:
                project_obj = None

            if project_obj is not None:
                try:
                    perms_raw = project_obj.get_permissions()
                except Exception as exc:
                    _LOGGER.warning("[projects] %s permissions fetch failed: %s", key, exc)

            listing = project if isinstance(project, dict) else {}
            version_number = _extract_project_version_number(listing, listing, {})
            permissions = _normalize_project_permissions(perms_raw)

            if key == 'PYTHONAUDIT_TEST' or (version_number == 0 and len(permissions) == 0):
                perms_raw_type = type(perms_raw).__name__ if perms_raw is not None else 'NoneType'
                perms_raw_keys = []
                if isinstance(perms_raw, dict):
                    perms_raw_keys = sorted(list(perms_raw.keys()))
                _LOGGER.info(
                    "[projects] %s version=%s perms=%s listingVersion=%s permsRawType=%s permsRawKeys=%s",
                    key,
                    version_number,
                    len(permissions),
                    _extract_nested_int(listing, 'versionTag.versionNumber'),
                    perms_raw_type,
                    perms_raw_keys,
                )

            projects.append({
                'key': key,
                'name': name.replace('_', ' ') if isinstance(name, str) else key,
                'owner': owner,
                'permissions': permissions,
                'versionNumber': version_number,
            })
            if idx % 50 == 0:
                _LOGGER.info(
                    "[projects] progress=%s/%s elapsed=%.2fs",
                    idx,
                    total,
                    time.time() - started,
                )

        _LOGGER.info("[projects] done count=%s elapsed=%.2fs", len(projects), time.time() - started)
        return {'projects': projects}

    data = _cache_get('projects', _BACKEND_SETTINGS['cache_ttl_projects'], loader)
    return jsonify(data)


@bp.route('/api/tools/inactive-projects', methods=['GET'])
def api_tools_inactive_projects():
    """List inactive projects using lastModifiedOn derived from a per-project git-log walk (via _list_projects_catalog) for edit-accurate timestamps; cached."""
    from datetime import datetime, timezone

    def _load():
        client = g.client
        catalog = _list_projects_catalog(client)
        inactive_threshold_days = _outreach_thresholds.get('inactive_project_days', 365)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        results = []
        for entry in catalog:
            last_modified_ms = entry.get('lastModifiedOn')
            if last_modified_ms is None:
                continue
            try:
                days_inactive = (now_ms - int(last_modified_ms)) / (1000 * 60 * 60 * 24)
            except (TypeError, ValueError):
                continue
            if days_inactive < inactive_threshold_days:
                continue
            results.append({
                'projectKey': entry['key'],
                'name': entry.get('name', entry['key']),
                'owner': entry.get('owner', 'Unknown'),
                'daysInactive': round(days_inactive),
            })
        return {'projects': results}

    data = _cache_get('inactive_projects', _BACKEND_SETTINGS['cache_ttl_inactive'], _load)
    return jsonify(data)


@bp.route('/api/tools/project-cleaner/<project_key>', methods=['DELETE'])
@advanced
def api_project_cleaner_delete(project_key):
    """Backup to managed folder then delete an inactive project after verifying the confirmation header."""
    import tempfile

    confirm = request.headers.get("X-Confirm-Name", "")
    if confirm != project_key:
        return jsonify({"error": "Confirmation header does not match project key"}), 400

    folder_id = request.args.get("folderId", "").strip()
    if not folder_id:
        return jsonify({"error": "folderId query parameter is required"}), 400

    client = g.client
    plugin_project = _active_support_project(client)

    # Validate managed folder exists
    try:
        dest_folder = plugin_project.get_managed_folder(folder_id)
        dest_folder.get_definition()  # verify it exists
    except Exception as e:
        _LOGGER.error("[project-cleaner] invalid folder %s: %s", folder_id, e)
        return jsonify({"error": "Invalid managed folder: %s" % str(e)}), 400

    target_project = client.get_project(project_key)

    # Backup first — export to temp file, upload to managed folder
    safe_key = re.sub(r'[^a-zA-Z0-9._-]', '_', project_key)
    zip_filename = "%s.zip" % safe_key
    try:
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=True) as tmp:
            target_project.export_to_file(tmp.name)
            with open(tmp.name, "rb") as f:
                dest_folder.put_file(zip_filename, f)
    except Exception as e:
        _LOGGER.error("[project-cleaner] backup/upload failed for %s: %s", project_key, e)
        return jsonify({"error": "Backup upload failed — deletion aborted: %s" % str(e)}), 500

    # Delete project
    try:
        target_project.delete()
    except Exception as e:
        _LOGGER.error("[project-cleaner] delete failed for %s: %s", project_key, e)
        return jsonify({"error": "Delete failed (backup saved to managed folder): %s" % str(e)}), 500

    # Invalidate caches
    _cache_pop('tools_outreach_data')
    _cache_pop('inactive_projects')

    _LOGGER.info("[project-cleaner] backed up %s to managed folder %s and deleted %s", zip_filename, folder_id, project_key)
    return jsonify({"backed_up_to": "managed folder", "zip_name": zip_filename, "deleted": project_key}), 200
