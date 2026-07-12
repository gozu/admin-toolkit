"""Confirm-token robustness to the agent-tool transport dropping null keys.

The DSS agent-tool output serializer strips null-valued dict keys from the
canonicalTarget it echoes to the model, so a canonical carrying None values
(a create's scenarioId, a genuinely-unset expectedCurrent email) would drift
between mint and execute. canonical_target normalizes those away symmetrically
— these tests pin that, and that genuine content drift is still rejected.
"""

import conftest  # noqa: F401  (installs the python-lib path + DSS stubs)

from atk_agent_common import confirm

_PW = 'unit-test-password'


def _strip_nulls(node):
    """Mirror of the transport: drop null dict-keys (recursively)."""
    if isinstance(node, dict):
        return {k: _strip_nulls(v) for k, v in node.items() if v is not None}
    if isinstance(node, list):
        return [_strip_nulls(v) for v in node]
    return node


def test_canonical_target_ignores_none_keys():
    assert confirm.canonical_target({'a': 1, 'b': None}) == \
        confirm.canonical_target({'a': 1})


def test_nested_none_keys_ignored():
    assert confirm.canonical_target({'x': {'e': None, 'k': 'v'}}) == \
        confirm.canonical_target({'x': {'k': 'v'}})


def test_scenario_create_token_survives_transport():
    canonical = {'name': 'S', 'scenarioId': None, 'dailyTriggerHour': None,
                 'active': False, 'ackCustomCode': True,
                 'steps': [{'type': 'custom_python', 'params': {'script': 'x'}}]}
    tok, _ = confirm.mint(_PW, 'toolkit-scenario-write', 'local', canonical)
    # verify against the transport-stripped echo (scenarioId/dailyTriggerHour gone)
    assert confirm.verify(_PW, tok, 'toolkit-scenario-write', 'local',
                          _strip_nulls(canonical))


def test_user_update_no_email_token_survives_transport():
    canonical = {'login': 'x', 'expectedCurrent': {'email': None},
                 'email': 'x@y.com'}
    tok, _ = confirm.mint(_PW, 'user-update', 'local', canonical)
    assert confirm.verify(_PW, tok, 'user-update', 'local',
                          _strip_nulls(canonical))


def test_genuine_drift_still_rejected():
    canonical = {'name': 'S', 'scenarioId': None, 'active': False}
    tok, _ = confirm.mint(_PW, 'toolkit-scenario-write', 'local', canonical)
    try:
        confirm.verify(_PW, tok, 'toolkit-scenario-write', 'local',
                       {'name': 'DIFFERENT', 'active': False})
    except confirm.ConfirmTokenError:
        return
    raise AssertionError('genuine content drift was not rejected')


def test_none_value_not_confused_with_present_value():
    # dropping None must NOT collapse a real value into absence
    assert confirm.canonical_target({'a': None}) != \
        confirm.canonical_target({'a': 0})
    assert confirm.canonical_target({'a': None}) != \
        confirm.canonical_target({'a': ''})
    assert confirm.canonical_target({'a': None}) != \
        confirm.canonical_target({'a': False})
