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
from adk_backend.utils import advanced

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
@advanced
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
_BENCH_MAX_PROJECTS_PER_LEVEL = 150


@bp.route('/api/settings/benchmark', methods=['POST'])
@advanced
def api_settings_benchmark():
    """Measure real scan throughput at several worker-pool sizes and recommend
    worker settings.

    Runs the footprint scan's own per-project fetcher (the heaviest, most
    representative call mix) over ONE random project sample: a warm pass
    first (so DSS-side config caches don't bias later levels), then the same
    sample at each of `_BENCH_WORKER_LEVELS`, shuffled per level. Same sample
    ⇒ identical project mix per level — disjoint samples were tried and the
    heavy-tailed project sizes drowned the signal in ±10% noise. The chosen
    level is the smallest within 95% of peak projects/s and is recommended
    as-is (no staging discount: the measured ceilings are low enough that
    halving would under-provision solo scans). With `apply: true` the
    recommendation is written to the live settings AND persisted to the saved
    plugin config."""
    from adk_backend.clients import _client_perform_json

    payload = request.get_json(silent=True) or {}
    do_apply = bool(payload.get('apply'))

    keys = _thread_client().list_project_keys()
    if not keys:
        return jsonify({'error': 'no-projects'}), 400
    rng = random.Random(0xADC)
    rng.shuffle(keys)

    def probe(project_key):
        """The footprint scan's per-project DSS call, made directly — NOT via
        _fetch_project_footprint, whose _sdk_fetch cache layer turns repeat
        probes into ~0ms local reads and voids the measurement."""
        client = _thread_client()
        started = time.time()
        try:
            if hasattr(client, 'get_data_directories_footprint'):
                client.get_data_directories_footprint().compute_project_footprint(project_key, wait=True)
            else:
                _client_perform_json(client, 'GET',
                                     f'/directories-footprint/projects/{project_key}?summaryOnly=false')
            return time.time() - started, None
        except Exception as exc:
            return time.time() - started, f'{type(exc).__name__}: {str(exc)[:120]}'

    # One shared sample. Levels a small instance can't feed meaningfully
    # (fewer than 3 projects per worker) are dropped.
    sample_keys = keys[:_BENCH_MAX_PROJECTS_PER_LEVEL]
    worker_levels = [w for w in _BENCH_WORKER_LEVELS if len(sample_keys) >= w * 3]
    worker_levels = worker_levels or [_BENCH_WORKER_LEVELS[0]]
    per_level = len(sample_keys)

    # Warm pass: loads DSS-side project/config caches for the whole sample so
    # every measured level runs against equally-warm state.
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(probe, sample_keys))

    # Two sweeps, second in reverse order, best rate per level wins: a single
    # pass is ±10-20% noisy (the first-measured level pays residual settling)
    # and the chosen level sat right on that noise boundary.
    bench_started = time.time()
    best_by_level = {}
    for sweep, order in enumerate((worker_levels, list(reversed(worker_levels)))):
        for workers in order:
            if time.time() - bench_started > _BENCH_TIME_BUDGET_S:
                break
            batch = list(sample_keys)
            rng.shuffle(batch)
            batch_started = time.time()
            with ThreadPoolExecutor(max_workers=workers) as pool:
                outcomes = list(pool.map(probe, batch))
            elapsed = max(time.time() - batch_started, 1e-6)
            errors = [err for _lat, err in outcomes if err]
            latencies = sorted(lat for lat, err in outcomes if not err)
            row = {
                'concurrency': workers,
                'calls': len(batch),
                'errors': len(errors),
                'errorSample': errors[0] if errors else None,
                'seconds': round(elapsed, 2),
                'callsPerSec': round((len(batch) - len(errors)) / elapsed, 1),
                'medianMs': round(latencies[len(latencies) // 2] * 1000) if latencies else None,
            }
            prev = best_by_level.get(workers)
            if prev is None or row['callsPerSec'] > prev['callsPerSec']:
                best_by_level[workers] = row
    levels = [best_by_level[w] for w in worker_levels if w in best_by_level]

    usable = [lv for lv in levels if lv['calls'] - lv['errors'] > 0]
    if not usable:
        return jsonify({'error': 'all-probes-failed', 'levels': levels}), 502

    peak = max(lv['callsPerSec'] for lv in usable)
    chosen = next(lv['concurrency'] for lv in usable if lv['callsPerSec'] >= 0.95 * peak)
    with _BACKEND_SETTINGS_LOCK:
        workers_max = _BACKEND_SETTINGS['parallel_workers_max']
    recommended_workers = max(4, min(workers_max, chosen))
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
        'projectsProbed': per_level,
        'elapsedSeconds': round(time.time() - bench_started, 1),
    })


@bp.route('/api/settings/threshold-defaults', methods=['GET'])
def api_settings_threshold_defaults():
    try:
        from db_adapter import load_plugin_threshold_defaults
        return jsonify(load_plugin_threshold_defaults())
    except Exception:
        return jsonify({})
