"""Data-directories footprint: per-host availability latch, payload compute, size math.

The availability state is a per-host negative cache: after
`_FOOTPRINT_FAIL_THRESHOLD` REST failures the footprint API is latched
unavailable for `_FOOTPRINT_COOLDOWN_SECS`, so footprint-hungry pages degrade
fast instead of hammering a dead endpoint. `/api/cache/clear` resets the latch
via `_footprint_reset_negative_cache`.
"""

import logging
import re
import threading
import time
from typing import Any, Dict, Optional

from adk_backend.caching import _cache_host_id
from adk_backend.clients import _client_perform_json, _sdk_fetch
from adk_backend.settings import _BACKEND_SETTINGS
from adk_backend.utils import _bench_call, _coerce_int

_LOGGER = logging.getLogger(__name__)


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


def _new_footprint_state() -> Dict[str, Any]:
    return {
        'unavailable': False,
        'reason': None,
        'failures': 0,
        'latched_at': 0.0,
    }


_FOOTPRINT_STATES: Dict[str, Dict[str, Any]] = {'local': _new_footprint_state()}
_FOOTPRINT_LOCK = threading.Lock()
_FOOTPRINT_FAIL_THRESHOLD = 2
_FOOTPRINT_COOLDOWN_SECS = 600


def _footprint_state_locked() -> Dict[str, Any]:
    host_id = _cache_host_id()
    return _FOOTPRINT_STATES.setdefault(host_id, _new_footprint_state())


def _footprint_available() -> bool:
    with _FOOTPRINT_LOCK:
        state = _footprint_state_locked()
        if not state.get('unavailable'):
            return True
        latched_at = float(state.get('latched_at') or 0.0)
        if latched_at and (time.time() - latched_at) > _FOOTPRINT_COOLDOWN_SECS:
            _LOGGER.info("[footprint] attempting after cooldown — %.0fs since latch",
                            time.time() - latched_at)
            state['unavailable'] = False
            state['reason'] = None
            state['failures'] = 0
            state['latched_at'] = 0.0
            return True
        return False


def _footprint_unavailable_reason() -> Optional[str]:
    with _FOOTPRINT_LOCK:
        state = _footprint_state_locked()
        return state.get('reason') if state.get('unavailable') else None


def _footprint_reset_negative_cache() -> None:
    with _FOOTPRINT_LOCK:
        state = _footprint_state_locked()
        state['unavailable'] = False
        state['reason'] = None
        state['failures'] = 0
        state['latched_at'] = 0.0


def _footprint_record_failure(reason: str) -> None:
    with _FOOTPRINT_LOCK:
        state = _footprint_state_locked()
        if state.get('unavailable'):
            return
        state['failures'] = int(state.get('failures') or 0) + 1
        if state['failures'] >= _FOOTPRINT_FAIL_THRESHOLD:
            state['unavailable'] = True
            state['reason'] = reason
            state['latched_at'] = time.time()
            _LOGGER.warning("[footprint] latched unavailable after %d failures: %s",
                               state['failures'], reason)


def _footprint_record_success() -> None:
    with _FOOTPRINT_LOCK:
        _footprint_state_locked()['failures'] = 0


def _compute_footprint_payload(
    client: Any,
    scope: str,
    project_key: Optional[str],
) -> Optional[Any]:
    if not _footprint_available():
        return None

    op_name = 'compute_all_dss_footprint'
    if scope == 'global':
        op_name = 'compute_global_footprint'
    elif scope == 'project' and project_key:
        op_name = 'compute_project_footprint'

    if hasattr(client, 'get_data_directories_footprint'):
        try:
            footprint_api = _bench_call('get_data_directories_footprint', client.get_data_directories_footprint)
            if scope == 'global':
                return _sdk_fetch(
                    'global_footprint',
                    _BACKEND_SETTINGS['cache_ttl_projects'],
                    lambda: _bench_call(op_name, lambda: _unwrap_footprint_payload(footprint_api.compute_global_only_footprint(wait=True))),
                )
            if scope == 'project' and project_key:
                return _sdk_fetch(
                    f'project_footprint:{project_key}',
                    _BACKEND_SETTINGS['cache_ttl_projects'],
                    lambda: _bench_call(op_name, lambda: _unwrap_footprint_payload(footprint_api.compute_project_footprint(project_key, wait=True))),
                )
            return _bench_call(op_name, lambda: _unwrap_footprint_payload(footprint_api.compute_all_dss_footprint(wait=True)))
        except Exception as exc:
            # On some DSS versions / under load the SDK path fails; fall back to REST.
            _LOGGER.debug(
                "[footprint] sdk %s scope=%s project=%s failed, falling back to REST: %s: %s",
                op_name, scope, project_key, type(exc).__name__, str(exc)[:200],
            )

    rest_path = '/directories-footprint/all-dss?summaryOnly=false'
    if scope == 'global':
        rest_path = '/directories-footprint/global?summaryOnly=false'
    elif scope == 'project' and project_key:
        rest_path = f'/directories-footprint/projects/{project_key}?summaryOnly=false'

    try:
        response = _bench_call(op_name, _client_perform_json, client, 'GET', rest_path)
    except Exception as exc:
        _footprint_record_failure(f"REST {rest_path}: {type(exc).__name__}: {str(exc)[:200]}")
        _LOGGER.debug("[footprint] REST %s failed: %s", rest_path, exc)
        return None

    if not isinstance(response, dict):
        _footprint_record_failure(f"REST {rest_path} returned non-dict: {type(response).__name__}")
        _LOGGER.debug(
            "[footprint] REST %s scope=%s project=%s returned non-dict: type=%s",
            rest_path, scope, project_key, type(response).__name__,
        )
        return None

    _footprint_record_success()
    unwrapped = _unwrap_footprint_payload(response)
    if scope == 'project':
        return _wrap_project_footprint_payload(unwrapped, project_key)
    return unwrapped


_FOOTPRINT_SCALAR_KEYS = frozenset({
    'size', 'nbFiles', 'nbFolders', 'nbErrors',
    'projectKey', 'name', 'language', 'type',
    'result',
})


def _footprint_details_map(footprint: Any) -> Dict[str, Any]:
    # Footprint payloads may be missing entirely (per-project fetch failed, stale cache, etc.)
    # so every accessor in this family must tolerate a non-dict input and return an empty map.
    if not isinstance(footprint, dict):
        return {}
    details = footprint.get('details')
    if isinstance(details, dict):
        return details
    # Sections with 'items' array -> expand items as named children
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
    # Otherwise children are dict-valued keys (excluding metadata scalars)
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
    size = _coerce_int(footprint.get('size'), 0)
    if size > 0:
        return size
    details = _footprint_details_map(footprint)
    if not details:
        return 0
    return sum(_footprint_size(child) for child in details.values())


def _normalize_bucket_name(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(name or '').lower())


def _collect_bucket_size_by_name(footprint: Any, matcher) -> int:
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


def _collect_bucket_file_count_by_name(footprint: Any, matcher) -> int:
    details = _footprint_details_map(footprint)
    if not details:
        return 0
    total = 0
    for name, child in details.items():
        normalized = _normalize_bucket_name(name)
        if matcher(normalized):
            total += _coerce_int(child.get('nbFiles'), 0)
            continue
        total += _collect_bucket_file_count_by_name(child, matcher)
    return total


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


def _footprint_bucket_breakdown(footprint, top_n=5):
    """Top-level footprint folders sorted by size desc, each {name,label,bytes,location}.
    Returns {'buckets': [top_n...], 'otherCount': n, 'otherBytes': sum_of_remainder}."""
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
        items.append({'name': key,
                      'label': _FOOTPRINT_BUCKET_LABELS.get(key, key),
                      'bytes': bytes_, 'location': loc})
    items.sort(key=lambda d: d['bytes'], reverse=True)
    top = items[:top_n]
    rest = items[top_n:]
    return {'buckets': top,
            'otherCount': len(rest),
            'otherBytes': sum(d['bytes'] for d in rest)}
