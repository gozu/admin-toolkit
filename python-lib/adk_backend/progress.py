"""Server-side progress event streams for long-running scans."""

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from adk_backend.caching import _cache_host_id

_PROGRESS: Dict[str, Dict[str, Any]] = {}
_PROGRESS_LOCK = threading.Lock()
_PROGRESS_EVENT_LIMIT = 10000
_PROGRESS_RETENTION_SEC = 1800


def _cleanup_progress_locked(now_ts: float) -> None:
    stale: List[str] = []
    for endpoint, state in _PROGRESS.items():
        updated_ts = float(state.get('updatedTs') or state.get('startedTs') or now_ts)
        if (now_ts - updated_ts) > _PROGRESS_RETENTION_SEC:
            stale.append(endpoint)
    for endpoint in stale:
        _PROGRESS.pop(endpoint, None)


def _progress_key(endpoint: str) -> str:
    host_id = _cache_host_id()
    return endpoint if host_id == 'local' else f'host:{host_id}:{endpoint}'


def _start_progress(endpoint: str) -> str:
    now_ts = time.time()
    stored_endpoint = _progress_key(endpoint)
    run_id = f"{endpoint}-{int(now_ts * 1000)}-{threading.get_ident()}"
    with _PROGRESS_LOCK:
        _cleanup_progress_locked(now_ts)
        _PROGRESS[stored_endpoint] = {
            'runId': run_id,
            'status': 'running',
            'startedTs': now_ts,
            'updatedTs': now_ts,
            'events': [],
            'nextIndex': 0,
            'droppedUntil': 0,
            'summary': None,
            'error': None,
            'partialRows': [],
            'partialRowsNext': 0,
        }
    return run_id


def _append_progress_event(endpoint: str, run_id: str, event: Dict[str, Any]) -> None:
    stored_endpoint = _progress_key(endpoint)
    with _PROGRESS_LOCK:
        state = _PROGRESS.get(stored_endpoint)
        if not isinstance(state, dict):
            return
        if str(state.get('runId') or '') != str(run_id or ''):
            return

        next_index = int(state.get('nextIndex') or 0)
        entry = dict(event)
        entry['idx'] = next_index
        events = state.get('events')
        if not isinstance(events, list):
            events = []
            state['events'] = events
        events.append(entry)
        state['nextIndex'] = next_index + 1

        if len(events) > _PROGRESS_EVENT_LIMIT:
            drop_count = len(events) - _PROGRESS_EVENT_LIMIT
            first_kept_idx = int(events[drop_count].get('idx') or (next_index + 1))
            state['droppedUntil'] = first_kept_idx
            del events[:drop_count]

        state['updatedTs'] = time.time()


def _append_progress_partial_row(endpoint: str, run_id: str, row: Dict[str, Any]) -> None:
    stored_endpoint = _progress_key(endpoint)
    with _PROGRESS_LOCK:
        state = _PROGRESS.get(stored_endpoint)
        if not isinstance(state, dict):
            return
        if str(state.get('runId') or '') != str(run_id or ''):
            return
        partial_rows = state.get('partialRows')
        if not isinstance(partial_rows, list):
            partial_rows = []
            state['partialRows'] = partial_rows
        partial_rows.append(row)
        state['partialRowsNext'] = len(partial_rows)
        state['updatedTs'] = time.time()


def _finish_progress(endpoint: str, run_id: str, status: str, summary: Optional[Dict[str, Any]] = None, error: Optional[str] = None) -> None:
    stored_endpoint = _progress_key(endpoint)
    with _PROGRESS_LOCK:
        state = _PROGRESS.get(stored_endpoint)
        if not isinstance(state, dict):
            return
        if str(state.get('runId') or '') != str(run_id or ''):
            return
        state['status'] = status
        state['summary'] = summary if isinstance(summary, dict) else None
        state['error'] = str(error or '') if error else None
        state['updatedTs'] = time.time()


def _set_progress_summary(endpoint: str, run_id: str, summary: Optional[Dict[str, Any]] = None) -> None:
    if not isinstance(summary, dict):
        return
    stored_endpoint = _progress_key(endpoint)
    with _PROGRESS_LOCK:
        state = _PROGRESS.get(stored_endpoint)
        if not isinstance(state, dict):
            return
        if str(state.get('runId') or '') != str(run_id or ''):
            return
        state['summary'] = dict(summary)
        state['updatedTs'] = time.time()


def _read_progress(endpoint: str, since: int = 0, run_id: Optional[str] = None, rows_since: int = 0) -> Dict[str, Any]:
    stored_endpoint = _progress_key(endpoint)
    with _PROGRESS_LOCK:
        now_ts = time.time()
        _cleanup_progress_locked(now_ts)
        state = _PROGRESS.get(stored_endpoint)
        if not isinstance(state, dict):
            return {
                'status': 'idle',
                'events': [],
                'next': max(0, int(since)),
            }

        current_run_id = str(state.get('runId') or '')
        dropped_until = int(state.get('droppedUntil') or 0)
        if run_id and str(run_id) != current_run_id:
            return {
                'runId': current_run_id,
                'status': 'replaced',
                'droppedUntil': dropped_until,
                'events': [],
                'next': int(state.get('nextIndex') or dropped_until),
            }

        cursor = max(int(since), dropped_until)
        events_raw = state.get('events')
        events = [dict(item) for item in events_raw if isinstance(item, dict) and int(item.get('idx', -1)) >= cursor] if isinstance(events_raw, list) else []

        partial_rows_all = state.get('partialRows')
        rows_cursor = max(0, int(rows_since))
        if isinstance(partial_rows_all, list) and rows_cursor < len(partial_rows_all):
            partial_rows = list(partial_rows_all[rows_cursor:])
        else:
            partial_rows = []
        partial_rows_next = int(state.get('partialRowsNext') or 0)

        return {
            'runId': current_run_id,
            'status': str(state.get('status') or 'idle'),
            'error': state.get('error'),
            'droppedUntil': dropped_until,
            'events': events,
            'next': int(state.get('nextIndex') or cursor),
            'summary': state.get('summary') if isinstance(state.get('summary'), dict) else None,
            'partialRows': partial_rows,
            'partialRowsNext': partial_rows_next,
        }



def _notify_progress(
    callback: Optional[Callable[..., None]],
    step: str,
    message: str,
    level: str = 'info',
    project_key: Optional[str] = None,
    elapsed_ms: Optional[float] = None,
) -> None:
    if not callable(callback):
        return
    try:
        callback(
            step=step,
            message=message,
            level=level,
            project_key=project_key,
            elapsed_ms=elapsed_ms,
        )
    except Exception:
        pass

