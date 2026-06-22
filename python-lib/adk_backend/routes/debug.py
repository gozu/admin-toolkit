"""Debug routes: perf introspection + gunicorn worker discovery.

Deliberately kept external-facing (no @advanced gate) — used for live
troubleshooting of deployed instances."""
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict

from flask import Blueprint, Response, g, jsonify

from adk_backend.caching import _cache_peek
from adk_backend.clients import _get_sdk_cache
from adk_backend.logparse import _coerce_log_text, _parse_log_errors
from adk_backend.prewarm import _PREWARM_STATUS
from adk_backend.progress import _PROGRESS, _PROGRESS_LOCK
from adk_backend.settings import _BACKEND_SETTINGS, _BACKEND_SETTINGS_LOCK
from adk_backend.sysinfo import _dip_home, _safe_read_json, _safe_read_text

bp = Blueprint('debug', __name__)

# Bigger than raw-tail's 100K: the bundle is for offline diagnosis, where the
# process-resource-monitor DEBUG flood would otherwise push webapp lines out.
_BUNDLE_LOG_MAX_CHARS = 1_000_000


def _perf_payload() -> Dict[str, Any]:
    """Performance debug data without triggering any scans."""
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
    # Which Python actually runs this backend, and is `cryptography` importable
    # here? Diagnoses code-env mismatch (useContextualCodeEnv can bind the webapp
    # to a different env than the one rebuilt) behind the host-key encrypt error.
    try:
        import cryptography
        crypto_info = {'ok': True, 'version': getattr(cryptography, '__version__', '?'),
                       'file': getattr(cryptography, '__file__', '?')}
    except Exception as ex:
        crypto_info = {'ok': False, 'error': '%s: %s' % (type(ex).__name__, ex)}
    python_env = {
        'executable': sys.executable,
        'prefix': sys.prefix,
        'version': sys.version.split()[0],
        'cryptography': crypto_info,
        'env_hints': {k: v for k, v in os.environ.items()
                      if 'CODE_ENV' in k.upper() or 'CODEENV' in k.upper() or 'DKU_VENV' in k.upper()},
    }
    return {
        'python_env': python_env,
        'cache_keys': cache_keys,
        'sdk_cache_stats': sdk_stats,
        'backend_settings': settings,
        'last_code_envs_benchmark': ce_benchmark,
        'last_project_footprint_benchmark': pf_benchmark,
        'progress_summaries': progress_summaries,
        'prewarm': dict(_PREWARM_STATUS),
    }


@bp.route('/api/debug/perf')
def api_debug_perf():
    """Return performance debug data without triggering any scans."""
    return jsonify(_perf_payload())


@bp.route('/api/debug/support-bundle')
def api_debug_support_bundle():
    """Bundle debug + log diagnostics into one downloadable zip.

    Aggregates what /api/debug/perf, /api/logs/raw-tail and /api/logs/errors
    expose so a customer can hand over a single file for offline diagnosis.
    Read-only; triggers no scans."""
    generated_at = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    dip_home = _dip_home()
    version_info = (
        _safe_read_json(os.path.join(dip_home, 'dss-version.json'))
        or _safe_read_json(os.path.join(dip_home, 'config', 'dss-version.json'))
        or {}
    )

    log_text = ''
    log_error = None
    try:
        try:
            log_content = g.client.get_log('backend.log')
        except Exception:
            log_content = _safe_read_text(os.path.join(dip_home, 'run', 'backend.log'))
        log_text = _coerce_log_text(log_content) or ''
        if len(log_text) > _BUNDLE_LOG_MAX_CHARS:
            log_text = log_text[-_BUNDLE_LOG_MAX_CHARS:]
    except Exception as e:
        log_error = str(e)

    try:
        log_errors = _parse_log_errors(log_text)
    except Exception as e:
        log_errors = {'error': str(e)}

    def dumps(obj):
        return json.dumps(obj, indent=2, default=str)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('bundle.json', dumps({
            'generated_at_utc': generated_at,
            'dss_version': (
                version_info.get('product_version')
                or version_info.get('version')
                or version_info.get('dssVersion')
            ),
            'log_tail_chars': len(log_text),
            'log_error': log_error,
        }))
        zf.writestr('debug-perf.json', dumps(_perf_payload()))
        zf.writestr('logs/errors.json', dumps(log_errors))
        zf.writestr('logs/backend-tail.log', log_text)
    filename = 'admin-toolkit-support-bundle-{}.zip'.format(generated_at)
    return Response(
        buf.getvalue(),
        mimetype='application/zip',
        headers={'Content-Disposition': 'attachment; filename="{}"'.format(filename)},
    )


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
