"""Debug routes: perf introspection + gunicorn worker discovery.

Deliberately kept external-facing (no @advanced gate) — used for live
troubleshooting of deployed instances."""
from typing import Any, Dict

from flask import Blueprint, jsonify

from adk_backend.caching import _cache_peek
from adk_backend.clients import _get_sdk_cache
from adk_backend.progress import _PROGRESS, _PROGRESS_LOCK
from adk_backend.settings import _BACKEND_SETTINGS, _BACKEND_SETTINGS_LOCK

bp = Blueprint('debug', __name__)


@bp.route('/api/debug/perf')
def api_debug_perf():
    """Return performance debug data without triggering any scans."""
    try:
        cache = _get_sdk_cache()
        cache_keys = cache.get_cache_keys() if hasattr(cache, 'get_cache_keys') else []
        sdk_stats = cache.get_stats() if hasattr(cache, 'get_stats') else {}
    except Exception:
        cache_keys = []
        sdk_stats = {}
    with _BACKEND_SETTINGS_LOCK:
        settings = dict(_BACKEND_SETTINGS)
    ce_benchmark = None
    pf_benchmark = None
    ce_val = _cache_peek('code_envs')
    if isinstance(ce_val, dict):
        ce_benchmark = ce_val.get('summary', {}).get('benchmark')
    # Extract benchmarks from progress (PF doesn't use _cache_get; CE as fallback)
    progress_summaries: Dict[str, Any] = {}
    with _PROGRESS_LOCK:
        for k, v in _PROGRESS.items():
            summary = v.get('summary')
            if isinstance(summary, dict):
                # Strip events array to keep response small
                progress_summaries[k] = {
                    key: val for key, val in summary.items() if key != 'events'
                }
                if k == 'project_footprint' and pf_benchmark is None:
                    pf_benchmark = summary
                if k == 'code_envs' and ce_benchmark is None:
                    ce_benchmark = summary
    # Strip events from benchmarks to keep response small
    if isinstance(ce_benchmark, dict):
        ce_benchmark = {k: v for k, v in ce_benchmark.items() if k != 'events'}
    if isinstance(pf_benchmark, dict):
        pf_benchmark = {k: v for k, v in pf_benchmark.items() if k != 'events'}
    return jsonify({
        'cache_keys': cache_keys,
        'sdk_cache_stats': sdk_stats,
        'backend_settings': settings,
        'last_code_envs_benchmark': ce_benchmark,
        'last_project_footprint_benchmark': pf_benchmark,
        'progress_summaries': progress_summaries,
    })


@bp.route('/api/debug/workers')
def api_debug_workers():
    """Introspect the webapp's gunicorn process tree to discover worker count.

    Returns this worker's pid/ppid, the master's cmdline, and a list of sibling
    workers (processes whose PPid matches our own). Read-only; touches /proc only.
    """
    import os as _os

    def _read_proc(path):
        try:
            with open(path, 'r') as fh:
                return fh.read()
        except OSError:
            return None

    def _read_status(pid):
        text = _read_proc('/proc/{}/status'.format(pid))
        if not text:
            return None
        out = {}
        for line in text.splitlines():
            if ':' in line:
                k, _, v = line.partition(':')
                out[k.strip()] = v.strip()
        return out

    def _read_cmdline(pid):
        text = _read_proc('/proc/{}/cmdline'.format(pid))
        if not text:
            return None
        return text.replace('\x00', ' ').strip()

    self_pid = _os.getpid()
    parent_pid = _os.getppid()
    self_cmd = _read_cmdline(self_pid)
    parent_cmd = _read_cmdline(parent_pid)
    parent_status = _read_status(parent_pid)

    siblings = []
    try:
        for entry in _os.listdir('/proc'):
            if not entry.isdigit():
                continue
            pid = int(entry)
            status = _read_status(pid)
            if not status:
                continue
            ppid_raw = status.get('PPid', '0').split()[0]
            try:
                ppid = int(ppid_raw)
            except ValueError:
                continue
            if ppid == parent_pid:
                siblings.append({
                    'pid': pid,
                    'name': status.get('Name'),
                    'threads': int((status.get('Threads') or '0').split()[0]),
                    'cmdline': _read_cmdline(pid),
                })
    except OSError as exc:
        return jsonify({'error': 'listdir /proc failed: {}'.format(exc)}), 500

    siblings.sort(key=lambda r: r['pid'])

    cpu_count = _os.cpu_count()
    return jsonify({
        'self_pid': self_pid,
        'parent_pid': parent_pid,
        'self_cmdline': self_cmd,
        'parent_cmdline': parent_cmd,
        'parent_name': (parent_status or {}).get('Name'),
        'siblings': siblings,
        'worker_count': len(siblings),
        'cpu_count': cpu_count,
    })
