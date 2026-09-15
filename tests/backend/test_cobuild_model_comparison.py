"""The replacement benchmark never grants the model execution authority."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import conftest  # noqa: F401
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts' / 'agents'))
from cobuild_model_compare import TaskExecutor, task_loop
from atk_agent_common import actuator, tools_impl


@pytest.fixture
def action(monkeypatch):
    effects = []
    client = SimpleNamespace(get=lambda path: {'gates': {'project-variables-set': True}})
    def plan(client, **kwargs):
        effects.append('plan')
        return {'canonicalTarget': {'projectKey': 'OWNED', 'path': 'standard.x', 'newValue': 1},
                'confirm_token': 'actual-secret-token', 'plan': {'summary': 'one fixture variable'}}
    def execute(client, **kwargs):
        effects.append(('execute', kwargs))
        return {'status': 'ok', 'result': {'changed': True}}
    monkeypatch.setattr(actuator, 'plan_admin_action', plan)
    monkeypatch.setattr(actuator, 'execute_admin_action', execute)
    executor = TaskExecutor(client, 'project-variables-set',
                            {'projectKey': 'OWNED', 'path': 'standard.x', 'newValue': 1},
                            action=True, approve_fixture=True)
    return executor, effects


def plan_args(executor):
    return {'action': executor.name, 'target': dict(executor.arguments)}


def execute_args(executor):
    return {'action': executor.name, 'target': dict(executor.arguments),
            'confirm': True, 'confirm_token': executor.reference}


def test_opaque_reference_and_approval_precede_single_execution(action):
    executor, effects = action
    displayed = executor.invoke('plan_admin_action', plan_args(executor))
    assert displayed['confirm_token'] == executor.reference
    assert 'actual-secret-token' not in json.dumps(displayed)
    with pytest.raises(PermissionError):
        executor.invoke('execute_admin_action', execute_args(executor))
    assert effects == ['plan']
    assert executor.fixture_authorization()
    assert executor.invoke('execute_admin_action', execute_args(executor))['status'] == 'ok'
    assert effects[1][1]['confirm_token'] == 'actual-secret-token'
    with pytest.raises(ValueError, match='already attempted'):
        executor.invoke('execute_admin_action', execute_args(executor))
    assert len(effects) == 2


@pytest.mark.parametrize('update', [
    {'action': 'project-delete'}, {'host': 'customer'}, {'confirm': False},
    {'confirm_token': 'invented'}, {'target': {'projectKey': 'UNOWNED'}},
    {'extra': 'unexpected'},
])
def test_model_cannot_change_approved_scope(action, update):
    executor, effects = action
    executor.invoke('plan_admin_action', plan_args(executor))
    executor.fixture_authorization()
    args = dict(execute_args(executor), **update)
    with pytest.raises((ValueError, PermissionError)):
        executor.invoke('execute_admin_action', args)
    assert effects == ['plan']


def test_failed_write_is_never_repeated(action, monkeypatch):
    executor, effects = action
    executor.invoke('plan_admin_action', plan_args(executor))
    executor.fixture_authorization()
    def fail(*args, **kwargs):
        effects.append('uncertain-write')
        raise TimeoutError()
    monkeypatch.setattr(actuator, 'execute_admin_action', fail)
    with pytest.raises(TimeoutError):
        executor.invoke('execute_admin_action', execute_args(executor))
    with pytest.raises(ValueError, match='already attempted'):
        executor.invoke('execute_admin_action', execute_args(executor))
    assert effects == ['plan', 'uncertain-write']


def test_revocation_after_plan_prevents_execution(action):
    executor, effects = action
    executor.invoke('plan_admin_action', plan_args(executor))
    executor.fixture_authorization()
    executor.client.get = lambda path: {'gates': {'project-variables-set': False}}
    with pytest.raises(PermissionError):
        executor.invoke('execute_admin_action', execute_args(executor))
    assert effects == ['plan']


def test_read_scope_and_existing_routing(monkeypatch):
    from atk_agent_common import capability_routing
    client = SimpleNamespace(get=lambda path: {'gates': {}})
    seen = []
    def toolkit_get(client, endpoint='list', host='local'):
        seen.append(capability_routing.selected(client, 'toolkit_get'))
        return {'version': '1'}
    monkeypatch.setattr(tools_impl, 'toolkit_get', toolkit_get)
    executor = TaskExecutor(client, 'toolkit_get', {'endpoint': 'version'})
    with pytest.raises(ValueError, match='scope'):
        executor.invoke('toolkit_get', {'endpoint': 'logs'})
    with capability_routing.override('cobuild'):
        assert executor.invoke('toolkit_get', {'endpoint': 'version', 'host': 'local'})['version'] == '1'
    assert seen == ['existing']


def test_cobuild_selects_tool_and_answers_in_same_conversation(monkeypatch):
    calls = []
    messages = iter([{'type': 'tool_calls', 'calls': [{'name': 'list_hosts', 'arguments': {}}]},
                     {'type': 'final', 'text': 'One host.'}])
    def send(message, allow_edit_project):
        assert not allow_edit_project
        calls.append(message)
        return SimpleNamespace(message=json.dumps(next(messages)), is_error=False, is_confirmation_request=False)
    conversation = SimpleNamespace(send_message=send)
    project = SimpleNamespace(new_cobuild_conversation=lambda: conversation)
    monkeypatch.setattr(tools_impl, 'list_hosts', lambda client: {'count': 1, 'hosts': []})
    executor = TaskExecutor(SimpleNamespace(get=lambda path: {'gates': {}}), 'list_hosts', {})
    row = task_loop(project, 'model', 'cobuild', 'instructions', [], 'List hosts', executor)
    assert row['status'] == 'completed' and len(row['model_calls']) == 2
    assert len(calls) == 2 and 'External ADTK tool results' in calls[1]


def test_final_text_without_tool_execution_is_not_success():
    conversation = SimpleNamespace(send_message=lambda *a, **k: SimpleNamespace(
        message='{"type":"final","text":"Done."}', is_error=False, is_confirmation_request=False))
    executor = TaskExecutor(None, 'list_hosts', {})
    row = task_loop(SimpleNamespace(new_cobuild_conversation=lambda: conversation),
                    'model', 'cobuild', '', [], '', executor)
    assert row['status'] == 'failed' and executor.result is None


def test_rejected_selection_can_be_corrected_without_executing_it(monkeypatch):
    replies = iter([
        {'type': 'tool_calls', 'calls': [{'name': 'triage_sweep', 'arguments': {}}]},
        {'type': 'tool_calls', 'calls': [{'name': 'list_hosts', 'arguments': {}}]},
        {'type': 'final', 'text': 'One host.'},
    ])
    messages = []
    def send(message, **kwargs):
        messages.append(message)
        return SimpleNamespace(message=json.dumps(next(replies)), is_error=False,
                               is_confirmation_request=False)
    monkeypatch.setattr(tools_impl, 'list_hosts', lambda client: {'count': 1})
    executor = TaskExecutor(SimpleNamespace(get=lambda p: {'gates': {}}), 'list_hosts', {})
    result = task_loop(SimpleNamespace(new_cobuild_conversation=lambda: SimpleNamespace(send_message=send)),
                       'model', 'cobuild', '', [], '', executor)
    assert result['status'] == 'completed'
    assert 'comparison-scope-rejected' in messages[1]
    assert [e['status'] for e in executor.events] == ['rejected_or_failed', 'returned']


def test_all_capabilities_registered_and_actions_require_host_runtime(monkeypatch):
    from cobuild_model_suite import inventory, run_group
    from render_cobuild_comparison import PURPOSE
    assert set(inventory()) == set(PURPOSE) and len(inventory()) == 64
    monkeypatch.delenv('DIP_HOME', raising=False)
    with pytest.raises(RuntimeError, match='selected DSS host'):
        run_group(SimpleNamespace(), 'fixture')


def test_report_does_not_relabel_historical_success_or_hide_latest_failure(tmp_path):
    from cobuild_model_compare import TRANSPORT
    from render_cobuild_model_suite import collect
    path = tmp_path / 'results.json'
    path.write_text(json.dumps([
        {'capability': 'list_hosts', 'status': 'same', 'transport': 'deployed HTTP bridge'},
        {'at': '1', 'capability': 'toolkit_get', 'status': 'same', 'transport': TRANSPORT},
        {'at': '2', 'capability': 'toolkit_get', 'status': 'failed', 'transport': TRANSPORT,
         'diagnostic': 'SECRET', 'confirm_token': 'SECRET', 'answer': 'SECRET'},
    ]))
    result = collect([path])
    rows = {r['capability']: r for r in result['rows']}
    assert rows['list_hosts']['status'] == 'unmeasured'
    assert rows['toolkit_get']['status'] == 'failed'
    assert 'SECRET' not in json.dumps(result)
    private = tmp_path / 'x-private.json'
    with pytest.raises(ValueError, match='Private'):
        collect([private])


def test_macro_package_uses_isolated_environment_and_no_legacy_approval_fixtures():
    import zipfile
    from run_cobuild_model_suite import package
    with zipfile.ZipFile(package('atk-model-checks-test')) as archive:
        names = archive.namelist()
        assert 'python-lib/cobuild_images_compare.py' not in names
        assert 'python-lib/cobuild_python_compare.py' not in names
        assert b'langchain-core' in archive.read('code-env/python/spec/requirements.txt')
        assert 'python-lib/cobuild_model_compare.py' in names


def test_temporary_plugin_cleanup_refuses_borrowed_environment():
    from run_cobuild_model_suite import delete_owned_plugin
    plugin = SimpleNamespace(get_settings=lambda: SimpleNamespace(
        get_raw=lambda: {'codeEnvName': 'plugin_admin-toolkit_managed'}))
    # No delete method: reaching it would fail the test.
    with pytest.raises(RuntimeError, match='foreign code environment'):
        delete_owned_plugin(SimpleNamespace(get_plugin=lambda p: plugin), 'atk-model-checks-test')


def test_missing_null_defaults_preserve_the_signed_target(action):
    from cobuild_model_compare import matches_plan
    executor, effects = action
    executor.invoke('plan_admin_action', plan_args(executor))
    executor.plan['canonicalTarget']['optional'] = None
    executor.fixture_authorization()
    executor.invoke('execute_admin_action', execute_args(executor))
    assert effects[-1][1]['target']['optional'] is None
    assert not matches_plan({}, {'retireSource': False})
    assert not matches_plan({'extra': None}, {})
    assert not matches_plan({'flag': 1}, {'flag': True})
    assert not matches_plan({'x': 0}, {'x': None})


def test_rejected_post_execution_read_does_not_repeat_or_fail_completed_write(action):
    executor, effects = action
    replies = iter([
        {'type': 'tool_calls', 'calls': [{'name': 'plan_admin_action', 'arguments': plan_args(executor)}]},
        {'type': 'tool_calls', 'calls': [{'name': 'execute_admin_action', 'arguments': execute_args(executor)}]},
        {'type': 'tool_calls', 'calls': [{'name': 'config_inspect', 'arguments': {'domain': 'projects'}}]},
        {'type': 'final', 'text': 'The variable was updated.'},
    ])
    conversation = SimpleNamespace(send_message=lambda *a, **k: SimpleNamespace(
        message=json.dumps(next(replies)), is_error=False, is_confirmation_request=False))
    result = task_loop(SimpleNamespace(new_cobuild_conversation=lambda: conversation),
                       'model', 'cobuild', '', [], '', executor)
    assert result['status'] == 'completed'
    assert len(effects) == 2
