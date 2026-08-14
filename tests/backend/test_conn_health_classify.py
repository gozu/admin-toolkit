"""Connection health-test outcome classification and the in-context probe.

Variable-expansion failures (project-scoped ${projectKey} connections) are
marked for a second-chance probe: a trivial SQL statement run through
/sql/queries/ with a using project's context, where the variables expand and
endpoint + credentials get a real exercise. Only when no probe is possible
(non-SQL type, no using project) does the result stay skipped-with-reason —
never 'fail', which would count as broken in the health score and triage."""

import pytest

from adk_backend.routes import connections as conn_routes
from adk_backend.routes.connections import (
    _classify_conn_test_failure,
    _dataset_conn_name,
    _probe_in_context,
)

CTX = ('local', 1)


def _variable_fallback():
    return _classify_conn_test_failure('SNOW_PROD', 'Snowflake',
                                       'Unknown DSS variable: projectKey')


def test_unknown_dss_variable_is_marked_for_context_probe():
    r = _variable_fallback()
    assert r['status'] == 'skipped'
    assert r['contextProbe'] is True
    assert 'Unknown DSS variable: projectKey' in r['error']
    assert 'not testable outside a project' in r['error']


def test_real_failure_stays_fail_without_probe_marker():
    r = _classify_conn_test_failure(
        'SNOW_PROD', 'Snowflake',
        "User 'u1' does not have credentials for connection 'SNOW_PROD'")
    assert r['status'] == 'fail'
    assert 'contextProbe' not in r
    assert 'does not have credentials' in r['error']


def test_empty_message_falls_back_to_generic_fail():
    r = _classify_conn_test_failure('c1', 'Snowflake', '')
    assert r == {'name': 'c1', 'type': 'Snowflake', 'status': 'fail',
                 'error': 'Connection test failed'}


def test_sanitizer_strips_ips_and_paths():
    r = _classify_conn_test_failure(
        'c1', 'PostgreSQL', 'could not connect to 10.1.2.3 at /var/lib/pgsql')
    assert r['status'] == 'fail'
    assert '10.1.2.3' not in r['error']
    assert '/var/lib/pgsql' not in r['error']


# ── _probe_in_context ────────────────────────────────────────────────────────


def test_probe_ok_returns_ok_with_note(monkeypatch):
    monkeypatch.setattr(conn_routes, '_find_context_project', lambda n, c: 'PROJ_A')
    monkeypatch.setattr(conn_routes, '_sql_probe', lambda n, t, pk: None)
    fb = _variable_fallback()
    fb.pop('contextProbe')
    r = _probe_in_context('SNOW_PROD', 'Snowflake', CTX, fb)
    assert r['status'] == 'ok'
    assert 'SELECT 1' in r['note'] and 'PROJ_A' in r['note']


def test_probe_failure_is_a_real_fail_with_context(monkeypatch):
    monkeypatch.setattr(conn_routes, '_find_context_project', lambda n, c: 'PROJ_A')

    def boom(n, t, pk):
        raise RuntimeError('SnowflakeSQLException: incorrect password')

    monkeypatch.setattr(conn_routes, '_sql_probe', boom)
    fb = _variable_fallback()
    fb.pop('contextProbe')
    r = _probe_in_context('SNOW_PROD', 'Snowflake', CTX, fb)
    assert r['status'] == 'fail'
    assert 'incorrect password' in r['error']
    assert 'PROJ_A' in r['error']


def test_probe_without_using_project_keeps_skip_and_says_why(monkeypatch):
    monkeypatch.setattr(conn_routes, '_find_context_project', lambda n, c: None)
    fb = _variable_fallback()
    fb.pop('contextProbe')
    r = _probe_in_context('SNOW_PROD', 'Snowflake', CTX, fb)
    assert r['status'] == 'skipped'
    assert 'no project uses it' in r['error']


def test_probe_non_sql_type_keeps_skip_and_says_why():
    fb = _classify_conn_test_failure('BUCKET', 'EC2', 'Unknown DSS variable: projectKey')
    fb.pop('contextProbe')
    r = _probe_in_context('BUCKET', 'EC2', CTX, fb)
    assert r['status'] == 'skipped'
    assert 'no in-context SQL probe' in r['error']


def test_probe_still_unexpanded_keeps_skip(monkeypatch):
    monkeypatch.setattr(conn_routes, '_find_context_project', lambda n, c: 'PROJ_A')

    def still(n, t, pk):
        raise RuntimeError('Unknown DSS variable: myLocalVar')

    monkeypatch.setattr(conn_routes, '_sql_probe', still)
    fb = _variable_fallback()
    fb.pop('contextProbe')
    r = _probe_in_context('SNOW_PROD', 'Snowflake', CTX, fb)
    assert r['status'] == 'skipped'
    assert 'still unexpanded' in r['error']


def test_oracle_probe_query_uses_dual(monkeypatch):
    monkeypatch.setattr(conn_routes, '_find_context_project', lambda n, c: 'PROJ_A')
    monkeypatch.setattr(conn_routes, '_sql_probe', lambda n, t, pk: None)
    r = _probe_in_context('ORA', 'Oracle', CTX, {'name': 'ORA', 'type': 'Oracle',
                                                 'status': 'skipped', 'error': 'x'})
    assert r['status'] == 'ok'
    assert 'SELECT 1 FROM DUAL' in r['note']


# ── _dataset_conn_name ───────────────────────────────────────────────────────


@pytest.mark.parametrize('params,expected', [
    ({'connection': 'snow1'}, 'snow1'),
    ('{"connection": "snow2"}', 'snow2'),
    ("{'connection': 'snow3'}", 'snow3'),
    ({'path': '/x'}, None),
    ('not parseable', None),
    (None, None),
])
def test_dataset_conn_name(params, expected):
    assert _dataset_conn_name(params) == expected
