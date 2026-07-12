"""Macro invocation wrappers — run python-runnables/ macros on the active host."""
from typing import Any, Dict

from adk_backend.clients import _resolve_macro_project


# Phase 2: macro invocation IDs. The runnables themselves live at
# python-runnables/host-metrics/ and python-runnables/dbhealth-query/.
_HOST_METRICS_MACRO_ID = 'pyrunnable_admin-toolkit_host-metrics'
_PROCESS_METRICS_MACRO_ID = 'pyrunnable_admin-toolkit_process-metrics'
_RESOURCE_SAMPLE_MACRO_ID = 'pyrunnable_admin-toolkit_resource-sample'
_DBHEALTH_MACRO_ID = 'pyrunnable_admin-toolkit_dbhealth-query'
_IMAGE_CLEANER_MACRO_ID = 'pyrunnable_admin-toolkit_image-cleaner'
_K8S_INSIGHTS_MACRO_ID = 'pyrunnable_admin-toolkit_k8s-insights'
_CRU_AUDIT_MACRO_ID = 'pyrunnable_admin-toolkit_cru-audit'
_ADOPTION_INVENTORY_MACRO_ID = 'pyrunnable_admin-toolkit_adoption-inventory'
_ADOPTION_EVENTS_MACRO_ID = 'pyrunnable_admin-toolkit_adoption-events'
_LOG_CLEANER_MACRO_ID = 'pyrunnable_admin-toolkit_log-cleaner'
_DOCKER_GOVERNOR_MACRO_ID = 'pyrunnable_admin-toolkit_docker-governor'
_K8S_APPLY_MACRO_ID = 'pyrunnable_admin-toolkit_k8s-apply'
_FS_CLEANUP_MACRO_ID = 'pyrunnable_admin-toolkit_fs-cleanup'
_HOST_CONFIG_MACRO_ID = 'pyrunnable_admin-toolkit_host-config'


def _host_metrics_macro(client: Any) -> Dict[str, Any]:
    """Invoke host-metrics macro on the active host. Returns the raw JSON
    result dict (see python-runnables/host-metrics/runnable.py for shape).

    Raises MacroProjectMissing if ADMINTOOLKIT doesn't exist on the host —
    the @errorhandler converts that to a 409 the frontend can react to.
    """
    project = _resolve_macro_project(client)
    macro = project.get_macro(_HOST_METRICS_MACRO_ID)
    run_id = macro.run(params={}, wait=True)
    result = macro.get_result(run_id, as_type='json')
    if not isinstance(result, dict):
        return {'error': f'macro returned non-dict: {type(result).__name__}'}
    return result


def _process_metrics_macro(client: Any) -> Dict[str, Any]:
    """Invoke process-metrics macro on the active host. Returns the raw JSON
    result dict (see python-runnables/process-metrics/runnable.py for shape:
    {ok, processes:[{pid,user,cpuPercent,memPercent,rssKb,vszKb,command}], ...}).

    Raises MacroProjectMissing if ADMINTOOLKIT doesn't exist on the host —
    the @errorhandler converts that to a 409 the frontend can react to.
    """
    project = _resolve_macro_project(client)
    macro = project.get_macro(_PROCESS_METRICS_MACRO_ID)
    run_id = macro.run(params={}, wait=True)
    result = macro.get_result(run_id, as_type='json')
    if not isinstance(result, dict):
        return {'ok': False, 'error': f'macro returned non-dict: {type(result).__name__}'}
    return result


def _resource_sample_macro(client: Any) -> Dict[str, Any]:
    """Invoke resource-sample macro on the active host. Returns the raw JSON
    result dict (see python-runnables/resource-sample/runnable.py for shape:
    {ok, ts, cpu:{user,...,steal,cpuCount}, mem:{totalKb,...,swapFreeKb}} with
    RAW cumulative counters — the frontend diffs consecutive samples).

    Raises MacroProjectMissing if ADMINTOOLKIT doesn't exist on the host —
    the @errorhandler converts that to a 409 the frontend can react to.
    """
    project = _resolve_macro_project(client)
    macro = project.get_macro(_RESOURCE_SAMPLE_MACRO_ID)
    run_id = macro.run(params={}, wait=True)
    result = macro.get_result(run_id, as_type='json')
    if not isinstance(result, dict):
        return {'ok': False, 'error': f'macro returned non-dict: {type(result).__name__}'}
    return result


def _dbhealth_macro(client: Any, operation: str, **params: Any) -> Dict[str, Any]:
    """Invoke dbhealth-query macro on the active host.

    operation ∈ {test-password, run-query, list-tables}. Extra params:
    sql, connection, password — included only when not None.
    """
    project = _resolve_macro_project(client)
    macro = project.get_macro(_DBHEALTH_MACRO_ID)
    macro_params: Dict[str, Any] = {'operation': operation}
    for k in ('sql', 'connection', 'password'):
        v = params.get(k)
        if v is not None and v != '':
            macro_params[k] = v
    run_id = macro.run(params=macro_params, wait=True)
    result = macro.get_result(run_id, as_type='json')
    if not isinstance(result, dict):
        return {'ok': False, 'error': f'macro returned non-dict: {type(result).__name__}'}
    return result


def _image_cleaner_macro(client: Any, operation: str, **params: Any) -> Dict[str, Any]:
    """Invoke the target-host image-cleaner macro."""
    project = _resolve_macro_project(client)
    macro = project.get_macro(_IMAGE_CLEANER_MACRO_ID)
    macro_params: Dict[str, Any] = {'operation': operation}
    for key, value in params.items():
        if value is not None:
            macro_params[key] = value
    run_id = macro.run(params=macro_params, wait=True)
    result = macro.get_result(run_id, as_type='json')
    if not isinstance(result, dict):
        return {'ok': False, 'error': f'macro returned non-dict: {type(result).__name__}'}
    return result


def _cru_audit_macro(client: Any, **params: Any) -> Dict[str, Any]:
    """Invoke the cru-audit macro on the active host. Parses the host audit
    logs and returns the rolled-up CRU JSON (see python-runnables/cru-audit/
    runnable.py for shape: {ok, span, totals, projects, users, contextTypes,
    idleResources}). The one optional param is max_files (INT, 0 = all).

    Raises MacroProjectMissing if ADMINTOOLKIT doesn't exist on the host —
    the @errorhandler converts that to a 409 the frontend can react to.
    """
    project = _resolve_macro_project(client)
    macro = project.get_macro(_CRU_AUDIT_MACRO_ID)
    macro_params: Dict[str, Any] = {}
    for key, value in params.items():
        if value is not None:
            macro_params[key] = value
    run_id = macro.run(params=macro_params, wait=True)
    result = macro.get_result(run_id, as_type='json')
    if not isinstance(result, dict):
        return {'ok': False, 'error': f'macro returned non-dict: {type(result).__name__}'}
    return result


def _adoption_inventory_macro(client: Any) -> Dict[str, Any]:
    """Invoke the adoption-inventory macro on the active host. Walks the
    config tree and returns the full-history surviving-object inventory (see
    python-runnables/adoption-inventory/runnable.py for shape: {ok, families,
    creationMonths, creators, projects, firstCreationMs, lastEditMs, ...}).

    Raises MacroProjectMissing if ADMINTOOLKIT doesn't exist on the host —
    the @errorhandler converts that to a 409 the frontend can react to.
    """
    project = _resolve_macro_project(client)
    macro = project.get_macro(_ADOPTION_INVENTORY_MACRO_ID)
    run_id = macro.run(params={}, wait=True)
    result = macro.get_result(run_id, as_type='json')
    if not isinstance(result, dict):
        return {'ok': False, 'error': f'macro returned non-dict: {type(result).__name__}'}
    return result


def _adoption_events_macro(client: Any, **params: Any) -> Dict[str, Any]:
    """Invoke the adoption-events macro on the active host. Mines the audit
    logs for the human msgType event mix (see python-runnables/adoption-events/
    runnable.py for shape: {ok, humans, msgTypeCounts, coverageDays, ...}).
    The one optional param is max_files (INT, 0 = all).

    Raises MacroProjectMissing if ADMINTOOLKIT doesn't exist on the host —
    the @errorhandler converts that to a 409 the frontend can react to.
    """
    project = _resolve_macro_project(client)
    macro = project.get_macro(_ADOPTION_EVENTS_MACRO_ID)
    macro_params: Dict[str, Any] = {}
    for key, value in params.items():
        if value is not None:
            macro_params[key] = value
    run_id = macro.run(params=macro_params, wait=True)
    result = macro.get_result(run_id, as_type='json')
    if not isinstance(result, dict):
        return {'ok': False, 'error': f'macro returned non-dict: {type(result).__name__}'}
    return result


def _generic_op_macro(client: Any, macro_id: str, operation: str, **params: Any) -> Dict[str, Any]:
    """Shared runner for {operation, **params} macros (log-cleaner,
    docker-governor, k8s-apply). None params are dropped; non-dict results
    become a structured error."""
    project = _resolve_macro_project(client)
    macro = project.get_macro(macro_id)
    macro_params: Dict[str, Any] = {'operation': operation}
    for key, value in params.items():
        if value is not None:
            macro_params[key] = value
    run_id = macro.run(params=macro_params, wait=True)
    result = macro.get_result(run_id, as_type='json')
    if not isinstance(result, dict):
        return {'ok': False, 'error': f'macro returned non-dict: {type(result).__name__}'}
    return result


def _host_config_macro(client: Any, operation: str, **params: Any) -> Dict[str, Any]:
    """Invoke the host-config macro (operation ∈ {read, apply}). Extra params
    for apply: file, section, key, value, expected_current. The whitelist +
    drift guard are enforced inside the macro itself."""
    return _generic_op_macro(client, _HOST_CONFIG_MACRO_ID, operation, **params)


def _log_cleaner_macro(client: Any, operation: str, **params: Any) -> Dict[str, Any]:
    """Invoke the log-cleaner macro (operation ∈ {scan, delete}). Extra params:
    roots (CSV), min_age_days, max_delete_gb, dry_run. The rotated-log
    whitelist is enforced inside the macro itself."""
    return _generic_op_macro(client, _LOG_CLEANER_MACRO_ID, operation, **params)


def _docker_governor_macro(client: Any, operation: str, **params: Any) -> Dict[str, Any]:
    """Invoke the docker-governor macro (operation ∈ {df, usage-scan,
    builder-prune, image-prune, daemon-config-script}). Extra params:
    keep_storage_gb, filter_until_hours, dry_run. Fixed-argv policy is
    enforced inside the macro."""
    return _generic_op_macro(client, _DOCKER_GOVERNOR_MACRO_ID, operation, **params)


def _fs_cleanup_macro(client: Any, operation: str, **params: Any) -> Dict[str, Any]:
    """Invoke the fs-cleanup macro (operation ∈ {scan, delete}). Extra params:
    policy, project_key, min_age_days, keep_last_runs, max_delete_gb, dry_run.
    Roots/age/keep-newest policy and the running-webapp exclusion are enforced
    inside the macro (atk_agent_common.policies.fs_paths)."""
    return _generic_op_macro(client, _FS_CLEANUP_MACRO_ID, operation, **params)


def _k8s_apply_macro(client: Any, operation: str, **params: Any) -> Dict[str, Any]:
    """Invoke the k8s-apply macro (operation ∈ {preview, execute}). Extra
    params: cluster_id, commands_json, manifest_yaml, dry_run. The kubectl
    verb/kind/namespace policy is enforced inside the macro."""
    return _generic_op_macro(client, _K8S_APPLY_MACRO_ID, operation, **params)


def _k8s_insights_macro(client: Any, operation: str = 'audit', **params: Any) -> Dict[str, Any]:
    """Invoke the K8S Insights macro on the active host.

    operation = 'audit' | 'list-clusters'. For 'audit', pass cluster_id and
    optional rules_filter via **params.
    """
    project = _resolve_macro_project(client)
    macro = project.get_macro(_K8S_INSIGHTS_MACRO_ID)
    macro_params: Dict[str, Any] = {'operation': operation}
    for key, value in params.items():
        if value is not None and value != '':
            macro_params[key] = value
    run_id = macro.run(params=macro_params, wait=True)
    result = macro.get_result(run_id, as_type='json')
    if not isinstance(result, dict):
        return {'ok': False, 'error': f'macro returned non-dict: {type(result).__name__}'}
    return result
