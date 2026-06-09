"""Connections routes: inventory, config audit, health tests (SSE), usage scan (SSE).

The two SSE endpoints read `g.client` inside their generators, so they wrap
with `stream_with_context`. Worker threads use the host-context-propagating
`ThreadPoolExecutor` from adk_backend.clients (NOT concurrent.futures'), so
`_thread_client()` keeps targeting the selected host.
"""

import hashlib
import json
import logging
import threading
import time
from concurrent.futures import as_completed
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, Response, g, jsonify, stream_with_context

from adk_backend.caching import _cache_get, _get_session_epoch
from adk_backend.clients import (
    ThreadPoolExecutor,
    _list_projects_catalog_cheap,
    _safe_request_host_id,
    _sdk_fetch,
    _thread_client,
)
from adk_backend.settings import _BACKEND_SETTINGS
from adk_backend.utils import _cex_item_raw, _find_llm_ids

bp = Blueprint('connections', __name__)

_LOGGER = logging.getLogger(__name__)


@bp.route('/api/connections')
def api_connections():
    client = g.client

    def loader():
        connections = _sdk_fetch(
            'list_connections',
            _BACKEND_SETTINGS['cache_ttl_overview'],
            lambda: client.list_connections(),
        )
        connection_counts: Dict[str, int] = {}
        details: List[Dict[str, Any]] = []

        if isinstance(connections, dict):
            items = connections.items()
        else:
            items = [(c.get('name'), c) for c in connections]

        for name, config in items:
            if not isinstance(config, dict):
                continue
            conn_type = config.get('type')
            if conn_type == 'EC2':
                conn_type = 'S3'
            if not conn_type:
                continue
            driver = None
            params = config.get('params') or {}
            if isinstance(params, dict):
                driver = params.get('driverClassName')

            display_type = conn_type
            if conn_type == 'JDBC' and driver:
                short_driver = driver if len(driver) <= 50 else driver[:47] + '...'
                display_type = f"JDBC ({short_driver})"

            details.append({
                'name': name or 'unknown',
                'type': conn_type,
                'driverClassName': driver,
            })

            connection_counts[display_type] = connection_counts.get(display_type, 0) + 1

        return {'connections': connection_counts, 'connectionDetails': details}

    data = _cache_get('connections', _BACKEND_SETTINGS['cache_ttl_connections'], loader)
    return jsonify(data)


_CLOUD_HDFS_INTERFACES = {
    'S3': ('S3A', 'EMRFS'),
    'EC2': ('S3A', 'EMRFS'),
    'Azure': ('ABFS', 'WASB', 'WASBS'),
    'GCS': ('GS',),
}


def _audit_details_readable(config: dict) -> bool:
    """True if connection details are readable by at least one group (ALL or ALLOWED with groups)."""
    dr = config.get('detailsReadability') or {}
    mode = dr.get('readableBy')
    if mode == 'ALL':
        return True
    if mode == 'ALLOWED' and dr.get('allowedGroups'):
        return True
    return False


def _audit_connection(name: str, config: dict) -> dict:
    """Inspect one connection and return {name,type,configIssues,severity}."""
    conn_type = config.get('type') or 'Unknown'
    params = config.get('params') if isinstance(config.get('params'), dict) else {}
    issues: List[str] = []
    severity = 'info'

    if conn_type == 'Filesystem' and name == 'filesystem_root':
        issues.append('Default filesystem_root connection should be removed')
        severity = 'critical'

    elif conn_type in ('S3', 'EC2', 'Azure', 'GCS'):
        if not _audit_details_readable(config):
            issues.append('Connection details not readable by any group (detailsReadability)')
        allowed_interfaces = _CLOUD_HDFS_INTERFACES.get(conn_type, ())
        hdfs_interface = params.get('hdfsInterface') or ''
        if not hdfs_interface:
            issues.append('HDFS interface not configured')
        elif allowed_interfaces and hdfs_interface not in allowed_interfaces:
            issues.append('HDFS interface %s not in recommended %s' % (hdfs_interface, '/'.join(allowed_interfaces)))
        if issues:
            severity = 'warning'

    elif conn_type == 'Snowflake':
        if not params.get('useSparkNative'):
            issues.append('Spark native integration not enabled (useSparkNative)')
        if not params.get('useUDF'):
            issues.append('UDF support not enabled (useUDF)')
        if not params.get('autoFastWriteConnection'):
            issues.append('Fast-write connection not configured (autoFastWriteConnection)')
        if not _audit_details_readable(config):
            issues.append('Connection details not readable by any group (detailsReadability)')
        if issues:
            severity = 'warning'

    elif conn_type == 'Databricks':
        if not params.get('autoFastWriteConnection'):
            issues.append('Fast-write connection not configured (autoFastWriteConnection)')
        if not _audit_details_readable(config):
            issues.append('Connection details not readable by any group (detailsReadability)')
        if issues:
            severity = 'warning'

    elif conn_type in ('Redshift', 'BigQuery', 'Synapse'):
        if not params.get('autoFastWriteConnection'):
            issues.append('Fast-write connection not configured (autoFastWriteConnection)')
        if issues:
            severity = 'warning'

    return {
        'name': name,
        'type': conn_type,
        'configIssues': issues,
        'severity': severity,
    }


@bp.route('/api/connections/audit')
def api_connections_audit():
    """Audit connection configuration (fast-write, details readability, HDFS interface, filesystem_root)."""
    _LOGGER.info("[connections-audit] endpoint hit")
    client = g.client

    def loader():
        connections = _sdk_fetch(
            'list_connections',
            _BACKEND_SETTINGS['cache_ttl_connections'],
            lambda: client.list_connections(),
        )
        if isinstance(connections, dict):
            items = connections.items()
        else:
            items = [(c.get('name'), c) for c in connections]

        results: List[Dict[str, Any]] = []
        summary = {'critical': 0, 'warning': 0, 'info': 0, 'total': 0}
        for name, config in items:
            if not name or not isinstance(config, dict):
                continue
            summary['total'] += 1
            audit = _audit_connection(name, config)
            if audit['configIssues']:
                results.append(audit)
                sev = audit['severity']
                if sev in summary:
                    summary[sev] += 1
        return {'connections': results, 'summary': summary}

    data = _cache_get('connections_audit', _BACKEND_SETTINGS['cache_ttl_connections'], loader)
    return jsonify(data)


_CONN_HEALTH_MEMO: Dict[Tuple[str, int, str], Dict[str, Any]] = {}
_CONN_HEALTH_MEMO_LOCK = threading.Lock()


@bp.route('/api/connections/health')
def api_connection_health():
    """Stream connection health-test results via SSE.

    Memoized by (session_epoch, connection_set_hash). If the same set was
    tested earlier in this epoch, replay the cached events and skip the
    141×850ms work. Global Refresh bumps the epoch and invalidates the memo.
    """
    import re
    _SANITIZE_RE = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b|/[\w/.-]{4,}')

    def _test_one(name, conn_type):
        try:
            client = _thread_client()
            resp = client.get_connection(name).test()
            ok = resp.get('connectionOK', False) if isinstance(resp, dict) else False
            if ok:
                return {'name': name, 'type': conn_type, 'status': 'ok'}
            error_msg = ''
            if isinstance(resp, dict):
                error_msg = resp.get('connectionErrorMsg') or resp.get('message') or ''
            sanitized = _SANITIZE_RE.sub('***', error_msg)[:200] if error_msg else 'Connection test failed'
            return {'name': name, 'type': conn_type, 'status': 'fail', 'error': sanitized}
        except Exception as exc:
            msg = str(exc)
            if 'NotImplementedException' in msg or 'not implemented' in msg.lower():
                return {'name': name, 'type': conn_type, 'status': 'skipped'}
            sanitized = _SANITIZE_RE.sub('***', msg)[:200]
            return {'name': name, 'type': conn_type, 'status': 'fail', 'error': sanitized}

    def generate():
        t0 = time.time()
        try:
            connections = _sdk_fetch(
                'list_connections',
                _BACKEND_SETTINGS['cache_ttl_connections'],
                lambda: g.client.list_connections(),
            )
            if isinstance(connections, dict):
                items = list(connections.items())
            else:
                items = [(c.get('name'), c) for c in connections]
        except Exception as e:
            yield "event: error\ndata: %s\n\n" % json.dumps({'error': str(e)[:200]})
            return

        epoch = _get_session_epoch()
        item_names = sorted([str(n) for n, _ in items if n])
        item_hash = hashlib.sha1('\n'.join(item_names).encode('utf-8')).hexdigest()
        memo_key = (_safe_request_host_id(), epoch, item_hash)
        with _CONN_HEALTH_MEMO_LOCK:
            cached = _CONN_HEALTH_MEMO.get(memo_key)
        if cached is not None:
            yield "event: init\ndata: %s\n\n" % json.dumps({'total': len(items), 'cached': True})
            for result in cached.get('results', []):
                yield "event: conn\ndata: %s\n\n" % json.dumps(result)
            yield "event: done\ndata: %s\n\n" % json.dumps(cached.get('done') or {})
            return

        yield "event: init\ndata: %s\n\n" % json.dumps({'total': len(items)})

        ok_count = 0
        fail_count = 0
        skipped_count = 0
        collected_results: List[Dict[str, Any]] = []
        workers = min(8, max(1, len(items)))
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            futures = {
                pool.submit(_test_one, name, (config.get('type', 'unknown') if isinstance(config, dict) else 'unknown')): name
                for name, config in items if name
            }
            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception as exc:
                    result = {'name': futures[future], 'type': 'unknown', 'status': 'fail',
                              'error': str(exc)[:200]}
                st = result.get('status')
                if st == 'ok':
                    ok_count += 1
                elif st == 'fail':
                    fail_count += 1
                else:
                    skipped_count += 1
                collected_results.append(result)
                yield "event: conn\ndata: %s\n\n" % json.dumps(result)
        except GeneratorExit:
            pool.shutdown(wait=False, cancel_futures=True)
            return
        finally:
            pool.shutdown(wait=False)

        testable = ok_count + fail_count
        pct = round((ok_count / testable) * 100) if testable > 0 else 100

        total_ms = int((time.time() - t0) * 1000)
        done_payload = {
            'total_ms': total_ms,
            'summary': {
                'total': len(items),
                'ok': ok_count,
                'fail': fail_count,
                'skipped': skipped_count,
                'healthPct': pct,
            },
        }
        with _CONN_HEALTH_MEMO_LOCK:
            # Drop other epochs' entries (keep only current).
            stale = [k for k in _CONN_HEALTH_MEMO if len(k) < 2 or k[1] != epoch]
            for k in stale:
                _CONN_HEALTH_MEMO.pop(k, None)
            _CONN_HEALTH_MEMO[memo_key] = {'results': collected_results, 'done': done_payload}
        yield "event: done\ndata: %s\n\n" % json.dumps(done_payload)

    return Response(stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@bp.route('/api/connections/usages')
def api_connection_usages():
    """Stream connection-project usage mapping via SSE.

    Scans all projects to find:
    - Dataset connections (params.connection)
    - LLM recipe connections (llmId field in recipe payload)
    """

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
                    parsed = json.loads(value)
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
        conn_name = (
            params.get('connection')
            or params.get('connectionName')
            or raw.get('connection')
            or raw.get('connectionName')
        )
        path = (
            params.get('path')
            or params.get('root')
            or params.get('folderPath')
            or raw.get('path')
        )
        return (str(conn_name) if conn_name else None, str(path) if path else None)

    def _format_dataset_path(params: Dict[str, Any]) -> str:
        path = params.get('path')
        if isinstance(path, str) and path:
            return path
        rules = params.get('filesSelectionRules')
        if isinstance(rules, dict):
            explicit = rules.get('explicitFiles')
            if isinstance(explicit, list) and explicit:
                files = [str(f) for f in explicit if f]
                return ', '.join(files)
            mode = rules.get('mode')
            return f'({mode})' if mode else ''
        return str(path) if path else ''

    def _scan_project(project_key, conn_types: Dict[str, str]):
        """Scan one project for dataset connections and LLM connections."""
        client = _thread_client()
        proj = client.get_project(project_key)
        dataset_conns = []
        llm_conns = []
        local_fs_objects = []
        errors = []

        # 1. Dataset connections
        try:
            for ds in proj.list_datasets():
                params = _parse_conn_params(ds.get('params', {}))
                conn_name = params.get('connection') if isinstance(params, dict) else None
                dataset_name = ds.get('name', '')
                dataset_type = ds.get('type', '')
                if conn_name:
                    dataset_conns.append({
                        'datasetName': dataset_name,
                        'datasetType': dataset_type,
                        'connection': conn_name,
                    })
                    if _is_local_filesystem_connection(conn_name, conn_types):
                        local_fs_objects.append({
                            'objectType': 'dataset',
                            'objectId': dataset_name,
                            'objectName': dataset_name,
                            'objectSubtype': dataset_type,
                            'connection': conn_name,
                            'path': _format_dataset_path(params),
                        })
        except Exception as e:
            _LOGGER.debug("[conn_usage] list_datasets failed for %s: %s", project_key, e)
            errors.append({'projectKey': project_key, 'area': 'datasets', 'error': str(e)[:240]})

        # 2. Managed folders using local filesystem connections
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
                    except Exception as exc:
                        _LOGGER.debug("[conn_usage] managed folder settings failed for %s/%s: %s", project_key, folder_id, exc)
                if conn_name and _is_local_filesystem_connection(conn_name, conn_types):
                    local_fs_objects.append({
                        'objectType': 'folder',
                        'objectId': folder_id,
                        'objectName': folder_name or folder_id,
                        'objectSubtype': str(raw.get('type') or 'managed folder'),
                        'connection': conn_name,
                        'path': folder_path or '',
                    })
        except Exception as e:
            _LOGGER.debug("[conn_usage] list_managed_folders failed for %s: %s", project_key, e)
            errors.append({'projectKey': project_key, 'area': 'folders', 'error': str(e)[:240]})

        # 3. LLM recipe connections
        try:
            recipes = proj.list_recipes()
            llm_recipes = [r for r in recipes
                           if r.get('type', '').startswith(_LLM_RECIPE_PREFIXES)
                           or 'llm' in r.get('type', '').lower()]
            for r in llm_recipes:
                try:
                    recipe = proj.get_recipe(r['name'])
                    settings = recipe.get_settings()
                    payload = settings.get_json_payload() if hasattr(settings, 'get_json_payload') else None
                    if not payload:
                        raw_str = settings.get_payload() if hasattr(settings, 'get_payload') else ''
                        try:
                            payload = json.loads(raw_str) if raw_str else {}
                        except Exception:
                            payload = {}
                    if not payload:
                        continue
                    for llm_id in _find_llm_ids(payload):
                        parts = llm_id.split(':')
                        if len(parts) >= 3:
                            conn_name = parts[1]
                            llm_conns.append({
                                'recipeName': r.get('name', ''),
                                'recipeType': r.get('type', ''),
                                'llmId': llm_id,
                                'connection': conn_name,
                            })
                except Exception as e:
                    _LOGGER.debug("[conn_usage] recipe %s/%s failed: %s", project_key, r.get('name'), e)
                    errors.append({'projectKey': project_key, 'area': 'recipes', 'error': str(e)[:240]})
        except Exception as e:
            _LOGGER.debug("[conn_usage] list_recipes failed for %s: %s", project_key, e)
            errors.append({'projectKey': project_key, 'area': 'recipes', 'error': str(e)[:240]})

        return {
            'projectKey': project_key,
            'datasetConns': dataset_conns,
            'llmConns': llm_conns,
            'localFilesystemObjects': local_fs_objects,
            'errors': errors,
        }

    def generate():
        t0 = time.time()
        try:
            client = g.client
            projects = _list_projects_catalog_cheap(client)
            project_names = {p['key']: p.get('name', p['key']) for p in projects}
            project_owner_by_key = {p['key']: p.get('owner', 'Unknown') for p in projects}
            project_keys = list(project_names.keys())
            users = client.list_users() if hasattr(client, 'list_users') else []
            user_email_by_login: Dict[str, str] = {}
            for user in users:
                if isinstance(user, dict) and user.get('login'):
                    user_email_by_login[str(user.get('login'))] = str(user.get('email') or user.get('login'))

            connections = _sdk_fetch(
                'list_connections',
                _BACKEND_SETTINGS['cache_ttl_connections'],
                lambda: client.list_connections(),
            )
            conn_types: Dict[str, str] = {}
            if isinstance(connections, dict):
                for name, config in connections.items():
                    if isinstance(config, dict):
                        conn_types[name] = config.get('type', 'unknown')
            else:
                for c in connections:
                    conn_types[c.get('name', '')] = c.get('type', 'unknown')
        except Exception as e:
            yield "event: error\ndata: %s\n\n" % json.dumps({'error': str(e)[:200]})
            return

        yield "event: init\ndata: %s\n\n" % json.dumps({'total': len(project_keys)})

        dataset_map: Dict[str, List[Dict]] = {}   # conn -> [{projectKey, projectName, datasetName, datasetType}]
        llm_map: Dict[str, List[Dict]] = {}       # conn -> [{projectKey, projectName, recipeName, recipeType, llmId}]
        local_fs_usages: List[Dict[str, Any]] = []
        scanned = 0
        scan_errors = []

        workers = min(8, max(1, len(project_keys)))
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            futures = {pool.submit(_scan_project, pk, conn_types): pk for pk in project_keys}
            for future in as_completed(futures):
                pk = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {'projectKey': pk, 'datasetConns': [], 'llmConns': []}
                    scan_errors.append({'projectKey': pk, 'area': 'scan', 'error': str(exc)[:240]})
                scan_errors.extend(result.get('errors', []) or [])

                pname = project_names.get(pk, pk)
                owner = str(project_owner_by_key.get(pk) or 'Unknown')
                owner_email = user_email_by_login.get(owner, owner)
                for u in result.get('datasetConns', []):
                    conn = u['connection']
                    dataset_map.setdefault(conn, []).append({
                        'projectKey': pk,
                        'projectName': pname,
                        'datasetName': u['datasetName'],
                        'datasetType': u['datasetType'],
                    })
                for u in result.get('llmConns', []):
                    conn = u['connection']
                    llm_map.setdefault(conn, []).append({
                        'projectKey': pk,
                        'projectName': pname,
                        'recipeName': u['recipeName'],
                        'recipeType': u['recipeType'],
                        'llmId': u['llmId'],
                    })
                for u in result.get('localFilesystemObjects', []):
                    if not isinstance(u, dict):
                        continue
                    local_fs_usages.append({
                        'owner': owner,
                        'ownerEmail': owner_email,
                        'projectKey': pk,
                        'projectName': pname,
                        'objectType': u.get('objectType') or 'object',
                        'objectId': u.get('objectId') or '',
                        'objectName': u.get('objectName') or u.get('objectId') or '',
                        'objectSubtype': u.get('objectSubtype') or '',
                        'connection': u.get('connection') or '',
                        'path': u.get('path') or '',
                    })

                scanned += 1
                if scanned % 20 == 0 or scanned == len(project_keys):
                    yield "event: progress\ndata: %s\n\n" % json.dumps({'scanned': scanned})
        except GeneratorExit:
            pool.shutdown(wait=False, cancel_futures=True)
            return
        finally:
            pool.shutdown(wait=False)

        # Build final payloads
        dataset_usages = []
        for conn_name in sorted(dataset_map.keys()):
            usages = dataset_map[conn_name]
            dataset_usages.append({
                'name': conn_name,
                'type': conn_types.get(conn_name, 'unknown'),
                'projects': usages,
                'projectCount': len(set(u['projectKey'] for u in usages)),
                'datasetCount': len(usages),
            })

        llm_usages = []
        for conn_name in sorted(llm_map.keys()):
            usages = llm_map[conn_name]
            llm_usages.append({
                'name': conn_name,
                'type': conn_types.get(conn_name, 'unknown'),
                'projects': usages,
                'projectCount': len(set(u['projectKey'] for u in usages)),
                'recipeCount': len(usages),
            })

        total_ms = int((time.time() - t0) * 1000)
        yield "event: done\ndata: %s\n\n" % json.dumps({
            'total_ms': total_ms,
            'scanErrors': scan_errors,
            'failedProjectCount': len({e['projectKey'] for e in scan_errors}),
            'scannedProjectCount': len(project_keys),
            'datasetUsages': dataset_usages,
            'llmUsages': llm_usages,
            'localFilesystemUsages': sorted(
                local_fs_usages,
                key=lambda item: (
                    str(item.get('owner') or '').lower(),
                    str(item.get('projectKey') or '').lower(),
                    str(item.get('objectName') or '').lower(),
                ),
            ),
        })

    return Response(stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )
