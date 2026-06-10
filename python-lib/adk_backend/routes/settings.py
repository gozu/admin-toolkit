"""Backend-settings routes: current/default perf settings + plugin threshold
defaults. `_BACKEND_SETTINGS` (adk_backend.settings) is the ONE shared dict —
read under its lock here; any mutation is in place, never a rebind."""
import random
import time

from flask import Blueprint, jsonify, request

from adk_backend.clients import ThreadPoolExecutor, _local_thread_client, _thread_client
from adk_backend.settings import (
    _BACKEND_SETTINGS,
    _BACKEND_SETTINGS_DEFAULTS,
    _BACKEND_SETTINGS_LOCK,
)

bp = Blueprint('settings', __name__)

_PLUGIN_ID = 'admin-toolkit'


def _persist_plugin_settings(updates: dict) -> dict:
    """Write backend-setting values through to the saved plugin config.

    Saved plugin params re-merge over code defaults at every backend start
    (db_adapter.load_plugin_performance_settings), so a runtime update that
    should survive restarts must also land in the plugin config. Plugin
    settings live on the LOCAL instance — always the local client, never
    g.client. Returns the perf_* params actually written."""
    from db_adapter import _PERF_MAP
    reverse = {setting_key: param_key for param_key, (setting_key, _cast) in _PERF_MAP.items()}
    writable = {reverse[k]: v for k, v in updates.items() if k in reverse}
    if not writable:
        return {}
    settings = _local_thread_client().get_plugin(_PLUGIN_ID).get_settings()
    settings.get_raw().setdefault('config', {}).update(writable)
    settings.save()
    return writable


@bp.route('/api/settings', methods=['GET'])
def api_settings_get():
    with _BACKEND_SETTINGS_LOCK:
        return jsonify({'current': dict(_BACKEND_SETTINGS), 'defaults': dict(_BACKEND_SETTINGS_DEFAULTS)})


@bp.route('/api/settings/update', methods=['POST'])
def api_settings_update():
    """Update known backend settings at runtime (until the next backend
    restart, when saved plugin config re-merges). All settings are ints;
    unknown keys and non-int values are rejected, not applied."""
    payload = request.get_json(silent=True) or {}
    persist = bool(payload.pop('persist', False))
    updated = {}
    rejected = []
    with _BACKEND_SETTINGS_LOCK:
        for key, value in payload.items():
            if key not in _BACKEND_SETTINGS:
                rejected.append(key)
                continue
            try:
                _BACKEND_SETTINGS[key] = int(value)
            except (TypeError, ValueError):
                rejected.append(key)
                continue
            updated[key] = _BACKEND_SETTINGS[key]
        current = dict(_BACKEND_SETTINGS)
    persisted = {}
    persist_error = None
    if persist and updated:
        try:
            persisted = _persist_plugin_settings(updated)
        except Exception as exc:
            persist_error = f'{type(exc).__name__}: {str(exc)[:200]}'
    return jsonify({'updated': updated, 'rejected': rejected, 'current': current,
                    'persisted': persisted, 'persistError': persist_error})


# Sweep levels for the concurrency benchmark. 64 ≈ 2× the observed saturation
# point on a mid-size instance; going higher only inflates latency and run time.
_BENCH_LEVELS = [4, 8, 16, 24, 32, 48, 64]
_BENCH_TIME_BUDGET_S = 60.0


@bp.route('/api/settings/benchmark', methods=['POST'])
def api_settings_benchmark():
    """Measure the active host's DSS API concurrency ceiling and recommend
    worker-pool sizes.

    Sweeps `_BENCH_LEVELS` pool sizes, each firing cheap per-project metadata
    GETs (the same request path the heavy scans saturate), and finds the knee:
    the smallest concurrency reaching ≥90% of peak throughput. Recommended
    per-endpoint workers = knee / 2, because phase-3 staging runs two heavy
    endpoints at a time. With `apply: true` the recommendation is written to
    the live settings AND persisted to the saved plugin config."""
    payload = request.get_json(silent=True) or {}
    do_apply = bool(payload.get('apply'))

    keys = _thread_client().list_project_keys()
    if not keys:
        return jsonify({'error': 'no-projects'}), 400
    rng = random.Random(0xADC)
    rng.shuffle(keys)

    def probe(project_key):
        client = _thread_client()
        started = time.time()
        try:
            client.get_project(project_key).get_metadata()
            return time.time() - started, None
        except Exception as exc:
            return time.time() - started, f'{type(exc).__name__}: {str(exc)[:120]}'

    # Warm-up batch: pay connection/SSL setup once, outside the measurements.
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(probe, keys[:8]))

    bench_started = time.time()
    levels = []
    key_cursor = 0
    for concurrency in _BENCH_LEVELS:
        if time.time() - bench_started > _BENCH_TIME_BUDGET_S:
            break
        calls = min(max(concurrency * 3, 12), 128)
        batch = [keys[(key_cursor + i) % len(keys)] for i in range(calls)]
        key_cursor += calls
        batch_started = time.time()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            outcomes = list(pool.map(probe, batch))
        elapsed = max(time.time() - batch_started, 1e-6)
        errors = [err for _, err in outcomes if err]
        latencies = sorted(lat for lat, err in outcomes if not err)
        levels.append({
            'concurrency': concurrency,
            'calls': calls,
            'errors': len(errors),
            'errorSample': errors[0] if errors else None,
            'seconds': round(elapsed, 2),
            'callsPerSec': round((calls - len(errors)) / elapsed, 1),
            'medianMs': round(latencies[len(latencies) // 2] * 1000) if latencies else None,
        })

    usable = [lv for lv in levels if lv['calls'] - lv['errors'] > 0]
    if not usable:
        return jsonify({'error': 'all-probes-failed', 'levels': levels}), 502

    peak = max(lv['callsPerSec'] for lv in usable)
    knee = next(lv['concurrency'] for lv in usable if lv['callsPerSec'] >= 0.9 * peak)
    with _BACKEND_SETTINGS_LOCK:
        workers_max = _BACKEND_SETTINGS['parallel_workers_max']
    recommended_workers = max(4, min(workers_max, knee // 2))
    recommendation = {
        'parallel_workers_default': recommended_workers,
        'code_env_detail_workers': recommended_workers,
    }

    applied = {}
    persisted = {}
    persist_error = None
    if do_apply:
        with _BACKEND_SETTINGS_LOCK:
            for key, value in recommendation.items():
                _BACKEND_SETTINGS[key] = value
            applied = dict(recommendation)
        try:
            persisted = _persist_plugin_settings(recommendation)
        except Exception as exc:
            persist_error = f'{type(exc).__name__}: {str(exc)[:200]}'

    return jsonify({
        'levels': levels,
        'peakCallsPerSec': peak,
        'kneeConcurrency': knee,
        'recommended': recommendation,
        'applied': applied,
        'persisted': persisted,
        'persistError': persist_error,
        'projectsProbed': min(len(keys), key_cursor),
        'elapsedSeconds': round(time.time() - bench_started, 1),
    })


@bp.route('/api/settings/threshold-defaults', methods=['GET'])
def api_settings_threshold_defaults():
    try:
        from db_adapter import load_plugin_threshold_defaults
        return jsonify(load_plugin_threshold_defaults())
    except Exception:
        return jsonify({})
