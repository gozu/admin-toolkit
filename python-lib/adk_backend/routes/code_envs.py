"""Code-env routes: list/detail scan, sizes, scan progress, package comparison,
and the Code Env Cleaner (SSE scan + backup-and-delete).

The `/api/code-envs` loader streams incremental rows through the shared
progress store (`adk_backend.progress`); `/api/code-envs/progress` is its
polling endpoint. The cleaner's SSE scan reads `g.client` inside its
generator, so it wraps with `stream_with_context`. Detail workers run on the
host-context-propagating `ThreadPoolExecutor` from adk_backend.clients (NOT
concurrent.futures'), so `_thread_client()` keeps targeting the selected host.
"""

import json
import logging
import re
import time
import zipfile
from concurrent.futures import TimeoutError as FuturesTimeoutError, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import Blueprint, Response, g, jsonify, request, stream_with_context

from adk_backend.caching import _cache_get, _cache_pop, _clear_shared_project_code_env_usage
from adk_backend.clients import (
    ThreadPoolExecutor,
    _THREAD_LOCAL,
    _active_support_project,
    _client_perform_json,
    _list_projects_catalog_cheap,
    _sdk_fetch,
    _thread_client,
)
from adk_backend.footprint import (
    _compute_footprint_payload,
    _footprint_available,
    _footprint_unavailable_reason,
)
from adk_backend.progress import (
    _append_progress_event,
    _append_progress_partial_row,
    _finish_progress,
    _read_progress,
    _set_progress_summary,
    _start_progress,
)
from adk_backend.settings import _BACKEND_SETTINGS
from adk_backend.usage_scan import (
    _dedupe_usage_entries,
    _extract_code_env_owner,
    _get_shared_project_code_env_usage,
    _normalize_language,
    _normalize_usage_entry,
    _usage_to_dict,
)
from adk_backend.utils import (
    _bench_call,
    _coerce_int,
    _extract_nested_text,
    _parallel_workers,
    advanced,
)

bp = Blueprint('code_envs', __name__)

_LOGGER = logging.getLogger(__name__)


def _safe_get_raw(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if hasattr(obj, 'get_raw'):
        try:
            raw = obj.get_raw()
            if isinstance(raw, dict):
                return raw
        except Exception:
            pass
    return {}


def _get_code_env_size_map(client: Any) -> Dict[str, int]:
    size_by_env: Dict[str, int] = {}
    global_footprint = _compute_footprint_payload(client, 'global', None)
    if isinstance(global_footprint, dict):
        code_envs_section = global_footprint.get('codeEnvs')
        if isinstance(code_envs_section, dict):
            items = code_envs_section.get('items')
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    name = item.get('name')
                    language = str(item.get('language') or '').strip().lower()
                    if not name or not language:
                        continue
                    size_by_env[f"{language}:{name}"] = _coerce_int(item.get('size'), 0)
    return size_by_env


def _fetch_code_env_details(
    client: Any, lang_upper: str, env_name: str,
    fetch_settings: bool = True,
) -> Tuple[Dict[str, Any], List[Any]]:
    """Fetch code env settings. Returns (settings_raw, [])."""
    settings_raw: Dict[str, Any] = {}
    if fetch_settings and hasattr(client, 'get_code_env'):
        try:
            settings_raw = _sdk_fetch(
                f'code_env_settings:{lang_upper}:{env_name}',
                _BACKEND_SETTINGS['cache_ttl_code_envs'],
                lambda: _safe_get_raw(_bench_call('get_code_env', client.get_code_env, lang_upper, env_name).get_settings()),
            )
        except Exception:
            settings_raw = {}
    return settings_raw, []


def _load_code_env_full_details(
    env_listing: Dict[str, Any],
    project_info: Dict[str, Dict[str, str]],
    size_by_env: Dict[str, int],
    include_usages: bool = True,
    usages_by_env: Optional[Dict[Tuple[str, str], List[Dict]]] = None,
    user_email_by_login: Optional[Dict[str, str]] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(env_listing, dict):
        return None

    name = env_listing.get('envName') or env_listing.get('name') or env_listing.get('id')
    lang = env_listing.get('envLang') or env_listing.get('language') or env_listing.get('type')
    version = env_listing.get('pythonVersion') or env_listing.get('rVersion') or env_listing.get('version')
    if not name:
        return None

    language = _normalize_language(lang)

    size_key = f"{language}:{name}"
    size_bytes = _coerce_int(size_by_env.get(size_key), 0)
    owner = _extract_code_env_owner(env_listing, {})

    # Fast path for large instances: avoid fetching settings unless needed.
    should_fetch = include_usages or (not version) or owner == 'Unknown'
    client = _thread_client()
    settings_raw, _ = _fetch_code_env_details(
        client, language.upper(), name,
        fetch_settings=should_fetch,
    )
    usages: List[Any] = []
    if include_usages and usages_by_env is not None:
        usages = usages_by_env.get((language.upper(), name), [])
    if settings_raw:
        owner = _extract_code_env_owner(env_listing, settings_raw)
    owner_email = (user_email_by_login or {}).get(owner, '') if owner and owner != 'Unknown' else ''
    normalized_usages: List[Dict[str, Any]] = []
    usage_counts: Dict[str, int] = {}
    project_keys: set = set()
    for raw_usage in usages:
        usage = _usage_to_dict(raw_usage)
        normalized = _normalize_usage_entry(usage, project_info)
        normalized.update({
            'codeEnvName': name,
            'codeEnvLanguage': language,
            'codeEnvOwner': owner,
            'codeEnvKey': size_key,
        })
        usage_type = str(normalized.get('usageType') or 'UNKNOWN')
        usage_counts[usage_type] = usage_counts.get(usage_type, 0) + 1
        project_key = str(normalized.get('projectKey') or '')
        if project_key:
            project_keys.add(project_key)
        normalized_usages.append(normalized)

    if language == 'r':
        version_label = str(version or 'R')
    else:
        detail_version = (
            _extract_nested_text(
                settings_raw,
                'desc.pythonInterpreter',
                'pythonInterpreter',
                'spec.pythonInterpreter',
            )
            or env_listing.get('pythonInterpreter')
            or version
        )
        if not detail_version and include_usages:
            detail = _bench_call('code_env_detail_lookup', _client_perform_json, client, 'GET', f"/admin/code-envs/PYTHON/{name}")
            if isinstance(detail, dict):
                detail_version = _extract_nested_text(detail, 'desc.pythonInterpreter', 'pythonInterpreter')

        raw_version_text = str(detail_version or 'Unknown')
        match = re.search(r'PYTHON(\d)(\d+)', raw_version_text, flags=re.IGNORECASE)
        if match:
            version_label = f"{int(match.group(1))}.{int(match.group(2))}"
        else:
            dotted = re.search(r'(\d+)\.(\d+)', raw_version_text)
            version_label = f"{dotted.group(1)}.{dotted.group(2)}" if dotted else raw_version_text

    return {
        'language': language,
        'versionLabel': version_label,
        'row': {
            'name': name,
            'version': version_label,
            'language': language,
            'sizeBytes': size_bytes,
            'owner': owner,
            'ownerEmail': owner_email,
            'usageCount': len(normalized_usages),
            'usageSummary': usage_counts,
            'projectCount': len(project_keys),
            'projectKeys': sorted(project_keys),
            'usageDetails': _dedupe_usage_entries(normalized_usages),
        },
    }


# ── Scan pipeline helpers: /api/code-envs ─────────────────────────────────────

def _env_key_from_listing(env: Dict[str, Any]) -> str:
    env_name = env.get('envName') or env.get('name') or env.get('id')
    env_lang_raw = env.get('envLang') or env.get('language') or env.get('type') or 'PYTHON'
    language = _normalize_language(env_lang_raw)
    return f"{language}:{env_name}" if env_name else 'unknown'


def _task_ce_catalog(
    client: Any,
    add_event: Callable,
    limit_label: str,
    project_limit: int,
) -> Dict[str, Any]:
    add_event('load_project_catalog', 'loading project catalog')
    project_catalog = _list_projects_catalog_cheap(client)
    selected_catalog: List[Dict[str, str]] = project_catalog[:] if project_limit <= 0 else project_catalog[:project_limit]
    add_event('select_projects_by_key', f"selecting projects by key limit={limit_label}")
    project_info: Dict[str, Dict[str, str]] = {}
    for project in selected_catalog:
        key = str(project.get('key') or '').strip()
        if not key:
            continue
        project_info[key] = {
            'name': str(project.get('name') or key),
            'owner': str(project.get('owner') or 'Unknown'),
        }
    add_event(
        'project_scope_ready',
        f"project scope ready selected={len(project_info)} total={len(project_catalog)} limit={limit_label}",
    )
    return {
        'project_catalog': project_catalog,
        'selected_catalog': selected_catalog,
        'project_info': project_info,
        'selected_count': len(project_info),
    }


def _task_ce_size_map(add_event: Callable) -> Dict[str, int]:
    """Runs in a background thread; acquires its own client via _thread_client()."""
    client = _thread_client()
    global_footprint = _compute_footprint_payload(client, 'global', None)
    size_by_env: Dict[str, int] = {}
    if isinstance(global_footprint, dict):
        code_envs_section = global_footprint.get('codeEnvs')
        if isinstance(code_envs_section, dict):
            code_env_items = code_envs_section.get('items')
            if isinstance(code_env_items, list):
                for item in code_env_items:
                    if not isinstance(item, dict):
                        continue
                    item_name = item.get('name')
                    item_lang = str(item.get('language') or '').strip().lower()
                    if not item_name or not item_lang:
                        continue
                    size_key = f"{item_lang}:{item_name}"
                    size_by_env[size_key] = _coerce_int(item.get('size'), 0)
    return size_by_env


def _task_ce_usage_scan(
    client: Any,
    project_info: Dict[str, Dict[str, str]],
    deadline_ts: float,
    add_event: Callable,
    progress_cb: Callable,
) -> Dict[str, Any]:
    if not project_info:
        return {}
    add_event('collect_project_code_env_usage', f"collecting usage for projects={len(project_info)}")
    return _get_shared_project_code_env_usage(
        client,
        project_info,
        {},
        include_project_object_scan=True,
        include_code_env_usage_api=False,
        deadline_ts=deadline_ts,
        progress_cb=progress_cb,
    )


def _task_ce_env_details(
    client: Any,
    envs: List[Dict[str, Any]],
    project_info: Dict[str, Dict[str, str]],
    size_by_env: Dict[str, int],
    progress_meta: Dict[str, Any],
    deadline_ts: float,
    add_event: Callable,
    append_partial_row: Callable,
    usages_by_env: Optional[Dict[Tuple[str, str], List[Dict]]] = None,
    user_email_by_login: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    env_details: List[Dict[str, Any]] = []
    max_workers = min(_parallel_workers(_BACKEND_SETTINGS['code_env_detail_workers']), max(1, len(envs)))
    progress_meta['envDetailsTotal'] = len(envs)
    progress_meta['envDetailsDone'] = 0
    add_event('load_code_env_details', f"loading env details envs={len(envs)} workers={max_workers}")
    if max_workers <= 1:
        for env in envs:
            if time.time() > deadline_ts:
                add_event('load_code_env_details', 'deadline reached at step=load_code_env_details', 'warn')
                break
            env_key = _env_key_from_listing(env)
            env_started = time.time()
            add_event('code_env_detail_start', 'loading code env detail', 'info', env_key)
            detail = _load_code_env_full_details(env, project_info, size_by_env, include_usages=True, usages_by_env=usages_by_env, user_email_by_login=user_email_by_login)
            if detail:
                env_details.append(detail)
                row = detail.get('row')
                if isinstance(row, dict):
                    append_partial_row(row)
                add_event('code_env_detail_ok', 'code env detail loaded', 'info', env_key, (time.time() - env_started) * 1000.0)
            else:
                add_event('code_env_detail_error', 'code env detail missing', 'warn', env_key, (time.time() - env_started) * 1000.0)
    else:
        future_to_env: Dict[Any, Dict[str, Any]] = {}
        env_started_at: Dict[str, float] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for env in envs:
                if time.time() > deadline_ts:
                    break
                env_key = _env_key_from_listing(env)
                add_event('code_env_detail_start', 'loading code env detail', 'info', env_key)
                env_started_at[env_key] = time.time()
                future = pool.submit(_load_code_env_full_details, env, project_info, size_by_env, True, usages_by_env, user_email_by_login)
                future_to_env[future] = env

            timed_out_futures = False
            remaining = max(0.0, deadline_ts - time.time())
            try:
                for future in as_completed(list(future_to_env.keys()), timeout=remaining):
                    if time.time() > deadline_ts:
                        timed_out_futures = True
                        break
                    env = future_to_env.get(future) or {}
                    env_key = _env_key_from_listing(env)
                    started_at = env_started_at.get(env_key, time.time())
                    try:
                        detail = future.result()
                    except Exception as exc:
                        add_event('code_env_detail_error', f"code env detail failed: {exc}", 'warn', env_key, (time.time() - started_at) * 1000.0)
                        continue
                    if detail:
                        env_details.append(detail)
                        row = detail.get('row')
                        if isinstance(row, dict):
                            append_partial_row(row)
                        add_event('code_env_detail_ok', 'code env detail loaded', 'info', env_key, (time.time() - started_at) * 1000.0)
                    else:
                        add_event('code_env_detail_error', 'code env detail missing', 'warn', env_key, (time.time() - started_at) * 1000.0)
            except FuturesTimeoutError:
                timed_out_futures = True
                add_event('load_code_env_details', 'timeout while waiting for env detail futures', 'warn')

            if timed_out_futures or time.time() > deadline_ts:
                for future, env in future_to_env.items():
                    if future.done():
                        continue
                    future.cancel()
                    env_key = _env_key_from_listing(env)
                    started_at = env_started_at.get(env_key, time.time())
                    add_event('code_env_detail_timeout', 'cancelled env detail future on deadline', 'warn', env_key, (time.time() - started_at) * 1000.0)
    return env_details


@bp.route('/api/code-envs')
def api_code_envs():
    client = g.client

    def loader():
        timeout_ms = _BACKEND_SETTINGS['code_env_timeout_ms']
        started = time.time()
        deadline = started + (timeout_ms / 1000.0)
        project_limit = 0
        project_selection = 'all_by_project_key'
        limit_label = 'all' if project_limit <= 0 else str(project_limit)
        code_envs = []
        python_counts: Dict[str, int] = {}
        r_counts: Dict[str, int] = {}
        steps: List[Dict[str, Any]] = []
        op_stats: Dict[str, Dict[str, Any]] = {}
        events: List[Dict[str, Any]] = []
        timed_out = False
        timeout_at_step: Optional[str] = None
        deadline_pressure_steps: set = set()
        timeout_event_steps: set = set()
        timed_out_or_error = False
        progress_run_id = _start_progress('code_envs')
        catalog: Optional[Dict[str, Any]] = None
        progress_meta: Dict[str, Any] = {
            'selectedProjects': 0,
            'projectUsageDone': 0,
            'envDetailsTotal': 0,
            'envDetailsDone': 0,
            'catalogDone': False,
            'sizeMapDone': False,
        }

        def elapsed_ms() -> float:
            return (time.time() - started) * 1000.0

        def remaining_ms() -> int:
            return max(0, int((deadline - time.time()) * 1000.0))

        def remaining_seconds() -> float:
            return max(0.0, deadline - time.time())

        def _compute_progress_pct(force_done: bool = False) -> int:
            if force_done:
                return 100
            usage_total = max(0, int(progress_meta['selectedProjects']))
            usage_ratio = min(1.0, float(progress_meta['projectUsageDone']) / float(usage_total)) if usage_total > 0 else 1.0
            detail_total = max(0, int(progress_meta['envDetailsTotal']))
            detail_ratio = min(1.0, float(progress_meta['envDetailsDone']) / float(detail_total)) if detail_total > 0 else 0.0
            pct = 0.0
            pct += 10.0 if progress_meta['catalogDone'] else 0.0
            pct += 15.0 if progress_meta['sizeMapDone'] else 0.0
            pct += 50.0 * usage_ratio
            pct += 25.0 * detail_ratio
            if timed_out_or_error:
                return int(max(0.0, min(100.0, pct)))
            return int(max(0.0, min(99.0, pct)))

        def _infer_phase() -> str:
            if not progress_meta['catalogDone']:
                return 'catalog'
            if not progress_meta['sizeMapDone']:
                return 'size_map'
            if progress_meta['selectedProjects'] > 0 and progress_meta['projectUsageDone'] < progress_meta['selectedProjects']:
                return 'usage_scan'
            if progress_meta['envDetailsTotal'] > 0 and progress_meta['envDetailsDone'] < progress_meta['envDetailsTotal']:
                return 'env_details'
            return 'finalizing'

        def _update_progress_summary(force_done: bool = False) -> None:
            _set_progress_summary(
                'code_envs',
                progress_run_id,
                {
                    'progressPct': _compute_progress_pct(force_done),
                    'phase': _infer_phase() if not force_done else 'done',
                    'selectedProjects': int(progress_meta['selectedProjects']),
                    'projectUsageDone': int(progress_meta['projectUsageDone']),
                    'envDetailsTotal': int(progress_meta['envDetailsTotal']),
                    'envDetailsDone': int(progress_meta['envDetailsDone']),
                    'timedOut': bool(timed_out),
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
            events.append(event)
            _append_progress_event('code_envs', progress_run_id, event)
            if step == 'project_env_refs_resolved' and project_key:
                progress_meta['projectUsageDone'] += 1
            if step in ('code_env_detail_ok', 'code_env_detail_error', 'code_env_detail_timeout'):
                progress_meta['envDetailsDone'] += 1
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
            nonlocal timed_out, timeout_at_step, timed_out_or_error
            now = time.time()
            if now < deadline:
                if step_name not in deadline_pressure_steps and (deadline - now) <= 10.0:
                    deadline_pressure_steps.add(step_name)
                    add_event(step_name, f"deadline pressure: only {remaining_ms()}ms remaining", 'warn')
                return False
            timed_out = True
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
        add_event('code_envs_start', f"code env analysis started timeoutMs={timeout_ms} limit={limit_label}")

        try:
            # User-email lookup for ownerEmail enrichment.
            users = _sdk_fetch(
                'list_users',
                _BACKEND_SETTINGS['cache_ttl_users'],
                lambda: client.list_users() if hasattr(client, 'list_users') else [],
            ) or []
            user_email_by_login: Dict[str, str] = {}
            for user in users:
                if isinstance(user, dict) and user.get('login'):
                    user_email_by_login[str(user.get('login'))] = str(user.get('email') or user.get('login'))

            # Phase 1: catalog
            step_started = time.time()
            catalog = _task_ce_catalog(client, add_event, limit_label, project_limit)
            record_step('load_project_catalog', step_started, calls=catalog['selected_count'])
            progress_meta['selectedProjects'] = catalog['selected_count']
            progress_meta['catalogDone'] = True
            _update_progress_summary(False)
            _LOGGER.info(
                "[code-envs] projectInfo selected=%s total=%s limit=%s elapsed=%.2fs",
                catalog['selected_count'],
                len(catalog['project_catalog']),
                limit_label,
                time.time() - started,
            )
            _LOGGER.info("[perf:ce] phase1_catalog elapsed=%.0fms projects=%d", elapsed_ms(), catalog['selected_count'])

            # Phase 2: usage_scan and size_map deferred.
            # Per-env usages come from list_code_env_usages() bulk call below; per-project
            # walk is only needed by /api/project-footprint.
            size_by_env: Dict[str, int] = {}
            progress_meta['sizeMapDone'] = True
            _update_progress_summary(False)
            _LOGGER.info("[perf:ce] usage+size deferred, elapsed=%.0fms", elapsed_ms())

            envs: List[Dict[str, Any]] = []
            if not deadline_reached('list_code_envs'):
                step_started = time.time()
                add_event('list_code_envs', 'listing code envs')
                envs = [env for env in (_sdk_fetch(
                    'list_code_envs',
                    _BACKEND_SETTINGS['cache_ttl_code_envs'],
                    lambda: client.list_code_envs() or [],
                ) or []) if isinstance(env, dict)]
                record_step('list_code_envs', step_started, calls=1)
                _LOGGER.info("[perf:ce] list_code_envs elapsed=%.0fms count=%d", elapsed_ms(), len(envs))

            _SKIP_DEPLOYMENT_MODES = {'PLUGIN_MANAGED', 'DSS_INTERNAL'}
            total_env_count = len(envs)
            skipped_env_count = 0
            if not deadline_reached('filter_selected_envs'):
                step_started = time.time()
                before_count = len(envs)
                envs = [
                    env for env in envs
                    if str(env.get('deploymentMode') or '').upper() not in _SKIP_DEPLOYMENT_MODES
                ]
                skipped_env_count = before_count - len(envs)
                add_event('filter_selected_envs', f"filtered out {skipped_env_count} plugin-managed/internal envs, keeping {len(envs)}/{before_count}")
                record_step('filter_selected_envs', step_started, calls=len(envs))
            _LOGGER.info("[code-envs] listed=%s", len(envs))

            env_details: List[Dict[str, Any]] = []
            if envs and not deadline_reached('load_code_env_details'):
                step_started = time.time()
                bulk_usages_raw = _sdk_fetch(
                    'list_code_env_usages',
                    _BACKEND_SETTINGS['cache_ttl_code_envs'],
                    lambda: client.list_code_env_usages() or [],
                )
                usages_by_env_details: Dict[Tuple[str, str], List[Dict]] = {}
                for _u in bulk_usages_raw:
                    _k = (str(_u.get('envLang', '')).upper(), str(_u.get('envName', '')))
                    usages_by_env_details.setdefault(_k, []).append(_u)
                _LOGGER.info("[perf:ce] list_code_env_usages bulk elapsed=%.0fms count=%d", elapsed_ms(), len(bulk_usages_raw))
                env_details = _task_ce_env_details(
                    client,
                    envs,
                    catalog['project_info'],
                    size_by_env,
                    progress_meta,
                    deadline,
                    add_event,
                    lambda row: _append_progress_partial_row('code_envs', progress_run_id, row),
                    usages_by_env=usages_by_env_details,
                    user_email_by_login=user_email_by_login,
                )
                record_step('load_code_env_details', step_started, calls=progress_meta['envDetailsDone'])
                _LOGGER.info("[perf:ce] env_details elapsed=%.0fms envs=%d workers=%d", elapsed_ms(), len(env_details), min(_parallel_workers(_BACKEND_SETTINGS['code_env_detail_workers']), max(1, len(envs))))
            _LOGGER.info("[code-envs] details=%s elapsed=%.2fs", len(env_details), time.time() - started)

            # Phase 4: aggregate rows
            if env_details and not deadline_reached('aggregate_code_env_rows'):
                step_started = time.time()
                add_event('aggregate_code_env_rows', f"aggregating rows count={len(env_details)}")
                processed = 0
                for detail in env_details:
                    row = detail.get('row')
                    if not isinstance(row, dict):
                        continue
                    code_envs.append(row)
                    language = str(detail.get('language') or 'python')
                    version_label = str(detail.get('versionLabel') or row.get('version') or 'Unknown')
                    if language == 'r':
                        r_counts[version_label] = r_counts.get(version_label, 0) + 1
                    else:
                        python_counts[version_label] = python_counts.get(version_label, 0) + 1
                    processed += 1
                record_step('aggregate_code_env_rows', step_started, calls=processed)

            code_envs.sort(key=lambda item: (_coerce_int(item.get('sizeBytes'), 0), str(item.get('name') or '')), reverse=True)
            _LOGGER.info("[code-envs] done rows=%s elapsed=%.2fs", len(code_envs), time.time() - started)
            _LOGGER.info("[perf:ce] total elapsed=%.0fms", elapsed_ms())
            add_event('code_envs_done', f"code envs done rows={len(code_envs)} timedOut={timed_out}")

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

            selected_count = len(catalog['project_info']) if catalog is not None else 0
            benchmark_summary = {
                'enabled': True,
                'projectLimit': selected_count,
                'projectSelection': project_selection,
                'timeoutMs': timeout_ms,
                'timedOut': bool(timed_out),
                'timeoutAtStep': timeout_at_step,
                'totalElapsedMs': round(elapsed_ms(), 2),
                'remainingMs': remaining_ms(),
                'selectedProjectCount': selected_count,
                'selectedEnvKeyCount': 0,
                'steps': steps,
                'apiCalls': api_calls,
                'events': events,
            }
            summary = {
                'benchmark': {
                    **benchmark_summary,
                },
            }
            _update_progress_summary(True)
            _finish_progress('code_envs', progress_run_id, status='done', summary=benchmark_summary)

            return {
                'codeEnvs': code_envs,
                'pythonVersionCounts': python_counts,
                'rVersionCounts': r_counts,
                'totalEnvCount': total_env_count,
                'skippedEnvCount': skipped_env_count,
                'summary': summary,
            }
        except Exception as exc:
            timed_out_or_error = True
            add_event('code_envs_error', f"code env analysis failed: {exc}", 'error')
            _update_progress_summary(False)
            _finish_progress(
                'code_envs',
                progress_run_id,
                status='error',
                summary={
                    'enabled': True,
                    'projectLimit': progress_meta['selectedProjects'],
                    'projectSelection': project_selection,
                    'timeoutMs': timeout_ms,
                    'timedOut': bool(timed_out),
                    'timeoutAtStep': timeout_at_step,
                    'totalElapsedMs': round(elapsed_ms(), 2),
                    'remainingMs': remaining_ms(),
                    'steps': steps,
                    'apiCalls': api_calls if 'api_calls' in locals() else [],
                    'events': events,
                },
                error=str(exc),
            )
            raise
        finally:
            setattr(_THREAD_LOCAL, 'bench_record_op', previous_recorder)

    data = _cache_get('code_envs', _BACKEND_SETTINGS['cache_ttl_code_envs'], loader)
    return jsonify(data)


@bp.route('/api/code-envs/sizes')
def api_code_envs_sizes():
    """Lazy-load code env sizes via global footprint. Cached for 300s."""
    def loader():
        if not _footprint_available():
            return {}
        client = g.client
        return _get_code_env_size_map(client)
    size_map = _cache_get('code_envs_sizes', _BACKEND_SETTINGS['cache_ttl_projects'], loader)
    available = _footprint_available() and bool(size_map)
    reason = _footprint_unavailable_reason() if not _footprint_available() else None
    return jsonify({
        'sizes': size_map,
        'available': available,
        'reason': reason,
    })


@bp.route('/api/code-envs/progress')
def api_code_envs_progress():
    since_raw = request.args.get('since', '0')
    run_id = request.args.get('runId')
    rows_since_raw = request.args.get('rowsSince', '0')
    try:
        since = max(0, int(str(since_raw or '0')))
    except Exception:
        since = 0
    try:
        rows_since = max(0, int(str(rows_since_raw or '0')))
    except Exception:
        rows_since = 0
    payload = _read_progress('code_envs', since=since, run_id=run_id, rows_since=rows_since)
    return jsonify(payload)


# ── Code env comparison helpers ─────────────────────────────────────────────

def _parse_spec_packages(spec: Any) -> Dict[str, str]:
    """Parse a spec package list into {normalized_name: version_spec}."""
    packages: Dict[str, str] = {}
    if not spec:
        return packages
    lines = spec if isinstance(spec, list) else str(spec).strip().split('\n')
    for line in lines:
        line = str(line).strip()
        if not line or line.startswith('#') or line.startswith('-'):
            continue
        m = re.match(r'^([A-Za-z0-9_.\-]+)(?:\[.*?\])?\s*(.*)', line)
        if m:
            name = re.sub(r'[-_.]+', '_', m.group(1)).lower()
            version = m.group(2).strip()
            packages[name] = version
    return packages


def _compare_code_envs_logic(
    envs: List[Tuple[str, str, Dict[str, str]]],
    max_diff: int = 3,
) -> Dict[str, Any]:
    """Classify environment relationships. Returns JSON-serializable result."""
    from collections import defaultdict

    pyver_map = {name: pyver for name, pyver, _ in envs}

    # Bucket by package-name fingerprint
    name_buckets: Dict[frozenset, List[Tuple[str, Dict[str, str]]]] = defaultdict(list)
    for name, pyver, packages in envs:
        key = frozenset(packages.keys())
        name_buckets[key].append((name, packages))

    green_groups: List[Dict[str, Any]] = []
    purple_groups: List[Dict[str, Any]] = []
    blue_groups: List[Dict[str, Any]] = []

    for pkg_names, members in name_buckets.items():
        if len(members) < 2:
            continue

        version_buckets: Dict[frozenset, List[str]] = defaultdict(list)
        for env_name, packages in members:
            vkey = frozenset(packages.items())
            version_buckets[vkey].append(env_name)

        for vkey, env_names in version_buckets.items():
            if len(env_names) < 2:
                continue
            py_sub: Dict[str, List[str]] = defaultdict(list)
            for en in env_names:
                py_sub[pyver_map[en]].append(en)

            # GREEN: same packages, same versions, same python
            for pv, names in py_sub.items():
                if len(names) >= 2:
                    green_groups.append({
                        'envNames': sorted(names),
                        'packageCount': len(dict(vkey)),
                        'pythonVersion': pv,
                    })

            # PURPLE: same packages, same versions, different python
            if len(py_sub) >= 2:
                all_names = sorted(env_names)
                pv_info = {en: pyver_map[en] for en in all_names}
                purple_groups.append({
                    'envNames': all_names,
                    'packageCount': len(dict(vkey)),
                    'pythonVersions': pv_info,
                })

        # BLUE: same package set, version diffs exist
        if len(version_buckets) >= 2:
            member_names = sorted(m[0] for m in members)
            diff_table: Dict[str, Dict[str, str]] = {}
            member_dict = {n: p for n, p in members}
            for pkg in sorted(pkg_names):
                versions = {n: member_dict[n].get(pkg, '') for n in member_names}
                if len(set(versions.values())) > 1:
                    diff_table[pkg] = versions
            if diff_table:
                total_pkgs = len(next(iter(member_dict.values())))
                blue_groups.append({
                    'envNames': member_names,
                    'packageCount': total_pkgs,
                    'diffCount': len(diff_table),
                    'diffs': diff_table,
                })

    # YELLOW: near-matches across different buckets (disabled — O(n^2) too slow)
    yellow_pairs: List[Dict[str, Any]] = []

    green_groups.sort(key=lambda g: g['envNames'][0])
    purple_groups.sort(key=lambda g: g['envNames'][0])
    blue_groups.sort(key=lambda g: g['envNames'][0])
    yellow_pairs.sort(key=lambda p: (p['envA'], p['envB']))

    return {
        'green': green_groups,
        'purple': purple_groups,
        'blue': blue_groups,
        'yellow': yellow_pairs,
        'analyzedCount': len(envs),
    }


@bp.route('/api/code-envs/compare')
def api_code_envs_compare():
    max_diff = 1
    try:
        max_diff = max(1, int(request.args.get('maxDiff', '1')))
    except Exception:
        pass

    def loader():
        client = g.client
        ttl = _BACKEND_SETTINGS['cache_ttl_code_envs']
        env_listings = _sdk_fetch('list_code_envs', ttl, lambda: client.list_code_envs() or [])
        _SKIP = {'PLUGIN_MANAGED', 'DSS_INTERNAL'}
        envs: List[Tuple[str, str, Dict[str, str]]] = []

        def fetch_one(env_listing: Dict[str, Any]) -> Optional[Tuple[str, str, Dict[str, str]]]:
            name = env_listing.get('envName') or env_listing.get('name')
            lang = (env_listing.get('envLang') or env_listing.get('language') or 'PYTHON').upper()
            if not name or lang != 'PYTHON':
                return None
            try:
                c = _thread_client()
                raw = _sdk_fetch(
                    f'code_env_settings:{lang}:{name}', ttl,
                    lambda: _safe_get_raw(_bench_call('get_code_env', c.get_code_env, lang, name).get_settings()),
                )
                if str(raw.get('deploymentMode') or '').upper() in _SKIP:
                    return None
                packages = _parse_spec_packages(raw.get('specPackageList', ''))
                pyver_raw = (
                    raw.get('desc', {}).get('pythonInterpreter')
                    or raw.get('pythonInterpreter')
                    or ''
                )
                ver = str(pyver_raw).replace('PYTHON', '')
                if len(ver) == 2:
                    pyver = f'{ver[0]}.{ver[1]}'
                elif len(ver) >= 3:
                    pyver = f'{ver[0]}.{ver[1:]}'
                else:
                    pyver = str(pyver_raw) or 'unknown'
                return (name, pyver, packages)
            except Exception:
                return None

        workers = min(8, len(env_listings))
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(fetch_one, e): e for e in env_listings}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    envs.append(result)

        return _compare_code_envs_logic(envs, max_diff)

    data = _cache_get('code_envs_compare', _BACKEND_SETTINGS.get('cache_ttl_projects', 300), loader)
    return jsonify(data)


# ── Code Env Cleaner helpers ──

def _cec_filter_envs(envs):
    """Filter out plugin-managed and DSS-internal environments."""
    return [
        e for e in envs
        if e.get("deploymentMode", "") not in ("PLUGIN_MANAGED", "DSS_INTERNAL")
    ]


def _cec_fetch_env_with_usages(client, env_info):
    """Fetch usage info for a single env and return result dict + timing."""
    env_name = env_info["envName"]
    env_lang = env_info["envLang"]
    t0 = time.time()

    try:
        usages = client._perform_json(
            "GET", "/admin/code-envs/%s/%s/usages" % (env_lang, env_name)
        )
        usage_count = len(usages) if isinstance(usages, list) else 0
    except Exception:
        usages = []
        usage_count = -1

    usage_ms = int((time.time() - t0) * 1000)

    return {
        "envName": env_name,
        "envLang": env_lang,
        "deploymentMode": env_info.get("deploymentMode", ""),
        "owner": env_info.get("owner", ""),
        "pythonInterpreter": env_info.get("pythonInterpreter", ""),
        "usageCount": usage_count,
        "usages": usages if isinstance(usages, list) else [],
    }, usage_ms


@bp.route('/api/tools/code-env-cleaner/scan')
def api_code_env_cleaner_scan():
    """Stream code env data via SSE for real-time progress."""
    threads = request.args.get("threads", "1", type=str)
    try:
        threads = max(1, min(20, int(threads)))
    except (ValueError, TypeError):
        threads = 1

    def generate():
        t0 = time.time()
        client = g.client

        try:
            all_envs = client._perform_json("GET", "/admin/code-envs/")
        except Exception as e:
            yield "event: error\ndata: %s\n\n" % json.dumps({"error": str(e)})
            return

        filtered = _cec_filter_envs(all_envs)
        list_ms = int((time.time() - t0) * 1000)

        yield "event: init\ndata: %s\n\n" % json.dumps({
            "total": len(filtered),
            "list_ms": list_ms,
            "threads": threads,
        })

        if threads <= 1:
            for i, env_info in enumerate(filtered):
                result, usage_ms = _cec_fetch_env_with_usages(client, env_info)
                result["index"] = i
                result["usage_ms"] = usage_ms
                yield "event: env\ndata: %s\n\n" % json.dumps(result)
        else:
            counter = [0]
            with ThreadPoolExecutor(max_workers=threads) as pool:
                futures = {
                    pool.submit(_cec_fetch_env_with_usages, client, env_info): env_info
                    for env_info in filtered
                }
                for future in as_completed(futures):
                    result, usage_ms = future.result()
                    result["index"] = counter[0]
                    result["usage_ms"] = usage_ms
                    counter[0] += 1
                    yield "event: env\ndata: %s\n\n" % json.dumps(result)

        total_ms = int((time.time() - t0) * 1000)
        yield "event: done\ndata: %s\n\n" % json.dumps({"total_ms": total_ms})

    return Response(stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@bp.route('/api/tools/code-env-cleaner/<lang>/<name>', methods=['DELETE'])
@advanced
def api_code_env_cleaner_delete(lang, name):
    """Backup to managed folder then delete a code env after verifying the confirmation header."""
    import tempfile

    confirm = request.headers.get("X-Confirm-Name", "")
    if confirm != name:
        return jsonify({"error": "Confirmation header does not match env name"}), 400

    folder_id = request.args.get("folderId", "").strip()
    if not folder_id:
        return jsonify({"error": "folderId query parameter is required"}), 400

    client = g.client
    project = _active_support_project(client)

    # Validate managed folder exists
    try:
        dest_folder = project.get_managed_folder(folder_id)
        dest_folder.get_definition()  # verify it exists
    except Exception as e:
        _LOGGER.error("[code-env-cleaner] invalid folder %s: %s", folder_id, e)
        return jsonify({"error": "Invalid managed folder: %s" % str(e)}), 400

    # Fetch the code env definition
    try:
        env_def = client._perform_json("GET", "/admin/code-envs/%s/%s/" % (lang, name))
    except Exception as e:
        _LOGGER.error("[code-env-cleaner] fetch failed for %s/%s: %s", lang, name, e)
        return jsonify({"error": "Failed to fetch env definition: %s" % str(e)}), 500

    # Backup first — build ZIP to temp file, upload to managed folder
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', name)
    zip_filename = "%s.zip" % safe_name
    try:
        env_lang = lang.lower()
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=True) as tmp:
            with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
                # Directory entries (match DSS on-disk export exactly)
                for d in ["%s/", "%s/spec/", "%s/actual/"]:
                    zf.writestr(zipfile.ZipInfo(d % env_lang), "")
                # desc.json — strip owner (not present in on-disk version)
                desc = dict(env_def.get("desc") or env_def)
                desc.pop("owner", None)
                zf.writestr("%s/desc.json" % env_lang, json.dumps(desc, indent=2))
                # spec/requirements.txt
                zf.writestr("%s/spec/requirements.txt" % env_lang, env_def.get("specPackageList", ""))
                # spec/resources_init.py (field is resourcesInitScript, NOT specResourcesInit)
                zf.writestr("%s/spec/resources_init.py" % env_lang, env_def.get("resourcesInitScript", ""))
                # spec/environment.spec
                zf.writestr("%s/spec/environment.spec" % env_lang, env_def.get("specCondaEnvironment", ""))
                # actual/requirements.txt
                zf.writestr("%s/actual/requirements.txt" % env_lang, env_def.get("actualPackageList", ""))
            # Upload to managed folder
            with open(tmp.name, "rb") as f:
                dest_folder.put_file(zip_filename, f)
    except Exception as e:
        _LOGGER.error("[code-env-cleaner] backup/upload failed for %s/%s: %s", lang, name, e)
        return jsonify({"error": "Backup upload failed — deletion aborted: %s" % str(e)}), 500

    # Delete code env
    try:
        client._perform_empty("DELETE", "/admin/code-envs/%s/%s/" % (lang, name))
    except Exception as e:
        _LOGGER.error("[code-env-cleaner] delete failed for %s/%s: %s", lang, name, e)
        return jsonify({"error": "Delete failed (backup saved to managed folder): %s" % str(e)}), 500

    # Invalidate caches so subsequent fetches reflect the deletion
    _cache_pop('code_envs')
    _cache_pop('tools_outreach_data')
    _cache_pop('project_code_env_usage_full')
    _clear_shared_project_code_env_usage()

    _LOGGER.info("[code-env-cleaner] backed up %s to managed folder %s and deleted %s/%s", zip_filename, folder_id, lang, name)
    return jsonify({"backed_up_to": "managed folder", "zip_name": zip_filename, "deleted": name}), 200
