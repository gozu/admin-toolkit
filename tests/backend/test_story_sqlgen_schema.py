"""Story DDL + SQL builders — idempotency, migrations, PK targeting, parameterization."""
import re

import pytest

from adk_backend.story import schema, sqlgen


class TestSchema:
    def test_all_ddl_is_if_not_exists(self):
        for stmt in schema.DDL_V1:
            norm = ' '.join(stmt.split()).upper()
            assert 'IF NOT EXISTS' in norm, stmt

    def test_migrations_strictly_increasing_and_above_v1(self):
        versions = [v for v, _stmts in schema.MIGRATIONS]
        assert versions == sorted(versions)
        assert len(versions) == len(set(versions))
        assert all(v > 1 for v in versions)

    def test_schema_version_matches_latest_migration(self):
        latest = max([1] + [v for v, _stmts in schema.MIGRATIONS])
        assert schema.SCHEMA_VERSION == latest

    def test_expected_tables_present(self):
        joined = ' '.join(' '.join(s.split()) for s in schema.DDL_V1)
        for table in ('story.schema_meta', 'story.ingest_runs',
                      'story.user_activity_daily', 'story.audit_event_counts',
                      'story.license_snapshots', 'story.license_profile_caps',
                      'story.object_inventory_daily', 'story.object_inventory_items'):
            assert 'CREATE TABLE IF NOT EXISTS %s' % table in joined


class _FakeCursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, params=None):
        self.log.append((' '.join(sql.split()), params))

    def fetchone(self):
        # to_regclass probe → pretend the schema does not exist yet
        return (None,)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeConn:
    def __init__(self):
        self.log = []
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return _FakeCursor(self.log)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def test_ensure_schema_runs_all_ddl_and_stamps_version():
    conn = _FakeConn()
    version = schema.ensure_schema(conn)
    assert version == schema.SCHEMA_VERSION
    assert conn.committed is True
    executed = [sql for sql, _params in conn.log]
    assert sum(1 for sql in executed if sql.startswith('CREATE')) == len(schema.DDL_V1)
    # version stamp is parameterized
    stamp = [(sql, params) for sql, params in conn.log if 'schema_meta' in sql and sql.startswith('INSERT')]
    assert stamp and stamp[0][1] == ('schema_version', str(schema.SCHEMA_VERSION))


def test_ensure_schema_rolls_back_on_error():
    conn = _FakeConn()

    class _Boom(_FakeCursor):
        def execute(self, sql, params=None):
            raise RuntimeError('ddl failed')

    conn.cursor = lambda: _Boom(conn.log)
    with pytest.raises(RuntimeError):
        schema.ensure_schema(conn)
    assert conn.rolled_back is True
    assert conn.committed is False


_ROWS_ACTIVITY = [
    {'login': 'alice', 'projectKey': 'P1', 'viewingActions': 5, 'developingActions': 2},
    {'login': 'bob', 'projectKey': '', 'viewingActions': 1, 'developingActions': 0},
]


class TestSqlgen:
    def test_user_activity_upsert_targets_full_pk(self):
        sql, values = sqlgen.user_activity_upsert(_ROWS_ACTIVITY, '2026-06-11', 'inst1')
        assert 'ON CONFLICT (day, instance_id, login, project_key)' in sql
        assert sql.count('%s') == 1  # execute_values placeholder only
        assert values[0] == ('2026-06-11', 'inst1', 'alice', 'P1', 5, 2)

    def test_event_counts_upsert(self):
        sql, values = sqlgen.event_counts_upsert(
            [{'projectKey': 'P1', 'msgType': 'flow-read', 'count': 3}], '2026-06-11', 'inst1')
        assert 'ON CONFLICT (day, instance_id, project_key, msg_type)' in sql
        assert values == [('2026-06-11', 'inst1', 'P1', 'flow-read', 3)]

    def test_license_snapshot_upsert(self):
        sql, values = sqlgen.license_snapshot_upsert(
            {'dssVersion': '13.1', 'licenseKind': 'COMMERCIAL', 'expiresOn': '2027-01-01',
             'usersTotal': 42, 'addonsJson': '{}', 'rawJson': '{}'},
            '2026-06-11', 'inst1')
        assert 'ON CONFLICT (snapshot_date, instance_id)' in sql
        assert values[0][:2] == ('2026-06-11', 'inst1')

    def test_license_caps_upsert_allows_null_cap(self):
        sql, values = sqlgen.license_caps_upsert(
            [{'profile': 'READER', 'cap': None, 'used': 7}], '2026-06-11', 'inst1')
        assert 'ON CONFLICT (snapshot_date, instance_id, profile)' in sql
        assert values == [('2026-06-11', 'inst1', 'READER', None, 7)]

    def test_inventory_upserts(self):
        sql, values = sqlgen.inventory_counts_upsert(
            [{'projectKey': 'P1', 'objectType': 'dataset', 'count': 9}], '2026-06-11', 'inst1')
        assert 'ON CONFLICT (snapshot_date, instance_id, project_key, object_type)' in sql
        assert values == [('2026-06-11', 'inst1', 'P1', 'dataset', 9)]

        sql, values = sqlgen.inventory_items_upsert(
            [{'projectKey': 'P1', 'objectType': 'dataset', 'objectId': 'ds1',
              'name': 'DS One', 'subtype': 'PostgreSQL'}], '2026-06-11', 'inst1')
        assert 'ON CONFLICT (snapshot_date, instance_id, project_key, object_type, object_id)' in sql
        assert values[0][-2:] == ('DS One', 'PostgreSQL')

    def test_delete_day_sql_only_for_day_tables(self):
        sql, _cols = sqlgen.delete_day_sql('story.user_activity_daily')
        assert sql == 'DELETE FROM story.user_activity_daily WHERE day = %s AND instance_id = %s'
        with pytest.raises(ValueError):
            sqlgen.delete_day_sql('story.license_snapshots')

    def test_ingest_run_upsert_parameterized_and_source_checked(self):
        sql, params = sqlgen.ingest_run_upsert(
            'inst1', 'audit', 'ok', cursor_value='2026-06-10', rows_written=12)
        assert 'ON CONFLICT (instance_id, source)' in sql
        assert params == ('inst1', 'audit', '2026-06-10', 'ok', None, 12)
        with pytest.raises(ValueError):
            sqlgen.ingest_run_upsert('inst1', 'nope', 'ok')

    def test_failed_run_keeps_previous_cursor(self):
        # COALESCE(EXCLUDED.cursor_value, existing) — a failed run passes NULL
        # and must not clobber the stored cursor.
        sql, params = sqlgen.ingest_run_upsert('inst1', 'audit', 'failed', error=RuntimeError('x'))
        assert 'COALESCE(EXCLUDED.cursor_value, story.ingest_runs.cursor_value)' in sql
        assert params[2] is None and params[4] == 'x'

    def test_no_value_interpolation_anywhere(self):
        # Builders must never format values into SQL: only identifiers (from
        # our own constants) and %s placeholders may appear.
        sql, _ = sqlgen.user_activity_upsert(_ROWS_ACTIVITY, '2026-06-11', 'inst1')
        assert 'alice' not in sql and '2026-06-11' not in sql
        sql, _ = sqlgen.ingest_run_upsert('inst1', 'audit', 'ok')
        assert 'inst1' not in sql

    def test_prune_sql_is_parameterized(self):
        sql = sqlgen.inventory_items_prune_sql()
        assert '%s' in sql and 'object_inventory_items' in sql
