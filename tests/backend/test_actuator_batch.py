"""Batch-target plan/execute protocol tests (actuator.py).

Uses settings-set (batchable, pure planner over /api/settings/raw) with a
fake ToolkitClient, so no DSS anywhere. The audit sink is exercised through
its no-connection path (returns None → auditWarning)."""

import pytest

from atk_agent_common import actuator, confirm
from atk_agent_common.errors import ToolkitError

PASSWORD = 'test-master-pw'


class FakeClient:
    def __init__(self, raw_settings=None):
        self.settings = {'master_password': PASSWORD, 'enable_red_actions': True}
        self.raw = raw_settings if raw_settings is not None else {
            'sparkSettings': {'sparkEnabled': False},
            'containerSettings': {'k8sEnabled': False},
            'cgroupSettings': {'enabled': False},
        }

    def get(self, path, host='local', **kwargs):
        if path == '/api/settings/raw':
            return self.raw
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
    client = FakeClient()
    with pytest.raises(ToolkitError, match='does not accept batched targets'):
        actuator.plan_admin_action(client, action='k8s-exec-config-tune',
                                   targets=[{'configName': 'a', 'changes': {'memLimitMB': 1}},
                                            {'configName': 'b', 'changes': {'memLimitMB': 1}}])


def test_batch_cap_refused():
    client = FakeClient()
    too_many = [{'path': 'sparkSettings.sparkEnabled', 'newValue': True}] * 21
    with pytest.raises(ToolkitError, match='capped at 20'):
        actuator.plan_admin_action(client, action='settings-set', targets=too_many)


def test_target_and_targets_both_refused():
    client = FakeClient()
    with pytest.raises(ToolkitError, match='not both'):
        actuator.plan_admin_action(client, action='settings-set',
                                   target=_targets()[0], targets=_targets())


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
