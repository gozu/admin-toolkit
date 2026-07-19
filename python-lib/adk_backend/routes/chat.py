"""Chat persistence routes: server-side conversation history for the Agents
module (Agent Hub storage model — see adk_backend/chat/).

NOT @local_only: the routes need the real g.host_id so conversations are
scoped per fleet host — but the store itself always lives on the LOCAL hub
(plugin settings + the selected SQL connection are local; chat/db.py forces
the local client internally). Per-user scoping is best-effort browser-header
identity (chat/identity.py), anonymous fallback.

Disabled (chat_storage=OFF, the default) is a clean no-op: /api/chat/config
answers {enabled: false} and every other route early-returns without ever
touching the DB layer.
"""

import json
import logging

from flask import Blueprint, g, jsonify, request

from adk_backend.chat import db as chat_db
from adk_backend.chat import store as chat_store
from adk_backend.chat.identity import resolve_chat_user
from adk_backend.routes.agents import get_ring_trace
from db_adapter import load_chat_persistence_config

bp = Blueprint('chat', __name__)
_LOGGER = logging.getLogger(__name__)

_MAX_TURN_MESSAGES = 50
_MAX_CONTENT_CHARS = 200000
# Segment[] is stored as one JSON TEXT cell; cap the serialized size so a
# single (unauthenticated) POST can't grow the store without bound.
_MAX_SEGMENTS_BYTES = 300000


def _config():
    from adk_backend.clients import _local_thread_client
    try:
        client = _local_thread_client()
    except Exception:
        client = None
    return load_chat_persistence_config(client=client)


def _disabled_response():
    return jsonify({'enabled': False})


def _scope():
    """(user_id, host_id) for every store call."""
    return resolve_chat_user(), str(getattr(g, 'host_id', 'local') or 'local')


def _ready_or_error(cfg):
    """Bring the store up (lazy, once); a jsonify error tuple on failure."""
    try:
        chat_db.ensure_ready(cfg)
        return None
    except chat_db.ChatPersistenceError as exc:
        _LOGGER.warning('chat persistence unavailable: %s', exc)
        return jsonify({'enabled': True, 'error': str(exc)[:300]}), 503


@bp.route('/api/chat/config')
def api_chat_config():
    """{enabled, mode} — the frontend's single feature gate. When enabled but
    the store can't come up, reports enabled:false with the reason so the UI
    stays in browser-only mode instead of erroring on every turn."""
    cfg = _config()
    if not cfg.enabled:
        return _disabled_response()
    try:
        chat_db.ensure_ready(cfg)
    except chat_db.ChatPersistenceError as exc:
        _LOGGER.warning('chat persistence configured but unavailable: %s', exc)
        return jsonify({'enabled': False, 'mode': cfg.mode, 'reason': str(exc)[:300]})
    return jsonify({'enabled': True, 'mode': cfg.mode})


@bp.route('/api/chat/conversations')
def api_chat_list():
    cfg = _config()
    if not cfg.enabled:
        return _disabled_response()
    err = _ready_or_error(cfg)
    if err:
        return err
    user_id, host_id = _scope()
    return jsonify({'enabled': True,
                    'conversations': chat_store.list_conversations(user_id, host_id)})


@bp.route('/api/chat/conversations/<conversation_id>')
def api_chat_get(conversation_id):
    cfg = _config()
    if not cfg.enabled:
        return _disabled_response()
    err = _ready_or_error(cfg)
    if err:
        return err
    user_id, host_id = _scope()
    conv = chat_store.get_conversation(user_id, host_id, conversation_id)
    if conv is None:
        return jsonify({'error': 'conversation-not-found'}), 404
    return jsonify({'enabled': True, 'conversation': conv})


@bp.route('/api/chat/conversations', methods=['POST'])
def api_chat_create():
    """Create/touch a conversation row without messages (normally the row is
    minted implicitly by the first POSTed turn)."""
    cfg = _config()
    if not cfg.enabled:
        return _disabled_response()
    err = _ready_or_error(cfg)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    conversation_id = str(body.get('id') or '').strip()
    agent_id = str(body.get('agentId') or '').strip()
    if not conversation_id or not agent_id:
        return jsonify({'error': 'id and agentId are required'}), 400
    user_id, host_id = _scope()
    result = chat_store.upsert_turn(user_id, host_id, conversation_id, agent_id,
                                    messages=[],
                                    title=str(body.get('title') or '').strip() or None)
    return jsonify({'enabled': True, 'conversation': result})


@bp.route('/api/chat/conversations/<conversation_id>', methods=['PUT'])
def api_chat_rename(conversation_id):
    cfg = _config()
    if not cfg.enabled:
        return _disabled_response()
    err = _ready_or_error(cfg)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    title = str(body.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'title is required'}), 400
    user_id, host_id = _scope()
    if not chat_store.rename_conversation(user_id, host_id, conversation_id, title):
        return jsonify({'error': 'conversation-not-found'}), 404
    return jsonify({'enabled': True, 'ok': True})


@bp.route('/api/chat/conversations/<conversation_id>', methods=['DELETE'])
def api_chat_delete(conversation_id):
    cfg = _config()
    if not cfg.enabled:
        return _disabled_response()
    err = _ready_or_error(cfg)
    if err:
        return err
    user_id, host_id = _scope()
    if not chat_store.soft_delete_conversation(user_id, host_id, conversation_id):
        return jsonify({'error': 'conversation-not-found'}), 404
    return jsonify({'enabled': True, 'ok': True})


@bp.route('/api/chat/conversations/<conversation_id>/turn', methods=['POST'])
def api_chat_turn(conversation_id):
    """Persist one settled turn. The frontend POSTs the authoritative message
    payloads (Segment[] is a frontend construct, mutated after settle by plan
    decisions — re-POSTed with the same message ids). The server enriches the
    turn with the raw trace from the agents ring (compressed at rest); trace
    JSON never travels over this request.

    Body: {agentId, title?, messages: [{id, role, content, display?,
           segments?, position, traceId?}], traceId?, lastDurationMs?,
           traceExplorerPath?}
    """
    cfg = _config()
    if not cfg.enabled:
        return _disabled_response()
    err = _ready_or_error(cfg)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    agent_id = str(body.get('agentId') or '').strip()
    messages = body.get('messages')
    if not agent_id or not isinstance(messages, list) or not messages:
        return jsonify({'error': 'agentId and a non-empty messages list are required'}), 400

    cleaned = []
    for entry in messages[:_MAX_TURN_MESSAGES]:
        if not isinstance(entry, dict):
            continue
        entry = dict(entry)
        entry['content'] = str(entry.get('content') or '')[:_MAX_CONTENT_CHARS]
        display = entry.get('display')
        entry['display'] = display[:_MAX_CONTENT_CHARS] if isinstance(display, str) else None
        segments = entry.get('segments')
        if not isinstance(segments, list) or \
                len(json.dumps(segments, default=str)) > _MAX_SEGMENTS_BYTES:
            entry['segments'] = []
        cleaned.append(entry)

    user_id, host_id = _scope()
    trace_id = str(body.get('traceId') or '').strip() or None
    trace_explorer_path = body.get('traceExplorerPath')
    result = chat_store.upsert_turn(
        user_id, host_id, conversation_id, agent_id,
        messages=cleaned,
        title=str(body.get('title') or '').strip() or None,
        trace_id=trace_id,
        trace_getter=get_ring_trace,
        last_duration_ms=body.get('lastDurationMs'),
        trace_explorer_path=(trace_explorer_path[:500]
                             if isinstance(trace_explorer_path, str) else None))
    return jsonify({'enabled': True, 'conversation': result})


@bp.route('/api/chat/conversations/<conversation_id>/messages/<message_id>/trace')
def api_chat_message_trace(conversation_id, message_id):
    """Durable trace fallback: the persisted, decompressed dku-trace for one
    message — used by "open trace" once the in-memory ring has rotated."""
    cfg = _config()
    if not cfg.enabled:
        return _disabled_response()
    err = _ready_or_error(cfg)
    if err:
        return err
    user_id, host_id = _scope()
    trace = chat_store.get_message_trace(user_id, host_id, conversation_id, message_id)
    if trace is None:
        return jsonify({'available': False, 'reason': 'trace-not-persisted'}), 404
    return jsonify({'available': True, 'trace': trace})
