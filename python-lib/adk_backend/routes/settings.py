"""Backend-settings routes: current/default perf settings + plugin threshold
defaults + the per-item finding whitelist (false-positive suppression).
`_BACKEND_SETTINGS` (adk_backend.settings) is the ONE shared dict — read under
its lock here; any mutation is in place, never a rebind."""
import json
import random
import threading
import time
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from adk_backend.clients import ThreadPoolExecutor, _local_thread_client, _thread_client
from adk_backend.settings import (
    _BACKEND_SETTINGS,
    _BACKEND_SETTINGS_DEFAULTS,
    _BACKEND_SETTINGS_LOCK,
)
from adk_backend.utils import advanced, local_only

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


# ── Agents & Outreach knobs ──────────────────────────────────────────────────
# Plugin params managed by the webapp Settings page ("Agents & Outreach" card)
# instead of the DSS plugin-settings screen. Each stays declared (hidden) in
# plugin.json so DSS never prunes the saved value, and agents/runnables keep
# reading them through atk_agent_common.config.resolve unchanged. Defaults here
# must mirror plugin.json.
_AGENT_KNOB_DEFAULTS = {
    'agent_runtime': 'native',
    'outreach_mail_channel': '',
    'host_allowlist': '',
    'verify_tls': True,
    'http_timeout_s': 30,
    'heavy_timeout_s': 900,
    'default_llm_id': '',
    'triage_score_threshold': 75,
    'triage_mail_channel': '',
    'triage_recipient': '',
    'auto_remediate_actions': '',
    'auto_remediate_enabled': True,
    'auto_remediate_remote_hosts': False,
    'auto_remediate_max_gb': 20,
    'auto_remediate_max_objects': 25,
    'python_run_timeout_seconds': 120,
    'log_cleanup_min_age_days': 3,
    'settings_set_blocked_extra': '',
}


def _auto_eligible_actions():
    from atk_agent_common.remediation_map import auto_catalog
    return [row['action'] for row in auto_catalog()]


def _knob_cast(key, value):
    default = _AGENT_KNOB_DEFAULTS[key]
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        return str(value).lower() not in ('false', '0', 'no', '')
    if isinstance(default, int):
        return int(value)
    return str(value or '').strip()


def _read_agent_knobs(config):
    values = {}
    for key, default in _AGENT_KNOB_DEFAULTS.items():
        raw = config.get(key)
        if raw is None or raw == '':
            values[key] = default
            continue
        try:
            values[key] = _knob_cast(key, raw)
        except (TypeError, ValueError):
            values[key] = default
    return values


@bp.route('/api/settings/agents', methods=['GET'])
@local_only
def api_agent_knobs_get():
    from adk_backend.mail import _list_mail_channels
    client = _local_thread_client()
    raw = client.get_plugin(_PLUGIN_ID).get_settings().get_raw()
    config = raw.get('config', {}) if isinstance(raw, dict) else {}
    try:
        channels = _list_mail_channels(client)
    except Exception:
        channels = []
    llms = []
    try:
        from adk_backend.clients import _local_toolkit_project
        llms = [{'id': llm['id'], 'label': llm.get('friendlyName') or llm['id']}
                for llm in _local_toolkit_project().list_llms()
                if llm.get('type') != 'RETRIEVAL_AUGMENTED']
    except Exception:
        pass
    return jsonify({'ok': True, 'values': _read_agent_knobs(config),
                    'mailChannels': channels, 'llms': llms,
                    'autoRemediateEligible': _auto_eligible_actions()})


@bp.route('/api/settings/agents/update', methods=['POST'])
@advanced
@local_only
def api_agent_knobs_update():
    body = request.get_json(force=True, silent=True) or {}
    updates = body.get('values')
    if not isinstance(updates, dict) or not updates:
        return jsonify({'ok': False, 'error': 'values must be a non-empty map'}), 400
    unknown = sorted(str(k) for k in updates if k not in _AGENT_KNOB_DEFAULTS)
    if unknown:
        return jsonify({'ok': False, 'error': 'unknown setting(s): %s' % ', '.join(unknown)}), 400
    casted = {}
    for key, value in updates.items():
        try:
            casted[key] = _knob_cast(key, value)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': '%s must be a number' % key}), 400
    if 'auto_remediate_actions' in casted:
        tokens = [t.strip() for t in casted['auto_remediate_actions'].split(',') if t.strip()]
        bad = sorted(set(tokens) - set(_auto_eligible_actions()))
        if bad:
            return jsonify({'ok': False,
                            'error': 'not auto-eligible: %s' % ', '.join(bad)}), 400
        casted['auto_remediate_actions'] = ','.join(tokens)
    if 'agent_runtime' in casted and casted['agent_runtime'] not in ('native', 'dataiku'):
        return jsonify({'ok': False,
                        'error': "agent_runtime must be 'native' or 'dataiku'"}), 400
    settings = _local_thread_client().get_plugin(_PLUGIN_ID).get_settings()
    settings.get_raw().setdefault('config', {}).update(casted)
    settings.save()
    # Saved knobs (agent_runtime, LLM picks…) must beat the native runtime's
    # setup-bundle TTL — the next chat turn reassembles from fresh config.
    from adk_backend import agent_native
    agent_native.clear_bundle_cache()
    config = settings.get_raw().get('config', {})
    return jsonify({'ok': True, 'values': _read_agent_knobs(config),
                    'written': sorted(casted)})


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


# ── Finding whitelist (per-item false-positive suppression) ──────────────────
# Rubric doctrine: every thresholded size/cleanup rule honors a per-item admin
# whitelist; whitelisted items are silently skipped wherever the rule applies
# (health score twins, issue lists, agent findings). Stored in the DSS
# INSTANCE VARIABLES (key below) — the native shared/persistent config store,
# immune to the plugin-param pruning trap. Legacy installs that still carry
# the old `finding_whitelist` plugin param are migrated on first read.

_WHITELIST_VARIABLE = 'admin_toolkit_finding_whitelist'

_WHITELIST_RULES = {
    'project-size': 'project key',
    'project-code-envs': 'project key',
    'code-env-size': 'code env name',
    'python-env-lifecycle': 'code env name',
    'disk-usage': 'mount point',
    'connection-broken': 'connection name',
    'exec-config-resources': 'exec config name',
    'sanity-check': 'message code',
}
_WHITELIST_MAX = 500
_whitelist_lock = threading.Lock()
_whitelist_cache = {'entries': None}


def _whitelist_parse(raw) -> list:
    try:
        parsed = json.loads(raw) if raw else []
        if isinstance(parsed, list):
            return [e for e in parsed if isinstance(e, dict) and e.get('rule') and e.get('item')]
    except Exception:
        pass
    return []


def _whitelist_load() -> list:
    with _whitelist_lock:
        if _whitelist_cache['entries'] is None:
            entries = []
            try:
                variables = _local_thread_client().get_global_variables()
                entries = _whitelist_parse(variables.get(_WHITELIST_VARIABLE))
                if not entries and _WHITELIST_VARIABLE not in variables:
                    # One-time migration from the legacy plugin-param store.
                    from db_adapter import _get_plugin_config
                    entries = _whitelist_parse(_get_plugin_config().get('finding_whitelist'))
                    if entries:
                        variables[_WHITELIST_VARIABLE] = json.dumps(entries)
                        variables.save()
            except Exception:
                entries = []
            _whitelist_cache['entries'] = entries
        return list(_whitelist_cache['entries'])


def _whitelist_save(entries: list) -> None:
    variables = _local_thread_client().get_global_variables()
    variables[_WHITELIST_VARIABLE] = json.dumps(entries)
    variables.save()
    with _whitelist_lock:
        _whitelist_cache['entries'] = entries


def _whitelist_key(entry: dict) -> tuple:
    return (entry.get('rule'), entry.get('item'), entry.get('host') or 'local')


@bp.route('/api/whitelist', methods=['GET'])
def api_whitelist_get():
    return jsonify({'entries': _whitelist_load(),
                    'rules': [{'rule': r, 'itemLabel': label} for r, label in _WHITELIST_RULES.items()]})


@bp.route('/api/whitelist/add', methods=['POST'])
@advanced
def api_whitelist_add():
    body = request.get_json(silent=True) or {}
    rule = str(body.get('rule') or '').strip()
    item = str(body.get('item') or '').strip()
    if rule not in _WHITELIST_RULES:
        return jsonify({'error': 'unknown-rule',
                        'message': 'rule must be one of: %s' % ', '.join(sorted(_WHITELIST_RULES))}), 400
    if not item:
        return jsonify({'error': 'missing-item', 'message': 'item is required'}), 400
    entry = {
        'rule': rule,
        'item': item,
        'host': str(body.get('host') or 'local').strip() or 'local',
        'note': str(body.get('note') or '').strip()[:300] or None,
        'addedBy': str(body.get('addedBy') or 'admin').strip()[:80],
        'addedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
    }
    entries = _whitelist_load()
    if len(entries) >= _WHITELIST_MAX:
        return jsonify({'error': 'whitelist-full',
                        'message': 'Whitelist is at its %d-entry cap.' % _WHITELIST_MAX}), 400
    entries = [e for e in entries if _whitelist_key(e) != _whitelist_key(entry)] + [entry]
    _whitelist_save(entries)
    return jsonify({'ok': True, 'entries': entries})


@bp.route('/api/whitelist/remove', methods=['POST'])
@advanced
def api_whitelist_remove():
    body = request.get_json(silent=True) or {}
    key = (str(body.get('rule') or '').strip(), str(body.get('item') or '').strip(),
           str(body.get('host') or 'local').strip() or 'local')
    entries = _whitelist_load()
    kept = [e for e in entries if _whitelist_key(e) != key]
    if len(kept) == len(entries):
        return jsonify({'error': 'not-found', 'message': 'No matching whitelist entry.'}), 404
    _whitelist_save(kept)
    return jsonify({'ok': True, 'entries': kept})
