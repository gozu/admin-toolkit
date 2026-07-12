"""Code-bearing step detection for toolkit-scenario-write (ackCustomCode).

The whitelist is gone: any step type is writable, but code-bearing steps are
gated behind an explicit acknowledgment. These tests pin the fail-safe
directions — unknown ⇒ code, unparseable ⇒ code, nested payload keys found.
"""

import conftest  # noqa: F401  (installs the python-lib path + DSS stubs)

from atk_agent_common.policies import toolkit_scenarios as policy


def _kinds(steps):
    return [(i, t) for i, t, _ in policy.code_bearing_steps(steps)]


def test_non_code_steps_pass_clean():
    steps = [{'type': 'build_flowitem', 'params': {'builds': []}},
             {'type': 'clear_items', 'params': {}},
             {'type': 'run_scenario', 'params': {'scenarioId': 'X'}},
             {'type': 'runnable',
              'params': {'runnableType': 'pyrunnable_admin-toolkit_log-cleaner'}}]
    assert policy.code_bearing_steps(steps) == []
    ok, reason = policy.validate_steps(steps)
    assert ok, reason


def test_explicit_code_types_detected():
    assert _kinds([{'type': 'custom_python', 'params': {'script': 'print(1)'}}]) == \
        [(0, 'custom_python')]
    assert _kinds([{'type': 'exec_sql', 'params': {'sql': 'DROP TABLE x'}}]) == \
        [(0, 'exec_sql')]


def test_code_payload_detected_whatever_the_type_claims():
    # a known-safe type smuggling a script payload (nested) still counts
    steps = [{'type': 'build_flowitem',
              'params': {'builds': [{'hook': {'code': 'import os'}}]}}]
    assert _kinds(steps) == [(0, 'build_flowitem')]


def test_unknown_type_is_code_fail_safe():
    assert _kinds([{'type': 'brand_new_dss_step', 'params': {}}]) == \
        [(0, 'brand_new_dss_step')]


def test_unparseable_step_is_code_fail_safe():
    assert _kinds(['not-a-dict']) == [(0, '?')]


def test_non_toolkit_runnable_is_code():
    steps = [{'type': 'runnable', 'params': {'runnableType': 'pyrunnable_other-plugin_x'}}]
    assert _kinds(steps) == [(0, 'runnable')]


def test_empty_code_payload_does_not_trigger():
    steps = [{'type': 'clear_items', 'params': {'script': '   '}}]
    assert policy.code_bearing_steps(steps) == []


def test_cross_project_run_scenario_still_refused():
    ok, reason = policy.validate_steps(
        [{'type': 'run_scenario', 'params': {'projectKey': 'OTHER', 'scenarioId': 'X'}}])
    assert not ok and 'cross-project' in reason


def test_typeless_step_refused_structurally():
    ok, reason = policy.validate_steps([{'params': {}}])
    assert not ok and 'no type' in reason
