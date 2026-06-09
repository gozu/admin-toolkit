"""Code-env replacement: retarget every usage surface (project default, recipe,
webapp, scenario, notebook kernel) from one code env to another.

`/api/code-envs/replace` is dry-run by default and gated by @advanced.
`_cer_env_catalog` / `_cer_fetch_env_detail` / `_cer_kernel_spec_name` are also
used by backend.py's algorithm-review routes (flat re-import there).
"""

from concurrent.futures import as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import Blueprint, g, jsonify, request

from adk_backend.caching import (
    _bump_session_epoch,
    _cache_pop_matching,
    _clear_shared_project_code_env_usage,
)
from adk_backend.clients import ThreadPoolExecutor, _list_projects_catalog, _thread_client
from adk_backend.usage_scan import (
    _dedupe_usage_entries,
    _normalize_language,
    _normalize_usage_entry,
    _usage_to_dict,
)
from adk_backend.utils import _bench_call, _extract_nested_text, _parallel_workers, advanced

bp = Blueprint('code_env_replace', __name__)


def _build_project_info(client: Any, limit: int, include_settings: bool = True) -> Dict[str, Dict[str, str]]:
    project_info: Dict[str, Dict[str, str]] = {}
    projects = _list_projects_catalog(client)
    if limit > 0:
        projects = projects[:limit]

    # Pre-populate from catalog data (no API calls)
    catalog_by_key: Dict[str, Dict[str, str]] = {}
    project_keys: List[str] = []
    for project in projects:
        key = project.get('key')
        if not key:
            continue
        cat_entry: Dict[str, Any] = {
            'owner': str(project.get('owner') or 'Unknown'),
            'name': str(project.get('name') or key),
        }
        if project.get('lastModifiedOn') is not None:
            cat_entry['lastModifiedOn'] = project['lastModifiedOn']
        catalog_by_key[key] = cat_entry
        project_keys.append(key)

    if not include_settings:
        for key in project_keys:
            entry: Dict[str, Any] = {
                'name': catalog_by_key[key]['name'],
                'owner': catalog_by_key[key]['owner'],
            }
            if catalog_by_key[key].get('lastModifiedOn') is not None:
                entry['lastModifiedOn'] = catalog_by_key[key]['lastModifiedOn']
            project_info[key] = entry
        return project_info

    def _fetch_project_settings(key: str) -> Tuple[str, Dict[str, str]]:
        local_client = _thread_client()
        info: Dict[str, Any] = {
            'name': catalog_by_key[key]['name'],
            'owner': catalog_by_key[key]['owner'],
        }
        if catalog_by_key[key].get('lastModifiedOn') is not None:
            info['lastModifiedOn'] = catalog_by_key[key]['lastModifiedOn']
        try:
            project_obj = _bench_call('get_project', local_client.get_project, key)
            settings = project_obj.get_settings().get_raw()
            if isinstance(settings, dict):
                if settings.get('owner'):
                    info['owner'] = str(settings.get('owner'))
                if settings.get('name'):
                    info['name'] = str(settings.get('name'))
                default_python_env = _extract_nested_text(
                    settings,
                    'settings.codeEnvs.python.envName',
                    'codeEnvs.python.envName',
                )
                if default_python_env:
                    info['defaultPythonEnv'] = default_python_env
        except Exception:
            pass
        return (key, info)

    workers = min(_parallel_workers(8), len(project_keys))
    if workers <= 1:
        for key in project_keys:
            _, info = _fetch_project_settings(key)
            project_info[key] = info
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch_project_settings, key): key for key in project_keys}
            for future in as_completed(futures):
                try:
                    key, info = future.result()
                    project_info[key] = info
                except Exception:
                    fkey = futures[future]
                    project_info[fkey] = {
                        'name': catalog_by_key[fkey]['name'],
                        'owner': catalog_by_key[fkey]['owner'],
                    }

    return project_info


# ── Code env replacement helpers ─────────────────────────────────────────────

_CER_SURFACE_TYPES = {'PROJECT', 'RECIPE', 'NOTEBOOK', 'WEBAPP', 'SCENARIO'}


def _cer_path_get(raw: Any, path: str) -> Any:
    current = raw
    for part in path.split('.'):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _cer_path_set(raw: Dict[str, Any], path: str, value: Any) -> None:
    current = raw
    parts = path.split('.')
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _cer_env_selection(env_name: str) -> Dict[str, Any]:
    return {'envMode': 'EXPLICIT_ENV', 'envName': env_name}


def _cer_selection_env_name(selection: Any) -> Optional[str]:
    if not isinstance(selection, dict):
        return None
    for key in ('envName', 'codeEnvName', 'name'):
        value = selection.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _cer_kernel_spec_name(env: Dict[str, Any], detail: Optional[Dict[str, Any]] = None) -> Optional[str]:
    for payload in (env, detail or {}):
        if not isinstance(payload, dict):
            continue
        value = _extract_nested_text(
            payload,
            'kernelSpecName',
            'desc.kernelSpecName',
            'settings.kernelSpecName',
            'spec.kernelSpecName',
            'jupyterKernelSpecName',
            'desc.jupyterKernelSpecName',
        )
        if value:
            return value
    return None


def _cer_env_catalog(client: Any) -> Dict[Tuple[str, str], Dict[str, Any]]:
    catalog: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for env in client.list_code_envs() or []:
        if not isinstance(env, dict):
            continue
        name = str(env.get('envName') or env.get('name') or env.get('id') or '').strip()
        if not name:
            continue
        language = _normalize_language(env.get('envLang') or env.get('language') or env.get('type'))
        catalog[(language, name)] = env
    return catalog


def _cer_fetch_env_detail(client: Any, language: str, env_name: str) -> Dict[str, Any]:
    try:
        detail = client._perform_json('GET', f"/admin/code-envs/{language.upper()}/{env_name}")
        return detail if isinstance(detail, dict) else {}
    except Exception:
        return {}


def _cer_object_type(raw_type: Any, usage_type: Any = None) -> str:
    text = str(raw_type or usage_type or '').strip().upper().replace('-', '_')
    if text in _CER_SURFACE_TYPES:
        return text
    if 'PROJECT' in text:
        return 'PROJECT'
    if 'RECIPE' in text:
        return 'RECIPE'
    if 'NOTEBOOK' in text or 'JUPYTER' in text:
        return 'NOTEBOOK'
    if 'WEBAPP' in text or 'WEB_APP' in text:
        return 'WEBAPP'
    if 'SCENARIO' in text:
        return 'SCENARIO'
    return text or 'UNKNOWN'


def _cer_build_usage_rows(
    client: Any,
    source_env_name: str,
    source_language: str,
    project_filter: Optional[set] = None,
    type_filter: Optional[set] = None,
) -> List[Dict[str, Any]]:
    project_info = _build_project_info(client, 0, include_settings=False)
    rows: List[Dict[str, Any]] = []
    for raw_usage in client.list_code_env_usages() or []:
        if not isinstance(raw_usage, dict):
            raw_usage = _usage_to_dict(raw_usage)
        env_name = str(raw_usage.get('envName') or raw_usage.get('codeEnvName') or '').strip()
        env_lang = _normalize_language(raw_usage.get('envLang') or raw_usage.get('codeEnvLanguage') or source_language)
        if env_name != source_env_name or env_lang != source_language:
            continue
        normalized = _normalize_usage_entry(raw_usage, project_info)
        project_key = str(normalized.get('projectKey') or '')
        if project_filter and project_key not in project_filter:
            continue
        object_type = _cer_object_type(normalized.get('objectType'), normalized.get('usageType'))
        if type_filter and object_type not in type_filter:
            continue
        object_id = str(normalized.get('objectId') or '')
        if object_type == 'PROJECT' and not object_id:
            object_id = project_key
        rows.append({
            'id': '|'.join([project_key, object_type, object_id, source_language, source_env_name]),
            'projectKey': project_key,
            'projectName': normalized.get('projectName') or project_key,
            'objectType': object_type,
            'objectId': object_id,
            'objectName': normalized.get('objectName') or object_id or project_key,
            'sourceLanguage': source_language,
            'sourceEnvName': source_env_name,
        })
    return _dedupe_usage_entries(rows)


def _cer_replace_project_default(client: Any, row: Dict[str, Any], source_env_name: str, target_env_name: str, language: str) -> Tuple[str, Optional[str]]:
    settings = client.get_project(row['projectKey']).get_settings()
    raw = settings.get_raw()
    lang_key = 'r' if language == 'r' else 'python'
    path = f'settings.codeEnvs.{lang_key}'
    selection = _cer_path_get(raw, path)
    if selection is None:
        path = f'codeEnvs.{lang_key}'
        selection = _cer_path_get(raw, path)
    current = _cer_selection_env_name(selection)
    if current != source_env_name:
        return ('skipped', f'Current project default is {current or "unset"}')
    next_selection = dict(selection) if isinstance(selection, dict) else {}
    next_selection['envName'] = target_env_name
    if not next_selection.get('envMode'):
        next_selection['envMode'] = 'EXPLICIT_ENV'
    _cer_path_set(raw, path, next_selection)
    settings.save()
    return ('updated', None)


def _cer_recipe_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    recipe = raw.get('recipe')
    return recipe if isinstance(recipe, dict) else raw


def _cer_replace_env_selection_payload(raw: Dict[str, Any], source_env_name: str, target_env_name: str, path: str = 'params.envSelection') -> Tuple[bool, Optional[str]]:
    selection = _cer_path_get(raw, path)
    current = _cer_selection_env_name(selection)
    if current != source_env_name:
        return (False, f'Current env is {current or "unset"}')
    _cer_path_set(raw, path, _cer_env_selection(target_env_name))
    return (True, None)


def _cer_replace_recipe(client: Any, row: Dict[str, Any], source_env_name: str, target_env_name: str) -> Tuple[str, Optional[str]]:
    project_key = row['projectKey']
    recipe_id = row['objectId']
    raw = client._perform_json('GET', f'/projects/{project_key}/recipes/{recipe_id}')
    payload = _cer_recipe_payload(raw if isinstance(raw, dict) else {})
    ok, reason = _cer_replace_env_selection_payload(payload, source_env_name, target_env_name)
    if not ok:
        return ('skipped', reason)
    client._perform_json('PUT', f'/projects/{project_key}/recipes/{recipe_id}', body=raw)
    return ('updated', None)


def _cer_replace_webapp(client: Any, row: Dict[str, Any], source_env_name: str, target_env_name: str) -> Tuple[str, Optional[str]]:
    project_key = row['projectKey']
    webapp_id = row['objectId']
    raw = client._perform_json('GET', f'/projects/{project_key}/webapps/{webapp_id}')
    ok, reason = _cer_replace_env_selection_payload(raw if isinstance(raw, dict) else {}, source_env_name, target_env_name)
    if not ok:
        return ('skipped', reason)
    client._perform_empty('PUT', f'/projects/{project_key}/webapps/{webapp_id}', body=raw)
    return ('updated', None)


def _cer_replace_scenario(client: Any, row: Dict[str, Any], source_env_name: str, target_env_name: str) -> Tuple[str, Optional[str]]:
    project_key = row['projectKey']
    scenario_id = row['objectId']
    raw = client._perform_json('GET', f'/projects/{project_key}/scenarios/{scenario_id}')
    ok, reason = _cer_replace_env_selection_payload(raw if isinstance(raw, dict) else {}, source_env_name, target_env_name)
    if not ok:
        return ('skipped', reason)
    client._perform_empty('PUT', f'/projects/{project_key}/scenarios/{scenario_id}', body=raw)
    return ('updated', None)


def _cer_fetch_notebook_content(client: Any, project_key: str, notebook_id: str) -> Tuple[Dict[str, Any], Callable[[Dict[str, Any]], None]]:
    try:
        notebook = client.get_project(project_key).get_jupyter_notebook(notebook_id)
        content = notebook.get_content()
        raw = content.get_raw()
        if isinstance(raw, dict):
            def save_sdk(next_raw: Dict[str, Any]) -> None:
                if hasattr(content, 'set_raw'):
                    content.set_raw(next_raw)
                elif hasattr(content, '_data'):
                    content._data = next_raw
                if hasattr(content, 'save'):
                    content.save()
                elif hasattr(notebook, 'set_content'):
                    notebook.set_content(next_raw)
                else:
                    raise ValueError('Notebook content object does not support save')
            return raw, save_sdk
    except Exception:
        pass

    path = f'/projects/{project_key}/jupyter-notebooks/{notebook_id}/content'
    raw = client._perform_json('GET', path)
    if not isinstance(raw, dict):
        raw = {}

    def save_rest(next_raw: Dict[str, Any]) -> None:
        client._perform_json('PUT', path, body=next_raw)

    return raw, save_rest


def _cer_replace_notebook(
    client: Any,
    row: Dict[str, Any],
    source_env_name: str,
    target_kernel_spec: str,
    source_kernel_spec: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    project_key = row['projectKey']
    notebook_id = row['objectId']
    raw, save = _cer_fetch_notebook_content(client, project_key, notebook_id)
    kernelspec = _cer_path_get(raw, 'metadata.kernelspec')
    current = kernelspec.get('name') if isinstance(kernelspec, dict) else None
    accepted_sources = {source_env_name}
    if source_kernel_spec:
        accepted_sources.add(source_kernel_spec)
    if current not in accepted_sources:
        return ('skipped', f'Current notebook kernel is {current or "unset"}')
    next_kernel = dict(kernelspec) if isinstance(kernelspec, dict) else {}
    next_kernel['name'] = target_kernel_spec
    if next_kernel.get('display_name') in accepted_sources or not next_kernel.get('display_name'):
        next_kernel['display_name'] = target_kernel_spec
    _cer_path_set(raw, 'metadata.kernelspec', next_kernel)
    save(raw)
    return ('updated', None)


def _cer_apply_replace_row(
    client: Any,
    row: Dict[str, Any],
    source_env_name: str,
    target_env_name: str,
    source_language: str,
    target_kernel_spec: Optional[str],
    source_kernel_spec: Optional[str],
) -> Tuple[str, Optional[str]]:
    object_type = str(row.get('objectType') or '').upper()
    if object_type == 'PROJECT':
        return _cer_replace_project_default(client, row, source_env_name, target_env_name, source_language)
    if object_type == 'RECIPE':
        return _cer_replace_recipe(client, row, source_env_name, target_env_name)
    if object_type == 'WEBAPP':
        return _cer_replace_webapp(client, row, source_env_name, target_env_name)
    if object_type == 'SCENARIO':
        return _cer_replace_scenario(client, row, source_env_name, target_env_name)
    if object_type == 'NOTEBOOK':
        if not target_kernel_spec:
            return ('failed', 'Target code env does not expose kernelSpecName')
        return _cer_replace_notebook(client, row, source_env_name, target_kernel_spec, source_kernel_spec)
    return ('failed', f'Unsupported replacement surface: {object_type}')


def _cer_clear_replacement_caches() -> None:
    _cache_pop_matching(lambda key_text: (
        key_text in {'code_envs', 'code_envs_sizes', 'outreach', 'project_code_env_usage_full'}
        or key_text.startswith('outreach')
        or key_text.startswith('project_footprint')
    ))
    _clear_shared_project_code_env_usage()
    _bump_session_epoch()


@bp.route('/api/code-envs/replace', methods=['POST'])
@advanced
def api_code_envs_replace():
    payload = request.get_json(silent=True) or {}
    source_env_name = str(payload.get('sourceEnvName') or '').strip()
    source_language = _normalize_language(payload.get('sourceLanguage') or 'python')
    target_env_name = str(payload.get('targetEnvName') or '').strip()
    dry_run = bool(payload.get('dryRun', True))
    if not source_env_name or not target_env_name:
        return jsonify({'error': 'sourceEnvName and targetEnvName are required'}), 400
    if source_env_name == target_env_name:
        return jsonify({'error': 'sourceEnvName and targetEnvName must differ'}), 400

    project_keys = payload.get('projectKeys')
    usage_types = payload.get('usageTypes')
    project_filter = {str(pk).strip() for pk in project_keys if str(pk).strip()} if isinstance(project_keys, list) else None
    type_filter = {_cer_object_type(t) for t in usage_types if str(t).strip()} if isinstance(usage_types, list) else None

    client = g.client
    catalog = _cer_env_catalog(client)
    target_env = catalog.get((source_language, target_env_name))
    if target_env is None:
        same_name = [(lang, name) for (lang, name) in catalog.keys() if name == target_env_name]
        if same_name:
            return jsonify({
                'error': f'Target code env language does not match sourceLanguage: {target_env_name}',
                'validTargetEnvNames': sorted(name for (lang, name) in catalog.keys() if lang == source_language),
            }), 400
        return jsonify({
            'error': f'Unknown targetEnvName: {target_env_name}',
            'validTargetEnvNames': sorted(name for (lang, name) in catalog.keys() if lang == source_language),
        }), 400

    source_env = catalog.get((source_language, source_env_name), {})
    target_detail = _cer_fetch_env_detail(client, source_language, target_env_name)
    source_detail = _cer_fetch_env_detail(client, source_language, source_env_name) if source_env else {}
    target_kernel_spec = _cer_kernel_spec_name(target_env, target_detail)
    source_kernel_spec = _cer_kernel_spec_name(source_env, source_detail) if source_env else None

    matched = _cer_build_usage_rows(
        client,
        source_env_name,
        source_language,
        project_filter=project_filter,
        type_filter=type_filter,
    )

    results: List[Dict[str, Any]] = []
    for row in matched:
        result = {
            'rowId': row.get('id'),
            'projectKey': row.get('projectKey'),
            'objectType': row.get('objectType'),
            'objectId': row.get('objectId'),
            'objectName': row.get('objectName'),
            'from': source_env_name,
            'to': target_env_name,
            'status': 'planned' if dry_run else 'updated',
        }
        if not dry_run:
            try:
                status, message = _cer_apply_replace_row(
                    client,
                    row,
                    source_env_name,
                    target_env_name,
                    source_language,
                    target_kernel_spec,
                    source_kernel_spec,
                )
                result['status'] = status
                if message:
                    result['error'] = message
            except Exception as exc:
                result['status'] = 'failed'
                result['error'] = str(exc)[:500]
        results.append(result)

    if not dry_run and any(r.get('status') == 'updated' for r in results):
        _cer_clear_replacement_caches()

    return jsonify({
        'dryRun': dry_run,
        'sourceEnvName': source_env_name,
        'sourceLanguage': source_language,
        'targetEnvName': target_env_name,
        'matchedRows': len(matched),
        'updatedRows': len([r for r in results if r.get('status') == 'updated']),
        'skippedRows': len([r for r in results if r.get('status') == 'skipped']),
        'failedRows': len([r for r in results if r.get('status') == 'failed']),
        'results': results,
    })
