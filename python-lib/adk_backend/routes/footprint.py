"""Project-footprint routes: the benchmark-instrumented scan + progress polling.

The scan runs footprint fetch, usage scan and saved-model scan in parallel
worker threads; those acquire their own client via `_thread_client()` (the
host-context-propagating `ThreadPoolExecutor` from adk_backend.clients keeps
them targeting the selected host). Partial rows + progress events stream to the
frontend through the adk_backend.progress store, polled via
`/api/project-footprint/progress`.
"""

import logging
import math
import time
from concurrent.futures import TimeoutError as FuturesTimeoutError, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import Blueprint, g, jsonify, request

from adk_backend.clients import (
    ThreadPoolExecutor,
    _list_projects_catalog_cheap,
    _thread_client,
)
from adk_backend.context import _THREAD_LOCAL
from adk_backend.footprint import (
    _collect_bucket_file_count_by_name,
    _collect_bucket_size_by_name,
    _compute_footprint_payload,
    _footprint_available,
    _footprint_bucket_breakdown,
    _footprint_size,
    _footprint_unavailable_reason,
)
from adk_backend.progress import (
    _append_progress_event,
    _append_progress_partial_row,
    _finish_progress,
    _notify_progress,
    _read_progress,
    _set_progress_summary,
    _start_progress,
)
from adk_backend.settings import _BACKEND_SETTINGS
from adk_backend.sysinfo import _format_size_human
from adk_backend.usage_scan import _get_shared_project_code_env_usage
from adk_backend.utils import _coerce_float, _coerce_int, _coerce_progress_params, _parallel_workers

bp = Blueprint('footprint', __name__)

_LOGGER = logging.getLogger(__name__)


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


def _fetch_project_footprint(project_key: str) -> Dict[str, Any]:
    project_key = str(project_key or '').strip()
    if not project_key:
        return {'projectKey': '', 'payload': None}
    client = _thread_client()
    payload = _compute_footprint_payload(client, 'project', project_key)
    return {'projectKey': project_key, 'payload': payload}


def _build_project_footprint_map(client: Any, project_keys: List[str]) -> Dict[str, Any]:
    return _build_project_footprint_map_with_deadline(client, project_keys, None, None)


def _build_project_footprint_map_with_deadline(
    client: Any,
    project_keys: List[str],
    deadline_ts: Optional[float] = None,
    progress_cb: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    wanted_keys = [str(key) for key in project_keys if str(key).strip()]
    footprint_map: Dict[str, Any] = {}

    started = time.time()
    if not wanted_keys:
        return footprint_map

    # Run direct per-project footprint calls with a fixed parallelism budget.
    max_workers = min(8, len(wanted_keys))
    _LOGGER.info("[footprint-map] mode=per-project wanted=%s workers=%s", len(wanted_keys), max_workers)
    _notify_progress(
        progress_cb,
        'project_footprint_fetch_pool_start',
        f"project footprint fetch started projects={len(wanted_keys)} workers={max_workers}",
    )
    if max_workers <= 1:
        for key in wanted_keys:
            if deadline_ts is not None and time.time() >= deadline_ts:
                _notify_progress(progress_cb, 'project_footprint_fetch_timeout', 'deadline reached before serial fetch', 'warn', key)
                break
            fetch_started = time.time()
            _notify_progress(progress_cb, 'project_footprint_fetch_start', 'fetch project footprint', 'info', key)
            result = _fetch_project_footprint(key)
            payload = result.get('payload')
            if payload is not None:
                footprint_map[key] = payload
                _notify_progress(
                    progress_cb,
                    'project_footprint_fetch_ok',
                    'project footprint loaded',
                    'info',
                    key,
                    elapsed_ms=(time.time() - fetch_started) * 1000.0,
                )
            else:
                _notify_progress(
                    progress_cb,
                    'project_footprint_fetch_error',
                    'project footprint payload missing',
                    'warn',
                    key,
                    elapsed_ms=(time.time() - fetch_started) * 1000.0,
                )
        _LOGGER.info("[footprint-map] serial rows=%s elapsed=%.2fs", len(footprint_map), time.time() - started)
        _notify_progress(
            progress_cb,
            'project_footprint_fetch_pool_done',
            f"project footprint fetch completed rows={len(footprint_map)}",
            'info',
            elapsed_ms=(time.time() - started) * 1000.0,
        )
        return footprint_map

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_key: Dict[Any, str] = {}
        future_started_at: Dict[str, float] = {}
        for key in wanted_keys:
            if deadline_ts is not None and time.time() >= deadline_ts:
                _notify_progress(progress_cb, 'project_footprint_fetch_timeout', 'deadline reached while submitting fetch jobs', 'warn', key)
                break
            _notify_progress(progress_cb, 'project_footprint_fetch_start', 'fetch project footprint', 'info', key)
            future_started_at[key] = time.time()
            future = pool.submit(_fetch_project_footprint, key)
            future_to_key[future] = key

        timed_out = False
        if future_to_key:
            timeout_seconds: Optional[float] = None
            if deadline_ts is not None:
                timeout_seconds = max(0.0, deadline_ts - time.time())
            try:
                future_iter = as_completed(list(future_to_key.keys()), timeout=timeout_seconds)
                for future in future_iter:
                    key = future_to_key.get(future, '')
                    if deadline_ts is not None and time.time() >= deadline_ts:
                        timed_out = True
                        _notify_progress(progress_cb, 'project_footprint_fetch_timeout', 'deadline reached while collecting results', 'warn', key or None)
                        break
                    try:
                        result = future.result()
                    except Exception as exc:
                        _notify_progress(progress_cb, 'project_footprint_fetch_error', f"fetch error: {exc}", 'warn', key or None)
                        continue
                    key = str(result.get('projectKey') or key or '')
                    payload = result.get('payload')
                    if key and payload is not None:
                        footprint_map[key] = payload
                        started_at = future_started_at.get(key, started)
                        _notify_progress(
                            progress_cb,
                            'project_footprint_fetch_ok',
                            'project footprint loaded',
                            'info',
                            key,
                            elapsed_ms=(time.time() - started_at) * 1000.0,
                        )
                    elif key:
                        started_at = future_started_at.get(key, started)
                        _notify_progress(
                            progress_cb,
                            'project_footprint_fetch_error',
                            'project footprint payload missing',
                            'warn',
                            key,
                            elapsed_ms=(time.time() - started_at) * 1000.0,
                        )
            except FuturesTimeoutError:
                timed_out = True
                _notify_progress(progress_cb, 'project_footprint_fetch_timeout', 'deadline reached while waiting for project footprint futures', 'warn')

        if timed_out or (deadline_ts is not None and time.time() >= deadline_ts):
            for future, key in future_to_key.items():
                if future.done():
                    continue
                future.cancel()
                started_at = future_started_at.get(key, started)
                _notify_progress(
                    progress_cb,
                    'project_footprint_fetch_timeout',
                    'project footprint fetch cancelled on deadline',
                    'warn',
                    key,
                    elapsed_ms=(time.time() - started_at) * 1000.0,
                )

    missing = max(0, len(wanted_keys) - len(footprint_map))
    if missing > 0:
        # Per-project fetch exceptions are logged at DEBUG in _compute_footprint_payload; surface
        # the aggregate here so customer logs reveal systemic failures (e.g., DSS 14.2 endpoint gone).
        _LOGGER.warning(
            "[footprint-map] final rows=%s wanted=%s missing=%s elapsed=%.2fs — run with DEBUG on 'webapps.backend' for per-project reasons",
            len(footprint_map), len(wanted_keys), missing, time.time() - started,
        )
    else:
        _LOGGER.info("[footprint-map] final rows=%s elapsed=%.2fs", len(footprint_map), time.time() - started)
    _notify_progress(
        progress_cb,
        'project_footprint_fetch_pool_done',
        f"project footprint fetch completed rows={len(footprint_map)}",
        'info',
        elapsed_ms=(time.time() - started) * 1000.0,
    )
    return footprint_map


# ── Scan pipeline helpers: /api/project-footprint ────────────────────────────

def _task_pf_catalog(
    client: Any,
    add_event: Callable,
    limit_label: str,
    project_limit: int,
) -> Dict[str, Any]:
    add_event('load_project_catalog', 'loading project catalog')
    catalog = _list_projects_catalog_cheap(client)
    total_project_count = len(catalog)
    selected_catalog: List[Dict[str, str]] = catalog[:] if project_limit <= 0 else catalog[:project_limit]
    add_event('select_projects_by_key', f"selecting projects by key limit={limit_label}")
    project_info: Dict[str, Dict[str, str]] = {
        str(project.get('key') or ''): {
            'name': str(project.get('name') or project.get('key') or ''),
            'owner': str(project.get('owner') or 'Unknown'),
        }
        for project in selected_catalog
        if str(project.get('key') or '').strip()
    }
    project_keys = list(project_info.keys())
    return {
        'catalog': catalog,
        'total_project_count': total_project_count,
        'project_info': project_info,
        'project_keys': project_keys,
        'selected_count': len(project_keys),
    }


def _task_pf_footprint(
    project_keys: List[str],
    project_info: Dict[str, Dict[str, str]],
    deadline_ts: float,
    add_event: Callable,
    append_partial_row: Callable,
    progress_cb: Callable,
) -> Dict[str, Any]:
    """Runs in a background thread; acquires its own client via _thread_client().
    Emits partial rows immediately so the frontend can render before usage scan finishes."""
    if not project_keys:
        return {}
    client = _thread_client()
    add_event('load_project_footprint_map', f"loading project footprint map for {len(project_keys)} projects")
    project_footprints = _build_project_footprint_map_with_deadline(
        client,
        project_keys,
        deadline_ts=deadline_ts,
        progress_cb=progress_cb,
    )
    for pk in project_keys:
        meta = project_info.get(pk) or {}
        pf = project_footprints.get(pk)
        mdb = _collect_bucket_size_by_name(pf, lambda n: 'manageddataset' in n or ('managed' in n and 'dataset' in n))
        mfb = _collect_bucket_size_by_name(pf, lambda n: 'managedfolder' in n or ('managed' in n and 'folder' in n))
        bb = _collect_bucket_size_by_name(pf, lambda n: 'preparedbundle' in n or n.endswith('bundles') or 'bundle' in n)
        bc = _collect_bucket_file_count_by_name(pf, lambda n: 'preparedbundle' in n or n.endswith('bundles') or 'bundle' in n)
        total = _footprint_size(pf)
        if total <= 0:
            total = mdb + mfb + bb
        append_partial_row({
            'projectKey': pk,
            'name': str(meta.get('name') or pk).replace('_', ' '),
            'owner': meta.get('owner') or 'Unknown',
            'codeEnvCount': 0,
            'codeStudioCount': 0,
            'codeEnvBytes': 0,
            'managedDatasetsBytes': mdb,
            'managedFoldersBytes': mfb,
            'bundleBytes': bb,
            'bundleCount': bc,
            'totalBytes': total,
            'totalGB': total / float(1024 ** 3),
            'codeEnvHealth': _code_env_health(0),
        })
    return project_footprints


def _task_pf_usage_scan(
    project_info: Dict[str, Dict[str, str]],
    deadline_ts: float,
    add_event: Callable,
    progress_cb: Callable,
) -> Dict[str, Any]:
    """Runs in a background thread; acquires its own client via _thread_client()."""
    if not project_info:
        return {}
    client = _thread_client()
    add_event('collect_project_code_env_usage', f"collecting project code env usage for {len(project_info)} projects")
    return _get_shared_project_code_env_usage(
        client,
        project_info,
        {},
        include_project_object_scan=True,
        include_code_env_usage_api=False,
        deadline_ts=deadline_ts,
        progress_cb=progress_cb,
    )


def _format_saved_model_kind(model: Dict[str, Any]) -> str:
    model_type = str(model.get('type') or '').strip().upper()
    prediction_type = str(model.get('predictionType') or '').strip().upper()
    if model_type == 'CLUSTERING':
        return 'Clustering'
    labels = {
        'BINARY_CLASSIFICATION': 'Binary classification',
        'MULTICLASS': 'Multiclass',
        'MULTICLASS_CLASSIFICATION': 'Multiclass',
        'REGRESSION': 'Regression',
        'TIMESERIES_FORECAST': 'Time series forecast',
        'TIME_SERIES_FORECAST': 'Time series forecast',
    }
    if prediction_type in labels:
        return labels[prediction_type]
    if model_type == 'PREDICTION':
        return 'Prediction'
    return 'Unknown'


def _summarize_saved_models(saved_models: List[Dict[str, Any]]) -> Tuple[Dict[str, int], str]:
    counts: Dict[str, int] = {}
    for model in saved_models:
        kind = _format_saved_model_kind(model)
        counts[kind] = counts.get(kind, 0) + 1
    if not counts:
        return {}, ''
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    parts = [label if count == 1 else f"{count} {label}" for label, count in ordered]
    return counts, ', '.join(parts)


def _scan_saved_models_for_project(project_key: str) -> Tuple[str, List[Dict[str, Any]]]:
    client = _thread_client()
    project = client.get_project(project_key)
    rows: List[Dict[str, Any]] = []
    try:
        raw_models = project.list_saved_models() or []
    except Exception:
        return project_key, rows

    for raw in raw_models:
        if not isinstance(raw, dict):
            continue
        model_id = str(raw.get('id') or raw.get('smId') or raw.get('name') or '').strip()
        row: Dict[str, Any] = {
            'id': model_id,
            'name': str(raw.get('name') or model_id or 'Unnamed model'),
            'type': str(raw.get('type') or 'UNKNOWN').strip().upper() or 'UNKNOWN',
            'savedModelType': raw.get('savedModelType'),
            'backendType': raw.get('backendType'),
            'predictionType': raw.get('predictionType'),
            'versionsCount': _coerce_int(raw.get('versionsCount'), 0),
        }
        if not row.get('predictionType') and row.get('type') == 'PREDICTION' and model_id:
            try:
                settings = project.get_saved_model(model_id).get_settings().get_raw()
                mini_task = settings.get('miniTask') if isinstance(settings, dict) else None
                if isinstance(mini_task, dict):
                    row['predictionType'] = mini_task.get('predictionType')
                    row['backendType'] = row.get('backendType') or mini_task.get('backendType')
            except Exception:
                pass
        if model_id:
            try:
                sm = project.get_saved_model(model_id)
                versions = sm.list_versions() or []
                row['versionsCount'] = len(versions)
                active = sm.get_active_version()
                if isinstance(active, dict) and active.get('id') is not None:
                    row['activeVersionId'] = str(active.get('id'))
            except Exception:
                pass
        rows.append(row)
    return project_key, rows


def _task_pf_saved_models(
    project_keys: List[str],
    deadline_ts: float,
    add_event: Callable,
) -> Dict[str, Dict[str, Any]]:
    saved_models_by_project: Dict[str, Dict[str, Any]] = {}
    if not project_keys:
        return saved_models_by_project
    add_event('collect_project_saved_models', f"collecting saved models for {len(project_keys)} projects")
    max_workers = min(_parallel_workers(8), len(project_keys))
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = {}
        for project_key in project_keys:
            if time.time() > deadline_ts:
                add_event('collect_project_saved_models', 'deadline reached while submitting saved model scans', 'warn')
                break
            futures[pool.submit(_scan_saved_models_for_project, project_key)] = project_key
        try:
            for future in as_completed(list(futures.keys()), timeout=max(0.0, deadline_ts - time.time())):
                project_key = futures.get(future) or ''
                try:
                    pk, saved_models = future.result()
                except Exception as exc:
                    add_event('project_saved_models_error', f"saved model scan failed: {exc}", 'warn', project_key)
                    continue
                type_counts, summary = _summarize_saved_models(saved_models)
                saved_models_by_project[pk] = {
                    'savedModels': saved_models,
                    'savedModelCount': len(saved_models),
                    'savedModelTypeCounts': type_counts,
                    'savedModelSummary': summary,
                }
                add_event('project_saved_models_done', f"saved models={len(saved_models)}", 'info', pk)
        except FuturesTimeoutError:
            add_event('collect_project_saved_models', 'timeout while waiting for saved model scans', 'warn')
    return saved_models_by_project


def _task_pf_aggregate(
    project_keys: List[str],
    project_info: Dict[str, Dict[str, str]],
    project_footprints: Dict[str, Any],
    usage_data: Dict[str, Any],
    saved_model_data: Dict[str, Dict[str, Any]],
    deadline_ts: float,
    add_event: Callable,
) -> Dict[str, Any]:
    envs_by_project: Dict[str, set] = usage_data.get('envsByProject') or {k: set() for k in project_info.keys()}
    usage_breakdown_by_project = usage_data.get('usageBreakdownByProject') or {k: {} for k in project_info.keys()}
    usage_details_by_project = usage_data.get('usageDetailsByProject') or {k: [] for k in project_info.keys()}
    code_studios_by_project = usage_data.get('codeStudiosByProject') or {}

    project_rows: List[Dict[str, Any]] = []
    project_risks: List[float] = []
    total_gb_values: List[float] = []

    add_event('aggregate_project_rows', f"aggregating project rows for {len(project_keys)} projects")
    raw_rows: List[Dict[str, Any]] = []
    for project_key in project_keys:
        if time.time() > deadline_ts:
            add_event('aggregate_project_rows', 'deadline reached at step=aggregate_project_rows', 'warn')
            break
        project_started = time.time()
        add_event('project_aggregate_start', 'aggregating project row', 'info', project_key)
        meta = project_info.get(project_key) or {}
        project_footprint = project_footprints.get(project_key)

        managed_datasets_bytes = _collect_bucket_size_by_name(
            project_footprint,
            lambda n: 'manageddataset' in n or ('managed' in n and 'dataset' in n),
        )
        managed_folders_bytes = _collect_bucket_size_by_name(
            project_footprint,
            lambda n: 'managedfolder' in n or ('managed' in n and 'folder' in n),
        )
        project_env_keys = envs_by_project.get(project_key) or set()
        code_env_count = len(project_env_keys)
        bundle_bytes = _collect_bucket_size_by_name(
            project_footprint,
            lambda n: 'preparedbundle' in n or n.endswith('bundles') or 'bundle' in n,
        )
        bundle_count = _collect_bucket_file_count_by_name(
            project_footprint,
            lambda n: 'preparedbundle' in n or n.endswith('bundles') or 'bundle' in n,
        )
        total_bytes = _footprint_size(project_footprint)
        if total_bytes <= 0:
            total_bytes = managed_datasets_bytes + managed_folders_bytes + bundle_bytes
        total_gb = total_bytes / float(1024 ** 3)
        total_gb_values.append(total_gb)
        saved_model_meta = saved_model_data.get(project_key) or {}

        raw_row = {
            'projectKey': project_key,
            'name': str(meta.get('name') or project_key).replace('_', ' '),
            'owner': meta.get('owner') or 'Unknown',
            'codeEnvCount': code_env_count,
            'codeStudios': code_studios_by_project.get(project_key) or [],
            'codeStudioCount': len(code_studios_by_project.get(project_key) or []),
            'codeEnvBytes': 0,
            'managedDatasetsBytes': managed_datasets_bytes,
            'managedFoldersBytes': managed_folders_bytes,
            'bundleBytes': bundle_bytes,
            'bundleCount': bundle_count,
            'footprintBreakdown': _footprint_bucket_breakdown(project_footprint),
            'totalBytes': total_bytes,
            'totalGB': total_gb,
            'codeEnvHealth': _code_env_health(code_env_count),
            'usageBreakdown': usage_breakdown_by_project.get(project_key) or {},
            'usageDetails': usage_details_by_project.get(project_key) or [],
            'codeEnvKeys': sorted(list(project_env_keys)),
            'savedModelCount': _coerce_int(saved_model_meta.get('savedModelCount'), 0),
            'savedModels': saved_model_meta.get('savedModels') or [],
            'savedModelTypeCounts': saved_model_meta.get('savedModelTypeCounts') or {},
            'savedModelSummary': saved_model_meta.get('savedModelSummary') or '',
        }
        raw_rows.append(raw_row)
        add_event(
            'project_aggregate_done',
            (
                f"aggregate complete codeEnvCount={code_env_count} "
                f"total={_format_size_human(total_bytes)} bundles={bundle_count}"
            ),
            'info',
            project_key,
            event_elapsed_ms=(time.time() - project_started) * 1000.0,
        )

    avg_project_gb = (sum(total_gb_values) / len(total_gb_values)) if total_gb_values else 0.0
    add_event('compute_health_scores', f"computing health scores for {len(raw_rows)} projects")
    for row in raw_rows:
        if time.time() > deadline_ts:
            break
        total_gb = _coerce_float(row.get('totalGB'), 0.0)
        size_index = _project_size_index(total_gb, avg_project_gb)
        size_health = _project_size_health(total_gb, size_index)
        code_env_count = _coerce_int(row.get('codeEnvCount'), 0)
        env_risk = _code_env_risk(code_env_count)
        project_risk = (0.7 * env_risk) + (0.3 * size_index)
        project_risks.append(project_risk)
        row.update({
            'instanceAvgProjectGB': round(avg_project_gb, 4),
            'projectSizeIndex': round(size_index, 4),
            'projectSizeHealth': size_health,
            'codeEnvRisk': round(env_risk, 4),
            'projectRisk': round(project_risk, 4),
        })
        project_rows.append(row)

    return {
        'project_rows': project_rows,
        'project_risks': project_risks,
        'total_gb_values': total_gb_values,
    }


@bp.route('/api/project-footprint')
def api_project_footprint():
    client = g.client

    def loader():
        timeout_ms = _BACKEND_SETTINGS['project_footprint_timeout_ms']
        project_limit = 0
        project_selection = 'all_by_project_key'
        limit_label = 'all' if project_limit <= 0 else str(project_limit)
        started = time.time()
        deadline = started + (timeout_ms / 1000.0)
        steps: List[Dict[str, Any]] = []
        op_stats: Dict[str, Dict[str, Any]] = {}
        benchmark_events: List[Dict[str, Any]] = []
        benchmark_timed_out = False
        timeout_at_step: Optional[str] = None
        deadline_pressure_steps: set = set()
        timeout_event_steps: set = set()
        timed_out_or_error = False
        progress_run_id = _start_progress('project_footprint')
        catalog_result: Optional[Dict[str, Any]] = None
        progress_meta: Dict[str, Any] = {
            'selectedProjects': 0,
            'projectFootprintDone': 0,
            'projectUsageDone': 0,
            'projectAggregateDone': 0,
            'catalogDone': False,
        }

        def elapsed_ms() -> float:
            return (time.time() - started) * 1000.0

        def remaining_ms() -> int:
            return max(0, int((deadline - time.time()) * 1000.0))

        def _compute_progress_pct(force_done: bool = False) -> int:
            if force_done:
                return 100
            footprint_total = max(0, int(progress_meta['selectedProjects']))
            usage_total = max(0, int(progress_meta['selectedProjects']))
            aggregate_total = max(0, int(progress_meta['selectedProjects']))
            footprint_ratio = min(1.0, float(progress_meta['projectFootprintDone']) / float(footprint_total)) if footprint_total > 0 else 0.0
            usage_ratio = min(1.0, float(progress_meta['projectUsageDone']) / float(usage_total)) if usage_total > 0 else 0.0
            aggregate_ratio = min(1.0, float(progress_meta['projectAggregateDone']) / float(aggregate_total)) if aggregate_total > 0 else 0.0
            pct = 0.0
            pct += 10.0 if progress_meta['catalogDone'] else 0.0
            pct += 50.0 * footprint_ratio
            pct += 25.0 * usage_ratio
            pct += 15.0 * aggregate_ratio
            if timed_out_or_error:
                return int(max(0.0, min(100.0, pct)))
            return int(max(0.0, min(99.0, pct)))

        def _infer_phase() -> str:
            if not progress_meta['catalogDone']:
                return 'catalog'
            if progress_meta['selectedProjects'] > 0 and progress_meta['projectFootprintDone'] < progress_meta['selectedProjects']:
                return 'footprint_fetch'
            if progress_meta['selectedProjects'] > 0 and progress_meta['projectUsageDone'] < progress_meta['selectedProjects']:
                return 'usage_scan'
            if progress_meta['selectedProjects'] > 0 and progress_meta['projectAggregateDone'] < progress_meta['selectedProjects']:
                return 'aggregate'
            return 'finalizing'

        def _update_progress_summary(force_done: bool = False) -> None:
            _set_progress_summary(
                'project_footprint',
                progress_run_id,
                {
                    'progressPct': _compute_progress_pct(force_done),
                    'phase': _infer_phase() if not force_done else 'done',
                    'selectedProjects': int(progress_meta['selectedProjects']),
                    'projectFootprintDone': int(progress_meta['projectFootprintDone']),
                    'projectUsageDone': int(progress_meta['projectUsageDone']),
                    'projectAggregateDone': int(progress_meta['projectAggregateDone']),
                    'timedOut': bool(benchmark_timed_out),
                    'timeoutAtStep': timeout_at_step,
                    'totalElapsedMs': round(elapsed_ms(), 2),
                    'remainingMs': remaining_ms(),
                },
            )

        def add_event(
            step: str,
            message: str,
            level: str = 'info',
            project_key: Optional[str] = None,
            event_elapsed_ms: Optional[float] = None,
        ) -> None:
            event: Dict[str, Any] = {
                'tMs': round(elapsed_ms(), 2),
                'level': level,
                'step': step,
                'message': message,
            }
            if project_key:
                event['projectKey'] = project_key
            if event_elapsed_ms is not None:
                event['elapsedMs'] = round(max(0.0, float(event_elapsed_ms)), 2)
            benchmark_events.append(event)
            _append_progress_event('project_footprint', progress_run_id, event)
            if step in ('project_footprint_fetch_ok', 'project_footprint_fetch_error', 'project_footprint_fetch_timeout') and project_key:
                progress_meta['projectFootprintDone'] += 1
            if step == 'project_env_refs_resolved' and project_key:
                progress_meta['projectUsageDone'] += 1
            if step == 'project_aggregate_done' and project_key:
                progress_meta['projectAggregateDone'] += 1
            _update_progress_summary(False)

        def progress_event(**kwargs) -> None:
            add_event(
                step=str(kwargs.get('step') or 'event'),
                message=str(kwargs.get('message') or ''),
                level=str(kwargs.get('level') or 'info'),
                project_key=kwargs.get('project_key'),
                event_elapsed_ms=kwargs.get('elapsed_ms'),
            )

        def deadline_reached(step_name: str) -> bool:
            nonlocal benchmark_timed_out, timeout_at_step, timed_out_or_error
            now = time.time()
            if now < deadline:
                if step_name not in deadline_pressure_steps and (deadline - now) <= 10.0:
                    deadline_pressure_steps.add(step_name)
                    add_event(step_name, f"deadline pressure: only {remaining_ms()}ms remaining", 'warn')
                return False
            benchmark_timed_out = True
            timed_out_or_error = True
            if timeout_at_step is None:
                timeout_at_step = step_name
            if step_name not in timeout_event_steps:
                timeout_event_steps.add(step_name)
                add_event(step_name, f"deadline reached at step={step_name}", 'warn')
            return True

        def record_step(name: str, step_start: float, calls: int = 0) -> None:
            elapsed = max(0.0, (time.time() - step_start) * 1000.0)
            avg_ms = (elapsed / calls) if calls > 0 else 0.0
            qps = (calls / (elapsed / 1000.0)) if calls > 0 and elapsed > 0 else 0.0
            steps.append({
                'name': name,
                'calls': int(calls),
                'elapsedMs': round(elapsed, 2),
                'avgMs': round(avg_ms, 2),
                'qps': round(qps, 2),
            })
            add_event(name, f"{name} done calls={calls}", 'info', event_elapsed_ms=elapsed)

        def record_op(name: str, elapsed_ms_value: float, calls: int = 1) -> None:
            entry = op_stats.setdefault(name, {'operation': name, 'calls': 0, 'elapsedMs': 0.0})
            entry['calls'] = int(entry.get('calls') or 0) + int(max(0, calls))
            entry['elapsedMs'] = float(entry.get('elapsedMs') or 0.0) + max(0.0, float(elapsed_ms_value))

        previous_recorder = getattr(_THREAD_LOCAL, 'bench_record_op', None)
        setattr(_THREAD_LOCAL, 'bench_record_op', record_op)

        try:
            # Phase 1: catalog (main thread)
            if not deadline_reached('load_project_catalog'):
                step_start = time.time()
                catalog_result = _task_pf_catalog(client, add_event, limit_label, project_limit)
                record_step('load_project_catalog', step_start, calls=catalog_result['selected_count'])
            else:
                catalog_result = {'catalog': [], 'total_project_count': 0, 'project_info': {}, 'project_keys': [], 'selected_count': 0}
            progress_meta['selectedProjects'] = catalog_result['selected_count']
            progress_meta['catalogDone'] = True
            _update_progress_summary(False)
            _LOGGER.info("[perf:pf] catalog elapsed=%.0fms projects=%d", elapsed_ms(), catalog_result['selected_count'])

            project_keys: List[str] = catalog_result['project_keys']
            project_info: Dict[str, Dict[str, str]] = catalog_result['project_info']
            total_project_count: int = catalog_result['total_project_count']

            # Phase 2: footprint + usage + saved models in parallel (off-thread)
            project_footprints: Dict[str, Any] = {}
            usage_data: Dict[str, Any] = {}
            saved_model_data: Dict[str, Dict[str, Any]] = {}
            if project_keys and not deadline_reached('load_project_footprint_map'):
                step_start_fp = time.time()
                with ThreadPoolExecutor(max_workers=3) as pool:
                    f_footprint = pool.submit(
                        _task_pf_footprint,
                        project_keys,
                        project_info,
                        deadline,
                        add_event,
                        lambda row: _append_progress_partial_row('project_footprint', progress_run_id, row),
                        progress_event,
                    )
                    f_usage = pool.submit(
                        _task_pf_usage_scan,
                        project_info,
                        deadline,
                        add_event,
                        progress_event,
                    )
                    f_saved_models = pool.submit(
                        _task_pf_saved_models,
                        project_keys,
                        deadline,
                        add_event,
                    )
                    project_footprints = f_footprint.result()
                    usage_data = f_usage.result()
                    saved_model_data = f_saved_models.result()
                record_step('load_project_footprint_map', step_start_fp, calls=len(project_keys))
                record_step('collect_project_code_env_usage', step_start_fp, calls=len(project_keys))
                record_step('collect_project_saved_models', step_start_fp, calls=len(project_keys))
            _LOGGER.info("[perf:pf] footprint_fetch elapsed=%.0fms projects=%d", elapsed_ms(), len(project_keys))
            _LOGGER.info("[perf:pf] usage_scan elapsed=%.0fms projects=%d", elapsed_ms(), len(project_keys))
            _LOGGER.info("[perf:pf] saved_model_scan elapsed=%.0fms projects=%d", elapsed_ms(), len(saved_model_data))

            # Phase 3: aggregate (main thread)
            agg_result: Dict[str, Any] = {'project_rows': [], 'project_risks': [], 'total_gb_values': []}
            if project_keys and not deadline_reached('aggregate_project_rows'):
                step_start = time.time()
                agg_result = _task_pf_aggregate(
                    project_keys,
                    project_info,
                    project_footprints,
                    usage_data,
                    saved_model_data,
                    deadline,
                    add_event,
                )
                record_step('aggregate_project_rows', step_start, calls=len(agg_result['project_rows']))
            _LOGGER.info("[perf:pf] aggregate elapsed=%.0fms rows=%d", elapsed_ms(), len(agg_result['project_rows']))

            project_rows: List[Dict[str, Any]] = agg_result['project_rows']
            total_gb_values: List[float] = agg_result['total_gb_values']
            project_risks: List[float] = agg_result['project_risks']

            project_rows.sort(key=lambda item: _coerce_int(item.get('totalBytes'), 0), reverse=True)
            avg_project_gb = (sum(total_gb_values) / len(total_gb_values)) if total_gb_values else 0.0
            avg_project_risk = (sum(project_risks) / len(project_risks)) if project_risks else 0.0

            api_calls = []
            for entry in sorted(op_stats.values(), key=lambda item: float(item.get('elapsedMs') or 0.0), reverse=True):
                calls = int(entry.get('calls') or 0)
                elapsed = float(entry.get('elapsedMs') or 0.0)
                avg_ms = (elapsed / calls) if calls > 0 else 0.0
                qps = (calls / (elapsed / 1000.0)) if calls > 0 and elapsed > 0 else 0.0
                api_calls.append({
                    'operation': entry.get('operation'),
                    'calls': calls,
                    'elapsedMs': round(elapsed, 2),
                    'avgMs': round(avg_ms, 2),
                    'qps': round(qps, 2),
                })

            _LOGGER.info("[perf:pf] total elapsed=%.0fms", elapsed_ms())
            benchmark_summary = {
                'enabled': True,
                'projectLimit': len(project_keys),
                'projectSelection': project_selection,
                'timeoutMs': timeout_ms,
                'timedOut': bool(benchmark_timed_out),
                'timeoutAtStep': timeout_at_step,
                'totalElapsedMs': round(elapsed_ms(), 2),
                'remainingMs': remaining_ms(),
                'totalProjectCount': total_project_count,
                'selectedProjectCount': len(project_keys),
                'steps': steps,
                'apiCalls': api_calls,
                'events': benchmark_events,
            }
            summary = {
                'instanceProjectRiskAvg': round(avg_project_risk, 4),
                'instanceAvgProjectGB': round(avg_project_gb, 4),
                'projectCount': len(project_rows),
                'footprintAvailable': _footprint_available(),
                'footprintReason': _footprint_unavailable_reason(),
                'benchmark': benchmark_summary,
            }
            _LOGGER.info(
                "[project-footprint] benchmark done rows=%s selected=%s total=%s elapsed=%.2fs timedOut=%s",
                len(project_rows),
                len(project_keys),
                total_project_count,
                time.time() - started,
                benchmark_timed_out,
            )
            add_event(
                'project_footprint_done',
                f"project footprint done rows={len(project_rows)} selected={len(project_keys)} total={total_project_count} timedOut={benchmark_timed_out}",
            )
            # Surface per-project scan failures collected during the footprint/usage phases.
            _scan_error_area = {
                'project_footprint_fetch_error': 'footprint',
                'project_footprint_fetch_timeout': 'footprint',
                'project_code_studios_error': 'code_studios',
                'project_permissions_error': 'permissions',
                'project_saved_models_error': 'saved_models',
            }
            scan_errors: List[Dict[str, Any]] = []
            failed_project_keys: set = set()
            for ev in benchmark_events:
                area = _scan_error_area.get(ev.get('step'))
                if not area:
                    continue
                pk = ev.get('projectKey') or ''
                scan_errors.append({
                    'projectKey': pk,
                    'area': area,
                    'error': str(ev.get('message') or '')[:240],
                })
                if pk:
                    failed_project_keys.add(pk)
            _update_progress_summary(True)
            _finish_progress('project_footprint', progress_run_id, status='done', summary=benchmark_summary)
            return {
                'projects': project_rows,
                'summary': summary,
                'scanErrors': scan_errors,
                'failedProjectCount': len(failed_project_keys),
                'scannedProjectCount': len(project_keys),
            }
        except Exception as exc:
            timed_out_or_error = True
            add_event('project_footprint_error', f"project footprint analysis failed: {exc}", 'error')
            _update_progress_summary(False)
            _finish_progress(
                'project_footprint',
                progress_run_id,
                status='error',
                summary={
                    'enabled': True,
                    'projectLimit': progress_meta['selectedProjects'],
                    'projectSelection': project_selection,
                    'timeoutMs': timeout_ms,
                    'timedOut': bool(benchmark_timed_out),
                    'timeoutAtStep': timeout_at_step,
                    'totalElapsedMs': round(elapsed_ms(), 2),
                    'remainingMs': remaining_ms(),
                    'totalProjectCount': catalog_result['total_project_count'] if catalog_result else 0,
                    'selectedProjectCount': progress_meta['selectedProjects'],
                    'steps': steps,
                    'apiCalls': [
                        {
                            'operation': entry.get('operation'),
                            'calls': int(entry.get('calls') or 0),
                            'elapsedMs': round(float(entry.get('elapsedMs') or 0.0), 2),
                            'avgMs': round((float(entry.get('elapsedMs') or 0.0) / max(1, int(entry.get('calls') or 0))), 2),
                            'qps': round((int(entry.get('calls') or 0) / max(0.001, float(entry.get('elapsedMs') or 0.0) / 1000.0)), 2),
                        }
                        for entry in sorted(op_stats.values(), key=lambda item: float(item.get('elapsedMs') or 0.0), reverse=True)
                    ],
                    'events': benchmark_events,
                },
                error=str(exc),
            )
            raise
        finally:
            setattr(_THREAD_LOCAL, 'bench_record_op', previous_recorder)

    data = loader()
    return jsonify(data)


@bp.route('/api/project-footprint/progress')
def api_project_footprint_progress():
    since_raw = request.args.get('since', '0')
    run_id = request.args.get('runId')
    rows_since_raw = request.args.get('rowsSince', '0')
    since, rows_since = _coerce_progress_params(since_raw, rows_since_raw)
    payload = _read_progress('project_footprint', since=since, run_id=run_id, rows_since=rows_since)
    return jsonify(payload)
