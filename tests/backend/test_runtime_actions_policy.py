"""Phase-2 runtime/user action policies: self-lockout guards, protected
variables, drift refusals — the safety floor below the model."""

import json

import pytest

from atk_agent_common.actions import runtime, users
from atk_agent_common.errors import ToolkitError
from adk_backend.routes import admin_actions


class FakeToolkitClient:
    """Minimal ToolkitClient stand-in: canned GET payloads by path."""

    def __init__(self, gets):
        self.gets = gets
        self.posts = []

    def get(self, path, host=None, params=None, **kw):
        key = (path, json.dumps(params or {}, sort_keys=True))
        if key in self.gets:
            return self.gets[key]
        return self.gets[path]

    def post(self, path, host=None, red=False, json=None, **kw):
        self.posts.append((path, json))
        return {'ok': True}


def _inv(domain, payload, **params):
    q = {'domain': domain}
    q.update({k: str(v) for k, v in params.items()})
    return {('/api/tools/admin-actions/inventory', json.dumps(q, sort_keys=True)): payload}


# ── variables-set protections ────────────────────────────────────────────────

def test_variables_set_refuses_finding_whitelist():
    client = FakeToolkitClient({})
    with pytest.raises(ToolkitError) as err:
        runtime._plan_variables_set(client, 'local',
                                    {'path': 'admin_toolkit_finding_whitelist',
                                     'newValue': []}, {})
    assert 'protected' in str(err.value)


def test_variables_set_refuses_nested_whitelist_path():
    client = FakeToolkitClient({})
    with pytest.raises(ToolkitError):
        runtime._plan_variables_set(client, 'local',
                                    {'path': 'admin_toolkit_finding_whitelist.entries',
                                     'newValue': 'x'}, {})


def test_variables_set_refuses_secret_path():
    client = FakeToolkitClient({})
    with pytest.raises(ToolkitError):
        runtime._plan_variables_set(client, 'local',
                                    {'path': 'myapp.api_token', 'newValue': 'x'}, {})


def test_impl_variables_set_backend_refusals():
    out = admin_actions._impl_variables_set(None, {
        'path': 'admin_toolkit_finding_whitelist', 'newValue': []})
    assert out['ok'] is False and 'protected' in out['error']
    out = admin_actions._impl_variables_set(None, {
        'path': 'service.password', 'newValue': 'x'})
    assert out['ok'] is False and 'blocked' in out['error']


# ── user-disable self-lockout ────────────────────────────────────────────────

def test_user_disable_refuses_own_identity():
    client = FakeToolkitClient(_inv('users', {
        'users': [{'login': 'toolkit-svc', 'displayName': 'svc', 'enabled': True,
                   'groups': ['administrators']}],
        'callerIdentity': 'toolkit-svc'}))
    with pytest.raises(ToolkitError) as err:
        users._plan_user_disable(client, 'local', {'login': 'toolkit-svc'}, {})
    assert 'self-lockout' in str(err.value)


def test_user_disable_warns_on_admin_group():
    client = FakeToolkitClient(_inv('users', {
        'users': [{'login': 'bob', 'displayName': 'Bob', 'enabled': True,
                   'groups': ['administrators']}],
        'callerIdentity': 'toolkit-svc'}))
    canonical, plan = users._plan_user_disable(client, 'local', {'login': 'bob'}, {})
    assert canonical == {'login': 'bob', 'enabled': False, 'expectedCurrent': True}
    assert any('administrators' in w for w in plan['warnings'])


def test_impl_user_set_enabled_self_lockout():
    class _Client:
        def get_auth_info(self):
            return {'authIdentifier': 'svc'}

    out = admin_actions._impl_user_set_enabled(_Client(), {
        'login': 'svc', 'enabled': False, 'expectedCurrent': True})
    assert out['ok'] is False and 'self-lockout' in out['error']


# ── api-key-delete guards ────────────────────────────────────────────────────

def test_api_key_delete_refuses_own_personal_key():
    client = FakeToolkitClient(_inv('api-keys', {
        'personal': [{'id': 'K1', 'user': 'svc', 'label': 'toolkit'}],
        'global': [], 'callerIdentity': 'svc'}))
    with pytest.raises(ToolkitError) as err:
        users._plan_api_key_delete(client, 'local',
                                   {'keyType': 'personal', 'keyId': 'K1'}, {})
    assert 'self-lockout' in str(err.value)


def test_api_key_delete_global_is_irreversible_with_warning():
    client = FakeToolkitClient(_inv('api-keys', {
        'personal': [], 'global': [{'id': 'G1', 'label': 'old key'}],
        'callerIdentity': 'svc'}))
    canonical, plan = users._plan_api_key_delete(
        client, 'local', {'keyType': 'global', 'keyId': 'G1'}, {})
    assert canonical == {'keyType': 'global', 'keyId': 'G1'}
    assert plan['irreversible'] is True
    assert any('IRREVERSIBLE' in w for w in plan['warnings'])
    assert any('CANNOT verify' in w for w in plan['warnings'])


# ── scenario toggle drift guard ──────────────────────────────────────────────

def test_scenario_disable_binds_current_state():
    client = FakeToolkitClient(_inv('scenarios', {
        'scenarios': [{'id': 'S1', 'name': 'nightly', 'active': True,
                       'running': False}]}, projectKey='P1'))
    canonical, plan = runtime._plan_scenario_disable(
        client, 'local', {'projectKey': 'P1', 'scenarioId': 'S1'}, {})
    assert canonical['expectedCurrent'] is True
    assert canonical['active'] is False
    assert plan['warnings'] is None


def test_scenario_disable_noop_warns():
    client = FakeToolkitClient(_inv('scenarios', {
        'scenarios': [{'id': 'S1', 'name': 'nightly', 'active': False,
                       'running': False}]}, projectKey='P1'))
    _, plan = runtime._plan_scenario_disable(
        client, 'local', {'projectKey': 'P1', 'scenarioId': 'S1'}, {})
    assert any('already' in w for w in plan['warnings'])


def test_impl_scenario_set_active_drift_refusal():
    class _Settings:
        active = True

        def save(self):
            raise AssertionError('must not save on drift')

    class _Scenario:
        def get_settings(self):
            return _Settings()

    class _Project:
        def get_scenario(self, sid):
            return _Scenario()

    class _Client:
        def get_project(self, pk):
            return _Project()

    out = admin_actions._impl_scenario_set_active(_Client(), {
        'projectKey': 'P1', 'scenarioId': 'S1', 'active': False,
        'expectedCurrent': False})  # planner saw False, live is True → drift
    assert out['ok'] is False and 'drifted' in out['error']


# ── cluster-stop terminate irreversibility ───────────────────────────────────

def test_cluster_stop_terminate_marks_irreversible():
    from atk_agent_common.actions import clusters
    client = FakeToolkitClient({'/api/k8s-insights/clusters': {
        'clusters': [{'id': 'c1', 'name': 'c1', 'state': 'RUNNING',
                      'type': 'pycluster_eks-clusters_create-eks-cluster'}],
        'unavailable': []}})
    canonical, plan = clusters._plan_cluster_stop(
        client, 'local', {'clusterId': 'c1', 'terminate': True}, {})
    assert canonical == {'clusterId': 'c1', 'terminate': True}
    assert plan['irreversible'] is True
    assert any('IRREVERSIBLE' in w for w in plan['warnings'])


def test_cluster_stop_refuses_manual_attachment():
    from atk_agent_common.actions import clusters
    client = FakeToolkitClient({'/api/k8s-insights/clusters': {
        'clusters': [], 'unavailable': [{'id': 'm1', 'state': None, 'type': 'manual'}]}})
    with pytest.raises(ToolkitError):
        clusters._plan_cluster_stop(client, 'local', {'clusterId': 'm1'}, {})


def test_cluster_row_finds_unavailable_clusters():
    """Detach candidates live in the `unavailable` list — the Phase-1 lookup
    only searched `clusters`, so planning against exactly the stale
    attachments the action exists for failed."""
    from atk_agent_common.actions import clusters
    client = FakeToolkitClient({'/api/k8s-insights/clusters': {
        'clusters': [], 'unavailable': [{'id': 'stale', 'state': 'NONE',
                                         'type': 'pycluster_eks-clusters_attach-eks-cluster'}]}})
    row = clusters._cluster_row(client, 'local', 'stale')
    assert row['id'] == 'stale'


def test_project_row_accepts_backend_key_field():
    """/api/projects rows carry 'key', not 'projectKey' — the export planner
    must find them (akaos live catch)."""
    from atk_agent_common.actions import projects_domain
    client = FakeToolkitClient({'/api/projects': {
        'projects': [{'key': 'SANDBOX', 'name': 'Sandbox', 'owner': 'admin'}]}})
    row = projects_domain._project_row(client, 'local', 'SANDBOX')
    assert row['key'] == 'SANDBOX'
    with pytest.raises(ToolkitError):
        projects_domain._project_row(client, 'local', 'MISSING')
