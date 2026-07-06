"""Agents routes: chat with the plugin's agents (SSE proxy over the LLM
Mesh) + the agent-action audit timeline.

POST /api/agents/chat streams the agents' own event protocol — token deltas,
tool_call / tool_result activity, plan / execution cards — straight from
`agent.as_llm().new_completion().execute_streamed()` on the active host, so
the webapp renders plans as approve/reject cards instead of prose. Agent
instances live in the AGENTOPS project (provisioned by scripts/agents/).
The audit timeline reads agents.agent_actions, written by the agents layer
into the shared audit Postgres — hub-scoped → @local_only.
"""

import json
import logging
import re
import threading
import uuid
from collections import deque
from typing import Any, Dict, List, Optional

from flask import Blueprint, g, jsonify, request

from adk_backend import agents_db
from adk_backend.utils import _sse_response, local_only
from db_adapter import load_agents_audit_config

bp = Blueprint('agents', __name__)
_LOGGER = logging.getLogger(__name__)

AGENTS_PROJECT_KEY = 'AGENTOPS'
_MAX_MESSAGES = 40
_MAX_MESSAGE_CHARS = 20000

# Native traces (DSS >= 14.5 interaction logging): the streamed footer carries
# the full dku-trace. It is never streamed over SSE — the done event only says
# it exists (traceAvailable + traceId); the JSON is fetched on demand from this
# in-memory ring (last few turns, gone on backend restart — the durable copy
# lives in the AGENTOPS interaction-logging dataset).
_trace_ring: 'deque' = deque(maxlen=8)  # (trace_id, trace dict)
_trace_lock = threading.Lock()
# Trace Explorer is a visual webapp the API cannot create (verified on 14.7) —
# discovered per host; only hits (not misses) are cached so a webapp created
# later is picked up on the next turn.
_trace_explorer_cache: Dict[str, str] = {}


def _remember_trace(trace: Any) -> str:
    trace_id = uuid.uuid4().hex[:12]
    with _trace_lock:
        _trace_ring.append((trace_id, trace))
    return trace_id


def _trace_explorer_path(client: Any, host_id: str) -> Optional[str]:
    """DSS-relative path of a Trace Explorer webapp in AGENTOPS, or None.
    The frontend prefixes the active host's base URL (multi-instance rule)."""
    cached = _trace_explorer_cache.get(host_id)
    if cached:
        return cached
    try:
        for item in client.get_project(AGENTS_PROJECT_KEY).list_webapps() or []:
            data = getattr(item, '_data', None) or {}
            blob = ('%s %s' % (data.get('type') or '', data.get('name') or '')).lower()
            if 'trace' in blob and data.get('id'):
                slug = re.sub(r'[^a-z0-9]+', '-',
                              (data.get('name') or '').lower()).strip('-') or 'trace-explorer'
                path = '/projects/%s/webapps/%s_%s/view' % (AGENTS_PROJECT_KEY, data['id'], slug)
                _trace_explorer_cache[host_id] = path
                return path
    except Exception as exc:
        _LOGGER.debug('trace explorer discovery failed on %s: %s', host_id, exc)
    return None


def _agent_rows(client: Any) -> List[Dict[str, Any]]:
    project = client.get_project(AGENTS_PROJECT_KEY)
    rows = []
    for item in project.list_agents() or []:
        raw = item if isinstance(item, dict) else getattr(item, 'raw', {}) or {}
        if raw.get('id'):
            rows.append({'id': raw.get('id'), 'name': raw.get('name') or raw.get('id'),
                         'type': raw.get('savedModelType') or raw.get('type')})
    return rows


@bp.route('/api/agents')
def api_agents_list():
    try:
        return jsonify({'available': True, 'projectKey': AGENTS_PROJECT_KEY,
                        'agents': _agent_rows(g.client)})
    except Exception as exc:
        # No AGENTOPS project / no agents plugin on this host is a normal state,
        # not an error — the page shows its provisioning empty-state.
        _LOGGER.info('agents list unavailable on host %s: %s',
                     getattr(g, 'host_id', 'local'), str(exc)[:200])
        return jsonify({'available': False, 'projectKey': AGENTS_PROJECT_KEY,
                        'agents': [], 'reason': str(exc)[:200]})


@bp.route('/api/agents/chat', methods=['POST'])
def api_agents_chat():
    """SSE: relay one streamed agent completion.

    Body: {agentId, messages: [{role: 'user'|'assistant', content}, ...]}
    Events out: chunk {text} · agent_event {eventKind, eventData} ·
    done {finishReason, durationMs, traceAvailable, traceId?, traceExplorerPath?} ·
    error {message}.
    """
    body = request.get_json(silent=True) or {}
    agent_id = (body.get('agentId') or '').strip()
    messages = body.get('messages') or []
    if not agent_id or not isinstance(messages, list) or not messages:
        return jsonify({'error': 'agentId and a non-empty messages list are required'}), 400
    client = g.client
    host_id = str(getattr(g, 'host_id', 'local') or 'local')

    def generate():
        def sse(event: str, payload: Dict[str, Any]) -> str:
            return 'event: %s\ndata: %s\n\n' % (event, json.dumps(payload, default=str))

        try:
            llm = client.get_project(AGENTS_PROJECT_KEY).get_agent(agent_id).as_llm()
            completion = llm.new_completion()
            for msg in messages[-_MAX_MESSAGES:]:
                role = msg.get('role') if isinstance(msg, dict) else None
                content = (msg.get('content') or '') if isinstance(msg, dict) else ''
                if role in ('user', 'assistant') and content:
                    completion.with_message(content[:_MAX_MESSAGE_CHARS], role=role)
            footer: Optional[Dict[str, Any]] = None
            for chunk in completion.execute_streamed():
                data = getattr(chunk, 'data', None) or {}
                kind = data.get('type')
                if kind == 'content':
                    if data.get('text'):
                        yield sse('chunk', {'text': data['text']})
                elif kind == 'event':
                    yield sse('agent_event', {'eventKind': data.get('eventKind'),
                                              'eventData': data.get('eventData') or {}})
                elif kind == 'footer':
                    footer = data
            trajectory = ((footer or {}).get('additionalInformation') or {}).get('trajectory') or {}
            trace = (footer or {}).get('trace')
            done_payload: Dict[str, Any] = {'finishReason': (footer or {}).get('finishReason'),
                                            'durationMs': trajectory.get('durationMs'),
                                            'traceAvailable': bool(trace)}
            if trace:
                done_payload['traceId'] = _remember_trace(trace)
            explorer_path = _trace_explorer_path(client, host_id)
            if explorer_path:
                done_payload['traceExplorerPath'] = explorer_path
            yield sse('done', done_payload)
        except Exception as exc:
            _LOGGER.warning('agent chat stream failed (agent %s): %s', agent_id, exc)
            yield sse('error', {'message': '%s: %s' % (type(exc).__name__, str(exc)[:300])})

    return _sse_response(generate)


@bp.route('/api/agents/last-trace')
def api_agents_last_trace():
    """One trace from the in-memory ring buffer, for Trace Explorer's native
    "Paste a new trace" — instant per-turn inspection without waiting for the
    logging dataset's flush interval. Ring survives only until backend restart;
    an expired id is a normal state, not an error."""
    trace_id = (request.args.get('id') or '').strip()
    with _trace_lock:
        for tid, trace in _trace_ring:
            if tid == trace_id:
                return jsonify({'available': True, 'trace': trace})
    return jsonify({'available': False, 'reason': 'trace-expired'}), 404


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
