"""connection-update policy tests: secret-segment refusal (planner side) and
drift refusal (backend impl side, run against the real impl function)."""

import pytest

from atk_agent_common.actions import connections as conn_actions
from atk_agent_common.errors import ToolkitError
from adk_backend.routes.admin_actions import _impl_connection_update


class FakeClient:
    """ToolkitClient stand-in for the planner path."""

    def __init__(self, definition):
        self.settings = {}
        self.definition = definition

    def get(self, path, host='local', params=None, **kwargs):
        if path == '/api/connections':
            return {'connectionDetails': [{'name': 'snow1', 'type': 'Snowflake'}]}
        if path == '/api/tools/admin-actions/connection-definition':
            return {'ok': True, 'name': params['name'], 'definition': self.definition}
        raise AssertionError('unexpected GET %s' % path)


class FakeDssConnection:
    def __init__(self, definition):
        self.definition = definition
        self.saved = None

    def get_definition(self):
        return self.definition

    def set_definition(self, definition):
        self.saved = definition


class FakeDssClient:
    def __init__(self, definition):
        self.conn = FakeDssConnection(definition)

    def get_connection(self, name):
        return self.conn


# ---- planner: secret-path refusal + expectedCurrent binding ----

@pytest.mark.parametrize('path', [
    'params.password',
    'params.sfPassword',
    'params.credentials.accessToken',
    'params.privateKey',
    'params.apiKeyId',
    'params.keytabPath',
    'params.oauth.clientSecret',
    # cloud-credential key families with no secret/token substring — these
    # escaped the blacklist (and redaction) before the <word>key broadening
    'params.accountKey',
    'params.storageAccountKey',
    'params.accessKey',
    'params.sharedKey',
    'params.passphrase',
])
def test_secret_paths_refused(path):
    client = FakeClient({'params': {'host': 'x'}})
    with pytest.raises(ToolkitError, match='secret-material'):
        conn_actions._plan_connection_update(
            client, 'local', {'name': 'snow1', 'path': path, 'newValue': 'v'}, {})


@pytest.mark.parametrize('key', ['accountKey', 'storageAccountKey', 'accessKey',
                                 'sharedKey', 'passphrase', 'secretKey', 'apiKey'])
def test_redact_secrets_covers_cloud_key_families(key):
    from adk_backend.routes.admin_actions import _redact_secrets
    out = _redact_secrets({'params': {key: 'PLAINTEXT-CRED', 'host': 'h'}})
    assert out['params'][key] == '<redacted>'
    assert out['params']['host'] == 'h'  # non-secret survives


def test_redact_secrets_keeps_property_key_names():
    # bare {key, value} property leaves and keyspace are not credentials
    from adk_backend.routes.admin_actions import _redact_secrets
    out = _redact_secrets({'dkuProperties': [{'key': 'env', 'value': 'prod'}],
                           'params': {'keyspace': 'analytics'}})
    assert out['dkuProperties'][0]['key'] == 'env'
    assert out['params']['keyspace'] == 'analytics'


def test_plan_binds_expected_current():
    client = FakeClient({'params': {'host': ''}})
    canonical, plan = conn_actions._plan_connection_update(
        client, 'local',
        {'name': 'snow1', 'path': 'params.host', 'newValue': 'acct.snowflake.com'}, {})
    assert canonical == {'name': 'snow1', 'path': 'params.host',
                         'newValue': 'acct.snowflake.com', 'expectedCurrent': ''}
    assert plan['currentValue'] == ''
    assert plan['proposedValue'] == 'acct.snowflake.com'


def test_plan_garbage_path_refused():
    client = FakeClient({'params': {}})
    with pytest.raises(ToolkitError, match='invalid path'):
        conn_actions._plan_connection_update(
            client, 'local', {'name': 'snow1', 'path': 'params..x', 'newValue': 1}, {})


# ---- backend impl: drift + re-checked blacklist + write ----

def test_impl_applies_when_current_matches():
    dss = FakeDssClient({'params': {'host': ''}})
    result = _impl_connection_update(dss, {
        'name': 'snow1', 'path': 'params.host',
        'newValue': 'acct.snowflake.com', 'expectedCurrent': ''})
    assert result['ok'] is True
    assert result['before'] == '' and result['after'] == 'acct.snowflake.com'
    assert dss.conn.saved['params']['host'] == 'acct.snowflake.com'


def test_impl_refuses_on_drift():
    dss = FakeDssClient({'params': {'host': 'someone-changed-it'}})
    result = _impl_connection_update(dss, {
        'name': 'snow1', 'path': 'params.host',
        'newValue': 'acct.snowflake.com', 'expectedCurrent': ''})
    assert result['ok'] is False
    assert 'drifted' in result['error']
    assert dss.conn.saved is None


def test_impl_rechecks_secret_blacklist():
    dss = FakeDssClient({'params': {'password': 'hunter2'}})
    result = _impl_connection_update(dss, {
        'name': 'snow1', 'path': 'params.password',
        'newValue': 'x', 'expectedCurrent': 'hunter2'})
    assert result['ok'] is False
    assert 'blocked' in result['error']
    assert dss.conn.saved is None


def test_connection_test_failing_probe_is_still_ok():
    """A test that RUNS but reports connectionOK=false is a successful action
    with a negative result — mapping it to ok:False made the route 409 and the
    executor report 'backend-error' for every broken connection (the exact
    population the action exists to probe)."""
    from adk_backend.routes import admin_actions

    class _Conn:
        def test(self):
            return {'connectionOK': False, 'connectionErrorMessage': 'Host should not be left blank'}

    class _Client:
        def get_connection(self, name):
            return _Conn()

    out = admin_actions._impl_connection_test(_Client(), {'name': 'fake'})
    assert out['ok'] is True
    assert out['connectionOK'] is False
