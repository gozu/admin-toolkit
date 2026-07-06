"""Chat persistence ORM models — Agent Hub pattern (models.py of agent-hub
v1.4.2), reduced to the two tables this webapp has concepts for.

IMPORT ORDER CONTRACT: this module reads TABLES_PREFIX / DB_SCHEMA from the
environment at import time (Agent Hub-verbatim), so it must only be imported
through chat/db.py, which sets both before the first import. `db` is a bare
declarative registry — never init_app'd; engine/session live in chat/db.py so
sessions work inside SSE generators and worker threads.
"""

import enum
import json
import os
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from sqlalchemy.types import Text, TypeDecorator

db = SQLAlchemy()


class JsonEncoded(TypeDecorator):
    """JSON stored as TEXT — DB-agnostic (SQLite and SQL engines without
    native JSON), same as Agent Hub's JsonEncoded."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return json.loads(value)


def string_enum(enum_cls, **kwargs):
    return db.Enum(enum_cls, native_enum=False,
                   values_callable=lambda obj: [e.value for e in obj], **kwargs)


class TimestampMixin:
    created_at = db.Column(db.DateTime(timezone=True),
                           default=lambda: datetime.now(timezone.utc), nullable=False)
    last_modified = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
        server_default=func.now(),
    )


class StatusEnum(enum.Enum):
    ACTIVE = 'active'
    DELETED = 'deleted'


tables_prefix = os.environ.get('TABLES_PREFIX', '') or ''

fk_prefix = ''  # needed for foreign key in case of schema usage
if os.environ.get('DB_SCHEMA', None):
    args = {'schema': os.environ['DB_SCHEMA']}
    fk_prefix = '%s.' % os.environ['DB_SCHEMA']
else:
    args = {}
fk_prefix = '%s%s' % (fk_prefix, tables_prefix)


class Conversation(db.Model, TimestampMixin):
    __tablename__ = '%sconversations' % tables_prefix

    conversation_id = db.Column(db.String, primary_key=True)  # client-minted uuid4
    user_id = db.Column(db.String, nullable=False)
    # Multi-instance dimension: which fleet host the chat happened against.
    # Conversations from host A must never surface on host B.
    host_id = db.Column(db.String, nullable=False, default='local')
    agent_id = db.Column(db.String, nullable=False)
    title = db.Column(db.Text, default='')
    trace_explorer_path = db.Column(db.String, default='')
    status = db.Column(string_enum(StatusEnum), default=StatusEnum.ACTIVE)

    messages = db.relationship(
        'Message', backref='conversation', lazy=True,
        cascade='all, delete-orphan', order_by='Message.position')

    __table_args__ = (
        db.Index('idx_%sconv_user_host_status' % tables_prefix,
                 'user_id', 'host_id', 'status'),
        args,
    )


class Message(db.Model):
    __tablename__ = '%smessages' % tables_prefix

    id = db.Column(db.String, primary_key=True)  # client-minted uuid
    conversation_id = db.Column(
        db.String, db.ForeignKey('%sconversations.conversation_id' % fk_prefix),
        nullable=False)
    role = db.Column(db.String, nullable=False)  # user|assistant
    content = db.Column(db.Text, nullable=False)  # model-facing text
    display = db.Column(db.Text)  # human-facing override (approval/handoff turns)
    segments = db.Column(JsonEncoded, default=list)  # frontend Segment[]
    # Deterministic transcript order: the message's index in the conversation
    # (created_at ties are possible inside one POSTed turn).
    position = db.Column(db.Integer, nullable=False, default=0)
    trace_id = db.Column(db.String)  # native dku-trace ring id for this turn
    trace = db.Column(db.LargeBinary)  # zlib-compressed trace JSON
    last_duration_ms = db.Column(db.Integer)
    created_at = db.Column(db.DateTime(timezone=True),
                           default=lambda: datetime.now(timezone.utc), nullable=False)
    status = db.Column(string_enum(StatusEnum), default=StatusEnum.ACTIVE)

    __table_args__ = (
        db.Index('idx_%smsg_conv_position' % tables_prefix,
                 'conversation_id', 'position'),
        args,
    )
