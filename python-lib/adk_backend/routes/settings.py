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
# Each level must sustain load long enough that timing noise doesn't dominate.
_BENCH_MIN_LEVEL_S = 1.5
_BENCH_MAX_BUNDLES_PER_LEVEL = 300


@bp.route('/api/settings/benchmark', methods=['POST'])
def api_settings_benchmark():
    """Measure the active host's DSS API concurrency ceiling and recommend
    worker-pool sizes.

    Sweeps `_BENCH_LEVELS` pool sizes, each running per-project "scan bundles"
    — the call mix the heavy scans actually issue (recipe list + recipe
    payloads + dataset/scenario lists). Cheap single GETs are useless probes:
    they're served at ~5-20ms from localhost and the curve stays flat. The
    knee is the smallest concurrency reaching ≥90% of peak API-calls/s.
    Recommended per-endpoint workers = knee / 2, because phase-3 staging runs
    two heavy endpoints at a time. With `apply: true` the recommendation is
    written to the live settings AND persisted to the saved plugin config."""
    payload = request.get_json(silent=True) or {}
    do_apply = bool(payload.get('apply'))

    keys = _thread_client().list_project_keys()
    if not keys:
        return jsonify({'error': 'no-projects'}), 400
    rng = random.Random(0xADC)
    rng.shuffle(keys)

    def probe(project_key):
        """One scan-shaped work unit. Returns (elapsed, api_calls, error)."""
        client = _thread_client()
        project = client.get_project(project_key)
        started = time.time()
        calls = 0
        try:
            recipes = project.list_recipes() or []
            calls += 1
            names = [r.get('name') for r in recipes if isinstance(r, dict) and r.get('name')]
            for rname in names[:2]:
                project.get_recipe(rname).get_settings()
                calls += 1
            project.list_datasets()
            calls += 1
            project.list_scenarios()
            calls += 1
            return time.time() - started, calls, None
        except Exception as exc:
            return time.time() - started, max(calls, 1), f'{type(exc).__name__}: {str(exc)[:120]}'

    # Warm-up batch: pays connection setup outside the measurements and yields
    # the per-bundle latency used to size each level's batch.
    with ThreadPoolExecutor(max_workers=4) as pool:
        warm = list(pool.map(probe, keys[:8]))
    warm_lat = sorted(lat for lat, _calls, err in warm if not err)
    median_lat = max(warm_lat[len(warm_lat) // 2] if warm_lat else 0.2, 0.005)

    bench_started = time.time()
    levels = []
    key_cursor = 0
    for concurrency in _BENCH_LEVELS:
        if time.time() - bench_started > _BENCH_TIME_BUDGET_S:
            break
        bundles = min(max(concurrency * 2, int(_BENCH_MIN_LEVEL_S / median_lat)),
                      _BENCH_MAX_BUNDLES_PER_LEVEL)
        batch = [keys[(key_cursor + i) % len(keys)] for i in range(bundles)]
        key_cursor += bundles
        batch_started = time.time()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            outcomes = list(pool.map(probe, batch))
        elapsed = max(time.time() - batch_started, 1e-6)
        errors = [err for _lat, _calls, err in outcomes if err]
        api_calls = sum(calls for _lat, calls, err in outcomes if not err)
        latencies = sorted(lat for lat, _calls, err in outcomes if not err)
        levels.append({
            'concurrency': concurrency,
            'calls': api_calls,
            'errors': len(errors),
            'errorSample': errors[0] if errors else None,
            'seconds': round(elapsed, 2),
            'callsPerSec': round(api_calls / elapsed, 1),
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
