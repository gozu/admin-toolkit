"""Agents routes: chat with the plugin's agents (SSE proxy over the LLM
Mesh) + the agent-action audit timeline.

POST /api/agents/chat streams the agents' own event protocol — token deltas,
tool_call / tool_result activity, plan / execution cards — straight from
`agent.as_llm().new_completion().execute_streamed()` on the active host, so
the webapp renders plans as approve/reject cards instead of prose. Agent
instances live in the ADMINTOOLKIT project (provisioned by scripts/agents/).
The audit timeline reads agents.agent_actions, written by the agents layer
into the shared audit Postgres — hub-scoped → @local_only.
"""

import json
import logging
import threading
import uuid
from collections import deque
from typing import Any, Dict, List, Optional

from flask import Blueprint, g, jsonify, request

from adk_backend import agent_native, agent_provision, agents_db, trace_explorer
from adk_backend.utils import _sse_response, advanced, local_only
from db_adapter import load_agents_audit_config

bp = Blueprint('agents', __name__)
_LOGGER = logging.getLogger(__name__)

AGENTS_PROJECT_KEY = 'ADMINTOOLKIT'
_MAX_MESSAGES = 40
_MAX_MESSAGE_CHARS = 20000

# Native traces (DSS >= 14.5 interaction logging): the streamed footer carries
# the full dku-trace. It is never streamed over SSE — the done event only says
# it exists (traceAvailable + traceId); the JSON is fetched on demand from this
# in-memory ring (last few turns, gone on backend restart — the durable copy
# lives in the ADMINTOOLKIT interaction-logging dataset).
_trace_ring: 'deque' = deque(maxlen=8)  # (trace_id, trace dict)
_trace_lock = threading.Lock()
# Trace Explorer plugin webapp — discovered per host; only hits (not misses)
# are cached so a webapp created later (e.g. via the provision route below) is
# picked up on the next turn. Values: {'viewPath','webAppId','projectKey'}.
_trace_explorer_cache: Dict[str, Dict[str, str]] = {}


def _remember_trace(trace: Any) -> str:
    trace_id = uuid.uuid4().hex[:12]
    with _trace_lock:
        _trace_ring.append((trace_id, trace))
    return trace_id


def get_ring_trace(trace_id: str) -> Optional[Any]:
    """Trace dict from the in-memory ring by id, or None once rotated out.
    Read under the lock — used by /api/agents/last-trace and by chat
    persistence to attach the turn's trace to the persisted message."""
    with _trace_lock:
        for tid, trace in _trace_ring:
            if tid == trace_id:
                return trace
    return None


def _trace_explorer_info(client: Any, host_id: str) -> Optional[Dict[str, str]]:
    """{'viewPath','webAppId','projectKey'} of a Trace Explorer webapp in
    ADMINTOOLKIT, or None. viewPath is DSS-relative — the frontend prefixes the
    active host's base URL (multi-instance rule); webAppId feeds the native
    readTraceFromLS localStorage handoff (same-origin only)."""
    cached = _trace_explorer_cache.get(host_id)
    if cached:
        return cached
    try:
        found = trace_explorer.find_trace_explorer(client.get_project(AGENTS_PROJECT_KEY))
        if found:
            info = {'viewPath': trace_explorer.view_path(
                        AGENTS_PROJECT_KEY, found['id'], found['name']),
                    'webAppId': found['id'],
                    'projectKey': AGENTS_PROJECT_KEY}
            _trace_explorer_cache[host_id] = info
            return info
    except Exception as exc:
        _LOGGER.debug('trace explorer discovery failed on %s: %s', host_id, exc)
    return None


def _agent_rows(client: Any) -> List[Dict[str, Any]]:
    project = client.get_project(AGENTS_PROJECT_KEY)
    rows = []
    for item in project.list_agents() or []:
        raw = item if isinstance(item, dict) else getattr(item, 'raw', {}) or {}
        if raw.get('id'):
            # activeVersion + projectKey let the frontend deep-link to this
            # agent's DSS config screen (e.g. to flip `allow_red_actions` after
            # an agent-execution-disabled refusal): the saved-model version id
            # is `S-<projectKey>-<id>-<activeVersion>`.
            rows.append({'id': raw.get('id'), 'name': raw.get('name') or raw.get('id'),
                         'type': raw.get('savedModelType') or raw.get('type'),
                         'activeVersion': raw.get('activeVersion') or 'v1',
                         'projectKey': raw.get('projectKey') or AGENTS_PROJECT_KEY})
    return rows


@bp.route('/api/agents')
def api_agents_list():
    host_id = str(getattr(g, 'host_id', 'local') or 'local')
    try:
        rows = _agent_rows(g.client)
    except Exception as exc:
        # No ADMINTOOLKIT project / no agents provisioned on this host is a
        # normal state, not an error — the page shows its provisioning empty-state.
        _LOGGER.info('agents list unavailable on host %s: %s', host_id, str(exc)[:200])
        rows, reason = [], str(exc)[:200]
    else:
        reason = None
    # The native runtime needs no provisioned instances: on the local hub the
    # virtual generalist replaces the provisioning empty-state regardless of
    # the configured runtime — chatting with it falls back to native (the
    # kernel relay has nothing to relay to) and the turn is tagged as such.
    if not rows and host_id == 'local':
        return jsonify({'available': True, 'projectKey': AGENTS_PROJECT_KEY,
                        'agents': [agent_native.virtual_agent_row()],
                        'runtime': 'native'})
    if not rows:
        return jsonify({'available': False, 'projectKey': AGENTS_PROJECT_KEY,
                        'agents': [], 'reason': reason or 'no agents provisioned'})
    return jsonify({'available': True, 'projectKey': AGENTS_PROJECT_KEY, 'agents': rows})


@bp.route('/api/agents/provision', methods=['POST'])
def api_agents_provision():
    """One-click agents provisioning — the Agents page's empty-state CTA (the
    no-CLI replacement for scripts/agents/provision_prod.py). Idempotent;
    ungated like the other bootstrap routes (/api/hosts/macro-project,
    install-toolkit): it only creates the toolkit's own standard objects, and
    the agents stay inert until an admin picks an LLM and enables actions."""
    return jsonify(agent_provision.ensure_agents_provisioned(g.client))


def _sse_frame(event: str, payload: Dict[str, Any]) -> str:
    return 'event: %s\ndata: %s\n\n' % (event, json.dumps(payload, default=str))


def _clip_messages(messages: List[Any]) -> List[Dict[str, str]]:
    """The shared history caps, applied once in the view (the SSE generators
    run outside the request context and must only touch plain values)."""
    clipped = []
    for msg in messages[-_MAX_MESSAGES:]:
        role = msg.get('role') if isinstance(msg, dict) else None
        content = (msg.get('content') or '') if isinstance(msg, dict) else ''
        if role in ('user', 'assistant') and content:
            clipped.append({'role': role, 'content': content[:_MAX_MESSAGE_CHARS]})
    return clipped


@bp.route('/api/agents/chat', methods=['POST'])
def api_agents_chat():
    """SSE: one streamed agent turn.

    Body: {agentId, messages: [{role: 'user'|'assistant', content}, ...],
           runtime?: 'native'|'dataiku' (per-request override, e.g. drills)}
    Events out: chunk {text} · agent_event {eventKind, eventData} ·
    done {finishReason, durationMs, traceAvailable, traceId?, traceExplorerPath?,
    runtime, fallbackFrom?, fallbackReason?} · error {message} · ping {}
    (native keep-alives).

    Two runtimes behind the same protocol. The relay over the Dataiku agent
    kernel (`agent.as_llm().execute_streamed()`) is the DEFAULT and the only
    vehicle for remote hosts; the in-process native loop (agent_native, local
    host only) serves explicit runtime='native' choices and two fallbacks —
    the virtual generalist (no provisioned instances) and a kernel relay that
    fails before streaming anything. Fallback turns are tagged in the done
    event; an explicit 'dataiku' override disables fallback (deterministic
    drills/tests).
    """
    body = request.get_json(silent=True) or {}
    agent_id = (body.get('agentId') or '').strip()
    messages = body.get('messages') or []
    if not agent_id or len(agent_id) > 64 or not isinstance(messages, list) or not messages:
        return jsonify({'error': 'agentId and a non-empty messages list are required'}), 400
    client = g.client
    host_id = str(getattr(g, 'host_id', 'local') or 'local')
    is_local = host_id == 'local'

    clipped = _clip_messages(messages)
    try:
        from adk_backend.chat.identity import resolve_chat_user
        chat_user = resolve_chat_user()
    except Exception:
        chat_user = None

    override = str(body.get('runtime') or '').strip().lower()
    if override not in ('native', 'dataiku'):
        override = ''
    mode = override or agent_native.runtime_mode()

    if is_local and mode == 'native':
        def native_only():
            for frame in _native_sse_frames(client, host_id, agent_id, clipped, chat_user):
                yield frame
        return _sse_response(native_only)

    if is_local and agent_id == agent_native.VIRTUAL_AGENT_ID and override != 'dataiku':
        # The virtual generalist only exists natively (the list route serves it
        # when zero instances are provisioned) — the kernel relay has nothing
        # to resolve the id against. agent_instance_config_local stays
        # fail-closed: it re-verifies the zero-instances state.
        def virtual_fallback():
            for frame in _native_sse_frames(
                    client, host_id, agent_id, clipped, chat_user,
                    fallback={'from': 'dataiku', 'reason': 'no-agent-instances'}):
                yield frame
        return _sse_response(virtual_fallback)

    def generate():
        streamed = False
        try:
            llm = client.get_project(AGENTS_PROJECT_KEY).get_agent(agent_id).as_llm()
            completion = llm.new_completion()
            for msg in clipped:
                completion.with_message(msg['content'], role=msg['role'])
            footer: Optional[Dict[str, Any]] = None
            for chunk in completion.execute_streamed():
                data = getattr(chunk, 'data', None) or {}
                kind = data.get('type')
                if kind == 'content':
                    if data.get('text'):
                        streamed = True
                        yield _sse_frame('chunk', {'text': data['text']})
                elif kind == 'event':
                    streamed = True
                    yield _sse_frame('agent_event', {'eventKind': data.get('eventKind'),
                                                     'eventData': data.get('eventData') or {}})
                elif kind == 'footer':
                    footer = data
            trajectory = ((footer or {}).get('additionalInformation') or {}).get('trajectory') or {}
            trace = (footer or {}).get('trace')
            done_payload: Dict[str, Any] = {'finishReason': (footer or {}).get('finishReason'),
                                            'durationMs': trajectory.get('durationMs'),
                                            'traceAvailable': bool(trace),
                                            'runtime': 'dataiku'}
            if trace:
                done_payload['traceId'] = _remember_trace(trace)
            explorer = _trace_explorer_info(client, host_id)
            if explorer:
                done_payload['traceExplorer'] = explorer
                # Back-compat alias (pre-0.4.648 frontends read the path).
                done_payload['traceExplorerPath'] = explorer['viewPath']
            yield _sse_frame('done', done_payload)
        except Exception as exc:
            _LOGGER.warning('agent chat stream failed (agent %s): %s', agent_id, exc)
            if not streamed and is_local and not override:
                # Nothing reached the client yet — retry the whole turn
                # natively (local only). Mid-stream failures do NOT retry:
                # kernel tool calls may have had side effects and partial text
                # is already on screen; a manual retry will itself fall back
                # here if the kernel is still down.
                reason = 'kernel-error: %s: %s' % (type(exc).__name__, str(exc)[:200])
                for frame in _native_sse_frames(client, host_id, agent_id, clipped, chat_user,
                                                fallback={'from': 'dataiku', 'reason': reason}):
                    yield frame
                return
            yield _sse_frame('error', {'message': '%s: %s' % (type(exc).__name__, str(exc)[:300])})

    return _sse_response(generate)


def _native_sse_frames(client, host_id: str, agent_id: str, clipped: List[Dict[str, str]],
                       chat_user: Optional[str], fallback: Optional[Dict[str, str]] = None):
    """SSE frames for one native-runtime turn: translate agent_native's
    (event, payload) tuples with the shared done-event enrichment (trace ring
    + Trace Explorer). `fallback` marks a turn the kernel relay could not
    serve — its from/reason merge into the done payload so the UI can tag the
    turn honestly (runtime stays 'native': that IS what ran)."""
    try:
        for event, payload in agent_native.stream_native_turn(agent_id, clipped,
                                                              user=chat_user):
            if event != 'final':
                yield _sse_frame(event, payload)
                continue
            trace = payload.get('trace')
            done_payload: Dict[str, Any] = {'finishReason': payload.get('finishReason'),
                                            'durationMs': payload.get('durationMs'),
                                            'traceAvailable': bool(trace),
                                            'runtime': 'native'}
            if fallback:
                done_payload['fallbackFrom'] = fallback.get('from')
                done_payload['fallbackReason'] = fallback.get('reason')
            # Turn stats (native loop): only when the generator provided them.
            for key in ('llmTurns', 'toolsRun', 'usage'):
                if payload.get(key) is not None:
                    done_payload[key] = payload[key]
            if trace:
                done_payload['traceId'] = _remember_trace(trace)
            explorer = _trace_explorer_info(client, host_id)
            if explorer:
                done_payload['traceExplorer'] = explorer
                done_payload['traceExplorerPath'] = explorer['viewPath']
            yield _sse_frame('done', done_payload)
    except Exception as exc:
        _LOGGER.warning('native agent chat failed (agent %s): %s', agent_id, exc)
        yield _sse_frame('error', {'message': '%s: %s' % (type(exc).__name__, str(exc)[:300])})


@bp.route('/api/agents/last-trace')
def api_agents_last_trace():
    """One trace from the in-memory ring buffer, for Trace Explorer's native
    "Paste a new trace" — instant per-turn inspection without waiting for the
    logging dataset's flush interval. Ring survives only until backend restart;
    an expired id is a normal state, not an error."""
    trace_id = (request.args.get('id') or '').strip()
    trace = get_ring_trace(trace_id)
    if trace is not None:
        return jsonify({'available': True, 'trace': trace})
    return jsonify({'available': False, 'reason': 'trace-expired'}), 404


@bp.route('/api/agents/trace-explorer/status')
def api_trace_explorer_status():
    """Trace Explorer readiness for the CTA — moves plugin/webapp discovery
    off the per-turn hot path. sameOrigin gates the native localStorage
    handoff: it only works when the explorer shares the browser origin, i.e.
    the active host is the local hub this webapp is served from."""
    client = g.client
    host_id = str(getattr(g, 'host_id', 'local') or 'local')
    installed = trace_explorer._plugin_installed(client)
    info = _trace_explorer_info(client, host_id) if installed else None
    payload: Dict[str, Any] = {'installed': installed,
                               'provisioned': bool(info),
                               'projectKey': AGENTS_PROJECT_KEY,
                               'sameOrigin': host_id == 'local'}
    if info:
        payload['webAppId'] = info['webAppId']
        payload['viewPath'] = info['viewPath']
    return jsonify(payload)


@bp.route('/api/agents/trace-explorer/provision', methods=['POST'])
@advanced
def api_trace_explorer_provision():
    """One-click provisioning: interaction-logging dataset + FULL logging on
    all ADMINTOOLKIT agents + the traces-explorer plugin webapp created via raw
    REST, configured onto the dataset's `trace` column, backend started.
    Mutating → @advanced-gated. Uses g.client — ADMINTOOLKIT may live on the
    active remote host."""
    host_id = str(getattr(g, 'host_id', 'local') or 'local')
    result = trace_explorer.ensure_trace_explorer(g.client,
                                                  project_key=AGENTS_PROJECT_KEY)
    if result.get('ok'):
        _trace_explorer_cache.pop(host_id, None)  # rediscover the fresh webapp
    return jsonify(result)


@bp.route('/api/agents/settings-history')
@local_only
def api_agents_settings_history():
    """Settings-change history (agents.settings_changes, written by the agents
    plugin's actuator). `?item=<item_key>` scopes to one item (last 50 — the
    restore window per the K97 doctrine); without it, the most recent changes
    across all items."""
    item = (request.args.get('item') or '').strip()
    limit = 50 if item else 100
    cfg = load_agents_audit_config(client=g.client)
    if not cfg.connection_name:
        return jsonify({'available': False, 'changes': [], 'reason': 'audit-db-not-configured'})
    try:
        conn = agents_db.connect(cfg.connection_name, client=g.client)
    except Exception as exc:
        return jsonify({'available': False, 'changes': [],
                        'reason': 'audit-db-unreachable: %s' % str(exc)[:200]})
    try:
        with conn.cursor() as cur:
            if item:
                cur.execute(
                    'SELECT id, ts, host, item_key, before, after, agent, actor, audit_id '
                    'FROM agents.settings_changes WHERE item_key = %s '
                    'ORDER BY id DESC LIMIT %s', (item, limit))
            else:
                cur.execute(
                    'SELECT id, ts, host, item_key, before, after, agent, actor, audit_id '
                    'FROM agents.settings_changes ORDER BY id DESC LIMIT %s', (limit,))
            cols = [d[0] for d in (cur.description or [])]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        for row in rows:
            row['ts'] = str(row.get('ts') or '')
            for field in ('before', 'after'):
                try:
                    row[field] = json.loads(row[field]) if row.get(field) else None
                except (ValueError, TypeError):
                    pass
        return jsonify({'available': True, 'changes': rows})
    except Exception as exc:
        # Table absent until the first settings-mutating action — normal empty state.
        return jsonify({'available': False, 'changes': [], 'reason': str(exc)[:200]})
    finally:
        try:
            conn.close()
        except Exception:
            pass


@bp.route('/api/agents/actions')
@local_only
def api_agents_actions():
    """Audit timeline: most recent agent-executed actions (agents.agent_actions)."""
    try:
        limit = max(1, min(int(request.args.get('limit', 100)), 500))
    except (TypeError, ValueError):
        limit = 100
    cfg = load_agents_audit_config(client=g.client)
    if not cfg.connection_name:
        return jsonify({'available': False, 'actions': [], 'reason': 'audit-db-not-configured'})
    try:
        conn = agents_db.connect(cfg.connection_name, client=g.client)
    except Exception as exc:
        return jsonify({'available': False, 'actions': [],
                        'reason': 'audit-db-unreachable: %s' % str(exc)[:200]})
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT id, ts, agent, llm_id, host, action, target, params, status, result_snippet '
                'FROM agents.agent_actions ORDER BY id DESC LIMIT %s', (limit,))
            cols = [d[0] for d in (cur.description or [])]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        for row in rows:
            row['ts'] = str(row.get('ts') or '')
            for field in ('target', 'params'):
                try:
                    row[field] = json.loads(row[field]) if row.get(field) else None
                except ValueError:
                    pass
        return jsonify({'available': True, 'actions': rows})
    except Exception as exc:
        # Table absent until the first executed action — normal empty state.
        return jsonify({'available': False, 'actions': [], 'reason': str(exc)[:200]})
    finally:
        try:
            conn.close()
        except Exception:
            pass
