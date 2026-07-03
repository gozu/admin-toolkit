"""Agents routes: chat with the agents-plugin agents (SSE proxy over the LLM
Mesh) + the agent-action audit timeline.

POST /api/agents/chat streams the agents' own event protocol — token deltas,
tool_call / tool_result activity, plan / execution cards — straight from
`agent.as_llm().new_completion().execute_streamed()` on the active host, so
the webapp renders plans as approve/reject cards instead of prose. Agent
instances live in the AGENTOPS project (provisioned by the agents plugin's
scripts). The audit timeline reads agents.agent_actions, written by the
agents plugin into the shared audit Postgres — hub-scoped → @local_only.
"""

import json
import logging
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
    done {finishReason, durationMs} · error {message}.
    """
    body = request.get_json(silent=True) or {}
    agent_id = (body.get('agentId') or '').strip()
    messages = body.get('messages') or []
    if not agent_id or not isinstance(messages, list) or not messages:
        return jsonify({'error': 'agentId and a non-empty messages list are required'}), 400
    client = g.client

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
            yield sse('done', {'finishReason': (footer or {}).get('finishReason'),
                               'durationMs': trajectory.get('durationMs')})
        except Exception as exc:
            _LOGGER.warning('agent chat stream failed (agent %s): %s', agent_id, exc)
            yield sse('error', {'message': '%s: %s' % (type(exc).__name__, str(exc)[:300])})

    return _sse_response(generate)


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
