"""Story collection — per-host isolation, cursors, version skew, API parsing."""
from datetime import datetime, timedelta, timezone

import pytest

from adk_backend.story import collect


# ── Fakes ──

class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        self.conn.executed.append((' '.join(sql.split()), params))

    def fetchone(self):
        if self.conn.cursor_rows:
            return self.conn.cursor_rows.pop(0)
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeConn:
    def __init__(self, cursor_rows=None):
        self.executed = []
        self.commits = 0
        self.rollbacks = 0
        # queued fetchone() results (for the SELECT cursor_value probes)
        self.cursor_rows = list(cursor_rows or [])

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeListItem:
    def __init__(self, raw):
        self._raw = raw

    def get_raw(self):
        return self._raw


class FakeProject:
    def __init__(self, datasets=(), recipes=(), scenarios=(), models=(), webapps=()):
        self._d, self._r, self._s, self._m, self._w = datasets, recipes, scenarios, models, webapps

    def list_datasets(self):
        return [FakeListItem(x) for x in self._d]

    def list_recipes(self):
        return [FakeListItem(x) for x in self._r]

    def list_scenarios(self):
        return [FakeListItem(x) for x in self._s]

    def list_saved_models(self):
        return [FakeListItem(x) for x in self._m]

    def list_webapps(self):
        return [FakeListItem(x) for x in self._w]


class FakeClient:
    def __init__(self, licensing=None, users=None, projects=None, project_objs=None):
        self._licensing = licensing
        self._users = users or []
        self._projects = projects or []
        self._project_objs = project_objs or {}

    def get_licensing_status(self):
        if isinstance(self._licensing, Exception):
            raise self._licensing
        return self._licensing

    def list_users(self):
        return self._users

    def get_instance_info(self):
        class _Info:
            raw = {'dssVersion': '14.7.0'}
        return _Info()

    def list_projects(self):
        return self._projects

    def get_project(self, key):
        return self._project_objs[key]


LICENSING = {
    'base': {
        'expiresOn': 1790640000000,
        'licenseContent': {
            'licenseKind': 'COMMERCIAL',
            'properties': {
                'maxFullDesigners': '10',
                'maxReaders': 'unlimited',
                'addons.advancedGovern': 'true',
                'emittedBy': 'dataiku',
            },
        },
    },
}

USERS = [
    {'login': 'a', 'enabled': True, 'userProfile': 'FULL_DESIGNER'},
    {'login': 'b', 'enabled': True, 'userProfile': 'FULL_DESIGNER'},
    {'login': 'c', 'enabled': True, 'userProfile': 'READER'},
    {'login': 'd', 'enabled': False, 'userProfile': 'READER'},  # disabled → not counted
    {'login': 'e', 'enabled': True, 'userProfile': 'EXPLORER'},  # used but uncapped
]


def _yesterday():
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime('%Y-%m-%d')


# ── collect_license / collect_inventory parsing ──

class TestCollectLicense:
    def test_parses_caps_usage_and_addons(self):
        data = collect.collect_license(FakeClient(licensing=LICENSING, users=USERS))
        snapshot = data['snapshot']
        assert snapshot['licenseKind'] == 'COMMERCIAL'
        assert snapshot['dssVersion'] == '14.7.0'
        assert snapshot['expiresOn'] == '2026-09-29'  # 1790640000000 ms → UTC date
        assert snapshot['usersTotal'] == 5
        assert '"advancedGovern": "true"' in snapshot['addonsJson']
        caps = {c['profile']: c for c in data['caps']}
        assert caps['FULL_DESIGNER'] == {'profile': 'FULL_DESIGNER', 'cap': 10, 'used': 2}
        assert caps['READER']['cap'] is None  # 'unlimited'
        assert caps['READER']['used'] == 1    # disabled user excluded
        assert caps['EXPLORER'] == {'profile': 'EXPLORER', 'cap': None, 'used': 1}

    def test_profile_limits_shape_wins_when_present(self):
        licensing = {
            'base': {
                'profileLimits': {
                    'DESIGNER': {'licensed': {'profile': 'DESIGNER', 'licensedLimit': 5}},
                },
                'licenseContent': {'licenseKind': 'X', 'properties': {'maxReaders': '3'}},
            },
        }
        data = collect.collect_license(FakeClient(licensing=licensing, users=[]))
        caps = {c['profile']: c for c in data['caps']}
        assert caps == {'DESIGNER': {'profile': 'DESIGNER', 'cap': 5, 'used': 0}}

    def test_empty_payload_raises(self):
        with pytest.raises(RuntimeError):
            collect.collect_license(FakeClient(licensing={}))


class TestCollectInventory:
    def test_counts_and_items(self):
        client = FakeClient(
            projects=[{'projectKey': 'P1', 'name': 'Proj One'}],
            project_objs={'P1': FakeProject(
                datasets=[{'name': 'ds1', 'type': 'PostgreSQL'}],
                recipes=[{'name': 'r1', 'type': 'shaker'}],
                scenarios=[{'id': 'scn1', 'name': 'Scenario 1'}],
                webapps=[{'id': 'w1', 'name': 'App', 'type': 'STANDARD'}],
            )},
        )
        data = collect.collect_inventory(client)
        counts = {(c['projectKey'], c['objectType']): c['count'] for c in data['counts']}
        assert counts[('P1', 'project')] == 1
        assert counts[('P1', 'dataset')] == 1
        assert counts[('P1', 'saved_model')] == 0  # zero counts are explicit
        items = {(i['objectType'], i['objectId']): i for i in data['items']}
        assert items[('dataset', 'ds1')]['subtype'] == 'PostgreSQL'
        assert items[('scenario', 'scn1')]['name'] == 'Scenario 1'
        assert items[('project', 'P1')]['name'] == 'Proj One'


# ── fetch_audit_payload version skew ──

class TestFetchAuditVersionSkew:
    def _macro_host(self, payload):
        class _Macro:
            def run(self, params=None, wait=True):
                return 'run-1'

            def get_result(self, run_id, as_type='json'):
                return payload

        class _Project:
            def get_macro(self, macro_id):
                assert macro_id == collect.AUDIT_MACRO_ID
                return _Macro()

        class _Client:
            def get_project(self, key):
                assert key == collect.MACRO_PROJECT_KEY
                return _Project()

        return {'id': 'remote1', 'client': _Client(), 'isLocal': False}

    def test_matching_versions_accepted(self):
        payload = {'ok': True, 'formatVersion': collect.FORMAT_VERSION,
                   'vocabVersion': collect.VOCAB_VERSION, 'days': {}}
        assert collect.fetch_audit_payload(self._macro_host(payload), None, 14) == payload

    def test_version_skew_rejected(self):
        payload = {'ok': True, 'formatVersion': collect.FORMAT_VERSION + 1,
                   'vocabVersion': collect.VOCAB_VERSION, 'days': {}}
        with pytest.raises(RuntimeError, match='version skew'):
            collect.fetch_audit_payload(self._macro_host(payload), None, 14)

    def test_not_ok_payload_rejected(self):
        with pytest.raises(RuntimeError, match='failed on host'):
            collect.fetch_audit_payload(self._macro_host({'ok': False, 'error': 'boom'}), None, 14)


# ── run_collection isolation + cursors ──

class _Cfg:
    audit_lookback_days = 14
    inventory_items_retention_days = 30


AUDIT_PAYLOAD = {
    'ok': True, 'formatVersion': collect.FORMAT_VERSION, 'vocabVersion': collect.VOCAB_VERSION,
    'days': {
        '2026-06-11': {
            'userActivity': [{'login': 'a', 'projectKey': 'P', 'viewingActions': 2, 'developingActions': 1}],
            'eventCounts': [{'projectKey': 'P', 'msgType': 'dataset-save', 'count': 2}],
        },
    },
}


@pytest.fixture(autouse=True)
def _no_real_execute_values(monkeypatch):
    def _fake_execute_values(cur, sql, values):
        cur.execute(sql, tuple(values))
    monkeypatch.setattr(collect, '_execute_values', _fake_execute_values)


def test_audit_unit_writes_days_and_advances_cursor(monkeypatch):
    monkeypatch.setattr(collect, 'fetch_audit_payload',
                        lambda host, since, lookback, max_files=0: AUDIT_PAYLOAD)
    conn = FakeConn(cursor_rows=[None])  # no stored cursor yet
    host = {'id': 'local', 'client': object(), 'isLocal': True}
    status = collect.run_collection(conn, [host], _Cfg(), ['audit'])
    assert status['ok'] is True
    executed = [sql for sql, _ in conn.executed]
    assert any(sql.startswith('DELETE FROM story.user_activity_daily') for sql in executed)
    assert any(sql.startswith('DELETE FROM story.audit_event_counts') for sql in executed)
    assert any('INSERT INTO story.user_activity_daily' in sql for sql in executed)
    # cursor row: status ok, cursor = yesterday
    ingest = [(sql, params) for sql, params in conn.executed if 'ingest_runs' in sql and sql.startswith('INSERT')]
    assert ingest[-1][1][2] == _yesterday()
    assert ingest[-1][1][3] == 'ok'
    assert conn.commits == 1


def test_audit_cursor_never_moves_backwards(monkeypatch):
    future = '2999-01-01'
    monkeypatch.setattr(collect, 'fetch_audit_payload',
                        lambda host, since, lookback, max_files=0: AUDIT_PAYLOAD)
    conn = FakeConn(cursor_rows=[(future,)])
    host = {'id': 'local', 'client': object(), 'isLocal': True}
    collect.run_collection(conn, [host], _Cfg(), ['audit'])
    ingest = [(sql, params) for sql, params in conn.executed if 'ingest_runs' in sql and sql.startswith('INSERT')]
    assert ingest[-1][1][2] == future


def test_one_failing_host_does_not_block_others(monkeypatch):
    calls = []

    def _fetch(host, since, lookback, max_files=0):
        calls.append(host['id'])
        if host['id'] == 'broken':
            raise RuntimeError('api key rejected')
        return AUDIT_PAYLOAD

    monkeypatch.setattr(collect, 'fetch_audit_payload', _fetch)
    conn = FakeConn(cursor_rows=[None, None])
    hosts = [
        {'id': 'broken', 'client': object(), 'isLocal': False},
        {'id': 'healthy', 'client': object(), 'isLocal': False},
    ]
    status = collect.run_collection(conn, hosts, _Cfg(), ['audit'])
    assert calls == ['broken', 'healthy']
    assert status['ok'] is False and status['failures'] == 1
    by_host = {r['host']: r for r in status['results']}
    assert by_host['broken']['status'] == 'failed'
    assert 'api key rejected' in by_host['broken']['error']
    assert by_host['healthy']['status'] == 'ok'
    # broken unit rolled back, then wrote its failure row (own commit);
    # healthy unit committed its data.
    assert conn.rollbacks >= 1
    failed_rows = [params for sql, params in conn.executed
                   if 'ingest_runs' in sql and params and len(params) > 3 and params[3] == 'failed']
    assert failed_rows and failed_rows[0][0] == 'broken'
    assert failed_rows[0][2] is None  # failed run must not clobber the cursor
    assert conn.commits == 2  # failure row + healthy unit


def test_license_and_inventory_units_commit_per_source():
    client = FakeClient(licensing=LICENSING, users=USERS,
                        projects=[{'projectKey': 'P1', 'name': 'Proj'}],
                        project_objs={'P1': FakeProject()})
    conn = FakeConn()
    host = {'id': 'local', 'client': client, 'isLocal': True}
    status = collect.run_collection(conn, [host], _Cfg(), ['license', 'inventory'])
    assert status['ok'] is True
    assert conn.commits == 2
    executed = [sql for sql, _ in conn.executed]
    assert any('license_snapshots' in sql for sql in executed)
    assert any('license_profile_caps' in sql for sql in executed)
    assert any('object_inventory_daily' in sql for sql in executed)
    assert any('object_inventory_items' in sql and sql.startswith('DELETE') for sql in executed)


def test_unknown_source_raises():
    with pytest.raises(ValueError):
        collect.run_collection(FakeConn(), [], _Cfg(), ['nope'])
