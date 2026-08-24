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


# ── storage tail (phase 3) ───────────────────────────────────────────────────

def test_dataset_clear_refuses_exposed_without_ack():
    from atk_agent_common.actions import storage
    client = FakeToolkitClient(_inv('datasets', {
        'datasets': [{'name': 'shared_ds', 'type': 'PostgreSQL', 'exposed': True}]},
        projectKey='P1'))
    with pytest.raises(ToolkitError) as err:
        storage._plan_dataset_clear(client, 'local',
                                    {'projectKey': 'P1', 'datasetName': 'shared_ds'}, {})
    assert 'EXPOSED' in str(err.value)
    # with the ack it plans, marked irreversible, ack bound into the canonical
    canonical, plan = storage._plan_dataset_clear(
        client, 'local', {'projectKey': 'P1', 'datasetName': 'shared_ds',
                          'ackExposed': True}, {})
    assert canonical == {'projectKey': 'P1', 'datasetName': 'shared_ds',
                         'ackExposed': True}
    assert plan['irreversible'] is True


def test_impl_dataset_clear_recheck_exposure():
    class _Settings:
        def get_raw(self):
            return {'exposedObjects': {'objects': [
                {'type': 'DATASET', 'localName': 'shared_ds'}]}}

    class _Project:
        def get_settings(self):
            return _Settings()

        def get_dataset(self, name):
            raise AssertionError('must not clear without ack')

    class _Client:
        def get_project(self, pk):
            return _Project()

    out = admin_actions._impl_dataset_clear(_Client(), {
        'projectKey': 'P1', 'datasetName': 'shared_ds', 'ackExposed': False})
    assert out['ok'] is False and 'ackExposed' in out['error']


def _delete_inv(row):
    """Fake gets for _plan_dataset_delete: backup folder + usage inventory."""
    gets = {'/api/managed-folders': {'folders': [{'id': 'F1', 'name': 'backups'}]}}
    gets.update(_inv('datasets', {'datasets': [row]}, detail='usage', projectKey='P1'))
    return gets


def test_dataset_delete_refuses_exposed_without_ack():
    from atk_agent_common.actions import storage
    row = {'name': 'shared_ds', 'type': 'PostgreSQL', 'exposed': True,
           'producers': [], 'consumers': [], 'webappRefs': [], 'scenarioRefs': []}
    client = FakeToolkitClient(_delete_inv(row))
    with pytest.raises(ToolkitError) as err:
        storage._plan_dataset_delete(client, 'local',
                                     {'projectKey': 'P1', 'datasetName': 'shared_ds'}, {})
    assert 'EXPOSED' in str(err.value)
    canonical, plan = storage._plan_dataset_delete(
        client, 'local', {'projectKey': 'P1', 'datasetName': 'shared_ds',
                          'ackExposed': True}, {})
    assert canonical == {'projectKey': 'P1', 'datasetName': 'shared_ds',
                         'dropData': False, 'ackExposed': True}
    assert plan['irreversible'] is True


def test_dataset_delete_refuses_referenced_without_ack():
    from atk_agent_common.actions import storage
    row = {'name': 'mid', 'type': 'Filesystem', 'exposed': False,
           'producers': ['r1'], 'consumers': ['r2'],
           'webappRefs': ['app (STANDARD)'], 'scenarioRefs': ['mon (inactive)']}
    client = FakeToolkitClient(_delete_inv(row))
    with pytest.raises(ToolkitError) as err:
        storage._plan_dataset_delete(client, 'local',
                                     {'projectKey': 'P1', 'datasetName': 'mid'}, {})
    assert 'referenced' in str(err.value)
    canonical, plan = storage._plan_dataset_delete(
        client, 'local', {'projectKey': 'P1', 'datasetName': 'mid',
                          'ackReferenced': True, 'dropData': True}, {})
    assert canonical == {'projectKey': 'P1', 'datasetName': 'mid',
                         'dropData': True, 'ackReferenced': True}
    # orphaned producer + inactive scenario surfaced as warnings, not refusals
    assert any('ORPHANED' in w for w in plan['warnings'])
    assert any('INACTIVE' in w for w in plan['warnings'])


def test_dataset_delete_inactive_scenario_ref_alone_does_not_refuse():
    from atk_agent_common.actions import storage
    row = {'name': 'old_out', 'type': 'Filesystem', 'exposed': False,
           'producers': [], 'consumers': [], 'webappRefs': [],
           'scenarioRefs': ['mon (inactive)']}
    client = FakeToolkitClient(_delete_inv(row))
    canonical, plan = storage._plan_dataset_delete(
        client, 'local', {'projectKey': 'P1', 'datasetName': 'old_out'}, {})
    assert canonical == {'projectKey': 'P1', 'datasetName': 'old_out',
                         'dropData': False}
    assert any('INACTIVE' in w for w in plan['warnings'])


def test_impl_dataset_delete_recheck_exposure_and_consumers():
    class _Settings:
        def __init__(self, objs):
            self._objs = objs

        def get_raw(self):
            return {'exposedObjects': {'objects': self._objs}}

    class _Project:
        def __init__(self, objs, recipes):
            self._objs, self._recipes = objs, recipes

        def get_settings(self):
            return _Settings(self._objs)

        def list_recipes(self):
            return self._recipes

        def get_dataset(self, name):
            raise AssertionError('must not delete without the required ack')

    class _Client:
        def __init__(self, project):
            self._project = project

        def get_project(self, pk):
            return self._project

    exposed = _Client(_Project([{'type': 'DATASET', 'localName': 'ds'}], []))
    out = admin_actions._impl_dataset_delete(exposed, {
        'projectKey': 'P1', 'datasetName': 'ds', 'ackExposed': False})
    assert out['ok'] is False and 'ackExposed' in out['error']

    consumed = _Client(_Project([], [
        {'name': 'r2', 'inputs': {'main': {'items': [{'ref': 'ds'}]}}, 'outputs': {}}]))
    out = admin_actions._impl_dataset_delete(consumed, {
        'projectKey': 'P1', 'datasetName': 'ds', 'ackReferenced': False})
    assert out['ok'] is False and 'ackReferenced' in out['error']


def test_fs_cleanup_planner_scopes_and_defaults():
    from atk_agent_common.actions import storage
    scan = {'ok': True, 'totalDirs': 3, 'totalBytes': 3 * 1024 ** 3, 'totalGB': 3.0,
            'groups': {'P1': {'entries': 4, 'deletable': 3, 'bytes': 3 * 1024 ** 3}}}
    client = FakeToolkitClient({'/api/tools/fs-cleanup/scan': scan})
    canonical, plan = storage._plan_job_logs_cleanup(
        client, 'local', {'projectKey': 'P1'}, {})
    assert canonical['policy'] == 'joblogs'
    assert canonical['minAgeDays'] == 15 and canonical['keepLast'] == 5
    assert canonical['projectKey'] == 'P1'
    # tmp-cleanup refuses a projectKey (not project-scoped)
    with pytest.raises(ToolkitError):
        storage._plan_tmp_cleanup(client, 'local', {'projectKey': 'P1'}, {})


def test_fs_cleanup_planner_nothing_to_delete_refuses():
    from atk_agent_common.actions import storage
    scan = {'ok': True, 'totalDirs': 0, 'totalBytes': 0, 'groups': {}}
    client = FakeToolkitClient({'/api/tools/fs-cleanup/scan': scan})
    with pytest.raises(ToolkitError) as err:
        storage._plan_exports_cleanup(client, 'local', {}, {})
    assert 'do not propose' in str(err.value.remediation or err.value)


def test_legacy_batchable_flips():
    from atk_agent_common import actuator
    assert {'db-vacuum', 'db-analyze', 'plugin-deploy',
            'project-delete'} <= actuator.BATCHABLE_ACTIONS


def test_impl_project_set_cluster_refuses_unknown_cluster():
    class _Client:
        def list_clusters(self):
            return [{'id': 'eks-prod'}]
    out = admin_actions._impl_project_set_cluster(_Client(), {
        'projectKey': 'P1',
        'clusterId': 'kubeconfig:/data/dataiku/.kube/config'})
    assert out['ok'] is False
    assert 'does not exist' in out['error'] and 'eks-prod' in out['error']
