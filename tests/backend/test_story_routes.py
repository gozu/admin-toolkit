"""Story routes — status shapes, provision wiring, SQL parameterization."""
from unittest import mock

import conftest  # noqa: F401

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'webapps', 'admin-toolkit'))

import backend  # noqa: E402
from adk_backend.routes import story as story_routes  # noqa: E402
from db_adapter import StoryConfig  # noqa: E402


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        self.conn.executed.append((' '.join(sql.split()), params))

    def fetchone(self):
        if self.conn.fetchone_queue:
            return self.conn.fetchone_queue.pop(0)
        return None

    @property
    def description(self):
        if self.conn.result_sets:
            return [(col,) for col in self.conn.result_sets[0][0]]
        return []

    def fetchmany(self, n):
        if self.conn.result_sets:
            _cols, rows = self.conn.result_sets.pop(0)
            return rows
        return []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeConn:
    def __init__(self, fetchone_queue=None, result_sets=None):
        self.executed = []
        self.closed = False
        self.fetchone_queue = list(fetchone_queue or [])
        self.result_sets = list(result_sets or [])

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


CONFIGURED = StoryConfig(connection_name='story-pg')


def _get(path, **patches):
    with mock.patch.object(story_routes, 'load_story_config',
                           return_value=patches.pop('cfg', CONFIGURED)), \
            mock.patch.object(story_routes.story_db, 'connect',
                              return_value=patches.pop('conn', FakeConn())):
        return backend.app.test_client().get(path)


def test_status_unconfigured_is_tolerant():
    with mock.patch.object(story_routes, 'load_story_config', return_value=StoryConfig()):
        resp = backend.app.test_client().get('/api/story/status')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['configured'] is False
    assert body['dbOk'] is False
    assert body['schemaVersion'] == 0
    assert body['ingest'] == []
    assert {'exists', 'active', 'triggerHour', 'reporterVerified', 'reporterShape', 'lastRun'} \
        <= set(body['scenario'])
    assert body['hosts'][0]['id'] == 'local'


def test_status_configured_reads_schema_and_ingest_runs():
    conn = FakeConn(
        # get_schema_version: to_regclass hit, then stored version
        fetchone_queue=[('story.schema_meta',), ('1',)],
        result_sets=[(
            ['instance_id', 'source', 'cursor_value', 'last_run_at',
             'last_status', 'last_error', 'last_rows_written'],
            [('local', 'audit', '2026-06-11', None, 'ok', None, 12)],
        )],
    )
    resp = _get('/api/story/status', conn=conn)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['configured'] is True
    assert body['dbOk'] is True
    assert body['schemaVersion'] == 1
    assert body['ingest'][0]['instance_id'] == 'local'
    assert body['ingest'][0]['last_status'] == 'ok'
    assert conn.closed is True


def test_status_db_error_is_reported_not_fatal():
    with mock.patch.object(story_routes, 'load_story_config', return_value=CONFIGURED), \
            mock.patch.object(story_routes.story_db, 'connect',
                              side_effect=RuntimeError('pg down')):
        resp = backend.app.test_client().get('/api/story/status')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['configured'] is True
    assert body['dbOk'] is False
    assert 'pg down' in body['dbError']


def test_provision_wires_config_and_invalidates_cache():
    result = {'ok': True, 'steps': [], 'reporterVerified': True, 'reporterShape': 'primary'}
    with mock.patch.object(story_routes, 'load_story_config', return_value=CONFIGURED), \
            mock.patch.object(story_routes.story_provision, 'provision_all',
                              return_value=result) as provision_all:
        resp = backend.app.test_client().post('/api/story/provision')
    assert resp.status_code == 200
    assert resp.get_json() == result
    assert provision_all.call_count == 1
    assert provision_all.call_args[0][1] == CONFIGURED


def test_run_now_without_scenario_is_409():
    with mock.patch.object(story_routes, '_find_story_scenario', return_value=None):
        resp = backend.app.test_client().post('/api/story/run-now')
    assert resp.status_code == 409
    assert resp.get_json()['error'] == 'story-scenario-missing'


def test_run_now_triggers_scenario():
    class _TriggerFire:
        runId = 'fire-1'

    scenario = mock.Mock()
    scenario.run.return_value = _TriggerFire()
    with mock.patch.object(story_routes, '_find_story_scenario', return_value=scenario):
        resp = backend.app.test_client().post('/api/story/run-now')
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True, 'runId': 'fire-1'}
    scenario.run.assert_called_once_with()


def test_user_activity_is_parameterized_and_clamped():
    conn = FakeConn(result_sets=[(['day'], []), (['day'], [])])
    resp = _get("/api/story/user-activity?days=99999&instance=x%27%3BDROP", conn=conn)
    assert resp.status_code == 200
    assert resp.get_json()['windowDays'] == 365  # clamped
    for sql, params in conn.executed:
        assert "DROP" not in sql  # instance travels as a parameter, never in SQL
        assert params[0] == 365
        assert params[1] == "x';DROP"
    assert conn.closed is True


def test_user_activity_unconfigured_is_400():
    with mock.patch.object(story_routes, 'load_story_config', return_value=StoryConfig()):
        resp = backend.app.test_client().get('/api/story/user-activity')
    assert resp.status_code == 400
    assert resp.get_json()['error'] == 'story-not-configured'


def test_event_counts_applies_taxonomy_at_query_time():
    conn = FakeConn(result_sets=[(
        ['day', 'instance_id', 'msg_type', 'event_count'],
        [('2026-06-11', 'local', 'dataset-save', 4),
         ('2026-06-11', 'local', 'weird-event', 1)],
    )])
    resp = _get('/api/story/event-counts', conn=conn)
    assert resp.status_code == 200
    rows = resp.get_json()['rows']
    assert rows[0]['taxonomy'] == 'Datasets'
    assert rows[1]['taxonomy'] == 'Other'


def test_licenses_and_inventory_shapes():
    conn = FakeConn(result_sets=[
        (['snapshot_date', 'instance_id', 'license_kind'], [('2026-06-11', 'local', 'COMMERCIAL')]),
        (['snapshot_date', 'instance_id', 'profile', 'cap', 'used'],
         [('2026-06-11', 'local', 'READER', None, 3)]),
    ])
    resp = _get('/api/story/licenses', conn=conn)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['latest'][0]['license_kind'] == 'COMMERCIAL'
    assert body['caps'][0]['profile'] == 'READER'

    conn = FakeConn(result_sets=[
        (['snapshot_date', 'instance_id', 'object_type', 'object_count'],
         [('2026-06-11', 'local', 'dataset', 7)]),
        (['snapshot_date', 'instance_id', 'project_key', 'object_type', 'object_count'],
         [('2026-06-11', 'local', 'P1', 'dataset', 7)]),
    ])
    resp = _get('/api/story/inventory', conn=conn)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['trends'][0]['object_count'] == 7
    assert body['latestByProject'][0]['project_key'] == 'P1'
