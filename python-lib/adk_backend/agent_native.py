"""Native agent runtime — the generalist loop run IN-PROCESS in this backend,
replacing the Dataiku agent kernel as the orchestration vehicle.

What stays identical to the kernel path (parity by construction):
  • tools, actuator protocol, gates, tuning overrides, prompts — all assembled
    by atk_agent_common/generalist.py and executed through the same
    ToolkitClient HTTP surface (self-calls onto this very backend), so every
    server-side safety layer (action gates, master kill-switch, HMAC confirm
    tokens, audit rows, secret redaction) applies untouched;
  • the LLM comes from the same resolution chain (Agent Tuning override >
    per-agent llm_id > plugin default_llm_id) via the local LLM Mesh;
  • the SSE event protocol and dku-trace span layout, so the frontend and the
    Trace Explorer handoff cannot tell the runtimes apart.

What the kernel could never give us:
  • no kernel spin-up (first token in ~a second, not tens) and no recycle
    ceremony after deploys — new code is live the moment the backend restarts;
  • parallel tool execution with live out-of-order results (native_loop);
  • heartbeat frames during long tools so proxies keep the stream open;
  • Stop actually stops server-side work at the next yield;
  • works without provisioned ADMINTOOLKIT instances — when none exist the
    chat serves a "virtual" generalist whose execute gate is the plugin-level
    master switch (enable_red_actions), every backend gate still enforced.

Local host only: the kernel path remains the vehicle for chatting with a
REMOTE host's agents (each deployed webapp is its own local hub, so in
practice every instance gets the native runtime for itself).
"""

import json
import logging
import threading
import time

from dataikuapi.dss.llm_tracing import new_trace

from atk_agent_common import agent_runtime, config as atk_config, generalist, native_loop
from atk_agent_common.adapter import build_client
from atk_agent_common.errors import ToolkitError
from db_adapter import _get_plugin_config

_LOGGER = logging.getLogger(__name__)

VIRTUAL_AGENT_ID = 'native-admin-generalist'
VIRTUAL_AGENT_NAME = 'ATK Admin Agent'

# ── setup-bundle cache ───────────────────────────────────────────────────────
# Per-turn assembly costs a handful of self-HTTP/DSS reads (plugin config,
# action gates, tuning prompts, agent instance). Within a short window none of
# those can change unobserved — gates are re-enforced server-side at plan AND
# execute, tuning prompts carry their own 60s cache — so follow-up turns reuse
# the whole bundle and get to the first token immediately. Settings saves
# clear it explicitly so knob changes still apply on the very next turn.
_BUNDLE_TTL_S = 20.0
_bundle_lock = threading.Lock()
_bundle_cache = {}  # agent_id -> (monotonic deadline, bundle dict)


def clear_bundle_cache():
    """Drop every cached setup bundle — called from the agents-settings update
    route so saved knobs (agent_runtime, LLM picks, gates…) skip the TTL."""
    with _bundle_lock:
        _bundle_cache.clear()


def _setup_bundle(agent_id):
    """{settings, client, agent_config, llm_id, llm, behavior, tools, prompt}
    for one agent, cached _BUNDLE_TTL_S. Failures are raised, never cached."""
    now = time.monotonic()
    with _bundle_lock:
        hit = _bundle_cache.get(agent_id)
        if hit is not None and hit[0] > now:
            return hit[1]
    plugin_config = _get_plugin_config()
    settings = atk_config.resolve(plugin_config)
    client = build_client(plugin_config)
    agent_config = agent_instance_config_local(agent_id)
    llm_id = agent_runtime.resolve_llm_id(client, agent_config or {})
    behavior = _behavior_for(agent_config, settings)
    tools = generalist.build_toolset(client, behavior, llm_id)
    bundle = {'settings': settings, 'client': client, 'agent_config': agent_config,
              'llm_id': llm_id, 'llm': agent_runtime.build_llm(llm_id),
              'behavior': behavior, 'tools': tools,
              'prompt': generalist.build_system_prompt(client, behavior, tools)}
    with _bundle_lock:
        _bundle_cache[agent_id] = (time.monotonic() + _BUNDLE_TTL_S, bundle)
    return bundle


def runtime_mode(plugin_config=None):
    """'native' | 'dataiku' — the agent_runtime knob (Settings → Agents &
    Outreach). Unknown values fall back to 'native'."""
    config = plugin_config if plugin_config is not None else _get_plugin_config()
    mode = str((config or {}).get('agent_runtime') or '').strip().lower()
    return mode if mode in ('native', 'dataiku') else 'native'


def virtual_agent_row():
    """The provision-free agent row served when ADMINTOOLKIT has no agent
    instances but the native runtime can chat anyway."""
    return {'id': VIRTUAL_AGENT_ID, 'name': VIRTUAL_AGENT_NAME,
            'type': 'native', 'activeVersion': 'v1', 'projectKey': 'ADMINTOOLKIT'}


def agent_instance_config(client, agent_id, project_key='ADMINTOOLKIT'):
    """The agent instance's pluginAgentConfig (active version), or None when
    the instance doesn't exist / isn't readable — the virtual-agent signal."""
    try:
        raw = client.get_project(project_key).get_agent(agent_id).get_settings().get_raw()
        active = raw.get('activeVersion')
        version = next((v for v in raw.get('versions', []) if v.get('versionId') == active),
                       (raw.get('versions') or [{}])[0])
        return dict(version.get('pluginAgentConfig') or {})
    except Exception as exc:
        _LOGGER.info('no readable agent instance %r (%s) — native virtual agent',
                     agent_id, str(exc)[:150])
        return None


def _behavior_for(agent_config, plugin_settings):
    """Kernel-identical behavior for a real instance; for the virtual agent
    (no instance) the per-agent execute gate collapses into the plugin-level
    master switch — every backend-side gate still applies on top."""
    if agent_config is not None:
        return generalist.agent_behavior(agent_config)
    behavior = generalist.agent_behavior({})
    behavior['allow_execute'] = bool(plugin_settings.get('enable_red_actions'))
    return behavior


def stream_native_turn(agent_id, messages, user=None):
    """Sync generator of (event, payload) SSE tuples for one native chat turn:
    'chunk' / 'agent_event' / 'ping' / 'error', then exactly one 'final' with
    {finishReason, durationMs, trace, llmTurns?, toolsRun?, usage?} for the
    route to enrich into 'done'. `user` (the browsing DSS login, resolved by
    the route) only feeds the interaction-logs parity row."""
    started = time.monotonic()
    began_at = time.time()
    trace = new_trace('atk-native-agent-turn')
    trace.begin(int(began_at * 1000))
    try:
        bundle = _setup_bundle(agent_id)
    except ToolkitError as exc:
        # Same wording as the kernel's startup refusal, as a chunk so the
        # transcript shows it inline (parity), then a clean final.
        yield 'chunk', {'text': 'Cannot start: %s %s' % (exc.message, exc.remediation or '')}
        yield 'final', {'finishReason': 'error', 'durationMs': _elapsed_ms(started), 'trace': None}
        return
    except Exception as exc:
        yield 'error', {'message': '%s: %s' % (type(exc).__name__, str(exc)[:300])}
        return

    trace.attributes['runtime'] = 'native'
    trace.attributes['agentId'] = agent_id
    trace.attributes['llmId'] = bundle['llm_id']
    trace.inputs['messages'] = len(messages)

    lc_messages = agent_runtime.messages_from_query({'messages': messages}, bundle['prompt'])
    text_parts = []
    stats = None
    try:
        for item in native_loop.run_native_loop(bundle['llm'], bundle['tools'],
                                                lc_messages, trace=trace):
            if item.get('heartbeat'):
                yield 'ping', {}
                continue
            if item.get('stats') is not None:
                stats = item['stats']
                continue
            data = item.get('chunk') or {}
            if data.get('type') == 'event':
                yield 'agent_event', {'eventKind': data.get('eventKind'),
                                      'eventData': data.get('eventData') or {}}
            elif data.get('text'):
                text_parts.append(data['text'])
                yield 'chunk', {'text': data['text']}
    except Exception as exc:
        _LOGGER.warning('native agent turn failed (agent %s): %s', agent_id, exc)
        yield 'error', {'message': '%s: %s' % (type(exc).__name__, str(exc)[:300])}
        return
    finally:
        trace.end(int(time.time() * 1000))

    final = {'finishReason': 'stop', 'durationMs': _elapsed_ms(started),
             'trace': trace.to_dict()}
    if stats is not None:
        final['llmTurns'] = stats.get('llmTurns')
        final['toolsRun'] = stats.get('toolsRun')
        if stats.get('usage'):
            final['usage'] = stats['usage']
    _log_interaction_async(agent_id=agent_id, user=user, began_at=began_at,
                           duration_ms=final['durationMs'], messages=messages,
                           response_text=''.join(text_parts), trace_dict=final['trace'])
    yield 'final', final


def agent_instance_config_local(agent_id):
    """pluginAgentConfig via the LOCAL client (native runtime is local-only);
    the virtual agent id never touches the API."""
    if agent_id == VIRTUAL_AGENT_ID:
        return None
    import dataiku
    return agent_instance_config(dataiku.api_client(), agent_id)


def _elapsed_ms(started):
    return int((time.monotonic() - started) * 1000)


# ── interaction-logs parity writer ───────────────────────────────────────────
# Kernel turns land in ADMINTOOLKIT/agent_interaction_logs via DSS's own
# logger; native turns bypass the kernel, so this writes the equivalent row.
# Ground truth (akaos, DSS 14.7, 2026-07-19): the dataset can be Filesystem-
# backed (DAY path partitions, one file per flush) — appends go through the
# standard writer with writeMode=APPEND (spec_item.appendMode), the same
# mechanism DSS's own flushes use, and NEVER clear the partition. Best-effort
# by contract: any failure logs at debug and the chat stream never notices.

LOG_PROJECT_KEY = 'ADMINTOOLKIT'
LOG_DATASET_NAME = 'agent_interaction_logs'
# Distinct from DSS's DATAIKU_CUSTOM_AGENT so native rows are identifiable.
NATIVE_AGENT_TYPE = 'NATIVE_TOOLKIT_AGENT'
_LOG_CHECK_TTL_S = 300.0
_log_check_lock = threading.Lock()
_log_check = {'deadline': 0.0, 'ok': False}


def _interaction_dataset_ready():
    """True when the interaction-logs dataset exists on the local host
    (cached ~5 min — not provisioned is a normal state, checked cheaply)."""
    with _log_check_lock:
        if time.monotonic() < _log_check['deadline']:
            return _log_check['ok']
    ok = False
    try:
        import dataiku
        project = dataiku.api_client().get_project(LOG_PROJECT_KEY)
        ok = any(d.name == LOG_DATASET_NAME for d in project.list_datasets())
    except Exception as exc:
        _LOGGER.debug('interaction-logs dataset check failed: %s', exc)
    with _log_check_lock:
        _log_check['deadline'] = time.monotonic() + _LOG_CHECK_TTL_S
        _log_check['ok'] = ok
    return ok


def _iso_utc(epoch_s):
    """DSS interaction-log date format: 2026-07-19T14:45:30.917Z."""
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(epoch_s)) \
        + '.%03dZ' % int((epoch_s - int(epoch_s)) * 1000)


def _log_interaction_async(**meta):
    threading.Thread(target=_log_interaction, kwargs=meta,
                     name='atk-native-interaction-log', daemon=True).start()


def _log_interaction(agent_id, user, began_at, duration_ms, messages,
                     response_text, trace_dict):
    """One parity row, column formats mirroring a live kernel-written row
    (agent_id `<project>.<id>`, ISO-Z dates, JSON input/response/trace).
    Columns the native turn can't honestly fill (conversation/message ids,
    caller auth details, raw mesh payloads) stay empty."""
    try:
        if not _interaction_dataset_ready():
            return
        import dataiku
        row = {
            'agent_id': '%s.%s' % (LOG_PROJECT_KEY, agent_id),
            'agent_type': NATIVE_AGENT_TYPE,
            'user': user or '',
            'begin_time': _iso_utc(began_at),
            'end_time': _iso_utc(began_at + (duration_ms or 0) / 1000.0),
            'duration_ms': duration_ms,
            'status': 'SUCCESS',
            'input_messages': json.dumps(messages),
            'response': json.dumps({'role': 'assistant', 'content': response_text,
                                    'toolCalls': [], 'toolValidationRequests': []}),
            'dku_trace': json.dumps(trace_dict) if trace_dict else '',
            'dku_agent_logging_log_time': _iso_utc(time.time()),
        }
        dataset = dataiku.Dataset(LOG_DATASET_NAME, project_key=LOG_PROJECT_KEY,
                                  ignore_flow=True)
        # MUST precede get_writer(): APPEND is what keeps DSS's own rows safe.
        dataset.spec_item['appendMode'] = True
        dataset.set_write_partition(time.strftime('%Y-%m-%d', time.gmtime(began_at)))
        with dataset.get_writer() as writer:
            # Maps by the dataset's live schema; columns we don't set stay empty.
            writer.write_row_dict(row)
        _LOGGER.debug('native interaction-log row written (agent %s)', agent_id)
    except Exception as exc:
        _LOGGER.debug('native interaction-log write skipped: %s: %s',
                      type(exc).__name__, str(exc)[:300])
