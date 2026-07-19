"""CRUD facade over the chat persistence tables — Agent Hub UserStore pattern:
every method is scoped (user_id, host_id); nothing crosses users or fleet
hosts. Traces are zlib-compressed JSON blobs (Agent Hub crud/message.py) pulled
server-side from the agents trace ring — trace JSON never travels client→server.
"""

import json
import logging
import zlib
from datetime import datetime, timezone

from adk_backend.chat import db as chat_db

_LOGGER = logging.getLogger(__name__)

_MAX_TITLE_CHARS = 120


def _as_int(value, default=0):
    """Client-supplied numbers arrive as arbitrary JSON — never let a bad one
    500 the whole turn upsert."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _compress(payload):
    try:
        return zlib.compress(json.dumps(payload, default=str).encode('utf-8'))
    except Exception as exc:
        _LOGGER.warning('trace compression failed: %s', exc)
        return None


def _decompress(blob):
    try:
        return json.loads(zlib.decompress(blob).decode('utf-8'))
    except Exception as exc:
        _LOGGER.warning('trace decompression failed: %s', exc)
        return None


def _iso(dt):
    return dt.isoformat() if dt is not None else None


def _conversation_meta(conv):
    return {
        'id': conv.conversation_id,
        'agentId': conv.agent_id,
        'title': conv.title or '',
        'createdAt': _iso(conv.created_at),
        'lastModified': _iso(conv.last_modified),
    }


def _message_row(msg):
    return {
        'id': msg.id,
        'role': msg.role,
        'content': msg.content or '',
        'display': msg.display,
        'segments': msg.segments or [],
        'traceId': msg.trace_id,
        'hasTrace': msg.trace is not None,
        'lastDurationMs': msg.last_duration_ms,
        'createdAt': _iso(msg.created_at),
    }


def _get_conversation_row(session, m, user_id, host_id, conversation_id,
                          include_deleted=False):
    query = session.query(m.Conversation).filter(
        m.Conversation.conversation_id == conversation_id,
        m.Conversation.user_id == user_id,
        m.Conversation.host_id == host_id)
    if not include_deleted:
        query = query.filter(m.Conversation.status == m.StatusEnum.ACTIVE)
    return query.first()


def list_conversations(user_id, host_id):
    """Metadata only (no messages), most recently touched first."""
    m = chat_db.get_models()
    with chat_db.session_scope() as session:
        rows = (session.query(m.Conversation)
                .filter(m.Conversation.user_id == user_id,
                        m.Conversation.host_id == host_id,
                        m.Conversation.status == m.StatusEnum.ACTIVE)
                .order_by(m.Conversation.last_modified.desc())
                .all())
        return [_conversation_meta(conv) for conv in rows]


def get_conversation(user_id, host_id, conversation_id):
    """Full conversation with messages (traces omitted — fetched on demand
    via get_message_trace). None when absent/deleted/foreign."""
    m = chat_db.get_models()
    with chat_db.session_scope() as session:
        conv = _get_conversation_row(session, m, user_id, host_id, conversation_id)
        if conv is None:
            return None
        payload = _conversation_meta(conv)
        payload['traceExplorerPath'] = conv.trace_explorer_path or ''
        payload['messages'] = [_message_row(msg) for msg in conv.messages
                               if msg.status == m.StatusEnum.ACTIVE]
        return payload


def upsert_turn(user_id, host_id, conversation_id, agent_id, messages,
                title=None, trace_id=None, trace_getter=None,
                last_duration_ms=None, trace_explorer_path=None):
    """Persist one settled turn: create the conversation row on first use,
    upsert each message by id (plan decisions mutate segments of existing
    rows), attach the ring trace (compressed) to the message carrying
    trace_id, and touch last_modified for list ordering.

    messages: [{id, role, content, display?, segments?, position}] — the
    authoritative Segment[] is a frontend construct, stored verbatim.
    """
    m = chat_db.get_models()
    trace_payload = None
    if trace_id and callable(trace_getter):
        trace_payload = trace_getter(trace_id)

    with chat_db.session_scope() as session:
        conv = _get_conversation_row(session, m, user_id, host_id, conversation_id,
                                     include_deleted=True)
        if conv is not None and conv.status == m.StatusEnum.DELETED:
            # A turn landing on a deleted conversation resurrects it — the
            # user kept chatting, the data must not silently vanish.
            conv.status = m.StatusEnum.ACTIVE
        if conv is None:
            conv = m.Conversation(
                conversation_id=conversation_id, user_id=user_id,
                host_id=host_id, agent_id=agent_id,
                title=(title or '')[:_MAX_TITLE_CHARS])
            session.add(conv)
        if title and not conv.title:
            conv.title = title[:_MAX_TITLE_CHARS]
        if trace_explorer_path is not None:
            conv.trace_explorer_path = trace_explorer_path

        for entry in messages:
            msg_id = str(entry.get('id') or '')
            role = entry.get('role')
            if not msg_id or role not in ('user', 'assistant'):
                continue
            row = session.get(m.Message, msg_id)
            if row is not None and row.conversation_id != conversation_id:
                continue  # id collision across conversations — never rebind
            if row is None:
                row = m.Message(id=msg_id, conversation_id=conversation_id,
                                role=role, content='')
                session.add(row)
            row.content = str(entry.get('content') or '')
            row.display = entry.get('display')
            row.segments = entry.get('segments') or []
            row.position = _as_int(entry.get('position'), 0)
            if entry.get('traceId'):
                row.trace_id = str(entry['traceId'])
            if trace_payload is not None and row.trace_id == trace_id:
                row.trace = _compress(trace_payload)
            if role == 'assistant' and last_duration_ms is not None:
                row.last_duration_ms = _as_int(last_duration_ms, None)

        # Touch for list ordering (Agent Hub touches the parent the same way).
        conv.last_modified = datetime.now(timezone.utc)
        return {'id': conv.conversation_id, 'title': conv.title or ''}


def rename_conversation(user_id, host_id, conversation_id, title):
    m = chat_db.get_models()
    with chat_db.session_scope() as session:
        conv = _get_conversation_row(session, m, user_id, host_id, conversation_id)
        if conv is None:
            return False
        conv.title = (title or '')[:_MAX_TITLE_CHARS]
        return True


def soft_delete_conversation(user_id, host_id, conversation_id):
    m = chat_db.get_models()
    with chat_db.session_scope() as session:
        conv = _get_conversation_row(session, m, user_id, host_id, conversation_id)
        if conv is None:
            return False
        conv.status = m.StatusEnum.DELETED
        return True


def get_message_trace(user_id, host_id, conversation_id, message_id):
    """Decompressed trace JSON for one persisted message, or None. The durable
    fallback once the in-memory ring has rotated the turn out."""
    m = chat_db.get_models()
    with chat_db.session_scope() as session:
        conv = _get_conversation_row(session, m, user_id, host_id, conversation_id)
        if conv is None:
            return None
        row = session.get(m.Message, message_id)
        if row is None or row.conversation_id != conversation_id or row.trace is None:
            return None
        return _decompress(row.trace)
