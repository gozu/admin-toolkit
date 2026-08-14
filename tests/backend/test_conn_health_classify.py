"""Connection health-test outcome classification: variable-expansion failures
(project-scoped ${projectKey} connections) become skipped-with-reason instead
of 'fail', so they stay out of the Issues table, the health-score cap and the
daily triage; everything else stays a sanitized 'fail'."""

from adk_backend.routes.connections import _classify_conn_test_failure


def test_unknown_dss_variable_is_skipped_with_reason():
    r = _classify_conn_test_failure('SNOW_PROD', 'Snowflake',
                                    'Unknown DSS variable: projectKey')
    assert r['status'] == 'skipped'
    assert 'Unknown DSS variable: projectKey' in r['error']
    assert 'not testable outside a project' in r['error']


def test_real_failure_stays_fail():
    r = _classify_conn_test_failure(
        'SNOW_PROD', 'Snowflake',
        "User 'u1' does not have credentials for connection 'SNOW_PROD'")
    assert r['status'] == 'fail'
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
