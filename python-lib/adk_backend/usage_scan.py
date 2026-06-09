"""Shared code-env usage scan: per-usage normalization + the single-flight bulk scan.

`_get_shared_project_code_env_usage` is the entry point shared by the
code-envs scan and the project-footprint scan: it single-flights the expensive
bulk usage collection per project-set (keyed via
`_shared_project_code_env_usage_key`), caches the result for
`cache_ttl_usage_full`, and lets concurrent callers wait on the owner's
`threading.Event` instead of re-scanning.
"""

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from adk_backend.caching import (
    _SHARED_USAGE_SCANS,
    _SHARED_USAGE_SCANS_LOCK,
    _shared_project_code_env_usage_key,
)
from adk_backend.clients import _sdk_fetch
from adk_backend.progress import _notify_progress
from adk_backend.settings import _BACKEND_SETTINGS
from adk_backend.utils import _coerce_int, _extract_nested_text

_LOGGER = logging.getLogger(__name__)


def _usage_to_dict(usage: Any) -> Dict[str, Any]:
    if isinstance(usage, dict):
        return usage
    if hasattr(usage, 'to_dict'):
        try:
            raw = usage.to_dict()
            if isinstance(raw, dict):
                return raw
        except Exception:
            pass
    if hasattr(usage, 'get_raw'):
        try:
            raw = usage.get_raw()
            if isinstance(raw, dict):
                return raw
        except Exception:
            pass
    out: Dict[str, Any] = {}
    for attr in (
        'projectKey',
        'project',
        'projectId',
        'projectSummary',
        'usageType',
        'type',
        'objectType',
        'objectId',
        'objectSmartId',
        'envName',
        'envLang',
    ):
        if hasattr(usage, attr):
            out[attr] = getattr(usage, attr)
    return out


def _extract_usage_project_key(usage: Dict[str, Any]) -> Optional[str]:
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
    for key in ('usageType', 'envUsage', 'type', 'objectType'):
        value = usage.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return 'UNKNOWN'


def _normalize_language(lang_raw: Any) -> str:
    if isinstance(lang_raw, str) and lang_raw.strip().lower().startswith('r'):
        return 'r'
    return 'python'


def _extract_code_env_owner(env_listing: Dict[str, Any], settings_raw: Optional[Dict[str, Any]]) -> str:
    if settings_raw:
        owner = settings_raw.get('owner')
        if isinstance(owner, str) and owner.strip():
            return owner.strip()

    owner = env_listing.get('owner')
    if isinstance(owner, str) and owner.strip():
        return owner.strip()
    return 'Unknown'


def _extract_usage_object_type(usage: Dict[str, Any]) -> str:
    value = _extract_nested_text(
        usage,
        'objectType',
        'targetType',
        'projectObjectType',
        'object.type',
    )
    if value:
        return value.upper()
    return _extract_usage_type(usage)


def _extract_usage_object_id(usage: Dict[str, Any]) -> str:
    value = _extract_nested_text(
        usage,
        'objectId',
        'targetId',
        'id',
        'object.id',
        'objectSmartId',
    )
    if value:
        return value
    return ''


def _extract_usage_object_name(usage: Dict[str, Any]) -> str:
    value = _extract_nested_text(
        usage,
        'objectName',
        'targetName',
        'name',
        'displayName',
        'object.name',
        'object.displayName',
    )
    if value:
        return value
    fallback = _extract_usage_object_id(usage)
    if fallback:
        return fallback
    return _extract_usage_object_type(usage)


def _normalize_usage_entry(
    usage: Dict[str, Any],
    project_names: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:
    project_key = _extract_usage_project_key(usage) or ''
    project_meta = project_names.get(project_key) or {}
    project_name = (
        _extract_nested_text(usage, 'projectSummary.name', 'project.name', 'projectName')
        or project_meta.get('name')
        or project_key
    )

    object_type = _extract_usage_object_type(usage)
    object_id = _extract_usage_object_id(usage)
    object_name = _extract_usage_object_name(usage)

    return {
        'projectKey': project_key,
        'projectName': project_name,
        'usageType': _extract_usage_type(usage),
        'objectType': object_type,
        'objectId': object_id,
        'objectName': object_name,
    }


def _usage_signature(usage: Dict[str, Any]) -> str:
    return '|'.join(
        [
            str(usage.get('projectKey') or ''),
            str(usage.get('usageType') or ''),
            str(usage.get('objectType') or ''),
            str(usage.get('objectId') or ''),
            str(usage.get('objectName') or ''),
            str(usage.get('codeEnvKey') or ''),
        ]
    )


def _dedupe_usage_entries(usages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for usage in usages:
        sig = _usage_signature(usage)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(usage)
    return out


def _check_env_usages(
    env_listing: Dict[str, Any],
    project_info: Dict[str, Dict[str, str]],
    size_by_env: Dict[str, int],
    usages_by_env: Dict[Tuple[str, str], List[Dict]],
) -> Optional[Dict[str, Any]]:
    """Look up usages for a single code env from the pre-fetched bulk dict.

    Returns a dict with env metadata and normalized usages, or None if the env
    should be skipped (e.g. plugin-managed or missing name).
    """
    if not isinstance(env_listing, dict):
        return None
    env_name = env_listing.get('envName') or env_listing.get('name') or env_listing.get('id')
    env_lang_raw = env_listing.get('envLang') or env_listing.get('language') or env_listing.get('type') or 'PYTHON'
    if not env_name:
        return None

    normalized_lang = _normalize_language(env_lang_raw)
    env_key = f"{normalized_lang}:{env_name}"
    deployment_mode = str(env_listing.get('deploymentMode') or '').upper()
    if deployment_mode in {'PLUGIN_MANAGED', 'DSS_INTERNAL'}:
        return None

    owner = _extract_code_env_owner(env_listing, {})
    env_key_tuple = (normalized_lang.upper(), env_name)
    usages: List[Any] = usages_by_env.get(env_key_tuple, [])

    normalized_usages: List[Dict[str, Any]] = []
    for raw_usage in usages:
        usage = _usage_to_dict(raw_usage)
        project_key = _extract_usage_project_key(usage)
        if not project_key:
            continue
        normalized = _normalize_usage_entry(usage, project_info)
        normalized_usages.append({
            'projectKey': project_key,
            'projectName': str(normalized.get('projectName') or project_key),
            'usageType': str(normalized.get('usageType') or 'UNKNOWN'),
            'objectType': str(normalized.get('objectType') or normalized.get('usageType') or 'UNKNOWN'),
            'objectId': str(normalized.get('objectId') or ''),
            'objectName': str(normalized.get('objectName') or normalized.get('objectId') or ''),
            'codeEnvKey': env_key,
            'codeEnvName': str(env_name),
            'codeEnvLanguage': normalized_lang,
            'codeEnvOwner': owner,
        })

    return {
        'envKey': env_key,
        'name': str(env_name),
        'language': normalized_lang,
        'owner': owner,
        'sizeBytes': _coerce_int(size_by_env.get(env_key), 0),
        'pythonVersion': str(env_listing.get('pythonVersion') or env_listing.get('pythonInterpreter') or ''),
        'usages': normalized_usages,
    }


def _collect_project_code_env_usage(
    client: Any,
    project_info: Dict[str, Dict[str, str]],
    size_by_env: Dict[str, int],
    include_project_object_scan: bool = True,
    include_code_env_usage_api: bool = True,
    deadline_ts: Optional[float] = None,
    progress_cb: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    """Collect code env usage by querying the Dataiku API for each env's usages."""
    _notify_progress(
        progress_cb,
        'collect_project_code_env_usage_start',
        f"start projects={len(project_info)}",
    )

    envs = [env for env in (_sdk_fetch(
        'list_code_envs',
        _BACKEND_SETTINGS['cache_ttl_code_envs'],
        lambda: client.list_code_envs() or [],
    ) or []) if isinstance(env, dict)]
    total = len(envs)

    bulk_usages_raw = _sdk_fetch(
        'list_code_env_usages',
        _BACKEND_SETTINGS['cache_ttl_code_envs'],
        lambda: client.list_code_env_usages() or [],
    )
    usages_by_env: Dict[Tuple[str, str], List[Dict]] = {}
    for u in bulk_usages_raw:
        k = (str(u.get('envLang', '')).upper(), str(u.get('envName', '')))
        usages_by_env.setdefault(k, []).append(u)

    _notify_progress(
        progress_cb,
        'code_env_usage_scan_start',
        f"checking {total} code envs",
    )

    env_payloads: List[Dict[str, Any]] = []
    checked = [0]

    def _check_and_report(env: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        payload = _check_env_usages(env, project_info, size_by_env, usages_by_env)
        env_name = env.get('envName') or env.get('name') or '?'
        checked[0] += 1
        idx = checked[0]
        if payload is None:
            _notify_progress(progress_cb, 'code_env_usage_check', f"[{idx}/{total}] {env_name} — skipped (plugin/internal)")
        else:
            usage_count = len(payload.get('usages') or [])
            status = f"{usage_count} usage(s)" if usage_count > 0 else "UNUSED"
            _notify_progress(progress_cb, 'code_env_usage_check', f"[{idx}/{total}] {env_name} — {status}")
        return payload

    for env in envs:
        payload = _check_and_report(env)
        if payload:
            env_payloads.append(payload)

    envs_by_project: Dict[str, set] = {k: set() for k in project_info.keys()}
    usage_breakdown_by_project: Dict[str, Dict[str, int]] = {k: {} for k in project_info.keys()}
    usage_details_by_project: Dict[str, List[Dict[str, Any]]] = {k: [] for k in project_info.keys()}
    env_meta_by_key: Dict[str, Dict[str, Any]] = {}

    for payload in env_payloads:
        env_key = str(payload.get('envKey') or '')
        env_name = str(payload.get('name') or '')
        if not env_key or not env_name:
            continue

        env_meta_by_key[env_key] = {
            'key': env_key,
            'name': env_name,
            'language': str(payload.get('language') or 'python'),
            'owner': str(payload.get('owner') or 'Unknown'),
            'sizeBytes': _coerce_int(payload.get('sizeBytes'), 0),
            'pythonVersion': str(payload.get('pythonVersion') or ''),
            'deploymentMode': '',
            'usageSummary': {},
            'usageDetails': [],
            'projectKeys': set(),
        }

        for usage in payload.get('usages') or []:
            project_key = str(usage.get('projectKey') or '')
            if not project_key:
                continue
            usage_type = str(usage.get('usageType') or 'UNKNOWN').upper()

            # Track usage in per-project maps only for known projects
            if project_key in envs_by_project:
                envs_by_project[project_key].add(env_key)
                counts = usage_breakdown_by_project[project_key]
                counts[usage_type] = counts.get(usage_type, 0) + 1
                usage_details_by_project[project_key].append(usage)

            # Always track in env metadata (determines unused status)
            env_meta = env_meta_by_key[env_key]
            env_meta['usageSummary'][usage_type] = env_meta['usageSummary'].get(usage_type, 0) + 1
            env_meta['usageDetails'].append(usage)
            env_meta['projectKeys'].add(project_key)

    for env_key, env_meta in env_meta_by_key.items():
        deduped = _dedupe_usage_entries(env_meta.get('usageDetails') or [])
        env_meta['usageDetails'] = deduped
        env_meta['usageCount'] = len(deduped)
        env_meta['projectKeys'] = sorted(set(env_meta.get('projectKeys') or []))
        env_meta['projectCount'] = len(env_meta['projectKeys'])
        env_meta['usageSummary'] = dict(env_meta.get('usageSummary') or {})

    for project_key, usages in usage_details_by_project.items():
        usage_details_by_project[project_key] = _dedupe_usage_entries(usages)

    unused_count = sum(1 for m in env_meta_by_key.values() if not m.get('usageDetails'))
    in_use_count = len(env_meta_by_key) - unused_count
    _notify_progress(
        progress_cb,
        'collect_project_code_env_usage_done',
        f"done — {len(env_meta_by_key)} checked, {in_use_count} in use, {unused_count} unused",
    )
    return {
        'envsByProject': envs_by_project,
        'usageBreakdownByProject': usage_breakdown_by_project,
        'usageDetailsByProject': usage_details_by_project,
        'envMetaByKey': env_meta_by_key,
        'codeStudiosByProject': _list_code_studios_by_project(client, project_info, progress_cb),
    }


def _list_code_studios_by_project(
    client: Any,
    project_info: Dict[str, Dict[str, str]],
    progress_cb: Optional[Callable[..., None]] = None,
) -> Dict[str, List[Dict[str, str]]]:
    """Return {project_key: [{id, name}, ...]} for all known projects."""
    studios_by_project: Dict[str, List[Dict[str, str]]] = {}
    for pk in project_info:
        entries: List[Dict[str, str]] = []
        try:
            items = client.get_project(pk).list_code_studios(as_type='listitems')
            for item in items:
                raw = getattr(item, '_data', {}) or {}
                cs_id = str(raw.get('id') or '')
                cs_name = str(raw.get('name') or cs_id)
                if cs_id:
                    entries.append({'id': cs_id, 'name': cs_name})
        except Exception as exc:
            _LOGGER.debug("[footprint-map] code studio list failed project=%s: %s", pk, exc)
            _notify_progress(progress_cb, 'project_code_studios_error', f"code studio list failed: {exc}", 'warn', pk)
            entries = []
        studios_by_project[pk] = entries
    return studios_by_project


def _get_shared_project_code_env_usage(
    client: Any,
    project_info: Dict[str, Dict[str, str]],
    size_by_env: Dict[str, int],
    include_project_object_scan: bool = True,
    include_code_env_usage_api: bool = True,
    deadline_ts: Optional[float] = None,
    progress_cb: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    if not project_info:
        return {}

    cache_key = _shared_project_code_env_usage_key(project_info)
    ttl_sec = max(1, int(_BACKEND_SETTINGS.get('cache_ttl_usage_full') or 5))
    now_ts = time.time()
    wait_event: Optional[threading.Event] = None
    is_owner = False

    with _SHARED_USAGE_SCANS_LOCK:
        stale_keys = [
            key
            for key, entry in _SHARED_USAGE_SCANS.items()
            if isinstance(entry, dict)
            and str(entry.get('status') or '') != 'running'
            and (now_ts - float(entry.get('ts') or now_ts)) > ttl_sec
        ]
        for key in stale_keys:
            _SHARED_USAGE_SCANS.pop(key, None)

        entry = _SHARED_USAGE_SCANS.get(cache_key)
        if isinstance(entry, dict):
            entry_status = str(entry.get('status') or '')
            entry_ts = float(entry.get('ts') or 0.0)
            entry_result = entry.get('result')
            if entry_status == 'done' and (now_ts - entry_ts) <= ttl_sec and isinstance(entry_result, dict):
                _notify_progress(
                    progress_cb,
                    'collect_project_code_env_usage_cache_hit',
                    f"reusing cached code env usage scan envs={len((entry_result.get('envMetaByKey') or {}))}",
                )
                return entry_result
            if entry_status == 'running':
                ready = entry.get('ready')
                if isinstance(ready, threading.Event):
                    wait_event = ready

        if wait_event is None:
            wait_event = threading.Event()
            _SHARED_USAGE_SCANS[cache_key] = {
                'status': 'running',
                'ts': now_ts,
                'ready': wait_event,
                'result': None,
                'error': None,
            }
            is_owner = True

    if is_owner:
        try:
            result = _collect_project_code_env_usage(
                client,
                project_info,
                size_by_env,
                include_project_object_scan=include_project_object_scan,
                include_code_env_usage_api=include_code_env_usage_api,
                deadline_ts=deadline_ts,
                progress_cb=progress_cb,
            )
        except Exception as exc:
            with _SHARED_USAGE_SCANS_LOCK:
                entry = _SHARED_USAGE_SCANS.get(cache_key)
                if isinstance(entry, dict) and entry.get('ready') is wait_event:
                    entry['status'] = 'error'
                    entry['ts'] = time.time()
                    entry['error'] = str(exc)
                    wait_event.set()
            raise

        with _SHARED_USAGE_SCANS_LOCK:
            entry = _SHARED_USAGE_SCANS.get(cache_key)
            if isinstance(entry, dict) and entry.get('ready') is wait_event:
                entry['status'] = 'done'
                entry['ts'] = time.time()
                entry['result'] = result
                entry['error'] = None
                wait_event.set()
        return result

    _notify_progress(
        progress_cb,
        'collect_project_code_env_usage_wait',
        'waiting for shared code env usage scan',
    )
    timeout_seconds = None if deadline_ts is None else max(0.0, deadline_ts - time.time())
    finished = wait_event.wait(timeout_seconds)

    with _SHARED_USAGE_SCANS_LOCK:
        entry = _SHARED_USAGE_SCANS.get(cache_key)
        entry_status = str(entry.get('status') or '') if isinstance(entry, dict) else ''
        entry_result = entry.get('result') if isinstance(entry, dict) else None
        entry_error = str(entry.get('error') or '') if isinstance(entry, dict) else ''

    if finished and entry_status == 'done' and isinstance(entry_result, dict):
        _notify_progress(
            progress_cb,
            'collect_project_code_env_usage_wait_done',
            f"shared code env usage scan ready envs={len((entry_result.get('envMetaByKey') or {}))}",
        )
        return entry_result

    if entry_status == 'error':
        _notify_progress(
            progress_cb,
            'collect_project_code_env_usage_wait_retry',
            f"shared code env usage scan failed ({entry_error or 'unknown error'}); retrying locally",
            'warn',
        )
    else:
        _notify_progress(
            progress_cb,
            'collect_project_code_env_usage_wait_timeout',
            'shared code env usage scan wait timed out; retrying locally',
            'warn',
        )

    return _collect_project_code_env_usage(
        client,
        project_info,
        size_by_env,
        include_project_object_scan=include_project_object_scan,
        include_code_env_usage_api=include_code_env_usage_api,
        deadline_ts=deadline_ts,
        progress_cb=progress_cb,
    )
