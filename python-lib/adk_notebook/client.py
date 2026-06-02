"""DSS client + macro plumbing for the notebook card files.

Mirrors the macro invocation layer in webapps/admin-toolkit/backend.py, but
for in-DSS use: a notebook running in the admin-toolkit code env already has an
authenticated session, so get_client() needs no URL or API key.

Host-bound work (filesystem reads, shell, /proc, <DIP_HOME> access) runs inside
the python-runnables/* macros — exactly as the webapp does — and the wrappers
below invoke them on the ADMINTOOLKIT project. Pure DSS-API scans use the
client directly.
"""
from __future__ import annotations

from typing import Any, Dict

import dataiku


# Macro invocation project — always ADMINTOOLKIT (backend.py:1833).
MACRO_PROJECT_KEY = 'ADMINTOOLKIT'

# Macro IDs (backend.py:2318-2322). The runnables live at python-runnables/*.
HOST_METRICS_MACRO_ID = 'pyrunnable_admin-toolkit_host-metrics'
PROCESS_METRICS_MACRO_ID = 'pyrunnable_admin-toolkit_process-metrics'
DBHEALTH_MACRO_ID = 'pyrunnable_admin-toolkit_dbhealth-query'
IMAGE_CLEANER_MACRO_ID = 'pyrunnable_admin-toolkit_image-cleaner'
K8S_INSIGHTS_MACRO_ID = 'pyrunnable_admin-toolkit_k8s-insights'


def get_client() -> Any:
    """Return the in-DSS API client for the host running this notebook.

    No URL / API key needed — the notebook kernel is already authenticated as
    the running user against the local DSS.
    """
    return dataiku.api_client()


def resolve_macro_project(client: Any) -> Any:
    """Return the ADMINTOOLKIT project on the active client (mirrors
    backend.py:2025 _resolve_macro_project, minus the bootstrap-modal error
    translation). Forces a server-side get_summary() so a missing project
    fails fast with a clear error.
    """
    project = client.get_project(MACRO_PROJECT_KEY)
    project.get_summary()
    return project


def run_macro(client: Any, macro_id: str, params: Dict[str, Any] | None = None) -> Any:
    """Run a python-runnable macro on ADMINTOOLKIT and return its JSON result.

    Drops None / '' params (matching the webapp's macro wrappers) so optional
    arguments are simply omitted.
    """
    project = resolve_macro_project(client)
    macro = project.get_macro(macro_id)
    macro_params: Dict[str, Any] = {}
    for key, value in (params or {}).items():
        if value is not None and value != '':
            macro_params[key] = value
    run_id = macro.run(params=macro_params, wait=True)
    return macro.get_result(run_id, as_type='json')


def host_metrics(client: Any) -> Dict[str, Any]:
    """Invoke host-metrics macro (backend.py:2325). Returns the raw result dict
    (memory / ulimit / df / supervisord log / cpu / license, etc.)."""
    result = run_macro(client, HOST_METRICS_MACRO_ID, {})
    if not isinstance(result, dict):
        return {'error': f'macro returned non-dict: {type(result).__name__}'}
    return result


def process_metrics(client: Any) -> Dict[str, Any]:
    """Invoke process-metrics macro (backend.py:2341). Returns
    {ok, processes:[{pid,user,cpuPercent,memPercent,rssKb,vszKb,command}], ...}."""
    result = run_macro(client, PROCESS_METRICS_MACRO_ID, {})
    if not isinstance(result, dict):
        return {'ok': False, 'error': f'macro returned non-dict: {type(result).__name__}'}
    return result


def dbhealth(client: Any, operation: str, **params: Any) -> Dict[str, Any]:
    """Invoke dbhealth-query macro (backend.py:2358).

    operation in {test-password, run-query, list-tables}. Extra params:
    sql, connection, password — included only when not None/''.
    """
    macro_params: Dict[str, Any] = {'operation': operation}
    for k in ('sql', 'connection', 'password'):
        v = params.get(k)
        if v is not None and v != '':
            macro_params[k] = v
    result = run_macro(client, DBHEALTH_MACRO_ID, macro_params)
    if not isinstance(result, dict):
        return {'ok': False, 'error': f'macro returned non-dict: {type(result).__name__}'}
    return result


def image_cleaner(client: Any, operation: str, **params: Any) -> Dict[str, Any]:
    """Invoke image-cleaner macro (backend.py:2378)."""
    macro_params: Dict[str, Any] = {'operation': operation}
    for key, value in params.items():
        if value is not None:
            macro_params[key] = value
    result = run_macro(client, IMAGE_CLEANER_MACRO_ID, macro_params)
    if not isinstance(result, dict):
        return {'ok': False, 'error': f'macro returned non-dict: {type(result).__name__}'}
    return result


def k8s_insights(client: Any, operation: str = 'audit', **params: Any) -> Dict[str, Any]:
    """Invoke k8s-insights macro (backend.py:2393).

    operation = 'audit' | 'list-clusters'. For 'audit', pass cluster_id and
    optional rules_filter via **params.
    """
    macro_params: Dict[str, Any] = {'operation': operation}
    for key, value in params.items():
        if value is not None and value != '':
            macro_params[key] = value
    result = run_macro(client, K8S_INSIGHTS_MACRO_ID, macro_params)
    if not isinstance(result, dict):
        return {'ok': False, 'error': f'macro returned non-dict: {type(result).__name__}'}
    return result
