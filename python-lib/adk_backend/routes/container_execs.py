"""Container-execs scan / stream / replace routes (Compute Fabric)."""
import hashlib
import json
import logging
import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

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
from adk_backend.settings import _BACKEND_SETTINGS
from adk_backend.utils import _cex_item_raw, _sse_response, advanced

bp = Blueprint('container_execs', __name__)
_LOGGER = logging.getLogger(__name__)


# ── Compute Fabric: container execution scan / replace ────────────────────────

_CEX_CODE_RECIPE_TYPES = {'python', 'r'}
_CEX_NON_CARRIER_RECIPE_TYPES = {'pyspark', 'spark_scala', 'spark_sql_query', 'shell'}


def _cex_path_get(raw: Any, path: str) -> Any:
    current = raw
    for part in path.split('.'):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _cex_path_set(raw: Dict[str, Any], path: str, value: Any) -> None:
    current = raw
    parts = path.split('.')
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _cex_selection(config: Optional[str], mode: str = 'EXPLICIT_CONTAINER') -> Dict[str, Any]:
    if config == '__INHERIT__':
        return {'containerMode': 'INHERIT'}
    if str(mode or '').upper() == 'EXPLICIT_CONTAINER' and config:
        return {'containerMode': 'EXPLICIT_CONTAINER', 'containerConf': config}
    return {'containerMode': mode}


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
    keys = [
        'name', 'type', 'usableBy', 'allowedGroups', 'workloadType', 'dockerNetwork',
        'kubernetesNamespace', 'repositoryURL', 'baseImageType', 'prePushMode',
        'nodeSelector', 'dockerTLSVerify',
    ]
    return {key: config.get(key) for key in keys if key in config}


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


def _cex_group_project_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        project_key = str(row.get('projectKey') or '')
        if not project_key:
            continue
        group = groups.setdefault(project_key, {
            'projectKey': project_key,
            'projectName': row.get('projectName') or project_key,
            'projectOverrides': [],
            'jobOverrides': [],
        })
        if row.get('overrideLevel') == 'project':
            group['projectOverrides'].append(row)
        elif row.get('overrideLevel') == 'job':
            group['jobOverrides'].append(row)
    return [
        group for group in sorted(groups.values(), key=lambda item: str(item.get('projectKey') or ''))
        if group.get('projectOverrides') or group.get('jobOverrides')
    ]


def _cex_cache_key(project_filter: Optional[set]) -> str:
    if project_filter:
        digest = hashlib.sha1('\n'.join(sorted(project_filter)).encode('utf-8')).hexdigest()
        return f'container_execs:{digest}'
    return 'container_execs'


def _cex_cached_scan(cache_key: str, ttl: int) -> Optional[Dict[str, Any]]:
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE.get(_cache_key(cache_key))
        cached_value = cached.get('value') if cached and now - cached.get('ts', 0) < ttl else None
    return cached_value if isinstance(cached_value, dict) else None


def _cex_execution_config_names(client: Any) -> List[str]:
    try:
        settings = client.get_general_settings().get_raw()
        container_settings = settings.get('containerSettings') if isinstance(settings, dict) else {}
        configs_raw = container_settings.get('executionConfigs') if isinstance(container_settings, dict) else []
        return sorted({str(cfg.get('name')) for cfg in (configs_raw or []) if isinstance(cfg, dict) and cfg.get('name')})
    except Exception:
        return []


def _cex_scan(
    client: Any,
    project_keys_filter: Optional[set] = None,
    timeout_ms: Optional[int] = None,
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    started = time.time()
    deadline = started + (float(timeout_ms) / 1000.0) if timeout_ms else None
    timed_out = False
    usage_rows: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    non_carrier_counts: Dict[str, int] = {
        'jupyterNotebooks': 0,
        'sqlNotebooks': 0,
        'scenarios': 0,
        'apiServices': 0,
        'sparkRecipes': 0,
        'shellRecipes': 0,
        'modelEvaluationStores': 0,
        'modelComparisons': 0,
    }

    def event(step: str, message: str, project_key: str = '', level: str = 'info') -> None:
        events.append({
            'tMs': round((time.time() - started) * 1000.0, 2),
            'level': level,
            'step': step,
            'message': message,
            'projectKey': project_key,
        })

    def should_stop(step: str, project_key: str = '') -> bool:
        nonlocal timed_out
        if deadline is None or time.time() <= deadline:
            return False
        if not timed_out:
            timed_out = True
            event('timeout', f'container exec scan exceeded timeoutMs={timeout_ms} at {step}', project_key, 'warn')
        return True

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

    catalog = _list_projects_catalog_cheap(client)
    if project_keys_filter:
        catalog = [project for project in catalog if project.get('key') in project_keys_filter]

    if progress_cb:
        progress_cb({'event': 'init', 'total': len(catalog)})

    scanned_projects = 0
    for project_meta in catalog:
        if should_stop('project_loop', str(project_meta.get('key') or '')):
            break
        project_key = str(project_meta.get('key') or '')
        project_name = str(project_meta.get('name') or project_key)
        if not project_key:
            continue
        try:
            project = client.get_project(project_key)
            settings_raw = project.get_settings().get_raw()
        except Exception as exc:
            event('project_settings_error', str(exc)[:200], project_key, 'warn')
            scanned_projects += 1
            if progress_cb:
                progress_cb({'event': 'progress', 'scanned': scanned_projects, 'total': len(catalog), 'projectKey': project_key})
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
            _cex_add_row(
                usage_rows,
                project_key=project_key,
                project_name=project_name,
                object_type='PROJECT',
                object_id=project_key,
                object_name=project_name,
                surface=surface,
                surface_label=label,
                raw_path=path,
                selection=selection,
                fallback_config=global_default,
                inherited_from='global default',
                writable=True,
                replacement_supported=True,
                notes=notes,
                override_level='project',
                object_subtype=label,
                project_config=global_default,
            )

        remap = _cex_path_get(settings_raw, 'bundleContainerSettings.remapping')
        if isinstance(remap, dict):
            for idx, item in enumerate(remap.get('containerExecs') or []):
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
                    _cex_add_row(
                        usage_rows,
                        project_key=project_key,
                        project_name=project_name,
                        object_type='RECIPE',
                        object_id=recipe_name,
                        object_name=recipe_name,
                        surface='recipe_code',
                        surface_label='Python/R code recipe',
                        raw_path='recipe.params.containerSelection',
                        selection=selection,
                        fallback_config=code_effective,
                        inherited_from='project code workload default',
                        writable=True,
                        replacement_supported=True,
                        notes=f'{recipe_type} recipe',
                        override_level='job',
                        object_subtype=f'{recipe_type} recipe',
                        project_config=code_effective,
                        extra={'recipeType': recipe_type},
                    )
            elif recipe_type in _CEX_NON_CARRIER_RECIPE_TYPES:
                non_carrier_counts['shellRecipes' if recipe_type == 'shell' else 'sparkRecipes'] += 1

            visual_selection = _cex_path_get(recipe_def, 'params.engineParams.containerSelection')
            if isinstance(visual_selection, dict):
                mode, _, _ = _cex_effective(visual_selection, visual_effective)
                if mode != 'EXPLICIT_CONTAINER' or not _cex_is_visible_job_override(visual_selection, visual_effective, global_default):
                    continue
                _cex_add_row(
                    usage_rows,
                    project_key=project_key,
                    project_name=project_name,
                    object_type='RECIPE',
                    object_id=recipe_name,
                    object_name=recipe_name,
                    surface='recipe_visual',
                    surface_label='Visual recipe',
                    raw_path='recipe.params.engineParams.containerSelection',
                    selection=visual_selection,
                    fallback_config=visual_effective,
                    inherited_from='project visual recipe default',
                    writable=True,
                    replacement_supported=True,
                    notes=f'{recipe_type} recipe using DSS engine',
                    override_level='job',
                    object_subtype=f'{recipe_type} visual recipe',
                    project_config=visual_effective,
                    extra={'recipeType': recipe_type},
                )

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
                _cex_add_row(
                    usage_rows,
                    project_key=project_key,
                    project_name=project_name,
                    object_type='WEBAPP',
                    object_id=webapp_id,
                    object_name=str(detail.get('name') or webapp_raw.get('name') or webapp_id),
                    surface='webapp_backend',
                    surface_label='Webapp backend',
                    raw_path='params.infra.containerSelection',
                    selection=selection,
                    fallback_config=webapp_effective,
                    inherited_from='project webapp backend default',
                    writable=True,
                    replacement_supported=True,
                    notes=str(detail.get('type') or webapp_raw.get('type') or 'webapp'),
                    override_level='job',
                    object_subtype=str(detail.get('type') or webapp_raw.get('type') or 'webapp'),
                    project_config=webapp_effective,
                )

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
                _cex_add_row(
                    usage_rows,
                    project_key=project_key,
                    project_name=project_name,
                    object_type='ML_TASK',
                    object_id=f'{analysis_id}/{task_id}',
                    object_name=str(task.get('mlTaskName') or task_id),
                    surface='ml_task',
                    surface_label='ML task',
                    raw_path='containerSelection',
                    selection=selection,
                    fallback_config=code_effective,
                    inherited_from='project/container default',
                    writable=True,
                    replacement_supported=True,
                    notes=str(task.get('taskType') or ''),
                    override_level='job',
                    object_subtype=str(task.get('taskType') or 'ML task'),
                    project_config=code_effective,
                    extra={'analysisId': analysis_id, 'mlTaskId': task_id},
                )

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

        scanned_projects += 1
        if progress_cb:
            progress_cb({'event': 'progress', 'scanned': scanned_projects, 'total': len(catalog), 'projectKey': project_key})

    by_config: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    by_mode: Dict[str, int] = {}
    explicit = supported = 0
    project_override_rows = 0
    job_override_rows = 0
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
        'timedOut': timed_out,
        'elapsedMs': round((time.time() - started) * 1000.0, 2),
        'configNames': config_names,
        'globalDefaultConfig': global_default,
    }


def _cex_replace_project_settings(client: Any, row: Dict[str, Any], target_config: str) -> None:
    settings = client.get_project(row['projectKey']).get_settings()
    raw = settings.get_raw()
    _cex_path_set(raw, str(row['rawPath']), _cex_selection(target_config))
    settings.save()


def _cex_replace_recipe(client: Any, row: Dict[str, Any], target_config: str) -> None:
    project_key = row['projectKey']
    recipe_name = row['objectId']
    raw = client._perform_json('GET', f'/projects/{project_key}/recipes/{recipe_name}')
    path = str(row['rawPath'])
    if path.startswith('recipe.'):
        path = path[len('recipe.'):]
    _cex_path_set(raw.setdefault('recipe', {}), path, _cex_selection(target_config))
    client._perform_json('PUT', f'/projects/{project_key}/recipes/{recipe_name}', body=raw)


def _cex_replace_webapp(client: Any, row: Dict[str, Any], target_config: str) -> None:
    project_key = row['projectKey']
    webapp_id = row['objectId']
    raw = client._perform_json('GET', f'/projects/{project_key}/webapps/{webapp_id}')
    _cex_path_set(raw, str(row['rawPath']), _cex_selection(target_config))
    client._perform_empty('PUT', f'/projects/{project_key}/webapps/{webapp_id}', body=raw)


def _cex_try_private_mltask_save(
    browser_ctx: Optional[Dict[str, Any]],
    project_key: str,
    analysis_id: str,
    mltask_settings: Dict[str, Any],
    diag: Dict[str, Any],
) -> Tuple[bool, Optional[str]]:
    """Attempt POST /dip/api/analysis/cml/save-settings using forwarded browser session.

    Populates `diag['privateAttempt']` with verbose info regardless of outcome.
    Returns (ok, error_message).
    """
    import requests as _rq

    ctx = browser_ctx or {}
    origin = str(ctx.get('origin') or '').rstrip('/')
    cookie_header = str(ctx.get('cookie_header') or '')
    xsrf = str(ctx.get('xsrf') or '')
    referer = str(ctx.get('referer') or '')

    attempt = {
        'originLen': len(origin),
        'origin': origin if len(origin) < 100 else origin[:97] + '...',
        'cookieHeaderLen': len(cookie_header),
        'cookieCount': cookie_header.count(';') + 1 if cookie_header else 0,
        'cookieNames': ctx.get('cookie_names', []),
        'xsrfPresent': bool(xsrf),
        'xsrfLen': len(xsrf),
        'xsrfSource': ctx.get('xsrf_source') or '',
        'referer': referer if len(referer) < 120 else referer[:117] + '...',
    }
    diag['privateAttempt'] = attempt

    if not origin or not cookie_header or not xsrf:
        attempt['skipped'] = 'missing browser context (origin/cookies/xsrf)'
        return False, attempt['skipped']

    url = f"{origin}/dip/api/analysis/cml/save-settings"
    headers = {
        'Cookie': cookie_header,
        'x-xsrf-token': xsrf,
        'Accept': 'application/json',
        'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
    }
    body = {
        'projectKey': project_key,
        'analysisId': analysis_id,
        'mlTask': json.dumps(mltask_settings),
    }
    attempt['url'] = url
    attempt['bodyFields'] = sorted(body.keys())
    attempt['mlTaskBodyLen'] = len(body['mlTask'])

    try:
        r = _rq.post(url, data=body, headers=headers, verify=False, timeout=30)
        attempt['status'] = r.status_code
        attempt['responseLen'] = len(r.text or '')
        attempt['responseSnippet'] = (r.text or '')[:400]
        if 200 <= r.status_code < 300:
            return True, None
        return False, f"HTTP {r.status_code}: {(r.text or '')[:200]}"
    except Exception as e:
        attempt['exception'] = str(e)[:300]
        return False, str(e)[:300]


def _cex_replace_ml_task(
    client: Any,
    row: Dict[str, Any],
    target_config: str,
    browser_ctx: Optional[Dict[str, Any]] = None,
    diag: Optional[Dict[str, Any]] = None,
) -> None:
    # Public API (POST /projects/{pk}/models/lab/{aid}/{tid}/settings) NPEs for
    # ML tasks that were never fully designed (no preprocessingParams). The DSS
    # UI uses the private endpoint with the user's session cookies, so we do
    # the same with the forwarded browser context.
    project_key = row['projectKey']
    analysis_id = row.get('analysisId')
    task_id = row.get('mlTaskId')
    if not analysis_id or not task_id:
        parts = str(row.get('objectId') or '').split('/', 1)
        if len(parts) == 2:
            analysis_id, task_id = parts
    if not analysis_id or not task_id:
        raise ValueError('Missing ML task identifiers')

    raw = client._perform_json(
        'GET', f'/projects/{project_key}/models/lab/{analysis_id}/{task_id}/settings'
    )
    _cex_path_set(raw, str(row['rawPath']), _cex_selection(target_config))

    if diag is None:
        diag = {}
    diag['projectKey'] = project_key
    diag['analysisId'] = analysis_id
    diag['taskId'] = task_id
    diag['settingsTopKeys'] = sorted(raw.keys())
    diag['containerSelection'] = raw.get('containerSelection')

    ok, err = _cex_try_private_mltask_save(browser_ctx, project_key, analysis_id, raw, diag)
    try:
        _LOGGER.info(
            "[cex:mltask] pk=%s aid=%s tid=%s save=%s",
            project_key, analysis_id, task_id, 'ok' if ok else 'failed',
        )
    except Exception:
        pass
    if not ok:
        raise RuntimeError(f"ML task save failed: {err}")


def _cex_replace_code_studio_template(client: Any, row: Dict[str, Any], target_config: str) -> None:
    template_id = str(row.get('templateId') or row.get('objectId') or '')
    if not template_id:
        raise ValueError('Missing Code Studio template id')
    settings = client.get_code_studio_template(template_id).get_settings()
    raw = settings.get_raw()
    raw_path = str(row.get('rawPath') or '')
    if raw_path == 'defaultContainerConf':
        raw['defaultContainerConf'] = target_config
    elif raw_path.startswith('containerConfs['):
        idx = int(row.get('listIndex'))
        raw.setdefault('containerConfs', [])[idx] = target_config
    else:
        raise ValueError(f'Unsupported template raw path: {raw_path}')
    settings.save()


def _cex_replace_bundle_remap(client: Any, row: Dict[str, Any], target_config: str) -> None:
    settings = client.get_project(row['projectKey']).get_settings()
    raw = settings.get_raw()
    idx = int(row.get('listIndex'))
    field = str(row.get('listField') or '')
    items = _cex_path_get(raw, 'bundleContainerSettings.remapping.containerExecs')
    if not isinstance(items, list) or idx >= len(items) or not isinstance(items[idx], dict):
        raise ValueError('Bundle remap row no longer exists')
    items[idx][field] = target_config
    settings.save()


def _cex_apply_replace_row(
    client: Any,
    row: Dict[str, Any],
    target_config: str,
    browser_ctx: Optional[Dict[str, Any]] = None,
    diag: Optional[Dict[str, Any]] = None,
) -> None:
    surface = str(row.get('surface') or '')
    if surface.startswith('project_'):
        return _cex_replace_project_settings(client, row, target_config)
    if surface in ('recipe_code', 'recipe_visual'):
        return _cex_replace_recipe(client, row, target_config)
    if surface == 'webapp_backend':
        return _cex_replace_webapp(client, row, target_config)
    if surface == 'ml_task':
        return _cex_replace_ml_task(client, row, target_config, browser_ctx=browser_ctx, diag=diag)
    if surface.startswith('code_studio_template_'):
        return _cex_replace_code_studio_template(client, row, target_config)
    if surface == 'bundle_remapping':
        return _cex_replace_bundle_remap(client, row, target_config)
    raise ValueError(f'Unsupported replacement surface: {surface}')


@bp.route('/api/container-execs')
def api_container_execs():
    client = g.client
    project_keys_arg = request.args.get('projectKeys', '').strip()
    project_filter = {part.strip() for part in project_keys_arg.split(',') if part.strip()} if project_keys_arg else None

    def loader():
        timeout_ms = int(_BACKEND_SETTINGS.get('container_exec_timeout_ms', 600000))
        return _cex_scan(client, project_keys_filter=project_filter, timeout_ms=timeout_ms)

    cache_key = _cex_cache_key(project_filter)
    data = _cache_get(cache_key, _BACKEND_SETTINGS.get('cache_ttl_projects', 600), loader)
    return jsonify(data)


@bp.route('/api/container-execs/stream')
def api_container_execs_stream():
    project_keys_arg = request.args.get('projectKeys', '').strip()
    project_filter = {part.strip() for part in project_keys_arg.split(',') if part.strip()} if project_keys_arg else None
    cache_key = _cex_cache_key(project_filter)
    ttl = int(_BACKEND_SETTINGS.get('cache_ttl_projects', 600))

    def sse(event_name: str, payload: Dict[str, Any]) -> str:
        return "event: %s\ndata: %s\n\n" % (event_name, json.dumps(payload))

    # Hoist client and host_id out of the SSE generator so the worker thread
    # captures them by closure. `g` is request-scoped and is NOT available
    # inside a threading.Thread spawned by the request handler.
    request_client = g.client
    request_host_id = getattr(g, 'host_id', 'local')

    def generate():
        cached_value = _cex_cached_scan(cache_key, ttl)
        if cached_value is not None:
            total = ((cached_value.get('summary') or {}).get('projectCount') or 0) if isinstance(cached_value, dict) else 0
            yield sse('init', {'total': total, 'cached': True})
            yield sse('done', cached_value)
            return

        events_q: "queue.Queue[Dict[str, Any]]" = queue.Queue()

        def progress_cb(payload: Dict[str, Any]) -> None:
            events_q.put(dict(payload))

        def worker() -> None:
            previous_host_id = getattr(_THREAD_LOCAL, 'host_id', None)
            _THREAD_LOCAL.host_id = request_host_id
            try:
                # Captured from the enclosing request context — DO NOT touch g here.
                client = request_client
                timeout_ms = int(_BACKEND_SETTINGS.get('container_exec_timeout_ms', 600000))
                result = _cex_scan(
                    client,
                    project_keys_filter=project_filter,
                    timeout_ms=timeout_ms,
                    progress_cb=progress_cb,
                )
                with _CACHE_LOCK:
                    _CACHE[_cache_key(cache_key)] = {'ts': time.time(), 'value': result}
                events_q.put({'event': 'done', 'payload': result})
            except Exception as exc:
                events_q.put({'event': 'error', 'error': str(exc)[:500]})
            finally:
                if previous_host_id is None:
                    try:
                        delattr(_THREAD_LOCAL, 'host_id')
                    except AttributeError:
                        pass
                else:
                    _THREAD_LOCAL.host_id = previous_host_id

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

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


@bp.route('/api/container-execs/replace', methods=['POST'])
@advanced
def api_container_execs_replace():
    payload = request.get_json(silent=True) or {}
    source_config = str(payload.get('sourceConfig') or '').strip()
    target_config = str(payload.get('targetConfig') or '').strip()
    dry_run = bool(payload.get('dryRun', True))
    if not source_config or not target_config:
        return jsonify({'error': 'sourceConfig and targetConfig are required'}), 400
    if source_config == target_config:
        return jsonify({'error': 'sourceConfig and targetConfig must differ'}), 400
    project_keys = payload.get('projectKeys')
    object_types = payload.get('objectTypes')
    project_filter = {str(pk).strip() for pk in project_keys if str(pk).strip()} if isinstance(project_keys, list) else None
    type_filter = {str(t).strip().upper() for t in object_types if str(t).strip()} if isinstance(object_types, list) else None

    target_is_inherit = target_config == '__INHERIT__'
    client = g.client
    _dss_xsrf_cookie = next(
        (name for name in request.cookies.keys() if name.startswith('dss_xsrf_token_')),
        '',
    )
    browser_ctx = {
        'origin': request.headers.get('Origin') or '',
        'referer': request.headers.get('Referer') or '',
        'cookie_header': request.headers.get('Cookie') or '',
        'cookie_names': sorted(request.cookies.keys()),
        'xsrf': request.cookies.get(_dss_xsrf_cookie, '') if _dss_xsrf_cookie else '',
        'xsrf_source': _dss_xsrf_cookie,
    }
    cheap_config_names = set(_cex_execution_config_names(client))
    if not target_is_inherit and cheap_config_names and target_config not in cheap_config_names:
        return jsonify({
            'error': f'Unknown targetConfig: {target_config}',
            'validConfigNames': sorted(cheap_config_names),
        }), 400

    ttl = int(_BACKEND_SETTINGS.get('cache_ttl_projects', 600))
    cache_key = _cex_cache_key(project_filter)
    scan = _cex_cached_scan(cache_key, ttl)
    scan_cached = scan is not None
    if scan is None:
        scan = _cex_scan(
            client,
            project_keys_filter=project_filter,
            timeout_ms=int(_BACKEND_SETTINGS.get('container_exec_timeout_ms', 600000)),
        )
        with _CACHE_LOCK:
            _CACHE[_cache_key(cache_key)] = {'ts': time.time(), 'value': scan}

    config_names = set(scan.get('configNames') or [])
    if not target_is_inherit and target_config not in config_names:
        return jsonify({
            'error': f'Unknown targetConfig: {target_config}',
            'validConfigNames': sorted(config_names),
            'scanCached': scan_cached,
        }), 400

    visible_source_configs = {
        str(row.get('containerConf') or '')
        for row in (scan.get('usageRows') or [])
        if isinstance(row, dict)
        and row.get('containerMode') == 'EXPLICIT_CONTAINER'
        and row.get('replacementSupported')
        and row.get('containerConf')
    }
    if source_config not in config_names and source_config not in visible_source_configs:
        return jsonify({
            'error': f'Source config is not a current config and is not present in explicit replaceable overrides: {source_config}',
            'validConfigNames': sorted(config_names),
            'visibleSourceConfigs': sorted(visible_source_configs),
            'scanCached': scan_cached,
        }), 400

    matched = []
    for row in scan.get('usageRows') or []:
        if not isinstance(row, dict):
            continue
        if type_filter and str(row.get('objectType') or '').upper() not in type_filter:
            continue
        if row.get('containerMode') != 'EXPLICIT_CONTAINER':
            continue
        if row.get('containerConf') != source_config:
            continue
        if not row.get('replacementSupported'):
            continue
        if target_is_inherit:
            surface = str(row.get('surface') or '')
            if surface.startswith('code_studio_template_') or surface == 'bundle_remapping':
                continue
        matched.append(row)

    results: List[Dict[str, Any]] = []
    for row in matched:
        result = {
            'rowId': row.get('id'),
            'projectKey': row.get('projectKey'),
            'objectType': row.get('objectType'),
            'objectId': row.get('objectId'),
            'objectName': row.get('objectName'),
            'surface': row.get('surface'),
            'rawPath': row.get('rawPath'),
            'from': source_config,
            'to': target_config,
            'status': 'planned' if dry_run else 'updated',
        }
        if not dry_run:
            row_diag = {} if str(row.get('surface') or '') == 'ml_task' else None
            try:
                _cex_apply_replace_row(client, row, target_config, browser_ctx=browser_ctx, diag=row_diag)
            except Exception as exc:
                result['status'] = 'failed'
                result['error'] = str(exc)[:500]
            if row_diag is not None:
                result['diag'] = row_diag
        results.append(result)

    if not dry_run:
        _cache_pop_matching(lambda key_text: str(key_text).startswith('container_execs'))
        _bump_session_epoch()

    return jsonify({
        'dryRun': dry_run,
        'sourceConfig': source_config,
        'targetConfig': target_config,
        'scanCached': scan_cached,
        'matchedRows': len(matched),
        'updatedRows': len([r for r in results if r.get('status') == 'updated']),
        'skippedRows': 0,
        'failedRows': len([r for r in results if r.get('status') == 'failed']),
        'results': results,
    })


