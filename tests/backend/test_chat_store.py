"""Chat persistence storage layer: SQLite create_all, CRUD, JSON/zlib
roundtrips, (user, host) scoping, prefix validation, disabled default."""

import json
import zlib

import pytest

from adk_backend.chat import db as chat_db
from db_adapter import ChatPersistenceConfig, load_chat_persistence_config

USER = 'u.alice'
HOST = 'local'
CONV = 'conv-0001'
AGENT = 'agent_admin-toolkit_ops-actuator'

SEGMENTS = [
    {'type': 'text', 'text': 'hello **world**'},
    {'type': 'plan', 'plan': {'action': 'db-vacuum', 'confirmToken': 'tok-1'}},
]
TRACE = {'trace': {'inputs': {'messages': [{'role': 'user', 'text': 'hi'}]},
                   'outputs': {'text': 'done'}, 'children': []}}


def _turn_messages():
    return [
        {'id': 'm-user-1', 'role': 'user', 'content': 'do the thing',
         'display': 'do the thing (pretty)', 'segments': [], 'position': 0},
        {'id': 'm-asst-1', 'role': 'assistant', 'content': 'planning',
         'segments': SEGMENTS, 'position': 1, 'traceId': 'trace-abc'},
    ]


@pytest.fixture(scope='module')
def store(tmp_path_factory):
    """One SQLite-backed store for the module (models bind the table prefix
    at import time, so init happens once)."""
    db_file = tmp_path_factory.mktemp('chatdb') / 'atk_chat_test.db'
    chat_db.reset_for_tests()
    original = chat_db._sqlite_db_path
    chat_db._sqlite_db_path = lambda: str(db_file)
    try:
        chat_db.ensure_ready(ChatPersistenceConfig(mode='LOCAL'))
        from adk_backend.chat import store as chat_store
        yield chat_store
    finally:
        chat_db._sqlite_db_path = original
        chat_db.reset_for_tests()


def test_upsert_turn_creates_conversation_and_messages(store):
    result = store.upsert_turn(
        USER, HOST, CONV, AGENT, messages=_turn_messages(),
        title='do the thing', trace_id='trace-abc',
        trace_getter=lambda tid: TRACE if tid == 'trace-abc' else None,
        last_duration_ms=1234, trace_explorer_path='/projects/ADMINTOOLKIT/webapps/x/view')
    assert result == {'id': CONV, 'title': 'do the thing'}

    conv = store.get_conversation(USER, HOST, CONV)
    assert conv is not None
    assert conv['agentId'] == AGENT
    assert conv['traceExplorerPath'] == '/projects/ADMINTOOLKIT/webapps/x/view'
    assert [m['id'] for m in conv['messages']] == ['m-user-1', 'm-asst-1']
    asst = conv['messages'][1]
    assert asst['segments'] == SEGMENTS  # JsonEncoded roundtrip
    assert asst['hasTrace'] is True
    assert asst['lastDurationMs'] == 1234
    assert conv['messages'][0]['display'] == 'do the thing (pretty)'


def test_trace_zlib_roundtrip(store):
    trace = store.get_message_trace(USER, HOST, CONV, 'm-asst-1')
    assert trace == TRACE
    # And the blob really is zlib-compressed JSON at rest.
    m = chat_db.get_models()
    with chat_db.session_scope() as session:
        row = session.get(m.Message, 'm-asst-1')
        assert json.loads(zlib.decompress(row.trace).decode('utf-8')) == TRACE


def test_reupsert_updates_segments_in_place(store):
    """Plan decisions mutate segments after settle; re-POST with the same id
    must update, not duplicate."""
    decided = [dict(SEGMENTS[0]),
               {'type': 'plan', 'plan': {'action': 'db-vacuum',
                                         'confirmToken': 'tok-1',
                                         'decision': 'approved'}}]
    store.upsert_turn(USER, HOST, CONV, AGENT, messages=[
        {'id': 'm-asst-1', 'role': 'assistant', 'content': 'planning',
         'segments': decided, 'position': 1}])
    conv = store.get_conversation(USER, HOST, CONV)
    assert len(conv['messages']) == 2
    assert conv['messages'][1]['segments'][1]['plan']['decision'] == 'approved'
    # The trace attached earlier must survive a segments-only re-upsert.
    assert conv['messages'][1]['hasTrace'] is True


def test_user_and_host_scoping(store):
    assert store.get_conversation('u.bob', HOST, CONV) is None
    assert store.get_conversation(USER, 'remote-1', CONV) is None
    assert store.list_conversations('u.bob', HOST) == []
    assert store.list_conversations(USER, 'remote-1') == []
    assert store.get_message_trace('u.bob', HOST, CONV, 'm-asst-1') is None
    assert store.rename_conversation('u.bob', HOST, CONV, 'stolen') is False
    assert store.soft_delete_conversation('u.bob', HOST, CONV) is False
    # Same conversation id on another host is a separate row space.
    store.upsert_turn(USER, 'remote-1', 'conv-remote', AGENT, messages=[
        {'id': 'm-r-1', 'role': 'user', 'content': 'remote hello', 'position': 0}])
    assert [c['id'] for c in store.list_conversations(USER, 'remote-1')] == ['conv-remote']
    assert [c['id'] for c in store.list_conversations(USER, HOST)] == [CONV]


def test_list_orders_by_last_modified(store):
    store.upsert_turn(USER, HOST, 'conv-0002', AGENT, messages=[
        {'id': 'm-c2-1', 'role': 'user', 'content': 'second conv', 'position': 0}],
        title='second')
    rows = store.list_conversations(USER, HOST)
    assert [c['id'] for c in rows] == ['conv-0002', CONV]
    # Touching the first conversation reorders it to the top.
    store.upsert_turn(USER, HOST, CONV, AGENT, messages=[
        {'id': 'm-user-2', 'role': 'user', 'content': 'again', 'position': 2}])
    rows = store.list_conversations(USER, HOST)
    assert [c['id'] for c in rows] == [CONV, 'conv-0002']


def test_rename_and_soft_delete_and_resurrect(store):
    assert store.rename_conversation(USER, HOST, 'conv-0002', 'renamed!') is True
    assert any(c['title'] == 'renamed!' for c in store.list_conversations(USER, HOST))

    assert store.soft_delete_conversation(USER, HOST, 'conv-0002') is True
    assert [c['id'] for c in store.list_conversations(USER, HOST)] == [CONV]
    assert store.get_conversation(USER, HOST, 'conv-0002') is None

    # A new turn on a deleted conversation resurrects it (user kept chatting).
    store.upsert_turn(USER, HOST, 'conv-0002', AGENT, messages=[
        {'id': 'm-c2-2', 'role': 'user', 'content': 'still here', 'position': 1}])
    assert 'conv-0002' in [c['id'] for c in store.list_conversations(USER, HOST)]


def test_message_id_never_rebinds_across_conversations(store):
    """Upserting an id that already belongs to another conversation must not
    steal or mutate the original row."""
    store.upsert_turn(USER, HOST, 'conv-0003', AGENT, messages=[
        {'id': 'm-asst-1', 'role': 'assistant', 'content': 'HIJACK',
         'segments': [], 'position': 0}])
    original = store.get_conversation(USER, HOST, CONV)
    assert original['messages'][1]['content'] == 'planning'
    thief = store.get_conversation(USER, HOST, 'conv-0003')
    assert (thief or {}).get('messages', []) == []


def test_invalid_roles_and_missing_ids_are_skipped(store):
    store.upsert_turn(USER, HOST, 'conv-0004', AGENT, messages=[
        {'id': '', 'role': 'user', 'content': 'no id', 'position': 0},
        {'id': 'm-sys', 'role': 'system', 'content': 'bad role', 'position': 1},
        {'id': 'm-ok', 'role': 'user', 'content': 'fine', 'position': 2}])
    conv = store.get_conversation(USER, HOST, 'conv-0004')
    assert [m['id'] for m in conv['messages']] == ['m-ok']


def test_upsert_turn_tolerates_non_numeric_client_fields(store):
    """position/lastDurationMs arrive from a public, unauthenticated POST, so a
    bad value must default — never raise and 500 the whole turn upsert."""
    result = store.upsert_turn(USER, HOST, 'conv-0005', AGENT, messages=[
        {'id': 'm-bad-pos', 'role': 'user', 'content': 'x', 'position': 'abc'},
        {'id': 'm-bad-pos2', 'role': 'assistant', 'content': 'y', 'position': {}}],
        last_duration_ms='not-a-number')
    assert result['id'] == 'conv-0005'
    conv = store.get_conversation(USER, HOST, 'conv-0005')
    ids = {m['id'] for m in conv['messages']}
    assert {'m-bad-pos', 'm-bad-pos2'} <= ids
    asst = next(m for m in conv['messages'] if m['id'] == 'm-bad-pos2')
    assert asst['lastDurationMs'] is None  # bad duration coerced to None, not 500


def test_tables_prefix_validation():
    assert chat_db.normalize_tables_prefix('') == 'atk_chat_'
    assert chat_db.normalize_tables_prefix('MyChat') == 'mychat_'
    assert chat_db.normalize_tables_prefix('chat_v2_') == 'chat_v2_'
    with pytest.raises(chat_db.ChatPersistenceError):
        chat_db.normalize_tables_prefix('bad;drop--')
    with pytest.raises(chat_db.ChatPersistenceError):
        chat_db.normalize_tables_prefix('1leading_digit')


def test_remote_url_builders():
    assert chat_db.get_remote_db_url('postgresql', {
        'user': 'u', 'password': 'p@ss', 'host': 'db', 'port': 5432, 'db': 'dss',
    }) == 'postgresql://u:p%40ss@db:5432/dss'
    assert chat_db.get_remote_db_url('sqlserver', {
        'user': 'u', 'password': 'p', 'host': 'db', 'port': 1433, 'db': 'dss',
    }) == 'mssql+pymssql://u:p@db:1433/dss?charset=utf8'
    with pytest.raises(chat_db.ChatPersistenceError):
        chat_db.get_remote_db_url('oracle', {})


def test_remote_url_dbname_fallback():
    """DSS connections may carry the database name under database/dbname rather
    than db — same fallback agents_db uses, so chat and audit resolve to the
    same DB instead of the URL landing on the server default."""
    assert chat_db.get_remote_db_url('postgresql', {
        'user': 'u', 'password': 'p', 'host': 'db', 'port': 5432, 'database': 'dss',
    }) == 'postgresql://u:p@db:5432/dss'
    assert chat_db.get_remote_db_url('postgresql', {
        'user': 'u', 'password': 'p', 'host': 'db', 'port': 5432, 'dbname': 'dss',
    }) == 'postgresql://u:p@db:5432/dss'


def test_config_defaults_to_disabled():
    """The stubbed dataiku client has no plugin settings — config loading must
    degrade to the OFF no-op, never raise."""
    cfg = load_chat_persistence_config()
    assert cfg.mode == 'OFF'
    assert cfg.enabled is False
    assert ChatPersistenceConfig().enabled is False
    assert ChatPersistenceConfig(mode='LOCAL').enabled is True
