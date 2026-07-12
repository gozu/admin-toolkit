"""plan_admin_action must return the signed canonicalTarget WHOLE.

The confirm token is minted over the canonical; enforce_budget trims the
longest lists in the plan envelope for token economy. If it trimmed
canonicalTarget itself (e.g. a multi-step custom-code scenario whose steps
list is the longest), the returned target would no longer match the token and
execute would always refuse. This pins that the canonical survives and its
token verifies even when the display envelope is truncated.
"""

import conftest  # noqa: F401  (installs the python-lib path + DSS stubs)

from atk_agent_common import actuator, confirm


class _Settings:
    def get(self, key, default=None):
        return {'master_password': 'unit-pw'}.get(key, default)


class _Client:
    settings = _Settings()


def _big_planner(client, host, target, params):
    # a canonical whose steps list is by far the longest thing in the envelope
    steps = [{'type': 'custom_python', 'name': 's%d' % i,
              'params': {'script': 'X' * 900}} for i in range(8)]
    canonical = {'name': 'big', 'scenarioId': None, 'steps': steps,
                 'ackCustomCode': True}
    plan = {'summary': 'big scenario', 'codeSteps': steps, 'warnings': ['w'] * 5}
    return canonical, plan


def test_canonical_target_survives_budget_trim(monkeypatch):
    monkeypatch.setattr(actuator.action_gates, 'action_enabled', lambda c, a: True)
    monkeypatch.setitem(actuator._PLANNERS, 'toolkit-scenario-write', _big_planner)

    out = actuator.plan_admin_action(_Client(), host='local',
                                     action='toolkit-scenario-write',
                                     target={'name': 'big'})
    ct = out['canonicalTarget']
    # all 8 steps preserved (not halved by enforce_budget)
    assert len(ct['steps']) == 8, out.get('truncated')
    # and the token verifies against the returned canonicalTarget
    assert confirm.verify('unit-pw', out['confirm_token'],
                          'toolkit-scenario-write', 'local', ct)
