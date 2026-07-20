"""Power-Up (python-run) contract: sha-bound canonical target, kernel-local
plan cache with single-use pop, local-only guard, and real subprocess
execution with stdout/stderr capture."""

import conftest  # noqa: F401  (installs the python-lib path + DSS stubs)

import pytest

from atk_agent_common import actuator
from atk_agent_common import actions as actions_registry
from atk_agent_common import remediation_map
from atk_agent_common.actions import python_run
from atk_agent_common.errors import ToolkitError


class _FakeClient:
    def __init__(self, settings=None):
        self.settings = settings or {}


def _plan(code, purpose='test', host='local', client=None):
    return python_run._plan_python_run(client or _FakeClient(), host,
                                       {'code': code, 'purpose': purpose}, {})


def test_registered_in_catalog():
    assert 'python-run' in actuator.ACTIONS
    assert 'python-run' in actuator._LOCAL_ONLY_ACTIONS
    assert 'python-run' not in actions_registry.BATCHABLE
    assert actions_registry.MODES['python-run'] == 'execute'
    assert actions_registry.ALL_RISKS['python-run'] == 'red'
    assert actions_registry.REQUIRED_TARGET_KEYS['python-run'] == {'code', 'purpose'}


def test_auto_remediation_hard_excluded():
    # Even a grant set naming python-run yields no autonomous candidates.
    issues = [{'id': 'anything'}]
    out = remediation_map.auto_candidates(issues, {'python-run'}, {})
    assert all(c['action'] != 'python-run' for c in out)


def test_python_run_never_auto_capable_in_catalog():
    from adk_backend.routes import agent_gates
    _sensors, actions = agent_gates._catalog({}, {})
    by = {a['action']: a for a in actions}
    assert by['python-run']['autoCapable'] is False
    # autoCapable covers the whole catalog EXCEPT python-run.
    assert all(row['autoCapable'] for a, row in by.items() if a != 'python-run')


def test_red_actions_default_on():
    # Part of the same consent story: the master switch defaults ON because
    # the per-action gates (and python-run's per-run ack) remain in the chain.
    from atk_agent_common import config as atk_config
    assert atk_config.resolve({})['enable_red_actions'] is True
    assert atk_config.resolve({'enable_red_actions': False})['enable_red_actions'] is False
    assert atk_config.resolve({'enable_red_actions': 'false'})['enable_red_actions'] is False


def test_plan_binds_sha_and_carries_code():
    code = "print('hello')\n"
    canonical, plan = _plan(code)
    assert set(canonical) == {'codeSha256', 'purpose'}
    assert len(canonical['codeSha256']) == 64
    assert plan['code'] == code
    assert plan['codeSha256'] == canonical['codeSha256']
    assert plan['warnings']


def test_plan_rejects_bad_input():
    with pytest.raises(ToolkitError):
        _plan('   ')
    with pytest.raises(ToolkitError):
        _plan('x' * (python_run.MAX_CODE_CHARS + 1))
    with pytest.raises(ToolkitError):
        _plan("print('x')", purpose='')
    with pytest.raises(ToolkitError):
        _plan("print('x')", host='remote-1')


def test_execute_runs_and_pops_cache():
    code = "import sys\nprint('out-line')\nprint('err-line', file=sys.stderr)\n"
    canonical, _ = _plan(code)
    client = _FakeClient()
    result = python_run._exec_python_run(client, 'local', canonical)
    assert result['exitCode'] == 0
    assert result['scriptFailed'] is False
    assert 'out-line' in result['stdout']
    assert 'err-line' in result['stderr']
    # Single-use: the cache entry was popped — a replayed token cannot run.
    with pytest.raises(ToolkitError) as exc:
        python_run._exec_python_run(client, 'local', canonical)
    assert 'plan cache lost' in str(exc.value)


def test_execute_reports_failure_with_traceback():
    canonical, _ = _plan("raise RuntimeError('boom-marker')\n")
    result = python_run._exec_python_run(_FakeClient(), 'local', canonical)
    assert result['exitCode'] != 0
    assert result['scriptFailed'] is True
    assert 'boom-marker' in result['stderr']
    assert 'NEW plan' in result['note']


def test_execute_refuses_unknown_sha():
    with pytest.raises(ToolkitError) as exc:
        python_run._exec_python_run(_FakeClient(), 'local',
                                    {'codeSha256': 'f' * 64, 'purpose': 'x'})
    assert 'plan cache lost' in str(exc.value)


def test_normalize_caches_only_under_true_sha():
    """A code-bearing execute target normalizes to the code's REAL sha; a
    claimed (stale) codeSha256 is preserved for the HMAC check but the edited
    code is never seeded under it — edited code can't ride an old ack."""
    import hashlib
    code_a, code_b = "print('a')\n", "print('b')\n"
    sha_a = hashlib.sha256(code_a.encode()).hexdigest()
    out = python_run.normalize_execute_target({'code': code_a, 'purpose': 'p'})
    assert out == {'codeSha256': sha_a, 'purpose': 'p'}
    # tamper: new code + old sha claim → claim preserved (token check will
    # compare it), cache holds code_b only under ITS OWN sha
    out = python_run.normalize_execute_target(
        {'code': code_b, 'codeSha256': sha_a, 'purpose': 'p'})
    assert out['codeSha256'] == sha_a
    assert python_run._PLAN_CACHE and sha_a not in {
        k for k in python_run._PLAN_CACHE
        if python_run._PLAN_CACHE[k][0] == code_b}


def test_token_hash_redemption_is_single_use():
    canonical, _ = _plan("print('once')\n")
    target = dict(canonical, _tokenHash='deadbeef00000001')
    result = python_run._exec_python_run(_FakeClient(), 'local', target)
    assert result['exitCode'] == 0
    # same token hash again (code re-supplied, so the cache is warm) → refused
    target2 = python_run.normalize_execute_target(
        {'code': "print('once')\n", 'purpose': 'test'})
    target2['_tokenHash'] = 'deadbeef00000001'
    with pytest.raises(ToolkitError) as exc:
        python_run._exec_python_run(_FakeClient(), 'local', target2)
    assert 'already redeemed' in str(exc.value)
    # a fresh token hash over the same code runs fine (re-plan + re-ack path)
    target3 = python_run.normalize_execute_target(
        {'code': "print('once')\n", 'purpose': 'test'})
    target3['_tokenHash'] = 'deadbeef00000002'
    assert python_run._exec_python_run(_FakeClient(), 'local', target3)['exitCode'] == 0


def test_timeout_clamped_from_settings():
    assert python_run._timeout_s(_FakeClient({'python_run_timeout_seconds': 1})) == 5
    assert python_run._timeout_s(_FakeClient({'python_run_timeout_seconds': 9999})) == 600
    assert python_run._timeout_s(_FakeClient({})) == python_run.DEFAULT_TIMEOUT_S
