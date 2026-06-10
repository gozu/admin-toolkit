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


# Worker-pool sizes swept by the benchmark. Synthetic probes (metadata or even
# recipe-payload reads) are served at 5-30ms from localhost and saturate the
# DSS dispatcher at concurrency 4 with a flat curve — useless for sizing. The
# only signal that matches reality is the real scan workload itself, so the
# sweep drives footprint._fetch_project_footprint over disjoint project
# samples at each pool size.
_BENCH_WORKER_LEVELS = [8, 16, 32]
_BENCH_TIME_BUDGET_S = 120.0
_BENCH_WARMUP_PROJECTS = 8
_BENCH_MAX_PROJECTS_PER_LEVEL = 150


@bp.route('/api/settings/benchmark', methods=['POST'])
def api_settings_benchmark():
    """Measure real scan throughput at several worker-pool sizes and recommend
    worker settings.

    Runs the footprint scan's own per-project fetcher (the heaviest, most
    representative call mix) over a disjoint random sample of projects at each
    of `_BENCH_WORKER_LEVELS`. The chosen level is the smallest within 92% of
    peak projects/s; recommended per-endpoint workers = chosen / 2, because
    phase-3 staging runs two heavy endpoints at a time. With `apply: true` the
    recommendation is written to the live settings AND persisted to the saved
    plugin config."""
    from adk_backend.routes.footprint import _fetch_project_footprint

    payload = request.get_json(silent=True) or {}
    do_apply = bool(payload.get('apply'))

    keys = _thread_client().list_project_keys()
    if not keys:
        return jsonify({'error': 'no-projects'}), 400
    rng = random.Random(0xADC)
    rng.shuffle(keys)

    def probe(project_key):
        started = time.time()
        try:
            _fetch_project_footprint(project_key)
            return time.time() - started, None
        except Exception as exc:
            return time.time() - started, f'{type(exc).__name__}: {str(exc)[:120]}'

    # Warm-up batch (connection setup, server-side caches for shared state) on
    # projects excluded from the measured samples.
    warm_keys = keys[:_BENCH_WARMUP_PROJECTS]
    sample_keys = keys[_BENCH_WARMUP_PROJECTS:]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(probe, warm_keys))

    # Disjoint, equal-size samples per level: comparable project mixes without
    # warm-cache bias between levels. Levels a small instance can't feed
    # meaningfully (fewer than 3 projects per worker) are dropped.
    worker_levels = [w for w in _BENCH_WORKER_LEVELS
                     if len(sample_keys) // len(_BENCH_WORKER_LEVELS) >= w * 3]
    worker_levels = worker_levels or [_BENCH_WORKER_LEVELS[0]]
    per_level = min(len(sample_keys) // len(worker_levels), _BENCH_MAX_PROJECTS_PER_LEVEL)
    per_level = max(per_level, 1)

    bench_started = time.time()
    levels = []
    for index, workers in enumerate(worker_levels):
        if time.time() - bench_started > _BENCH_TIME_BUDGET_S:
            break
        batch = sample_keys[index * per_level:(index + 1) * per_level]
        batch_started = time.time()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(probe, batch))
        elapsed = max(time.time() - batch_started, 1e-6)
        errors = [err for _lat, err in outcomes if err]
        latencies = sorted(lat for lat, err in outcomes if not err)
        levels.append({
            'concurrency': workers,
            'calls': len(batch),
            'errors': len(errors),
            'errorSample': errors[0] if errors else None,
            'seconds': round(elapsed, 2),
            'callsPerSec': round((len(batch) - len(errors)) / elapsed, 1),
            'medianMs': round(latencies[len(latencies) // 2] * 1000) if latencies else None,
        })

    usable = [lv for lv in levels if lv['calls'] - lv['errors'] > 0]
    if not usable:
        return jsonify({'error': 'all-probes-failed', 'levels': levels}), 502

    peak = max(lv['callsPerSec'] for lv in usable)
    chosen = next(lv['concurrency'] for lv in usable if lv['callsPerSec'] >= 0.92 * peak)
    with _BACKEND_SETTINGS_LOCK:
        workers_max = _BACKEND_SETTINGS['parallel_workers_max']
    recommended_workers = max(4, min(workers_max, chosen // 2))
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
        'kneeConcurrency': chosen,
        'recommended': recommendation,
        'applied': applied,
        'persisted': persisted,
        'persistError': persist_error,
        'projectsProbed': len(warm_keys) + len(worker_levels) * per_level,
        'elapsedSeconds': round(time.time() - bench_started, 1),
    })


@bp.route('/api/settings/threshold-defaults', methods=['GET'])
def api_settings_threshold_defaults():
    try:
        from db_adapter import load_plugin_threshold_defaults
        return jsonify(load_plugin_threshold_defaults())
    except Exception:
        return jsonify({})
