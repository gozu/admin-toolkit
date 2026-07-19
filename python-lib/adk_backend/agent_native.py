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

import logging
import time

from dataikuapi.dss.llm_tracing import new_trace

from atk_agent_common import agent_runtime, config as atk_config, generalist, native_loop
from atk_agent_common.adapter import build_client
from atk_agent_common.errors import ToolkitError
from db_adapter import _get_plugin_config

_LOGGER = logging.getLogger(__name__)

VIRTUAL_AGENT_ID = 'native-admin-generalist'
VIRTUAL_AGENT_NAME = 'ATK Admin Agent'


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


def stream_native_turn(agent_id, messages):
    """Sync generator of (event, payload) SSE tuples for one native chat turn:
    'chunk' / 'agent_event' / 'ping' / 'error', then exactly one 'final' with
    {finishReason, durationMs, trace} for the route to enrich into 'done'."""
    started = time.monotonic()
    trace = new_trace('atk-native-agent-turn')
    trace.begin(int(time.time() * 1000))
    try:
        plugin_config = _get_plugin_config()
        settings = atk_config.resolve(plugin_config)
        client = build_client(plugin_config)
        agent_config = agent_instance_config_local(agent_id)
        llm_id = agent_runtime.resolve_llm_id(client, agent_config or {})
        llm = agent_runtime.build_llm(llm_id)
        behavior = _behavior_for(agent_config, settings)
        tools = generalist.build_toolset(client, behavior, llm_id)
        prompt = generalist.build_system_prompt(client, behavior, tools)
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
    trace.attributes['llmId'] = llm_id
    trace.inputs['messages'] = len(messages)

    lc_messages = agent_runtime.messages_from_query({'messages': messages}, prompt)
    try:
        for item in native_loop.run_native_loop(llm, tools, lc_messages, trace=trace):
            if item.get('heartbeat'):
                yield 'ping', {}
                continue
            data = item.get('chunk') or {}
            if data.get('type') == 'event':
                yield 'agent_event', {'eventKind': data.get('eventKind'),
                                      'eventData': data.get('eventData') or {}}
            elif data.get('text'):
                yield 'chunk', {'text': data['text']}
    except Exception as exc:
        _LOGGER.warning('native agent turn failed (agent %s): %s', agent_id, exc)
        yield 'error', {'message': '%s: %s' % (type(exc).__name__, str(exc)[:300])}
        return
    finally:
        trace.end(int(time.time() * 1000))

    yield 'final', {'finishReason': 'stop', 'durationMs': _elapsed_ms(started),
                    'trace': trace.to_dict()}


def agent_instance_config_local(agent_id):
    """pluginAgentConfig via the LOCAL client (native runtime is local-only);
    the virtual agent id never touches the API."""
    if agent_id == VIRTUAL_AGENT_ID:
        return None
    import dataiku
    return agent_instance_config(dataiku.api_client(), agent_id)


def _elapsed_ms(started):
    return int((time.monotonic() - started) * 1000)
