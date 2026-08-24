"""Shared data fetchers — the heavier scans reused by more than one card.

Each function mirrors the corresponding ``backend.py`` endpoint, but with the
Flask / ``g.client`` / SSE-progress / deadline / thread-pool / caching plumbing
stripped out (none of that is meaningful for a one-shot notebook fetch). The
SDK calls and the pure transforms are kept faithful, and the compact navigation
/ health helpers are copied verbatim so the produced shapes match the webapp.

Needs ``dataiku`` (imported lazily by :mod:`adk_notebook`), so this module only
loads inside DSS.
"""
from __future__ import annotations

import math
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .parse import coerce_int, coerce_float, normalize_language, format_size_human
from . import client as _client_mod


# ─────────────────────────────────────────────────────────────────────────────
# Overview  (backend.py:5280 api_overview — macro/"loader_remote" path)
# ─────────────────────────────────────────────────────────────────────────────
def _instance_info_from_install_map(install: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """backend.py:5216 — verbatim."""
    if not isinstance(install, dict):
        return {}
    normalized = {str(k).strip().lower(): v for k, v in install.items()}

    def pick(*keys: str) -> Any:
        for key in keys:
            value = normalized.get(key.lower())
            if value not in (None, ''):
                return value
        return None

    info: Dict[str, Any] = {}
    node_id = pick('general.nodeid', 'nodeid', 'general.nodeId')
    install_id = pick('general.installid', 'installid', 'general.installId')
    instance_url = pick('general.instanceurl', 'instanceurl', 'general.instanceUrl')
    ssl = pick('server.ssl', 'ssl')
    port = pick('server.port', 'port')
    if node_id:
        info['nodeId'] = node_id
    if install_id:
        info['installId'] = install_id
    if instance_url:
        info['instanceUrl'] = instance_url
    if ssl is not None:
        info['https'] = str(ssl).lower() in ('true', '1', 'yes')
    if port:
        info['port'] = port
    return info


def _find_spark_version(settings: Any) -> Optional[str]:
    """backend.py:989 — verbatim."""
    if isinstance(settings, dict):
        for key, value in settings.items():
            if isinstance(key, str) and key.lower() in ('spark.version', 'sparkversion'):
                return str(value)
            found = _find_spark_version(value)
            if found:
                return found
    elif isinstance(settings, list):
        for item in settings:
            found = _find_spark_version(item)
            if found:
                return found
    return None


def overview(client: Any) -> Dict[str, Any]:
    """System/instance overview — host-metrics macro + general settings.

    Mirrors api_overview's ``loader_remote`` (the macro path), which works for
    any host in-DSS. Returns cpuCores / osInfo / memoryInfo / systemLimits /
    filesystemInfo / pythonVersion / sparkVersion / lastRestartTime / dssVersion
    / instanceInfo / javaMemRaw.
    """
    from .parse import (
        parse_memory_info, parse_system_limits, parse_filesystem_info,
        parse_supervisord_restart,
    )
    m = _client_mod.host_metrics(client)
    install = m.get('install') or {}
    version = m.get('version') or {}
    cpu = m.get('cpu') or {}
    os_info = m.get('os') or {}
    physical_cores = coerce_int(cpu.get('physicalCores'), 0)
    logical_cores = coerce_int(cpu.get('logicalCores'), 0)
    if physical_cores > 0 and logical_cores > physical_cores:
        cpu_label = f"{physical_cores} Cores / {logical_cores} Threads"
    else:
        cpu_label = str(physical_cores or logical_cores or '')
    try:
        settings = client.get_general_settings().get_raw()
    except Exception:
        settings = None
    return {
        'cpuCores': cpu_label,
        'osInfo': os_info.get('PRETTY_NAME') or os_info.get('NAME') or '',
        'memoryInfo': parse_memory_info(m.get('freeOutput')),
        'systemLimits': parse_system_limits(m.get('ulimitOutput')),
        'filesystemInfo': parse_filesystem_info(m.get('dfOutput')),
        'pythonVersion': m.get('pythonVersion') or '',
        'sparkVersion': _find_spark_version(settings) or '',
        'lastRestartTime': parse_supervisord_restart(m.get('supervisordLog')) or '',
        'dssVersion': version.get('product_version') or version.get('version'),
        'instanceInfo': _instance_info_from_install_map(install),
        'javaMemRaw': m.get('javaMemRaw'),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Process snapshot  (backend.py:5368 api_process_metrics — process-metrics macro)
# ─────────────────────────────────────────────────────────────────────────────
def process_snapshot(client: Any) -> Dict[str, Any]:
    """Per-process CPU+memory snapshot. Shared by the Memory and CPU pages.

    Returns {ok, processes:[{pid,user,cpuPercent,memPercent,rssKb,vszKb,command}],
    totalProcesses, truncated}.
    """
    return _client_mod.process_metrics(client)


def llm_audit_report(client: Any) -> Dict[str, Any]:
    """LLM model audit: every project's LLM profiles classified against the
    LiteLLM pricing catalog (current / obsolete / unknown) + a status summary.

    Reuses the plugin's existing ``llm_audit`` python-lib (the same module the
    webapp uses). Lean port of api_llm_audit (backend.py:10675): pricing lookup
    + per-project list_llms + classify_llm + summarize_rows. The webapp's
    phase-4b per-asset usage-reference scan is omitted (it only enriches each row
    with projectsUsing / referencingProjects / usageAssets), so those three row
    keys are absent here.

    Returns {rows, summary, pricingFetchedAt} where summary carries
    scanErrors / failedProjectCount / scannedProjectCount (matching the webapp,
    which nests scan instrumentation inside summary).
    """
    from datetime import datetime, timezone
    import llm_audit  # plugin python-lib (sibling of adk_notebook)

    lookup = llm_audit.build_lookup()
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    try:
        connections_by_name = client.list_connections() or {}
    except Exception:
        connections_by_name = {}

    projects = client.list_projects() or []
    project_names = {p['projectKey']: (p.get('name') or p['projectKey'])
                     for p in projects if isinstance(p, dict) and p.get('projectKey')}

    total_projects = len(project_names)
    llm_rows: List[Dict[str, Any]] = []
    scan_errors: List[Dict[str, Any]] = []
    failed_project_keys: set = set()
    for pk in project_names:
        try:
            project = client.get_project(pk)
            for llm in project.list_llms() or []:
                if not isinstance(llm, dict) or llm.get('type') in llm_audit.NOT_APPLICABLE_TYPES:
                    continue
                llm_rows.append({
                    'projectKey': pk, 'llmId': llm.get('id'), 'type': llm.get('type'),
                    'connection': llm.get('connection'),
                    'rawModel': llm.get('model') or llm.get('deployment'),
                    'model': llm.get('model'), 'deployment': llm.get('deployment'),
                    'friendlyName': llm.get('friendlyName'), 'friendlyNameShort': llm.get('friendlyNameShort'),
                })
        except Exception as exc:
            # Mirrors backend's 'scan_project_failed' event → area='scan',
            # message=f'{pk}: {exc}'.
            scan_errors.append({
                'projectKey': pk,
                'area': 'scan',
                'error': f'{pk}: {exc}'[:240],
            })
            failed_project_keys.add(pk)
            continue

    seen: set = set()
    classified_rows: List[Dict[str, Any]] = []
    for row in llm_rows:
        key = (row.get('projectKey'), row.get('llmId'))
        if key in seen:
            continue
        seen.add(key)
        verdict = llm_audit.classify_llm(row, lookup, connections_by_name=connections_by_name)
        merged = {
            'projectKey': row.get('projectKey'),
            'projectName': project_names.get(row.get('projectKey') or '', row.get('projectKey') or ''),
            'llmId': row.get('llmId'), 'friendlyName': row.get('friendlyName'),
            'friendlyNameShort': row.get('friendlyNameShort'), 'type': row.get('type'),
            'connection': row.get('connection'), 'rawModel': row.get('rawModel'),
        }
        merged.update(verdict)
        classified_rows.append(merged)

    summary = llm_audit.summarize_rows(classified_rows)
    summary['pricingFetchedAt'] = fetched_at
    summary['scanErrors'] = scan_errors
    summary['failedProjectCount'] = len(failed_project_keys)
    summary['scannedProjectCount'] = total_projects
    return {
        'rows': classified_rows,
        'summary': summary,
        'pricingFetchedAt': fetched_at,
    }


def db_query(client: Any, sql: str, connection: Optional[str] = None,
             password: Optional[str] = None) -> Dict[str, Any]:
    """Run a read-only SQL query through the dbhealth-query macro (run-query).

    Mirrors backend.py's _pg_query_rows path. ``connection`` is a DSS PostgreSQL
    connection name; leave it None to hit the local dataiku runtimedb via the
    unix socket as the dataiku service account. Returns the macro result
    {ok, columns, rows, rowCount} (or {ok:False, error}); use db_rows() to get
    list-of-dicts.
    """
    return _client_mod.dbhealth(client, 'run-query', sql=sql, connection=connection, password=password)


def db_rows(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Turn a db_query() result into a list of {column: value} dicts."""
    cols = result.get('columns') or []
    return [dict(zip(cols, row)) for row in (result.get('rows') or [])]


def display_user(user: str) -> str:
    """backend frontend utils/processUsage.ts:displayUser — strip dssuser_ prefix."""
    return user[len('dssuser_'):] if user.startswith('dssuser_') else user


def aggregate_by_user(processes: List[Dict[str, Any]], metric: str) -> List[Dict[str, Any]]:
    """Per-user ranking (utils/processUsage.ts:aggregateByUser).

    metric = 'rssKb' (memory: value=Σrss, share=Σmem%) or
             'cpuPercent' (cpu: value=share=Σcpu%). Sorted by value desc.
    """
    by_user: Dict[str, Dict[str, Any]] = {}
    for p in processes or []:
        user = p.get('user', '')
        row = by_user.get(user)
        if not row:
            row = {'user': user, 'value': 0.0, 'share': 0.0, 'count': 0}
            by_user[user] = row
        row['value'] += coerce_float(p.get(metric), 0.0)
        row['share'] += coerce_float(p.get('memPercent' if metric == 'rssKb' else 'cpuPercent'), 0.0)
        row['count'] += 1
    return sorted(by_user.values(), key=lambda r: r['value'], reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# Footprint engine  (backend.py:2647 _compute_footprint_payload + navigation)
# Powers project_footprint() and the code-env size map. Compact pure helpers are
# copied verbatim; the failure-latch state machine (webapp infra) is dropped.
# ─────────────────────────────────────────────────────────────────────────────
def _client_perform_json(client: Any, method: str, path: str) -> Optional[Any]:
    if not hasattr(client, '_perform_json'):
        return None
    for attempt in (
        lambda: client._perform_json(method, path),
        lambda: client._perform_json(path),
    ):
        try:
            response = attempt()
            if isinstance(response, (dict, list)):
                return response
        except Exception:
            continue
    return None


def _unwrap_footprint_payload(value: Any) -> Any:
    current = value
    seen = 0
    while isinstance(current, dict) and seen < 8:
        seen += 1
        nested = current.get('result')
        if not isinstance(nested, dict):
            break
        current = nested
    return current


def _wrap_project_footprint_payload(payload: Any, project_key: Optional[str]) -> Any:
    if not isinstance(payload, dict):
        return payload
    projects = payload.get('projects')
    if not isinstance(projects, dict):
        return payload
    items = projects.get('items')
    if not isinstance(items, list):
        return payload
    if project_key:
        for item in items:
            if isinstance(item, dict) and item.get('projectKey') == project_key:
                return item
    if items:
        first = items[0]
        if isinstance(first, dict):
            return first
    return payload


def compute_footprint_payload(client: Any, scope: str, project_key: Optional[str]) -> Optional[Any]:
    """Lean port of backend.py:2647 — SDK first, REST fallback. No failure latch."""
    if hasattr(client, 'get_data_directories_footprint'):
        try:
            footprint_api = client.get_data_directories_footprint()
            if scope == 'global':
                return _unwrap_footprint_payload(footprint_api.compute_global_only_footprint(wait=True))
            if scope == 'project' and project_key:
                return _unwrap_footprint_payload(footprint_api.compute_project_footprint(project_key, wait=True))
            return _unwrap_footprint_payload(footprint_api.compute_all_dss_footprint(wait=True))
        except Exception:
            pass

    rest_path = '/directories-footprint/all-dss?summaryOnly=false'
    if scope == 'global':
        rest_path = '/directories-footprint/global?summaryOnly=false'
    elif scope == 'project' and project_key:
        rest_path = f'/directories-footprint/projects/{project_key}?summaryOnly=false'
    response = _client_perform_json(client, 'GET', rest_path)
    if not isinstance(response, dict):
        return None
    unwrapped = _unwrap_footprint_payload(response)
    if scope == 'project':
        return _wrap_project_footprint_payload(unwrapped, project_key)
    return unwrapped


_FOOTPRINT_SCALAR_KEYS = frozenset({
    'size', 'nbFiles', 'nbFolders', 'nbErrors',
    'projectKey', 'name', 'language', 'type', 'result',
})

_FOOTPRINT_BUCKET_LABELS = {
    'managedDatasets': 'Managed datasets', 'managedFolders': 'Managed folders',
    'preparedBundles': 'Bundles', 'savedModels': 'Saved models',
    'analysis': 'Visual analyses', 'notebookResults': 'Notebook results',
    'uploadedDatasets': 'Uploaded datasets', 'shakerSamples': 'Prepare samples',
    'codeStudioResources': 'Code Studio resources', 'scenarios': 'Scenario logs',
    'webApps': 'Web app runs', 'dkuWorkdirs': 'Jupyter work dirs',
    'thumbnails': 'Thumbnails', 'projectStandards': 'Project standards',
    'docportal': 'Doc portal', 'libResources': 'Library resources',
    'wikiAttachments': 'Wiki attachments', 'config': 'Project config',
    'git': 'Git history',
}


def _footprint_details_map(footprint: Any) -> Dict[str, Any]:
    if not isinstance(footprint, dict):
        return {}
    details = footprint.get('details')
    if isinstance(details, dict):
        return details
    items = footprint.get('items')
    if isinstance(items, list):
        result: Dict[str, Any] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            item_name = str(item.get('projectKey') or item.get('name') or '').strip()
            if item_name:
                result[item_name] = item
        return result
    result = {}
    for key, val in footprint.items():
        if key in _FOOTPRINT_SCALAR_KEYS:
            continue
        if isinstance(val, dict):
            result[key] = val
    return result


def _footprint_size(footprint: Any) -> int:
    if not isinstance(footprint, dict):
        return 0
    size = coerce_int(footprint.get('size'), 0)
    if size > 0:
        return size
    details = _footprint_details_map(footprint)
    if not details:
        return 0
    return sum(_footprint_size(child) for child in details.values())


def _normalize_bucket_name(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(name or '').lower())


def _collect_bucket_size_by_name(footprint: Any, matcher: Callable[[str], bool]) -> int:
    details = _footprint_details_map(footprint)
    if not details:
        return 0
    total = 0
    for name, child in details.items():
        normalized = _normalize_bucket_name(name)
        if matcher(normalized):
            total += _footprint_size(child)
            continue
        total += _collect_bucket_size_by_name(child, matcher)
    return total


def _collect_bucket_file_count_by_name(footprint: Any, matcher: Callable[[str], bool]) -> int:
    details = _footprint_details_map(footprint)
    if not details:
        return 0
    total = 0
    for name, child in details.items():
        normalized = _normalize_bucket_name(name)
        if matcher(normalized):
            total += coerce_int(child.get('nbFiles'), 0)
            continue
        total += _collect_bucket_file_count_by_name(child, matcher)
    return total


def _footprint_bucket_breakdown(footprint: Any, top_n: int = 5) -> Dict[str, Any]:
    details = _footprint_details_map(footprint)
    items = []
    for key, child in details.items():
        bytes_ = _footprint_size(child)
        if bytes_ <= 0:
            continue
        loc = ''
        if isinstance(child, dict):
            locs = child.get('locations')
            if isinstance(locs, list) and locs:
                loc = str(locs[0])
        items.append({'name': key, 'label': _FOOTPRINT_BUCKET_LABELS.get(key, key),
                      'bytes': bytes_, 'location': loc})
    items.sort(key=lambda d: d['bytes'], reverse=True)
    top = items[:top_n]
    rest = items[top_n:]
    return {'buckets': top, 'otherCount': len(rest), 'otherBytes': sum(d['bytes'] for d in rest)}


def _scope_root(scope: str, project_key: Optional[str]) -> Dict[str, str]:
    if scope == 'all':
        return {'name': '/', 'path': '/'}
    if scope == 'global':
        return {'name': 'global', 'path': '/dss-data/global'}
    if scope == 'project' and project_key:
        return {'name': project_key, 'path': f'/dss-data/projects/{project_key}'}
    return {'name': 'dss_data', 'path': '/dss-data'}


def _build_footprint_node(name: str, path: str, footprint: Any, depth: int, max_depth: int,
                          bonus_depth: int = 0) -> Dict[str, Any]:
    """backend.py:5029 — verbatim (adaptive-depth footprint tree node)."""
    details = _footprint_details_map(footprint)
    children: List[Dict[str, Any]] = []
    has_hidden = False
    effective_max = max_depth + bonus_depth
    if depth < effective_max:
        child_items = []
        for child_name, child_footprint in details.items():
            child_size = coerce_int(child_footprint.get('size'), 0)
            child_items.append((child_name, child_footprint, child_size))
        child_items.sort(key=lambda x: x[2], reverse=True)
        top_n = 5
        for idx, (child_name, child_footprint, _child_size) in enumerate(child_items):
            clean_name = str(child_name).strip('/') or str(child_name)
            child_path = f"{path.rstrip('/')}/{clean_name}" if path != '/' else f"/{clean_name}"
            child_bonus = 2 if (idx < top_n and bonus_depth == 0 and depth == 0) else bonus_depth
            children.append(_build_footprint_node(clean_name, child_path, child_footprint, depth + 1,
                                                  max_depth, bonus_depth=child_bonus))
    elif details:
        has_hidden = True

    children.sort(key=lambda c: c.get('size', 0), reverse=True)
    size = coerce_int(footprint.get('size'), 0)
    file_count = coerce_int(footprint.get('nbFiles'), 0)
    if size <= 0 and children:
        size = sum(child['size'] for child in children)
    if file_count <= 0 and children:
        file_count = sum(child['fileCount'] for child in children)
    own_size = max(0, size - sum(child['size'] for child in children))
    locations_raw = footprint.get('locations')
    locations: List[str] = []
    if isinstance(locations_raw, list):
        locations = [str(loc) for loc in locations_raw if loc is not None and str(loc).strip()]
    elif isinstance(locations_raw, str) and locations_raw.strip():
        locations = [locations_raw.strip()]
    if not children and not details:
        file_count = max(file_count, 1)
    return {
        'name': name, 'path': path, 'size': size, 'ownSize': own_size,
        'isDirectory': True, 'children': children, 'fileCount': file_count,
        'depth': depth, 'hasHiddenChildren': has_hidden, 'locations': locations,
    }


def dir_tree(client: Any, scope: str = 'dss', project_key: Optional[str] = None,
             max_depth: int = 3) -> Dict[str, Any]:
    """Disk footprint as a tree (backend.py:11196 api_dir_tree, default root view).

    scope = 'dss' (whole instance) | 'project'. Returns {root, totalSize,
    totalFiles, rootPath, scope, projectKey}; root is a node from
    _build_footprint_node ({name, size, fileCount, children, ...}).
    """
    footprint_scope = 'all' if scope == 'dss' else scope
    root_footprint = compute_footprint_payload(client, footprint_scope, project_key)
    root_meta = _scope_root(scope, project_key)
    if not root_footprint:
        return {'root': None, 'totalSize': 0, 'totalFiles': 0,
                'rootPath': root_meta['path'], 'scope': scope, 'projectKey': project_key}
    root_node = _build_footprint_node(root_meta['name'], root_meta['path'], root_footprint, 0, max_depth)
    return {
        'root': root_node, 'totalSize': root_node['size'], 'totalFiles': root_node['fileCount'],
        'rootPath': root_node['path'], 'scope': scope, 'projectKey': project_key,
    }


def _project_size_index(total_gb: float, avg_gb: float) -> float:
    safe_total = max(0.0, total_gb)
    if safe_total >= 40.0:
        return 1.0
    abs_norm = math.log1p(min(safe_total, 40.0)) / math.log1p(40.0)
    ratio = safe_total / max(avg_gb, 0.1)
    rel_norm = math.log1p(min(max(ratio, 0.0), 4.0)) / math.log1p(4.0)
    return max(0.0, min(1.0, (0.6 * abs_norm) + (0.4 * rel_norm)))


def _project_size_health(total_gb: float, size_index: float) -> str:
    if total_gb >= 40.0:
        return 'angry-red'
    if size_index >= 0.85:
        return 'angry-red'
    if size_index >= 0.60:
        return 'red'
    if size_index >= 0.35:
        return 'orange'
    return 'green'


def _code_env_health(code_env_count: int) -> str:
    if code_env_count >= 5:
        return 'angry-red'
    if code_env_count == 4:
        return 'red'
    if code_env_count == 3:
        return 'orange'
    if code_env_count == 2:
        return 'yellow'
    return 'green'


def _code_env_risk(code_env_count: int) -> float:
    if code_env_count <= 1:
        return 0.0
    if code_env_count == 2:
        return 0.45
    if code_env_count == 3:
        return 0.75
    return 1.0


def _projects_catalog_cheap(client: Any) -> List[Dict[str, str]]:
    """backend.py:4153 _list_projects_catalog_cheap — list_projects only."""
    out: List[Dict[str, str]] = []
    for project in (client.list_projects() or []):
        if not isinstance(project, dict):
            continue
        key = str(project.get('projectKey') or project.get('key') or project.get('id') or '').strip()
        if not key:
            continue
        out.append({
            'key': key,
            'name': str(project.get('name') or key),
            'owner': str(project.get('ownerLogin') or project.get('owner') or project.get('ownerName') or 'Unknown'),
        })
    out.sort(key=lambda item: item.get('key') or '')
    return out


def _envs_by_project(client: Any, project_info: Dict[str, Any]) -> Dict[str, Any]:
    """Per-project code-env usage maps.

    Faithful port of backend.py:_collect_project_code_env_usage (the
    `include_project_object_scan=True, include_code_env_usage_api=False` path used
    by api_project_footprint). Walks list_code_envs(), skips PLUGIN_MANAGED /
    DSS_INTERNAL envs, matches each to list_code_env_usages() by (lang.upper(),
    name), and for every *known* project that uses it builds three maps mirroring
    backend.py:4790-4854:
      - envsByProject:          project → set of 'lang:name' env keys
      - usageBreakdownByProject: project → {usageType: count} (counts raw usages)
      - usageDetailsByProject:   project → deduped, augmented usage entries

    Usage entries are normalized via _normalize_usage_entry, augmented with the
    codeEnv* fields (backend.py:4588-4599), then deduped via _dedupe_usage_entries
    (whose signature keys on codeEnvKey). The notebook reads the same raw
    list_code_env_usages() source that backend normalizes at backend.py:4587.
    """
    envs_by_project: Dict[str, set] = {k: set() for k in project_info.keys()}
    usage_breakdown_by_project: Dict[str, Dict[str, int]] = {k: {} for k in project_info.keys()}
    usage_details_by_project: Dict[str, List[Dict[str, Any]]] = {k: [] for k in project_info.keys()}

    envs = [e for e in (client.list_code_envs() or []) if isinstance(e, dict)]
    usages_by_env: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for u in (client.list_code_env_usages() or []):
        if not isinstance(u, dict):
            continue
        k = (str(u.get('envLang', '')).upper(), str(u.get('envName', '')))
        usages_by_env.setdefault(k, []).append(u)

    for env in envs:
        env_name = env.get('envName') or env.get('name') or env.get('id')
        if not env_name:
            continue
        env_lang_raw = env.get('envLang') or env.get('language') or env.get('type') or 'PYTHON'
        normalized_lang = normalize_language(env_lang_raw)
        if str(env.get('deploymentMode') or '').upper() in _SKIP_DEPLOYMENT_MODES:
            continue
        env_key = f"{normalized_lang}:{env_name}"
        env_owner_raw = env.get('owner')
        env_owner = env_owner_raw.strip() if isinstance(env_owner_raw, str) and env_owner_raw.strip() else 'Unknown'
        for raw_usage in usages_by_env.get((normalized_lang.upper(), env_name), []):
            project_key = _extract_usage_project_key(raw_usage)
            if project_key and project_key in envs_by_project:
                envs_by_project[project_key].add(env_key)
                normalized = _normalize_usage_entry(raw_usage, project_info)
                usage_type = str(normalized.get('usageType') or 'UNKNOWN')
                counts = usage_breakdown_by_project[project_key]
                counts[usage_type] = counts.get(usage_type, 0) + 1
                # Augment to backend's usage shape (backend.py:4588-4599) — the
                # codeEnv* fields matter for both display and _usage_signature dedup.
                usage_details_by_project[project_key].append({
                    'projectKey': project_key,
                    'projectName': str(normalized.get('projectName') or project_key),
                    'usageType': usage_type,
                    'objectType': str(normalized.get('objectType') or normalized.get('usageType') or 'UNKNOWN'),
                    'objectId': str(normalized.get('objectId') or ''),
                    'objectName': str(normalized.get('objectName') or normalized.get('objectId') or ''),
                    'codeEnvKey': env_key,
                    'codeEnvName': str(env_name),
                    'codeEnvLanguage': normalized_lang,
                    'codeEnvOwner': env_owner,
                })

    for project_key, usages in usage_details_by_project.items():
        usage_details_by_project[project_key] = _dedupe_usage_entries(usages)

    return {
        'envsByProject': envs_by_project,
        'usageBreakdownByProject': usage_breakdown_by_project,
        'usageDetailsByProject': usage_details_by_project,
    }


def project_footprint(client: Any) -> Dict[str, Any]:
    """Per-project disk footprint + bucket breakdown + code-env risk.

    Lean port of api_project_footprint (backend.py:7868): one footprint compute
    per project + a single list_code_env_usages() to count code envs per project.
    The webapp's parallel saved-model / code-studio sub-scans are omitted (those
    feed separate columns); the size/footprint/code-env columns are faithful.

    NOTE: walks every project's footprint serially — slow on large instances,
    same heavy work the webapp parallelises. Fine for a one-shot notebook cell.
    """
    catalog = _projects_catalog_cheap(client)
    project_info = {p['key']: {'name': p['name'], 'owner': p['owner']} for p in catalog}
    usage_data = _envs_by_project(client, project_info)
    envs_by_project = usage_data['envsByProject']
    usage_breakdown_by_project = usage_data['usageBreakdownByProject']
    usage_details_by_project = usage_data['usageDetailsByProject']

    raw_rows: List[Dict[str, Any]] = []
    scan_errors: List[Dict[str, Any]] = []
    failed_project_keys: set = set()
    total_gb_values: List[float] = []
    for key, meta in project_info.items():
        fp = compute_footprint_payload(client, 'project', key)
        if fp is None:
            # Backend keeps the project (zero-byte row) and records a 'footprint'
            # scan error rather than dropping it.
            scan_errors.append({
                'projectKey': key,
                'area': 'footprint',
                'error': 'project footprint payload missing'[:240],
            })
            failed_project_keys.add(key)
        managed_datasets = _collect_bucket_size_by_name(fp, lambda n: 'manageddataset' in n or ('managed' in n and 'dataset' in n))
        managed_folders = _collect_bucket_size_by_name(fp, lambda n: 'managedfolder' in n or ('managed' in n and 'folder' in n))
        code_env_keys = envs_by_project.get(key) or set()
        code_env_count = len(code_env_keys)
        bundle_bytes = _collect_bucket_size_by_name(fp, lambda n: 'preparedbundle' in n or n.endswith('bundles') or 'bundle' in n)
        bundle_count = _collect_bucket_file_count_by_name(fp, lambda n: 'preparedbundle' in n or n.endswith('bundles') or 'bundle' in n)
        total_bytes = _footprint_size(fp)
        if total_bytes <= 0:
            total_bytes = managed_datasets + managed_folders + bundle_bytes
        total_gb = total_bytes / float(1024 ** 3)
        total_gb_values.append(total_gb)
        raw_rows.append({
            'projectKey': key,
            'name': str(meta.get('name') or key).replace('_', ' '),
            'owner': meta.get('owner') or 'Unknown',
            'codeEnvCount': code_env_count,
            'codeEnvBytes': 0,
            'codeEnvKeys': sorted(code_env_keys),
            'managedDatasetsBytes': managed_datasets,
            'managedFoldersBytes': managed_folders,
            'bundleBytes': bundle_bytes,
            'bundleCount': bundle_count,
            'footprintBreakdown': _footprint_bucket_breakdown(fp),
            'totalBytes': total_bytes,
            'totalGB': total_gb,
            'codeEnvHealth': _code_env_health(code_env_count),
            'usageBreakdown': usage_breakdown_by_project.get(key) or {},
            'usageDetails': usage_details_by_project.get(key) or [],
        })

    avg_project_gb = (sum(total_gb_values) / len(total_gb_values)) if total_gb_values else 0.0
    project_risks: List[float] = []
    for row in raw_rows:
        total_gb = coerce_float(row.get('totalGB'), 0.0)
        size_index = _project_size_index(total_gb, avg_project_gb)
        env_risk = _code_env_risk(coerce_int(row.get('codeEnvCount'), 0))
        project_risk = (0.7 * env_risk) + (0.3 * size_index)
        project_risks.append(project_risk)
        row.update({
            'instanceAvgProjectGB': round(avg_project_gb, 4),
            'projectSizeIndex': round(size_index, 4),
            'projectSizeHealth': _project_size_health(total_gb, size_index),
            'codeEnvRisk': round(env_risk, 4),
            'projectRisk': round(project_risk, 4),
        })

    raw_rows.sort(key=lambda item: coerce_int(item.get('totalBytes'), 0), reverse=True)
    avg_project_risk = (sum(project_risks) / len(project_risks)) if project_risks else 0.0
    # backend's summary carries _footprint_available()/_footprint_unavailable_reason()
    # (a cross-request circuit-breaker the frontend uses for an "unavailable" banner).
    # A one-shot notebook has no latch state, so derive it from this run: available
    # unless every project's footprint payload was missing.
    footprint_available = (not project_info) or (len(failed_project_keys) < len(project_info))
    footprint_reason = None if footprint_available else 'project footprint payload unavailable'
    return {
        'projects': raw_rows,
        'summary': {
            'instanceProjectRiskAvg': round(avg_project_risk, 4),
            'instanceAvgProjectGB': round(avg_project_gb, 4),
            'projectCount': len(raw_rows),
            'footprintAvailable': footprint_available,
            'footprintReason': footprint_reason,
        },
        'scanErrors': scan_errors,
        'failedProjectCount': len(failed_project_keys),
        'scannedProjectCount': len(project_info),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Code env catalog  (backend.py:6582 api_code_envs — streaming machinery dropped)
# ─────────────────────────────────────────────────────────────────────────────
def _extract_usage_project_key(usage: Dict[str, Any]) -> Optional[str]:
    """backend.py:2925 — verbatim."""
    for key in ('projectKey', 'projectId', 'project_key'):
        value = usage.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    nested_project = usage.get('project')
    if isinstance(nested_project, dict):
        for key in ('projectKey', 'key', 'id'):
            value = nested_project.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    elif isinstance(nested_project, str) and nested_project.strip():
        return nested_project.strip()
    summary = usage.get('projectSummary')
    if isinstance(summary, dict):
        for key in ('projectKey', 'key', 'id'):
            value = summary.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _extract_usage_type(usage: Dict[str, Any]) -> str:
    """backend.py:2949 — verbatim."""
    for key in ('usageType', 'envUsage', 'type', 'objectType'):
        value = usage.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return 'UNKNOWN'


_USAGE_SENTINEL = object()


def _resolve_nested_path(payload: dict, path: str) -> Any:
    """backend.py:2997 _resolve_nested_path — verbatim."""
    current: Any = payload
    for part in path.split('.'):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return _USAGE_SENTINEL
    return current


def _extract_nested_text(payload: Any, *paths: str) -> Optional[str]:
    """backend.py:2989 — verbatim."""
    if not isinstance(payload, dict):
        return None
    for path in paths:
        value = _resolve_nested_path(payload, path)
        if value is _USAGE_SENTINEL:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_usage_object_type(usage: Dict[str, Any]) -> str:
    """backend.py:3064 — verbatim."""
    value = _extract_nested_text(usage, 'objectType', 'targetType', 'projectObjectType', 'object.type')
    if value:
        return value.upper()
    return _extract_usage_type(usage)


def _extract_usage_object_id(usage: Dict[str, Any]) -> str:
    """backend.py:3077 — verbatim."""
    value = _extract_nested_text(usage, 'objectId', 'targetId', 'id', 'object.id', 'objectSmartId')
    if value:
        return value
    return ''


def _extract_usage_object_name(usage: Dict[str, Any]) -> str:
    """backend.py:3091 — verbatim."""
    value = _extract_nested_text(usage, 'objectName', 'targetName', 'name', 'displayName',
                                 'object.name', 'object.displayName')
    if value:
        return value
    fallback = _extract_usage_object_id(usage)
    if fallback:
        return fallback
    return _extract_usage_object_type(usage)


def _normalize_usage_entry(usage: Dict[str, Any], project_names: Dict[str, Any]) -> Dict[str, Any]:
    """backend.py:3109 — verbatim."""
    project_key = _extract_usage_project_key(usage) or ''
    project_meta = project_names.get(project_key) or {}
    project_name = (
        _extract_nested_text(usage, 'projectSummary.name', 'project.name', 'projectName')
        or project_meta.get('name')
        or project_key
    )
    return {
        'projectKey': project_key,
        'projectName': project_name,
        'usageType': _extract_usage_type(usage),
        'objectType': _extract_usage_object_type(usage),
        'objectId': _extract_usage_object_id(usage),
        'objectName': _extract_usage_object_name(usage),
    }


def _usage_signature(usage: Dict[str, Any]) -> str:
    """backend.py:3135 — verbatim."""
    return '|'.join([
        str(usage.get('projectKey') or ''),
        str(usage.get('usageType') or ''),
        str(usage.get('objectType') or ''),
        str(usage.get('objectId') or ''),
        str(usage.get('objectName') or ''),
        str(usage.get('codeEnvKey') or ''),
    ])


def _dedupe_usage_entries(usages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """backend.py:3148 — verbatim."""
    seen = set()
    out: List[Dict[str, Any]] = []
    for usage in usages:
        sig = _usage_signature(usage)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(usage)
    return out


def get_code_env_size_map(client: Any) -> Dict[str, int]:
    """backend.py:4350 — 'lang:name' → bytes, from the global footprint."""
    size_by_env: Dict[str, int] = {}
    global_footprint = compute_footprint_payload(client, 'global', None)
    if isinstance(global_footprint, dict):
        section = global_footprint.get('codeEnvs')
        if isinstance(section, dict):
            items = section.get('items')
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    name = item.get('name')
                    language = str(item.get('language') or '').strip().lower()
                    if not name or not language:
                        continue
                    size_by_env[f"{language}:{name}"] = coerce_int(item.get('size'), 0)
    return size_by_env


def _code_env_version_label(env_listing: Dict[str, Any], language: str) -> str:
    """Mirror the version parsing in backend.py:_load_code_env_full_details,
    using only the listing (no per-env settings fetch)."""
    version = env_listing.get('pythonVersion') or env_listing.get('rVersion') or env_listing.get('version')
    if language == 'r':
        return str(version or 'R')
    raw_version_text = str(
        env_listing.get('pythonInterpreter') or version or 'Unknown'
    )
    match = re.search(r'PYTHON(\d)(\d+)', raw_version_text, flags=re.IGNORECASE)
    if match:
        return f"{int(match.group(1))}.{int(match.group(2))}"
    dotted = re.search(r'(\d+)\.(\d+)', raw_version_text)
    return f"{dotted.group(1)}.{dotted.group(2)}" if dotted else raw_version_text


_SKIP_DEPLOYMENT_MODES = {'PLUGIN_MANAGED', 'DSS_INTERNAL'}


def code_env_catalog(client: Any) -> Dict[str, Any]:
    """Code env inventory: per-env rows + python/R version counts.

    Lean port of api_code_envs (backend.py:6613). The current webapp defers the
    size map and loads it lazily via /api/code-envs/sizes; here it is merged in
    eagerly from the global footprint (_get_code_env_size_map) since the notebook
    is one-shot. The webapp's per-env settings fetch (an owner / version
    fallback) and its phase-4b usage-detail normalization are omitted —
    owner/version come from the listing — so each row stays a single bulk call
    instead of N, and the row drops the webapp's enrichment-only `ownerEmail`
    and `usageDetails` keys. Core row shape matches the webapp: name, version,
    language, sizeBytes, owner, usageCount, usageSummary, projectCount,
    projectKeys. totalEnvCount / skippedEnvCount and the python/R version counts
    match.
    """
    envs = [e for e in (client.list_code_envs() or []) if isinstance(e, dict)]
    total_env_count = len(envs)
    envs = [e for e in envs if str(e.get('deploymentMode') or '').upper() not in _SKIP_DEPLOYMENT_MODES]
    skipped_env_count = total_env_count - len(envs)

    size_by_env = get_code_env_size_map(client)

    usages_by_env: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for u in (client.list_code_env_usages() or []):
        if not isinstance(u, dict):
            continue
        k = (str(u.get('envLang', '')).upper(), str(u.get('envName', '')))
        usages_by_env.setdefault(k, []).append(u)

    code_envs: List[Dict[str, Any]] = []
    python_counts: Dict[str, int] = {}
    r_counts: Dict[str, int] = {}
    for env in envs:
        name = env.get('envName') or env.get('name') or env.get('id')
        if not name:
            continue
        language = normalize_language(env.get('envLang') or env.get('language') or env.get('type'))
        version_label = _code_env_version_label(env, language)
        owner = env.get('owner') if isinstance(env.get('owner'), str) and env.get('owner').strip() else 'Unknown'
        size_key = f"{language}:{name}"

        usages = usages_by_env.get((language.upper(), name), [])
        usage_counts: Dict[str, int] = {}
        project_keys: set = set()
        for raw in usages:
            usage_type = _extract_usage_type(raw)
            usage_counts[usage_type] = usage_counts.get(usage_type, 0) + 1
            pk = _extract_usage_project_key(raw)
            if pk:
                project_keys.add(pk)

        code_envs.append({
            'name': name,
            'version': version_label,
            'language': language,
            'sizeBytes': coerce_int(size_by_env.get(size_key), 0),
            'owner': owner,
            'usageCount': len(usages),
            'usageSummary': usage_counts,
            'projectCount': len(project_keys),
            'projectKeys': sorted(project_keys),
        })
        if language == 'r':
            r_counts[version_label] = r_counts.get(version_label, 0) + 1
        else:
            python_counts[version_label] = python_counts.get(version_label, 0) + 1

    code_envs.sort(key=lambda i: (coerce_int(i.get('sizeBytes'), 0), str(i.get('name') or '')), reverse=True)
    return {
        'codeEnvs': code_envs,
        'pythonVersionCounts': python_counts,
        'rVersionCounts': r_counts,
        'totalEnvCount': total_env_count,
        'skippedEnvCount': skipped_env_count,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Connection usages  (backend.py:5669 api_connection_usages — SSE→synchronous)
# Shared by 3 cards: connection-project-matrix, connection-usage, local-fs-usage.
# ─────────────────────────────────────────────────────────────────────────────
import json as _json  # noqa: E402


def _cex_item_raw(item: Any) -> Dict[str, Any]:
    raw = getattr(item, '_data', item)
    return raw if isinstance(raw, dict) else {}


def _find_llm_ids(d: Any):
    if isinstance(d, dict):
        for k, v in d.items():
            if k == 'llmId' and isinstance(v, str) and v:
                yield v
            else:
                yield from _find_llm_ids(v)
    elif isinstance(d, list):
        for item in d:
            yield from _find_llm_ids(item)


_LLM_RECIPE_PREFIXES = ('prompt', 'nlp_llm_')


def _parse_conn_params(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            import ast
            parsed = ast.literal_eval(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            try:
                parsed = _json.loads(value)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
    return {}


def _is_local_filesystem_connection(conn_name: Any, conn_types: Dict[str, str]) -> bool:
    name = str(conn_name or '').strip()
    if not name:
        return False
    if name == 'filesystem_root':
        return True
    typ = str(conn_types.get(name) or '').strip().lower()
    if not typ:
        return False
    if any(token in typ for token in ('s3', 'snowflake', 'jdbc', 'sql', 'hdfs', 'azure', 'gcs', 'google', 'adls')):
        return False
    return 'filesystem' in typ or typ in {'fs', 'localfs', 'local-filesystem', 'local_filesystem'}


def _folder_connection_from_raw(raw: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    params = _parse_conn_params(raw.get('params') or raw.get('folderParams') or raw.get('folderParamsMap'))
    conn_name = (params.get('connection') or params.get('connectionName')
                 or raw.get('connection') or raw.get('connectionName'))
    path = (params.get('path') or params.get('root') or params.get('folderPath') or raw.get('path'))
    return (str(conn_name) if conn_name else None, str(path) if path else None)


def _format_dataset_path(params: Dict[str, Any]) -> str:
    path = params.get('path')
    if isinstance(path, str) and path:
        return path
    rules = params.get('filesSelectionRules')
    if isinstance(rules, dict):
        explicit = rules.get('explicitFiles')
        if isinstance(explicit, list) and explicit:
            return ', '.join(str(f) for f in explicit if f)
        mode = rules.get('mode')
        return f'({mode})' if mode else ''
    return str(path) if path else ''


def _scan_project_connections(client: Any, project_key: str, conn_types: Dict[str, str]) -> Dict[str, Any]:
    proj = client.get_project(project_key)
    dataset_conns: List[Dict[str, Any]] = []
    llm_conns: List[Dict[str, Any]] = []
    local_fs_objects: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    try:
        for ds in proj.list_datasets():
            params = _parse_conn_params(ds.get('params', {}))
            conn_name = params.get('connection') if isinstance(params, dict) else None
            dataset_name = ds.get('name', '')
            dataset_type = ds.get('type', '')
            if conn_name:
                dataset_conns.append({'datasetName': dataset_name, 'datasetType': dataset_type, 'connection': conn_name})
                if _is_local_filesystem_connection(conn_name, conn_types):
                    local_fs_objects.append({
                        'objectType': 'dataset', 'objectId': dataset_name, 'objectName': dataset_name,
                        'objectSubtype': dataset_type, 'connection': conn_name, 'path': _format_dataset_path(params),
                    })
    except Exception as exc:
        errors.append({'projectKey': project_key, 'area': 'datasets',
                       'error': str(exc)[:240]})

    try:
        for folder in proj.list_managed_folders():
            raw = _cex_item_raw(folder)
            folder_id = str(raw.get('id') or raw.get('odbId') or raw.get('name') or '').strip()
            folder_name = str(raw.get('name') or folder_id or '').strip()
            conn_name, folder_path = _folder_connection_from_raw(raw)
            if not conn_name and folder_id:
                try:
                    folder_obj = proj.get_managed_folder(folder_id)
                    settings = folder_obj.get_settings() if hasattr(folder_obj, 'get_settings') else None
                    folder_raw = settings.get_raw() if settings is not None and hasattr(settings, 'get_raw') else {}
                    if isinstance(folder_raw, dict):
                        conn_name, folder_path = _folder_connection_from_raw(folder_raw)
                        folder_name = str(folder_raw.get('name') or folder_name or folder_id)
                except Exception:
                    pass
            if conn_name and _is_local_filesystem_connection(conn_name, conn_types):
                local_fs_objects.append({
                    'objectType': 'folder', 'objectId': folder_id, 'objectName': folder_name or folder_id,
                    'objectSubtype': str(raw.get('type') or 'managed folder'),
                    'connection': conn_name, 'path': folder_path or '',
                })
    except Exception as exc:
        errors.append({'projectKey': project_key, 'area': 'folders',
                       'error': str(exc)[:240]})

    try:
        recipes = proj.list_recipes()
        llm_recipes = [r for r in recipes
                       if r.get('type', '').startswith(_LLM_RECIPE_PREFIXES) or 'llm' in r.get('type', '').lower()]
        for r in llm_recipes:
            try:
                recipe = proj.get_recipe(r['name'])
                settings = recipe.get_settings()
                payload = settings.get_json_payload() if hasattr(settings, 'get_json_payload') else None
                if not payload:
                    raw_str = settings.get_payload() if hasattr(settings, 'get_payload') else ''
                    try:
                        payload = _json.loads(raw_str) if raw_str else {}
                    except Exception:
                        payload = {}
                if not payload:
                    continue
                for llm_id in _find_llm_ids(payload):
                    parts = llm_id.split(':')
                    if len(parts) >= 3:
                        llm_conns.append({'recipeName': r.get('name', ''), 'recipeType': r.get('type', ''),
                                          'llmId': llm_id, 'connection': parts[1]})
            except Exception as exc:
                errors.append({'projectKey': project_key, 'area': 'recipes',
                               'error': str(exc)[:240]})
    except Exception as exc:
        errors.append({'projectKey': project_key, 'area': 'recipes',
                       'error': str(exc)[:240]})

    return {'projectKey': project_key, 'datasetConns': dataset_conns,
            'llmConns': llm_conns, 'localFilesystemObjects': local_fs_objects,
            'errors': errors}


def connection_usages(client: Any) -> Dict[str, Any]:
    """Connection→project usage map: dataset connections, LLM-recipe connections,
    and local-filesystem objects. Synchronous port of api_connection_usages
    (backend.py:5669) — scans every project serially via the SDK.

    Returns {datasetUsages, llmUsages, localFilesystemUsages}.
    """
    catalog = _projects_catalog_cheap(client)
    project_names = {p['key']: p.get('name', p['key']) for p in catalog}
    project_owner_by_key = {p['key']: p.get('owner', 'Unknown') for p in catalog}
    users = client.list_users() if hasattr(client, 'list_users') else []
    user_email_by_login = {str(u.get('login')): str(u.get('email') or u.get('login'))
                           for u in users if isinstance(u, dict) and u.get('login')}

    connections = client.list_connections()
    conn_types: Dict[str, str] = {}
    if isinstance(connections, dict):
        for name, config in connections.items():
            if isinstance(config, dict):
                conn_types[name] = config.get('type', 'unknown')
    else:
        for c in connections:
            conn_types[c.get('name', '')] = c.get('type', 'unknown')

    dataset_map: Dict[str, List[Dict]] = {}
    llm_map: Dict[str, List[Dict]] = {}
    local_fs_usages: List[Dict[str, Any]] = []
    scan_errors: List[Dict[str, Any]] = []
    for pk in project_names:
        try:
            result = _scan_project_connections(client, pk, conn_types)
        except Exception as exc:
            result = {'projectKey': pk, 'datasetConns': [], 'llmConns': []}
            scan_errors.append({'projectKey': pk, 'area': 'scan', 'error': str(exc)[:240]})
        scan_errors.extend(result.get('errors', []) or [])
        pname = project_names.get(pk, pk)
        owner = str(project_owner_by_key.get(pk) or 'Unknown')
        owner_email = user_email_by_login.get(owner, owner)
        for u in result.get('datasetConns', []):
            dataset_map.setdefault(u['connection'], []).append({
                'projectKey': pk, 'projectName': pname,
                'datasetName': u['datasetName'], 'datasetType': u['datasetType']})
        for u in result.get('llmConns', []):
            llm_map.setdefault(u['connection'], []).append({
                'projectKey': pk, 'projectName': pname, 'recipeName': u['recipeName'],
                'recipeType': u['recipeType'], 'llmId': u['llmId']})
        for u in result.get('localFilesystemObjects', []):
            if not isinstance(u, dict):
                continue
            local_fs_usages.append({
                'owner': owner, 'ownerEmail': owner_email, 'projectKey': pk, 'projectName': pname,
                'objectType': u.get('objectType') or 'object', 'objectId': u.get('objectId') or '',
                'objectName': u.get('objectName') or u.get('objectId') or '',
                'objectSubtype': u.get('objectSubtype') or '', 'connection': u.get('connection') or '',
                'path': u.get('path') or '',
            })

    dataset_usages = [{
        'name': conn_name, 'type': conn_types.get(conn_name, 'unknown'), 'projects': dataset_map[conn_name],
        'projectCount': len(set(u['projectKey'] for u in dataset_map[conn_name])),
        'datasetCount': len(dataset_map[conn_name]),
    } for conn_name in sorted(dataset_map.keys())]

    llm_usages = [{
        'name': conn_name, 'type': conn_types.get(conn_name, 'unknown'), 'projects': llm_map[conn_name],
        'projectCount': len(set(u['projectKey'] for u in llm_map[conn_name])),
        'recipeCount': len(llm_map[conn_name]),
    } for conn_name in sorted(llm_map.keys())]

    return {
        'datasetUsages': dataset_usages,
        'llmUsages': llm_usages,
        'localFilesystemUsages': sorted(local_fs_usages, key=lambda item: (
            str(item.get('owner') or '').lower(), str(item.get('projectKey') or '').lower(),
            str(item.get('objectName') or '').lower())),
        'scanErrors': scan_errors,
        'failedProjectCount': len({e['projectKey'] for e in scan_errors}),
        'scannedProjectCount': len(project_names),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Container execs  (backend.py:9501 api_container_execs / _cex_scan)
# Backs 4 cards: exec-config-summary, project-overrides-summary, mode-tables,
# project-breakdown. Faithful port — timeout/events/progress machinery dropped.
# ─────────────────────────────────────────────────────────────────────────────
_CEX_CODE_RECIPE_TYPES = {'python', 'r'}
_CEX_NON_CARRIER_RECIPE_TYPES = {'pyspark', 'spark_scala', 'spark_sql_query', 'shell'}


def _cex_path_get(raw: Any, path: str) -> Any:
    current = raw
    for part in path.split('.'):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _cex_effective(selection: Any, fallback: Optional[str]) -> Tuple[str, Optional[str], bool]:
    if not isinstance(selection, dict):
        return 'MISSING', None, False
    mode = str(selection.get('containerMode') or 'INHERIT').upper()
    explicit = selection.get('containerConf')
    if mode == 'EXPLICIT_CONTAINER' and explicit:
        return mode, str(explicit), False
    if mode == 'INHERIT':
        return mode, fallback, True
    return mode, None, False


def _cex_clean_config(config: Dict[str, Any]) -> Dict[str, Any]:
    keys = ['name', 'type', 'usableBy', 'allowedGroups', 'workloadType', 'dockerNetwork',
            'kubernetesNamespace', 'repositoryURL', 'baseImageType', 'prePushMode',
            'nodeSelector', 'dockerTLSVerify']
    cleaned = {key: config.get(key) for key in keys if key in config}
    krc = config.get('kubernetesRuntimeConfig')
    if isinstance(krc, dict):
        cleaned.update({key: krc[key] for key in ('kubernetesNamespace', 'nodeSelector') if key in krc})
    return cleaned


def _cex_explicit_config(selection: Any) -> Optional[str]:
    if not isinstance(selection, dict):
        return None
    mode = str(selection.get('containerMode') or 'INHERIT').upper()
    conf = selection.get('containerConf')
    if mode == 'EXPLICIT_CONTAINER' and conf:
        return str(conf)
    return None


def _cex_is_same_config(left: Optional[str], right: Optional[str]) -> bool:
    return bool(left) and bool(right) and str(left) == str(right)


def _cex_is_visible_project_override(selection: Any, global_default: Optional[str]) -> bool:
    conf = _cex_explicit_config(selection)
    return bool(conf) and not _cex_is_same_config(conf, global_default)


def _cex_is_visible_job_override(selection: Any, project_config: Optional[str], global_default: Optional[str]) -> bool:
    conf = _cex_explicit_config(selection)
    if not conf:
        return False
    if _cex_is_same_config(conf, global_default):
        return False
    if _cex_is_same_config(conf, project_config):
        return False
    return True


def _cex_add_row(rows: List[Dict[str, Any]], **kwargs) -> None:
    selection = kwargs.pop('selection', None)
    fallback_config = kwargs.pop('fallback_config', None)
    inherited_from = kwargs.pop('inherited_from', None)
    mode, effective, inherited = _cex_effective(selection, fallback_config)
    container_conf = str(selection.get('containerConf')) if isinstance(selection, dict) and selection.get('containerConf') else None
    row = {
        'id': '|'.join([
            str(kwargs.get('project_key') or ''),
            str(kwargs.get('object_type') or ''),
            str(kwargs.get('object_id') or ''),
            str(kwargs.get('surface') or ''),
            str(kwargs.get('raw_path') or ''),
        ]),
        'projectKey': kwargs.get('project_key') or '',
        'projectName': kwargs.get('project_name') or kwargs.get('project_key') or '',
        'objectType': kwargs.get('object_type') or '',
        'objectId': kwargs.get('object_id') or '',
        'objectName': kwargs.get('object_name') or kwargs.get('object_id') or '',
        'surface': kwargs.get('surface') or '',
        'surfaceLabel': kwargs.get('surface_label') or kwargs.get('surface') or '',
        'rawPath': kwargs.get('raw_path') or '',
        'containerMode': mode,
        'containerConf': container_conf,
        'effectiveContainerConf': effective,
        'inheritedFrom': inherited_from if inherited else None,
        'writable': bool(kwargs.get('writable')),
        'replacementSupported': bool(kwargs.get('replacement_supported')),
        'notes': kwargs.get('notes') or '',
        'overrideLevel': kwargs.get('override_level') or '',
        'objectSubtype': kwargs.get('object_subtype') or '',
        'projectConfig': kwargs.get('project_config'),
    }
    extra = kwargs.get('extra')
    if isinstance(extra, dict):
        row.update(extra)
    rows.append(row)


def _cex_group_project_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        project_key = str(row.get('projectKey') or '')
        if not project_key:
            continue
        group = groups.setdefault(project_key, {
            'projectKey': project_key, 'projectName': row.get('projectName') or project_key,
            'projectOverrides': [], 'jobOverrides': []})
        if row.get('overrideLevel') == 'project':
            group['projectOverrides'].append(row)
        elif row.get('overrideLevel') == 'job':
            group['jobOverrides'].append(row)
    return [g for g in sorted(groups.values(), key=lambda i: str(i.get('projectKey') or ''))
            if g.get('projectOverrides') or g.get('jobOverrides')]


def container_execs(client: Any) -> Dict[str, Any]:
    """Container-execution-config inventory + per-project/per-object explicit
    overrides. Faithful synchronous port of _cex_scan (backend.py:8983).

    Returns {configs, usageRows, projectRows, summary, nonCarrierCounts, events,
    scanErrors, failedProjectCount, scannedProjectCount, timedOut, elapsedMs,
    configNames, globalDefaultConfig}.

    NOTE: scans every project's recipes / webapps / ML tasks via the SDK +
    _perform_json — slow on large instances (the same heavy work the webapp
    streams behind a progress bar). The deadline/timeout machinery is dropped,
    so timedOut is always False here.
    """
    started = time.time()
    usage_rows: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    non_carrier_counts: Dict[str, int] = {
        'jupyterNotebooks': 0, 'sqlNotebooks': 0, 'scenarios': 0, 'apiServices': 0,
        'sparkRecipes': 0, 'shellRecipes': 0, 'modelEvaluationStores': 0, 'modelComparisons': 0}

    def event(step: str, message: str, project_key: str = '', level: str = 'info') -> None:
        events.append({
            'tMs': round((time.time() - started) * 1000.0, 2),
            'level': level,
            'step': step,
            'message': message,
            'projectKey': project_key,
        })

    configs_raw: List[Dict[str, Any]] = []
    global_default = None
    try:
        settings = client.get_general_settings().get_raw()
        container_settings = settings.get('containerSettings') if isinstance(settings, dict) else {}
        if isinstance(container_settings, dict):
            configs_raw = [cfg for cfg in (container_settings.get('executionConfigs') or []) if isinstance(cfg, dict)]
            if container_settings.get('defaultExecutionConfig'):
                global_default = str(container_settings.get('defaultExecutionConfig'))
    except Exception as exc:
        event('general_settings_error', str(exc)[:200], '*', 'warn')

    configs = [_cex_clean_config(cfg) for cfg in configs_raw]
    config_names = sorted({str(cfg.get('name')) for cfg in configs_raw if cfg.get('name')})
    template_default_by_id: Dict[str, Optional[str]] = {}
    try:
        for template_item in client.list_code_studio_templates() or []:
            raw_item = _cex_item_raw(template_item)
            template_id = str(raw_item.get('id') or raw_item.get('templateId') or raw_item.get('name') or '').strip()
            if not template_id:
                continue
            try:
                template_raw = client.get_code_studio_template(template_id).get_settings().get_raw()
            except Exception as exc:
                event('code_studio_template_error', str(exc)[:200], '*', 'warn')
                template_raw = raw_item
            default_conf = template_raw.get('defaultContainerConf') if isinstance(template_raw, dict) else None
            template_default_by_id[template_id] = str(default_conf) if default_conf else None
    except Exception as exc:
        event('code_studio_templates_error', str(exc)[:200], '*', 'warn')

    catalog = _projects_catalog_cheap(client)
    for project_meta in catalog:
        project_key = str(project_meta.get('key') or '')
        project_name = str(project_meta.get('name') or project_key)
        if not project_key:
            continue
        try:
            project = client.get_project(project_key)
            settings_raw = project.get_settings().get_raw()
        except Exception as exc:
            event('project_settings_error', str(exc)[:200], project_key, 'warn')
            continue

        code_sel = _cex_path_get(settings_raw, 'settings.container')
        visual_sel = _cex_path_get(settings_raw, 'settings.containerForVisualRecipesWorkloads')
        webapp_sel = _cex_path_get(settings_raw, 'settings.virtualWebAppBackendSettings.infra.containerSelection')
        code_mode, code_effective, _ = _cex_effective(code_sel, global_default)
        visual_mode, visual_effective, _ = _cex_effective(visual_sel, global_default)
        webapp_mode, webapp_effective, _ = _cex_effective(webapp_sel, global_default)

        for surface, label, path, selection, mode, notes in (
            ('project_code_default', 'Project code workload default', 'settings.container', code_sel, code_mode, 'Default for Python/R code workloads'),
            ('project_visual_default', 'Project visual recipe default', 'settings.containerForVisualRecipesWorkloads', visual_sel, visual_mode, 'Default for visual recipes using the DSS engine'),
            ('project_webapp_default', 'Project webapp backend default', 'settings.virtualWebAppBackendSettings.infra.containerSelection', webapp_sel, webapp_mode, 'Default for webapp backends'),
        ):
            if mode != 'EXPLICIT_CONTAINER' or not _cex_is_visible_project_override(selection, global_default):
                continue
            _cex_add_row(usage_rows, project_key=project_key, project_name=project_name,
                         object_type='PROJECT', object_id=project_key, object_name=project_name,
                         surface=surface, surface_label=label, raw_path=path, selection=selection,
                         fallback_config=global_default, inherited_from='global default',
                         writable=True, replacement_supported=True, notes=notes, override_level='project',
                         object_subtype=label, project_config=global_default)

        remap = _cex_path_get(settings_raw, 'bundleContainerSettings.remapping')
        if isinstance(remap, dict):
            for item in (remap.get('containerExecs') or []):
                if not isinstance(item, dict):
                    continue
                for field in ('source', 'target'):
                    conf = item.get(field)
                    if not conf:
                        continue
                    non_carrier_counts['bundleRemaps'] = non_carrier_counts.get('bundleRemaps', 0) + 1

        try:
            recipes = project.list_recipes() or []
        except Exception as exc:
            event('recipes_error', str(exc)[:200], project_key, 'warn')
            recipes = []
        for recipe_item in recipes:
            if not isinstance(recipe_item, dict):
                continue
            recipe_name = str(recipe_item.get('name') or recipe_item.get('id') or '')
            recipe_type = str(recipe_item.get('type') or '').lower()
            if not recipe_name:
                continue
            try:
                recipe_raw = client._perform_json('GET', f'/projects/{project_key}/recipes/{recipe_name}')
                recipe_def = recipe_raw.get('recipe') if isinstance(recipe_raw, dict) else None
            except Exception as exc:
                event('recipe_error', f'{recipe_name}: {exc}'[:200], project_key, 'warn')
                continue
            if not isinstance(recipe_def, dict):
                continue
            if recipe_type in _CEX_CODE_RECIPE_TYPES:
                selection = _cex_path_get(recipe_def, 'params.containerSelection')
                if isinstance(selection, dict):
                    mode, _, _ = _cex_effective(selection, code_effective)
                    if mode != 'EXPLICIT_CONTAINER' or not _cex_is_visible_job_override(selection, code_effective, global_default):
                        continue
                    _cex_add_row(usage_rows, project_key=project_key, project_name=project_name,
                                 object_type='RECIPE', object_id=recipe_name, object_name=recipe_name,
                                 surface='recipe_code', surface_label='Python/R code recipe',
                                 raw_path='recipe.params.containerSelection', selection=selection,
                                 fallback_config=code_effective, inherited_from='project code workload default',
                                 writable=True, replacement_supported=True, notes=f'{recipe_type} recipe',
                                 override_level='job', object_subtype=f'{recipe_type} recipe',
                                 project_config=code_effective, extra={'recipeType': recipe_type})
            elif recipe_type in _CEX_NON_CARRIER_RECIPE_TYPES:
                non_carrier_counts['shellRecipes' if recipe_type == 'shell' else 'sparkRecipes'] += 1

            visual_selection = _cex_path_get(recipe_def, 'params.engineParams.containerSelection')
            if isinstance(visual_selection, dict):
                mode, _, _ = _cex_effective(visual_selection, visual_effective)
                if mode != 'EXPLICIT_CONTAINER' or not _cex_is_visible_job_override(visual_selection, visual_effective, global_default):
                    continue
                _cex_add_row(usage_rows, project_key=project_key, project_name=project_name,
                             object_type='RECIPE', object_id=recipe_name, object_name=recipe_name,
                             surface='recipe_visual', surface_label='Visual recipe',
                             raw_path='recipe.params.engineParams.containerSelection', selection=visual_selection,
                             fallback_config=visual_effective, inherited_from='project visual recipe default',
                             writable=True, replacement_supported=True, notes=f'{recipe_type} recipe using DSS engine',
                             override_level='job', object_subtype=f'{recipe_type} visual recipe',
                             project_config=visual_effective, extra={'recipeType': recipe_type})

        try:
            webapps = project.list_webapps() or []
        except Exception as exc:
            event('webapps_error', str(exc)[:200], project_key, 'warn')
            webapps = []
        for webapp_item in webapps:
            webapp_raw = _cex_item_raw(webapp_item)
            webapp_id = str(webapp_raw.get('id') or '')
            if not webapp_id:
                continue
            try:
                detail = project.get_webapp(webapp_id).get_settings().get_raw()
            except Exception as exc:
                event('webapp_error', f'{webapp_id}: {exc}'[:200], project_key, 'warn')
                continue
            selection = _cex_path_get(detail, 'params.infra.containerSelection')
            if isinstance(selection, dict):
                mode, _, _ = _cex_effective(selection, webapp_effective)
                if mode != 'EXPLICIT_CONTAINER' or not _cex_is_visible_job_override(selection, webapp_effective, global_default):
                    continue
                _cex_add_row(usage_rows, project_key=project_key, project_name=project_name,
                             object_type='WEBAPP', object_id=webapp_id,
                             object_name=str(detail.get('name') or webapp_raw.get('name') or webapp_id),
                             surface='webapp_backend', surface_label='Webapp backend',
                             raw_path='params.infra.containerSelection', selection=selection,
                             fallback_config=webapp_effective, inherited_from='project webapp backend default',
                             writable=True, replacement_supported=True,
                             notes=str(detail.get('type') or webapp_raw.get('type') or 'webapp'),
                             override_level='job',
                             object_subtype=str(detail.get('type') or webapp_raw.get('type') or 'webapp'),
                             project_config=webapp_effective)

        try:
            lab = client._perform_json('GET', f'/projects/{project_key}/models/lab/')
            tasks = lab.get('mlTasks') if isinstance(lab, dict) else []
        except Exception as exc:
            event('ml_tasks_error', str(exc)[:200], project_key, 'warn')
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
                event('ml_task_error', f'{task_id}: {exc}'[:200], project_key, 'warn')
                continue
            selection = task_settings.get('containerSelection') if isinstance(task_settings, dict) else None
            if isinstance(selection, dict):
                mode, _, _ = _cex_effective(selection, code_effective)
                if mode != 'EXPLICIT_CONTAINER' or not _cex_is_visible_job_override(selection, code_effective, global_default):
                    continue
                _cex_add_row(usage_rows, project_key=project_key, project_name=project_name,
                             object_type='ML_TASK', object_id=f'{analysis_id}/{task_id}',
                             object_name=str(task.get('mlTaskName') or task_id),
                             surface='ml_task', surface_label='ML task', raw_path='containerSelection',
                             selection=selection, fallback_config=code_effective,
                             inherited_from='project/container default', writable=True, replacement_supported=True,
                             notes=str(task.get('taskType') or ''), override_level='job',
                             object_subtype=str(task.get('taskType') or 'ML task'), project_config=code_effective,
                             extra={'analysisId': analysis_id, 'mlTaskId': task_id})

        for key, getter in (
            ('jupyterNotebooks', lambda: project.list_jupyter_notebooks(as_type='listitems')),
            ('sqlNotebooks', lambda: project.list_sql_notebooks(as_type='listitems')),
            ('scenarios', lambda: project.list_scenarios()),
            ('apiServices', lambda: project.list_api_services(as_type='listitems')),
            ('modelEvaluationStores', lambda: project.list_model_evaluation_stores()),
            ('modelComparisons', lambda: project.list_model_comparisons()),
        ):
            try:
                non_carrier_counts[key] += len(getter() or [])
            except Exception as exc:
                event(f'{key}_error', str(exc)[:200], project_key, 'warn')

        try:
            studios = project.list_code_studios(as_type='listitems') or []
        except Exception:
            studios = []
        for studio_item in studios:
            studio_raw = _cex_item_raw(studio_item)
            studio_id = str(studio_raw.get('id') or '')
            template_id = str(studio_raw.get('templateId') or '')
            if not studio_id:
                continue
            if template_id and template_default_by_id.get(template_id):
                non_carrier_counts['codeStudioTemplateReferences'] = non_carrier_counts.get('codeStudioTemplateReferences', 0) + 1

    by_config: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    by_mode: Dict[str, int] = {}
    explicit = supported = project_override_rows = job_override_rows = 0
    projects_with_explicit = set()
    for row in usage_rows:
        conf = str(row.get('containerConf') or row.get('effectiveContainerConf') or 'none')
        by_config[conf] = by_config.get(conf, 0) + 1
        typ = str(row.get('objectType') or 'UNKNOWN')
        by_type[typ] = by_type.get(typ, 0) + 1
        mode = str(row.get('containerMode') or 'UNKNOWN')
        by_mode[mode] = by_mode.get(mode, 0) + 1
        explicit += 1 if mode == 'EXPLICIT_CONTAINER' else 0
        supported += 1 if row.get('replacementSupported') else 0
        project_override_rows += 1 if row.get('overrideLevel') == 'project' else 0
        job_override_rows += 1 if row.get('overrideLevel') == 'job' else 0
        if row.get('projectKey'):
            projects_with_explicit.add(str(row.get('projectKey')))

    project_rows = _cex_group_project_rows(usage_rows)
    project_override_count = len([row for row in project_rows if row.get('projectOverrides')])

    scan_errors = [
        {
            'projectKey': str(ev.get('projectKey')),
            'area': str(ev.get('area') or ev.get('step') or 'scan'),
            'error': str(ev.get('message') or ev.get('error') or '')[:240],
        }
        for ev in events
        if ev.get('level') in ('warn', 'error') and ev.get('projectKey') and ev.get('projectKey') != '*'
    ]
    failed_project_count = len({err['projectKey'] for err in scan_errors})

    return {
        'configs': configs,
        'usageRows': usage_rows,
        'projectRows': project_rows,
        'summary': {
            'configCount': len(configs),
            'usageCount': len(usage_rows),
            'explicitUsageCount': explicit,
            'inheritedUsageCount': 0,
            'replacementSupportedCount': supported,
            'projectOverrideCount': project_override_count,
            'projectOverrideRowCount': project_override_rows,
            'jobOverrideCount': job_override_rows,
            'byConfig': by_config,
            'byObjectType': by_type,
            'byMode': by_mode,
            'projectCount': len(catalog),
            'projectUsageCount': len(projects_with_explicit),
        },
        'nonCarrierCounts': non_carrier_counts,
        'events': events[-500:],
        'scanErrors': scan_errors,
        'failedProjectCount': failed_project_count,
        'scannedProjectCount': len(catalog),
        'timedOut': False,
        'elapsedMs': round((time.time() - started) * 1000.0, 2),
        'configNames': config_names,
        'globalDefaultConfig': global_default,
    }
