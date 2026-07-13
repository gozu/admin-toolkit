"""Batch-target plan/execute protocol tests (actuator.py).

Uses settings-set (batchable, pure planner over /api/settings/raw) with a
fake ToolkitClient, so no DSS anywhere. The audit sink is exercised through
its no-connection path (returns None → auditWarning)."""

import pytest

from atk_agent_common import actuator, confirm
from atk_agent_common.errors import ToolkitError

PASSWORD = 'test-master-pw'

# Agent Settings gates (v0.4.732) default every actuator action OFF until an
# admin enables it, so plan/execute refuse before any planning. The FakeClient
# answers the gate endpoint with these enabled — the real action_gates code then
# resolves them open, exactly as it would for an admin who ticked them on. We
# configure the gate's state rather than stub the gate function, so the real
# resolution path still runs (test_disabled_action_refused pins the OFF case).
_ENABLED_ACTIONS = {'settings-set': True, 'code-env-consolidate': True}


class FakeClient:
    def __init__(self, raw_settings=None, gates=None):
        self.settings = {'master_password': PASSWORD, 'enable_red_actions': True}
        self._gates = dict(_ENABLED_ACTIONS) if gates is None else gates
        self.raw = raw_settings if raw_settings is not None else {
            'sparkSettings': {'sparkEnabled': False},
            'containerSettings': {'k8sEnabled': False},
            'cgroupSettings': {'enabled': False},
        }

    def get(self, path, host='local', **kwargs):
        if path == '/api/settings/raw':
            return self.raw
        if path == '/api/agents/action-settings':
            return {'gates': self._gates}
        raise AssertionError('unexpected GET %s' % path)

    def post(self, path, **kwargs):
        raise AssertionError('unexpected POST %s' % path)


def _targets():
    return [
        {'path': 'sparkSettings.sparkEnabled', 'newValue': True},
        {'path': 'containerSettings.k8sEnabled', 'newValue': True},
        {'path': 'cgroupSettings.enabled', 'newValue': True},
    ]


# ---- planning ----

def test_single_target_path_unchanged():
    """targets=[one] must produce the byte-identical single-target canonical."""
    client = FakeClient()
    single = actuator.plan_admin_action(client, action='settings-set',
                                        target=_targets()[0])
    via_list = actuator.plan_admin_action(client, action='settings-set',
                                          targets=[_targets()[0]])
    assert single['canonicalTarget'] == via_list['canonicalTarget']
    assert 'batchTargets' not in single['canonicalTarget']


def test_batch_canonical_deterministic():
    client = FakeClient()
    a = actuator.plan_admin_action(client, action='settings-set', targets=_targets())
    b = actuator.plan_admin_action(client, action='settings-set',
                                   targets=list(reversed(_targets())))
    assert a['canonicalTarget'] == b['canonicalTarget']
    assert len(a['canonicalTarget']['batchTargets']) == 3
    # expectedCurrent bound per entry (drift guard reaches every target)
    assert all('expectedCurrent' in t for t in a['canonicalTarget']['batchTargets'])
    assert a['plan']['targetCount'] == 3
    assert len(a['plan']['targets']) == 3


def test_batch_token_verifies_against_combined_canonical():
    client = FakeClient()
    plan = actuator.plan_admin_action(client, action='settings-set', targets=_targets())
    assert confirm.verify(PASSWORD, plan['confirm_token'], 'settings-set', 'local',
                          plan['canonicalTarget'])
    # any drift in ONE entry kills the token
    tampered = {'batchTargets': [dict(t, newValue=False)
                                 for t in plan['canonicalTarget']['batchTargets']]}
    with pytest.raises(confirm.ConfirmTokenError):
        confirm.verify(PASSWORD, plan['confirm_token'], 'settings-set', 'local', tampered)


def test_non_batchable_refused():
    # code-env-consolidate is genuinely single-target (distinct source->target
    # migrations); the batch gate refuses multi-target before any planning.
    client = FakeClient()
    with pytest.raises(ToolkitError, match='does not accept batched targets'):
        actuator.plan_admin_action(client, action='code-env-consolidate',
                                   targets=[{'sourceEnvName': 'a', 'targetEnvName': 'z'},
                                            {'sourceEnvName': 'b', 'targetEnvName': 'z'}])


def test_large_batch_uncapped():
    """No batch-size ceiling (the 20-target cap was removed in 0.4.678) —
    23 targets = the real-world dataset-delete sweep that hit the old cap."""
    client = FakeClient()
    many = [{'path': 'sparkSettings.sparkEnabled', 'newValue': True}] * 23
    plan = actuator.plan_admin_action(client, action='settings-set', targets=many)
    assert plan['plan']['targetCount'] == 23
    assert len(plan['canonicalTarget']['batchTargets']) == 23
    assert confirm.verify(PASSWORD, plan['confirm_token'], 'settings-set', 'local',
                          plan['canonicalTarget'])


def test_target_and_targets_both_refused():
    client = FakeClient()
    with pytest.raises(ToolkitError, match='not both'):
        actuator.plan_admin_action(client, action='settings-set',
                                   target=_targets()[0], targets=_targets())


def test_disabled_action_refused():
    # The gate is enforced, not bypassed: with nothing enabled in Agent
    # Settings, plan refuses before any planning (no canonical, no token) — and
    # execute refuses on the same check.
    client = FakeClient(gates={})
    plan = actuator.plan_admin_action(client, action='settings-set', target=_targets()[0])
    assert plan['error']['code'] == 'action-disabled'
    assert 'canonicalTarget' not in plan
    ex = actuator.execute_admin_action(client, action='settings-set',
                                       target=_targets()[0], confirm_flag=True,
                                       confirm_token='irrelevant')
    assert ex['error']['code'] == 'action-disabled'


# ---- execution ----

def _minted_batch(client):
    plan = actuator.plan_admin_action(client, action='settings-set', targets=_targets())
    return plan['canonicalTarget'], plan['confirm_token']


def test_batch_execute_partial_failure(monkeypatch):
    client = FakeClient()
    canonical, token = _minted_batch(client)

    def flaky_executor(c, h, t):
        if t['path'] == 'containerSettings.k8sEnabled':
            raise ToolkitError('boom on k8s flag')
        return {'ok': True, 'path': t['path'], 'before': False, 'after': True}

    monkeypatch.setitem(actuator._EXECUTORS, 'settings-set', flaky_executor)
    monkeypatch.setattr(actuator, '_settings_changes_from_result',
                        lambda action, target, result: [{'itemKey': 'settings:%s' % target['path'],
                                                         'before': False, 'after': True}])

    out = actuator.execute_admin_action(client, action='settings-set', target=canonical,
                                        confirm_flag=True, confirm_token=token)
    assert out['status'] == 'partial'
    per_target = out['result']['perTarget']
    assert len(per_target) == 3
    assert out['result']['okCount'] == 2 and out['result']['errorCount'] == 1
    failed = [row for row in per_target if row['status'] == 'error']
    assert len(failed) == 1
    assert 'boom on k8s flag' in failed[0]['error']['message']
    # one audit row semantics: no connection configured → single auditWarning
    assert out['auditId'] is None and 'auditWarning' in out


def test_batch_execute_all_ok(monkeypatch):
    client = FakeClient()
    canonical, token = _minted_batch(client)
    monkeypatch.setitem(actuator._EXECUTORS, 'settings-set',
                        lambda c, h, t: {'ok': True, 'path': t['path'],
                                         'before': False, 'after': True})
    out = actuator.execute_admin_action(client, action='settings-set', target=canonical,
                                        confirm_flag=True, confirm_token=token)
    assert out['status'] == 'ok'
    assert out['result']['okCount'] == 3 and out['result']['errorCount'] == 0


def test_batch_execute_token_drift_refused(monkeypatch):
    client = FakeClient()
    canonical, token = _minted_batch(client)
    canonical['batchTargets'][0]['newValue'] = 'tampered'
    out = actuator.execute_admin_action(client, action='settings-set', target=canonical,
                                        confirm_flag=True, confirm_token=token)
    assert out['error']['code'] == 'confirm-token-rejected'


def test_audit_connection_fallback_chain():
    """The actuator must resolve the audit DB through the same chain as the
    backend's read side (db_adapter): dedicated audit param first, then the
    legacy Story key, then triage_connection. akaos live-acceptance caught the
    actuator passing triage_connection only — auditing silently skipped on
    instances configured via agents_audit_postgres_connection."""
    from atk_agent_common import audit
    assert audit.resolve_connection({'agents_audit_postgres_connection': 'kaosdb',
                                     'triage_connection': ''}) == 'kaosdb'
    assert audit.resolve_connection({'story_postgres_connection': 'legacy'}) == 'legacy'
    assert audit.resolve_connection({'triage_connection': 'tri'}) == 'tri'
    assert audit.resolve_connection({'triage_connection': '  '}) is None
    assert audit.resolve_connection({}) is None


def test_config_resolve_carries_audit_chain_keys():
    """config.resolve() whitelists settings keys — if it drops the dedicated
    audit param, kernels can never resolve the audit DB (akaos live catch #2:
    the resolver was right but its input was pre-filtered)."""
    from atk_agent_common import audit, config
    settings = config.resolve({'agents_audit_postgres_connection': 'kaosdb',
                               'triage_connection': ''})
    assert settings['agents_audit_postgres_connection'] == 'kaosdb'
    assert audit.resolve_connection(settings) == 'kaosdb'
