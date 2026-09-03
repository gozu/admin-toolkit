"""Compute Placement (AI Compute): where every compute-using object runs.

The Container Execs page deliberately shows only explicit overrides that
differ from the instance default. This module is the complement: the FULL
inventory — every project default, Python/R recipe, DSS-engine visual recipe,
webapp backend, ML task, Jupyter notebook and Spark recipe — resolved to its
effective placement (local DSS host vs container config + cluster), plus a
mass local→container migration and the owner grounding for outreach mails.

Resolution chain (verified shapes, see
docs/dss-api-reference/containerized-execution-object-scan.md):
  object containerSelection → project default for that workload family →
  containerSettings.defaultExecutionConfig. `NONE` at any level pins local;
  a missing instance default means INHERIT resolves to local.
Cluster: project settings.k8sCluster (EXPLICIT_CLUSTER) → general-settings
defaultK8sClusterId. Only meaningful for KUBERNETES-type configs.
"""
import hashlib
import json
import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from flask import Blueprint, g, jsonify, request

from adk_backend.caching import (
    _CACHE,
    _CACHE_LOCK,
    _bump_session_epoch,
    _cache_get,
    _cache_key,
    _cache_pop_matching,
)
from adk_backend.clients import _list_projects_catalog_cheap
from adk_backend.context import _THREAD_LOCAL
from adk_backend.routes.container_execs import (
    _cex_apply_replace_row,
    _cex_browser_ctx,
    _cex_clean_config,
    _cex_path_get,
)
from adk_backend.settings import _BACKEND_SETTINGS
from adk_backend.utils import _cex_item_raw, _parallel_workers, _sse_response, advanced

bp = Blueprint('compute_placement', __name__)
_LOGGER = logging.getLogger(__name__)

_CODE_RECIPE_TYPES = {'python', 'r'}
_SPARK_RECIPE_TYPES = {'pyspark', 'spark_scala', 'spark_sql_query', 'sparkr'}

# Project-default surface → (raw path, label, which object surfaces inherit it)
_PROJECT_SURFACES: Tuple[Tuple[str, str, str], ...] = (
    ('project_code_default', 'settings.container', 'Project default · code workloads'),
    ('project_visual_default', 'settings.containerForVisualRecipesWorkloads', 'Project default · visual recipes'),
    ('project_webapp_default', 'settings.virtualWebAppBackendSettings.infra.containerSelection', 'Project default · webapp backends'),
)
_OBJECT_TO_PROJECT_SURFACE = {
    'recipe_code': 'project_code_default',
    'ml_task': 'project_code_default',
    'recipe_visual': 'project_visual_default',
    'webapp_backend': 'project_webapp_default',
}
_MIGRATABLE_SURFACES = set(_OBJECT_TO_PROJECT_SURFACE) | {s for s, _, _ in _PROJECT_SURFACES}


# ── resolution ───────────────────────────────────────────────────────────────

def _declared(selection: Any) -> Tuple[str, Optional[str]]:
    """(containerMode, containerConf) as written on the object; a missing
    selection block reads as INHERIT (DSS treats it that way)."""
    if not isinstance(selection, dict):
        return 'INHERIT', None
    mode = str(selection.get('containerMode') or 'INHERIT').upper()
    conf = selection.get('containerConf')
    if mode == 'EXPLICIT_CONTAINER' and conf:
        return mode, str(conf)
    if mode == 'EXPLICIT_CONTAINER':
        # Explicit mode without a config name: DSS falls back to local.
        return 'NONE', None
    return mode, None


def _resolve_project_default(selection: Any, global_default: Optional[str]) -> Dict[str, Any]:
    mode, conf = _declared(selection)
    if mode == 'EXPLICIT_CONTAINER':
        return {'containerMode': mode, 'containerConf': conf, 'effectiveConf': conf, 'resolvedFrom': 'project'}
    if mode == 'NONE':
        return {'containerMode': mode, 'containerConf': None, 'effectiveConf': None, 'resolvedFrom': 'project'}
    return {'containerMode': 'INHERIT', 'containerConf': None, 'effectiveConf': global_default, 'resolvedFrom': 'instance'}


def _resolve_object(selection: Any, project_default: Dict[str, Any]) -> Dict[str, Any]:
    mode, conf = _declared(selection)
    if mode == 'EXPLICIT_CONTAINER':
        return {'containerMode': mode, 'containerConf': conf, 'effectiveConf': conf, 'resolvedFrom': 'object'}
    if mode == 'NONE':
        return {'containerMode': mode, 'containerConf': None, 'effectiveConf': None, 'resolvedFrom': 'object'}
    return {
        'containerMode': 'INHERIT',
        'containerConf': None,
        'effectiveConf': project_default.get('effectiveConf'),
        'resolvedFrom': project_default.get('resolvedFrom') or 'project',
    }


def _resolve_cluster(settings_raw: Any, global_cluster: Optional[str]) -> Dict[str, Any]:
    sel = _cex_path_get(settings_raw, 'settings.k8sCluster')
    mode = str((sel or {}).get('clusterMode') or 'INHERIT').upper() if isinstance(sel, dict) else 'INHERIT'
    cluster_id = str(sel.get('clusterId')) if isinstance(sel, dict) and sel.get('clusterId') else None
    if mode == 'EXPLICIT_CLUSTER' and cluster_id:
        return {'clusterMode': mode, 'clusterId': cluster_id, 'effectiveClusterId': cluster_id, 'clusterSource': 'project'}
    if mode == 'NONE':
        return {'clusterMode': mode, 'clusterId': None, 'effectiveClusterId': None, 'clusterSource': 'project'}
    return {'clusterMode': 'INHERIT', 'clusterId': None, 'effectiveClusterId': global_cluster, 'clusterSource': 'instance' if global_cluster else None}


def _tag_login(item: Any) -> Optional[str]:
    """lastModifiedBy login from a DSS list item's versionTag / creationTag."""
    raw = _cex_item_raw(item) if not isinstance(item, dict) else item
    if not isinstance(raw, dict):
        return None
    for tag_key in ('versionTag', 'creationTag'):
        tag = raw.get(tag_key)
        if isinstance(tag, dict):
            who = tag.get('lastModifiedBy')
            if isinstance(who, dict) and who.get('login'):
                return str(who.get('login'))
    return None


def _kernel_placement(kernel_name: str) -> str:
    # DSS containerized Jupyter kernels carry the 'containerized' token in
    # their kernelspec name; every other kernel runs on the DSS host.
    return 'container' if 'containerized' in str(kernel_name or '').lower() else 'local'


# ── per-project scan (runs inside the worker pool: client calls only) ────────

def _make_row(ctx: Dict[str, Any], project: Dict[str, Any], **kw) -> Dict[str, Any]:
    surface = str(kw.get('surface') or '')
    resolved = kw.get('resolved') or {}
    placement = kw.get('placement') or ('container' if resolved.get('effectiveConf') else 'local')
    effective_conf = resolved.get('effectiveConf')
    config_type = ctx['config_types'].get(effective_conf) if effective_conf else None
    cluster = project['cluster']
    cluster_id = cluster.get('effectiveClusterId') if (placement == 'spark' or config_type == 'KUBERNETES') else None
    owner = kw.get('owner') or project['owner']
    user = ctx['users'].get(owner) or {}
    migratable = placement == 'local' and surface in _MIGRATABLE_SURFACES
    blocker = kw.get('blocker')
    if placement == 'local' and not migratable and not blocker:
        blocker = 'Not a container-selection carrier'
    object_type = str(kw.get('object_type') or '')
    object_id = str(kw.get('object_id') or '')
    return {
        'id': '|'.join([project['projectKey'], object_type, object_id, surface]),
        'projectKey': project['projectKey'],
        'projectName': project['projectName'],
        'objectType': object_type,
        'objectId': object_id,
        'objectName': str(kw.get('object_name') or object_id),
        'objectKind': str(kw.get('object_kind') or object_type.lower()),
        'surface': surface,
        'rawPath': str(kw.get('raw_path') or ''),
        'containerMode': resolved.get('containerMode') or kw.get('container_mode') or '',
        'containerConf': resolved.get('containerConf'),
        'effectiveConf': effective_conf,
        'resolvedFrom': resolved.get('resolvedFrom') or kw.get('resolved_from') or '',
        'placement': placement,
        'configType': config_type,
        'clusterId': cluster_id,
        'clusterSource': cluster.get('clusterSource') if cluster_id else None,
        'owner': owner,
        'ownerSource': 'object' if kw.get('owner') else 'project',
        'ownerEmail': user.get('email') or '',
        'ownerDisplayName': user.get('displayName') or '',
        'migratable': migratable,
        'migrateBlocker': blocker if placement == 'local' and not migratable else None,
        'extra': kw.get('extra') or {},
    }


def _scan_project(client: Any, project_meta: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    project_key = str(project_meta.get('key') or '')
    project_name = str(project_meta.get('name') or project_key)
    errors: List[Dict[str, str]] = []

    def err(area: str, exc: Any) -> None:
        errors.append({'projectKey': project_key, 'area': area, 'error': str(exc)[:240]})

    handle = client.get_project(project_key)
    settings_raw = handle.get_settings().get_raw()
    global_default = ctx['global_default']
    defaults = {
        surface: _resolve_project_default(_cex_path_get(settings_raw, path), global_default)
        for surface, path, _ in _PROJECT_SURFACES
    }
    project = {
        'projectKey': project_key,
        'projectName': project_name,
        'owner': str(project_meta.get('owner') or 'Unknown'),
        'cluster': _resolve_cluster(settings_raw, ctx['global_cluster']),
        'defaults': defaults,
    }
    rows: List[Dict[str, Any]] = []

    for surface, path, label in _PROJECT_SURFACES:
        rows.append(_make_row(
            ctx, project, surface=surface, raw_path=path, object_type='PROJECT',
            object_id=project_key, object_name=project_name, object_kind=label,
            resolved=defaults[surface],
        ))

    try:
        recipes = handle.list_recipes() or []
    except Exception as exc:
        err('recipes', exc)
        recipes = []
    for item in recipes:
        raw_item = _cex_item_raw(item) if not isinstance(item, dict) else item
        if not isinstance(raw_item, dict):
            continue
        name = str(raw_item.get('name') or raw_item.get('id') or '')
        rtype = str(raw_item.get('type') or '').lower()
        if not name:
            continue
        owner = _tag_login(raw_item)
        if rtype in _SPARK_RECIPE_TYPES:
            rows.append(_make_row(
                ctx, project, surface='recipe_spark', raw_path='', object_type='RECIPE',
                object_id=name, object_name=name, object_kind=f'{rtype} recipe',
                resolved={'containerMode': '', 'containerConf': None, 'effectiveConf': None, 'resolvedFrom': 'engine'},
                placement='spark', owner=owner, extra={'recipeType': rtype},
                blocker='Spark recipes run through the Spark engine, not a container config',
            ))
            continue
        try:
            recipe_raw = client._perform_json('GET', f'/projects/{project_key}/recipes/{name}')
            recipe_def = recipe_raw.get('recipe') if isinstance(recipe_raw, dict) else None
        except Exception as exc:
            err(f'recipe:{name}', exc)
            continue
        if not isinstance(recipe_def, dict):
            continue
        if rtype in _CODE_RECIPE_TYPES:
            rows.append(_make_row(
                ctx, project, surface='recipe_code', raw_path='recipe.params.containerSelection',
                object_type='RECIPE', object_id=name, object_name=name, object_kind=f'{rtype} recipe',
                resolved=_resolve_object(_cex_path_get(recipe_def, 'params.containerSelection'), defaults['project_code_default']),
                owner=owner, extra={'recipeType': rtype},
            ))
            continue
        visual_sel = _cex_path_get(recipe_def, 'params.engineParams.containerSelection')
        if isinstance(visual_sel, dict):
            engine = str(_cex_path_get(recipe_def, 'params.engineType') or '')
            rows.append(_make_row(
                ctx, project, surface='recipe_visual', raw_path='recipe.params.engineParams.containerSelection',
                object_type='RECIPE', object_id=name, object_name=name,
                object_kind=f'{rtype} visual recipe' + (f' ({engine})' if engine else ''),
                resolved=_resolve_object(visual_sel, defaults['project_visual_default']),
                owner=owner, extra={'recipeType': rtype, 'engineType': engine},
            ))

    try:
        webapps = handle.list_webapps() or []
    except Exception as exc:
        err('webapps', exc)
        webapps = []
    for item in webapps:
        raw_item = _cex_item_raw(item)
        webapp_id = str(raw_item.get('id') or '')
        if not webapp_id:
            continue
        try:
            detail = handle.get_webapp(webapp_id).get_settings().get_raw()
        except Exception as exc:
            err(f'webapp:{webapp_id}', exc)
            continue
        selection = _cex_path_get(detail, 'params.infra.containerSelection')
        wtype = str(detail.get('type') or raw_item.get('type') or 'webapp')
        rows.append(_make_row(
            ctx, project, surface='webapp_backend', raw_path='params.infra.containerSelection',
            object_type='WEBAPP', object_id=webapp_id,
            object_name=str(detail.get('name') or raw_item.get('name') or webapp_id),
            object_kind=f'{wtype} webapp',
            resolved=_resolve_object(selection, defaults['project_webapp_default']),
            owner=_tag_login(raw_item) or _tag_login(detail), extra={'webappType': wtype},
        ))

    try:
        lab = client._perform_json('GET', f'/projects/{project_key}/models/lab/')
        tasks = lab.get('mlTasks') if isinstance(lab, dict) else []
    except Exception as exc:
        err('ml_tasks', exc)
        tasks = []
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        analysis_id = str(task.get('analysisId') or '')
        task_id = str(task.get('mlTaskId') or '')
        if not analysis_id or not task_id:
            continue
        try:
            task_settings = client._perform_json('GET', f'/projects/{project_key}/models/lab/{analysis_id}/{task_id}/settings')
        except Exception as exc:
            err(f'ml_task:{task_id}', exc)
            continue
        selection = task_settings.get('containerSelection') if isinstance(task_settings, dict) else None
        ttype = str(task.get('taskType') or 'ML task')
        rows.append(_make_row(
            ctx, project, surface='ml_task', raw_path='containerSelection', object_type='ML_TASK',
            object_id=f'{analysis_id}/{task_id}', object_name=str(task.get('mlTaskName') or task_id),
            object_kind=f'{ttype.lower()} ML task',
            resolved=_resolve_object(selection, defaults['project_code_default']),
            owner=_tag_login(task), extra={'analysisId': analysis_id, 'mlTaskId': task_id, 'taskType': ttype},
        ))

    try:
        notebooks = handle.list_jupyter_notebooks(as_type='listitems') or []
    except Exception as exc:
        err('notebooks', exc)
        notebooks = []
    for item in notebooks:
        raw_item = _cex_item_raw(item)
        nb_name = str(raw_item.get('name') or raw_item.get('id') or '')
        if not nb_name:
            continue
        spec = raw_item.get('kernelSpec')
        kernel = str(spec.get('name') if isinstance(spec, dict) else (spec or '')) or ''
        placement = _kernel_placement(kernel)
        rows.append(_make_row(
            ctx, project, surface='notebook_kernel', raw_path='kernelSpec.name', object_type='NOTEBOOK',
            object_id=nb_name, object_name=nb_name, object_kind='Jupyter notebook',
            resolved={'containerMode': '', 'containerConf': None, 'effectiveConf': None, 'resolvedFrom': 'kernel'},
            placement=placement, owner=_tag_login(raw_item), extra={'kernel': kernel},
            blocker='Notebook kernels are chosen per session — re-open it with a containerized kernel',
        ))

    summary = {
        'projectKey': project_key,
        'projectName': project_name,
        'owner': project['owner'],
        'ownerEmail': (ctx['users'].get(project['owner']) or {}).get('email') or '',
        'defaults': {surface: dict(defaults[surface]) for surface in defaults},
        'cluster': dict(project['cluster']),
        'objectCount': sum(1 for r in rows if r['objectType'] != 'PROJECT'),
        'localCount': sum(1 for r in rows if r['objectType'] != 'PROJECT' and r['placement'] == 'local'),
        'containerCount': sum(1 for r in rows if r['objectType'] != 'PROJECT' and r['placement'] == 'container'),
        'sparkCount': sum(1 for r in rows if r['placement'] == 'spark'),
    }
    return {'projectKey': project_key, 'rows': rows, 'project': summary, 'errors': errors}


# ── full scan ────────────────────────────────────────────────────────────────

def _users_by_login(client: Any) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    try:
        for user in client.list_users() or []:
            raw = _cex_item_raw(user) if not isinstance(user, dict) else user
            login = str(raw.get('login') or '') if isinstance(raw, dict) else ''
            if login:
                out[login] = {'email': str(raw.get('email') or ''), 'displayName': str(raw.get('displayName') or '')}
    except Exception as exc:
        _LOGGER.warning("[compute-placement] list_users failed (owner emails off): %s", exc)
    return out


def _instance_context(client: Any) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """(ctx, cleaned configs, clusters, warnings)."""
    warnings: List[str] = []
    configs_raw: List[Dict[str, Any]] = []
    global_default = None
    global_cluster = None
    try:
        gs = client.get_general_settings().get_raw()
        cs = gs.get('containerSettings') if isinstance(gs, dict) else {}
        if isinstance(cs, dict):
            configs_raw = [c for c in (cs.get('executionConfigs') or []) if isinstance(c, dict)]
            if cs.get('defaultExecutionConfig'):
                global_default = str(cs.get('defaultExecutionConfig'))
        if isinstance(gs, dict) and gs.get('defaultK8sClusterId'):
            global_cluster = str(gs.get('defaultK8sClusterId'))
    except Exception as exc:
        warnings.append(f'general settings: {str(exc)[:200]}')
    clusters: List[Dict[str, Any]] = []
    try:
        for c in client.list_clusters() or []:
            raw = _cex_item_raw(c) if not isinstance(c, dict) else c
            if isinstance(raw, dict) and raw.get('id'):
                clusters.append({
                    'id': str(raw.get('id')),
                    'name': str(raw.get('name') or raw.get('id')),
                    'type': str(raw.get('type') or ''),
                    'architecture': str(raw.get('architecture') or ''),
                    'state': str(raw['state'].get('state') or '') if isinstance(raw.get('state'), dict) else str(raw.get('state') or ''),
                })
    except Exception as exc:
        warnings.append(f'clusters: {str(exc)[:200]}')
    configs = [_cex_clean_config(c) for c in configs_raw]
    ctx = {
        'global_default': global_default,
        'global_cluster': global_cluster,
        'config_types': {str(c.get('name')): str(c.get('type') or '') for c in configs_raw if c.get('name')},
        'users': _users_by_login(client),
    }
    return ctx, configs, clusters, warnings


def _summarize(rows: List[Dict[str, Any]], projects: List[Dict[str, Any]], catalog_size: int) -> Dict[str, Any]:
    by_placement: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    by_config: Dict[str, int] = {}
    by_cluster: Dict[str, int] = {}
    local_owners: Set[str] = set()
    projects_with_local: Set[str] = set()
    migratable = 0
    for row in rows:
        is_object = row['objectType'] != 'PROJECT'
        if is_object:
            by_placement[row['placement']] = by_placement.get(row['placement'], 0) + 1
            by_type[row['objectType']] = by_type.get(row['objectType'], 0) + 1
            if row['placement'] == 'local':
                local_owners.add(row['owner'])
                projects_with_local.add(row['projectKey'])
            if row.get('effectiveConf'):
                by_config[row['effectiveConf']] = by_config.get(row['effectiveConf'], 0) + 1
            if row.get('clusterId'):
                by_cluster[row['clusterId']] = by_cluster.get(row['clusterId'], 0) + 1
        if row.get('migratable'):
            migratable += 1
    return {
        'projectCount': catalog_size,
        'scannedProjectCount': len(projects),
        'rowCount': len(rows),
        'objectRowCount': sum(1 for r in rows if r['objectType'] != 'PROJECT'),
        'projectDefaultRowCount': sum(1 for r in rows if r['objectType'] == 'PROJECT'),
        'byPlacement': by_placement,
        'byObjectType': by_type,
        'byConfig': by_config,
        'byCluster': by_cluster,
        'localCount': by_placement.get('local', 0),
        'containerCount': by_placement.get('container', 0),
        'sparkCount': by_placement.get('spark', 0),
        'migratableCount': migratable,
        'localOwnerCount': len(local_owners),
        'projectsWithLocalCount': len(projects_with_local),
        'projectsLocalByDefault': sum(
            1 for p in projects if not (p['defaults']['project_code_default'].get('effectiveConf'))),
    }


def _scan(
    client: Any,
    project_keys_filter: Optional[Set[str]] = None,
    timeout_ms: Optional[int] = None,
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    started = time.time()
    deadline = started + float(timeout_ms) / 1000.0 if timeout_ms else None
    ctx, configs, clusters, warnings = _instance_context(client)

    catalog = _list_projects_catalog_cheap(client)
    if project_keys_filter:
        catalog = [p for p in catalog if p.get('key') in project_keys_filter]
    if progress_cb:
        progress_cb({'event': 'init', 'total': len(catalog)})

    rows: List[Dict[str, Any]] = []
    projects: List[Dict[str, Any]] = []
    scan_errors: List[Dict[str, str]] = []
    timed_out = False
    scanned = 0
    workers = max(1, min(8, _parallel_workers()))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_scan_project, client, meta, ctx): str(meta.get('key') or '') for meta in catalog if meta.get('key')}
        for future in as_completed(futures):
            key = futures[future]
            if deadline is not None and time.time() > deadline and not timed_out:
                timed_out = True
                for pending in futures:
                    pending.cancel()
            try:
                result = future.result()
            except Exception as exc:
                _LOGGER.warning("[compute-placement] project %s failed: %s", key, exc)
                scan_errors.append({'projectKey': key, 'area': 'project', 'error': f'{type(exc).__name__}: {str(exc)[:200]}'})
                result = None
            scanned += 1
            if result:
                rows.extend(result['rows'])
                projects.append(result['project'])
                scan_errors.extend(result['errors'])
            if progress_cb:
                progress_cb({'event': 'progress', 'scanned': scanned, 'total': len(catalog), 'projectKey': key})
            if timed_out:
                break

    rows.sort(key=lambda r: (r['projectKey'], 0 if r['objectType'] == 'PROJECT' else 1, r['objectType'], r['objectName'].lower()))
    projects.sort(key=lambda p: p['projectKey'])
    return {
        'rows': rows,
        'projects': projects,
        'configs': configs,
        'configNames': sorted(ctx['config_types']),
        'configTypes': ctx['config_types'],
        'clusters': clusters,
        'globalDefaultConfig': ctx['global_default'],
        'globalDefaultClusterId': ctx['global_cluster'],
        'summary': _summarize(rows, projects, len(catalog)),
        'scanErrors': scan_errors[:500],
        'failedProjectCount': len({e['projectKey'] for e in scan_errors}),
        'scannedProjectCount': len(catalog),
        'warnings': warnings,
        'timedOut': timed_out,
        'elapsedMs': round((time.time() - started) * 1000.0, 2),
    }


def _cache_key_for(project_filter: Optional[Set[str]]) -> str:
    if project_filter:
        digest = hashlib.sha1('\n'.join(sorted(project_filter)).encode('utf-8')).hexdigest()
        return f'compute_placement:{digest}'
    return 'compute_placement'


def _cached_scan(cache_key: str, ttl: int) -> Optional[Dict[str, Any]]:
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE.get(_cache_key(cache_key))
        value = cached.get('value') if cached and now - cached.get('ts', 0) < ttl else None
    return value if isinstance(value, dict) else None


def _timeout_ms() -> int:
    return int(_BACKEND_SETTINGS.get('container_exec_timeout_ms', 600000))


def _project_filter_from_arg(arg: str) -> Optional[Set[str]]:
    parts = {p.strip() for p in (arg or '').split(',') if p.strip()}
    return parts or None


# ── migration planning (pure) ────────────────────────────────────────────────

def _plan_migration(
    scan: Dict[str, Any],
    row_ids: Set[str],
    target_config: str,
    cluster_id: Optional[str],
    strategy: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """→ (matched rows, ops). Pure: works off the cached scan only.

    strategy 'objects': pin every selected local row explicitly to the target.
    strategy 'project-defaults': set the project default for each selected
    row's workload family to the target and flip objects that explicitly say
    NONE back to INHERIT, so the whole family follows the project.
    Cluster: one project-level op per touched project whose declared
    k8sCluster is not already {EXPLICIT_CLUSTER, cluster_id}."""
    rows_by_id = {r['id']: r for r in scan.get('rows') or []}
    projects_by_key = {p['projectKey']: p for p in scan.get('projects') or []}
    matched = [rows_by_id[rid] for rid in sorted(row_ids) if rid in rows_by_id
               and rows_by_id[rid].get('placement') == 'local' and rows_by_id[rid].get('migratable')]
    ops: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, str]] = set()

    def op(kind: str, row: Dict[str, Any], to: str, **extra) -> None:
        sig = (kind, row['projectKey'], row.get('surface') or '') if kind == 'project-default' else (kind, row['id'], to)
        if sig in seen:
            return
        seen.add(sig)
        ops.append({
            'kind': kind,
            'rowId': row.get('id'),
            'projectKey': row['projectKey'],
            'objectType': row.get('objectType'),
            'objectId': row.get('objectId'),
            'objectName': row.get('objectName'),
            'objectKind': row.get('objectKind'),
            'surface': row.get('surface'),
            'rawPath': row.get('rawPath'),
            'from': row.get('containerMode') or 'INHERIT',
            'to': to,
            'status': 'planned',
            **extra,
        })

    for row in matched:
        surface = str(row.get('surface') or '')
        if strategy == 'project-defaults' and surface in _OBJECT_TO_PROJECT_SURFACE:
            project_surface = _OBJECT_TO_PROJECT_SURFACE[surface]
            default_row = rows_by_id.get('|'.join([row['projectKey'], 'PROJECT', row['projectKey'], project_surface]))
            if default_row is not None and default_row.get('effectiveConf') != target_config:
                op('project-default', default_row, target_config)
            if row.get('containerMode') == 'NONE':
                op('object-inherit', row, 'INHERIT')
            else:
                op('object-unchanged', row, 'INHERIT', status='unchanged',
                   note='Already inherits — follows the project default')
        else:
            op('object-explicit' if row.get('objectType') != 'PROJECT' else 'project-default', row, target_config)

    if cluster_id:
        for project_key in sorted({r['projectKey'] for r in matched}):
            project = projects_by_key.get(project_key)
            cluster = (project or {}).get('cluster') or {}
            if cluster.get('clusterMode') == 'EXPLICIT_CLUSTER' and cluster.get('clusterId') == cluster_id:
                continue
            ops.append({
                'kind': 'project-cluster',
                'rowId': None,
                'projectKey': project_key,
                'objectType': 'PROJECT',
                'objectId': project_key,
                'objectName': (project or {}).get('projectName') or project_key,
                'objectKind': 'Project cluster',
                'surface': 'project_cluster',
                'rawPath': 'settings.k8sCluster',
                'from': f"{cluster.get('clusterMode') or 'INHERIT'}"
                        + (f":{cluster.get('clusterId')}" if cluster.get('clusterId') else ''),
                'to': cluster_id,
                'status': 'planned',
            })
    return matched, ops


def _apply_project_cluster(client: Any, project_key: str, cluster_id: str) -> None:
    settings = client.get_project(project_key).get_settings()
    raw = settings.get_raw()
    raw.setdefault('settings', {})['k8sCluster'] = {'clusterMode': 'EXPLICIT_CLUSTER', 'clusterId': cluster_id}
    settings.save()


def _apply_op(client: Any, operation: Dict[str, Any], target_config: str, browser_ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    kind = operation['kind']
    if kind == 'project-cluster':
        _apply_project_cluster(client, operation['projectKey'], str(operation['to']))
        return None
    row_like = {
        'projectKey': operation['projectKey'],
        'objectId': operation.get('objectId'),
        'surface': operation.get('surface'),
        'rawPath': operation.get('rawPath'),
    }
    if operation.get('surface') == 'ml_task':
        analysis_id, _, task_id = str(operation.get('objectId') or '').partition('/')
        row_like.update({'analysisId': analysis_id, 'mlTaskId': task_id})
    diag: Optional[Dict[str, Any]] = {} if operation.get('surface') == 'ml_task' else None
    to = '__INHERIT__' if kind == 'object-inherit' else target_config
    _cex_apply_replace_row(client, row_like, to, browser_ctx=browser_ctx, diag=diag)
    return diag


# ── routes ───────────────────────────────────────────────────────────────────

@bp.route('/api/compute-placement')
def api_compute_placement():
    client = g.client
    project_filter = _project_filter_from_arg(request.args.get('projectKeys', ''))
    cache_key = _cache_key_for(project_filter)
    data = _cache_get(
        cache_key,
        _BACKEND_SETTINGS.get('cache_ttl_projects', 600),
        lambda: _scan(client, project_keys_filter=project_filter, timeout_ms=_timeout_ms()),
    )
    return jsonify(data)


@bp.route('/api/compute-placement/stream')
def api_compute_placement_stream():
    project_filter = _project_filter_from_arg(request.args.get('projectKeys', ''))
    cache_key = _cache_key_for(project_filter)
    ttl = int(_BACKEND_SETTINGS.get('cache_ttl_projects', 600))
    request_client = g.client
    request_host_id = getattr(g, 'host_id', 'local')

    def sse(event_name: str, payload: Dict[str, Any]) -> str:
        return "event: %s\ndata: %s\n\n" % (event_name, json.dumps(payload))

    def generate():
        cached = _cached_scan(cache_key, ttl)
        if cached is not None:
            yield sse('init', {'total': (cached.get('summary') or {}).get('projectCount') or 0, 'cached': True})
            yield sse('done', cached)
            return

        events_q: "queue.Queue[Dict[str, Any]]" = queue.Queue()

        def worker() -> None:
            previous = getattr(_THREAD_LOCAL, 'host_id', None)
            _THREAD_LOCAL.host_id = request_host_id
            try:
                result = _scan(request_client, project_keys_filter=project_filter,
                               timeout_ms=_timeout_ms(), progress_cb=lambda p: events_q.put(dict(p)))
                with _CACHE_LOCK:
                    _CACHE[_cache_key(cache_key)] = {'ts': time.time(), 'value': result}
                events_q.put({'event': 'done', 'payload': result})
            except Exception as exc:
                _LOGGER.exception("[compute-placement] scan failed")
                events_q.put({'event': 'error', 'error': str(exc)[:500]})
            finally:
                if previous is None:
                    try:
                        delattr(_THREAD_LOCAL, 'host_id')
                    except AttributeError:
                        pass
                else:
                    _THREAD_LOCAL.host_id = previous

        threading.Thread(target=worker, daemon=True).start()
        while True:
            item = events_q.get()
            event_name = str(item.pop('event', 'progress'))
            if event_name == 'done':
                yield sse('done', item.get('payload') if isinstance(item.get('payload'), dict) else {})
                break
            yield sse(event_name, item)
            if event_name == 'error':
                break

    return _sse_response(generate)


@bp.route('/api/compute-placement/migrate', methods=['POST'])
@advanced
def api_compute_placement_migrate():
    payload = request.get_json(silent=True) or {}
    row_ids = {str(r) for r in (payload.get('rowIds') or []) if str(r).strip()} if isinstance(payload.get('rowIds'), list) else set()
    target_config = str(payload.get('targetConfig') or '').strip()
    cluster_id = str(payload.get('clusterId') or '').strip() or None
    strategy = str(payload.get('strategy') or 'objects').strip()
    dry_run = bool(payload.get('dryRun', True))
    if not row_ids:
        return jsonify({'error': 'rowIds is required'}), 400
    if not target_config:
        return jsonify({'error': 'targetConfig is required'}), 400
    if strategy not in ('objects', 'project-defaults'):
        return jsonify({'error': "strategy must be 'objects' or 'project-defaults'"}), 400

    client = g.client
    ttl = int(_BACKEND_SETTINGS.get('cache_ttl_projects', 600))
    scan = _cached_scan('compute_placement', ttl)
    scan_cached = scan is not None
    if scan is None:
        scan = _scan(client, timeout_ms=_timeout_ms())
        with _CACHE_LOCK:
            _CACHE[_cache_key('compute_placement')] = {'ts': time.time(), 'value': scan}

    config_types = scan.get('configTypes') or {}
    if target_config not in config_types:
        return jsonify({'error': f'Unknown targetConfig: {target_config}',
                        'validConfigNames': sorted(config_types)}), 400
    if cluster_id:
        if config_types.get(target_config) != 'KUBERNETES':
            return jsonify({'error': f'{target_config} is a {config_types.get(target_config) or "non-Kubernetes"} config; '
                                     'a cluster only applies to Kubernetes configs'}), 400
        # Existence guard: never write a dangling cluster reference (it fires
        # ERR_CLUSTERS_INVALID_SELECTED and can break agent-tool kernel init).
        valid = sorted(str(c.get('id')) for c in (client.list_clusters() or []))
        if cluster_id not in valid:
            return jsonify({'error': f'Cluster {cluster_id!r} does not exist on this instance',
                            'validClusterIds': valid}), 400

    matched, ops = _plan_migration(scan, row_ids, target_config, cluster_id, strategy)
    browser_ctx = _cex_browser_ctx(request)
    if not dry_run:
        for operation in ops:
            if operation.get('status') != 'planned':
                continue
            try:
                diag = _apply_op(client, operation, target_config, browser_ctx)
                operation['status'] = 'updated'
                if diag:
                    operation['diag'] = diag
            except Exception as exc:
                operation['status'] = 'failed'
                operation['error'] = str(exc)[:500]
        _cache_pop_matching(lambda key_text: str(key_text).startswith(('compute_placement', 'container_execs')))
        _bump_session_epoch()

    return jsonify({
        'dryRun': dry_run,
        'strategy': strategy,
        'targetConfig': target_config,
        'clusterId': cluster_id,
        'scanCached': scan_cached,
        'matchedRows': len(matched),
        'plannedOps': sum(1 for o in ops if o['status'] == 'planned'),
        'updatedOps': sum(1 for o in ops if o['status'] == 'updated'),
        'failedOps': sum(1 for o in ops if o['status'] == 'failed'),
        'unchangedOps': sum(1 for o in ops if o['status'] == 'unchanged'),
        'results': ops,
    })
