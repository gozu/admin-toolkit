"""Actual MCP wire exchange with controlled DSS responses; never native writes."""
import json
import threading
import time
from types import SimpleNamespace as NS
import uuid

import conftest  # noqa
import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool

from adk_backend import headless_service as service
from atk_agent_common import reasoning, agent_runtime, capability_routing
from atk_agent_common.errors import ToolkitError


class DSS:
    def __init__(self, label, responses=None, delay=0):
        self.host = 'https://' + label + '.invalid'
        self.label, self.delay = label, delay
        self.responses = iter(responses or [label])
        self.calls = []

    def get_project(self, project):
        assert project == 'ADMINTOOLKIT'
        return self

    def new_cobuild_conversation(self):
        def send(message, allow_edit_project):
            self.calls.append((message, allow_edit_project))
            assert allow_edit_project is False
            time.sleep(self.delay)
            value = next(self.responses)
            if isinstance(value, Exception):
                raise value
            return NS(message=value, is_error=False, type='answer')
        return NS(conversation_id=uuid.uuid4().hex, send_message=send)


def wait(row, owner):
    deadline = time.monotonic() + 8
    while row['status'] == 'pending' and time.monotonic() < deadline:
        time.sleep(.01)
        row = service.snapshot(row['taskId'], owner)
    assert row['status'] != 'pending'
    return row


@pytest.fixture(autouse=True)
def cleanup_sessions():
    yield
    for row in list(service._SESSIONS.values()):
        service.stop(row.id, row.owner)
    # Let MCP transports close before the next test changes bindings.
    time.sleep(.03)
    with service._LOCK:
        service._SESSIONS.clear()


def test_actual_mcp_multiturn_and_concurrent_identity_isolation():
    a, b = DSS('a', ['a1', 'a2'], .02), DSS('b', ['b1'], .01)
    ar = service.submit(a, 'user-a', 'ADMINTOOLKIT', 'one')
    br = service.submit(b, 'user-b', 'ADMINTOOLKIT', 'one')
    with pytest.raises(ValueError):
        service.snapshot(ar['taskId'], 'user-b')
    ar, br = wait(ar, 'user-a'), wait(br, 'user-b')
    assert ar['message'] == 'a1' and br['message'] == 'b1'
    with pytest.raises(ValueError):
        service.submit(b, 'user-a', 'ADMINTOOLKIT', 'two', ar['taskId'], ar['revision'])
    ar = wait(service.submit(a, 'user-a', 'ADMINTOOLKIT', 'two', ar['taskId'], ar['revision']), 'user-a')
    assert ar['message'] == 'a2'
    assert a.calls == [('one', False), ('two', False)]


def test_pending_turn_is_polled_without_resubmitting(monkeypatch):
    from dataiku_mcp.tools import cobuild
    monkeypatch.setattr(cobuild, 'TURN_WAIT_TIMEOUT_SECONDS', .01)
    dss = DSS('slow', ['eventual'], .08)
    row = wait(service.submit(dss, 'owner', 'ADMINTOOLKIT', 'once'), 'owner')
    assert row['message'] == 'eventual' and len(dss.calls) == 1


def test_timeout_unknown_stop_is_not_remote_cancellation(monkeypatch):
    monkeypatch.setattr(service, 'TURN_SECONDS', .03)
    dss = DSS('slow', ['later'], .1)
    row = wait(service.submit(dss, 'owner', 'ADMINTOOLKIT', 'once'), 'owner')
    assert row['status'] == 'unknown'
    assert row['remoteCancellationVerified'] is False
    with pytest.raises(ValueError):
        service.submit(dss, 'owner', 'ADMINTOOLKIT', 'retry', row['taskId'], row['revision'])
    time.sleep(.12)
    assert len(dss.calls) == 1


def test_failure_does_not_expose_private_exception_and_restart_is_explicit():
    row = wait(service.submit(DSS('x', [RuntimeError('SECRET TRANSCRIPT')]), 'owner',
                              'ADMINTOOLKIT', 'private'), 'owner')
    assert row['status'] == 'failed' and 'SECRET' not in row['message']
    service._SESSIONS.clear()
    with pytest.raises(ValueError, match='restarted'):
        service.snapshot(row['taskId'], 'owner')


class Toolkit:
    def __init__(self, responses, gates=None):
        self.responses = iter(responses)
        self.gates = gates or {}
        self.sent = []

    def get(self, path):
        assert path == '/api/agents/action-settings'
        return {'reasoningMode': 'headless', 'gates': self.gates}

    def post(self, path, json):
        self.sent.append((path, json))
        if path.endswith('/stop'):
            return {}
        return {'status': 'completed', 'message': next(self.responses), 'taskId': 't',
                'revision': len(self.sent), 'transport': service.TRANSPORT}


def tool(name, fn):
    return StructuredTool.from_function(fn, name=name, description='Test external executor')


def request(name, **args):
    return json.dumps({'type': 'tool_calls', 'calls': [{'name': name, 'arguments': args}]})


FINAL = json.dumps({'type': 'final', 'text': 'Observed answer'})


def test_headless_has_no_outer_mesh_or_nested_cobuild_calls(monkeypatch):
    monkeypatch.setattr(agent_runtime, 'build_llm', lambda *_: pytest.fail('outer Mesh'))
    effects = []
    def read():
        assert capability_routing.selected(NS(settings={}), 'read') == 'existing'
        effects.append(True)
        return '{"observed": 42}'
    client = Toolkit([request('read'), FINAL])
    with capability_routing.override('cobuild'):
        output = list(reasoning.run(client, [tool('read', read)], [HumanMessage('read')]))
    assert effects == [True]
    assert any(p.get('chunk', {}).get('text') == 'Observed answer' for p in output)
    assert '42' in client.sent[1][1]['message']


def test_legacy_never_calls_headless_or_cobuild(monkeypatch):
    from atk_agent_common import native_loop
    client = Toolkit([])
    monkeypatch.setattr(agent_runtime, 'build_llm', lambda _: object())
    monkeypatch.setattr(capability_routing, '_wait', lambda *a: pytest.fail('Cobuild'))
    def run(llm, tools, messages, *args):
        assert tools[0].func() == 'ok'
        yield {'chunk': {'text': 'legacy'}}
    monkeypatch.setattr(native_loop, 'run_native_loop', run)
    def read():
        assert capability_routing.selected(client, 'read') == 'existing'
        return 'ok'
    list(reasoning.run(client, [tool('read', read)], [], llm_id='mesh', selected='legacy'))
    assert client.sent == []


def test_malformed_batch_executes_nothing():
    effects = []
    def read():
        effects.append(True)
    bad = '{"type":"tool_calls","calls":[{"name":"read","arguments":{}},{"name":"native_write","arguments":{}}]}'
    with pytest.raises(ToolkitError):
        list(reasoning.run(Toolkit([bad]), [tool('read', read)], []))
    assert effects == []


def test_plan_cannot_authorize_itself_and_user_confirmation_executes_once():
    effects = []
    def plan():
        return json.dumps({'canonicalTarget': {}, 'confirm_token': 'signed'})
    def execute(confirm_token: str, confirm: bool):
        effects.append(confirm_token)
        return '{"status":"ok","auditId":42}'
    tools = [tool('plan_admin_action', plan), tool('execute_admin_action', execute)]
    execute_request = request('execute_admin_action', confirm_token='signed', confirm=True)
    client = Toolkit([request('plan_admin_action'), execute_request, FINAL])
    list(reasoning.run(client, tools, [HumanMessage('plan it')]))
    assert effects == [] and 'signed' not in client.sent[1][1]['message']
    client = Toolkit([execute_request, execute_request, FINAL])
    list(reasoning.run(client, tools, [HumanMessage('Approved — I confirm. confirm_token signed')]))
    assert effects == ['signed']


def test_fresh_sensor_denial():
    def read():
        pytest.fail('disabled sensor executed')
    client = Toolkit([request('list_hosts'), FINAL], {'list_hosts': False})
    list(reasoning.run(client, [tool('list_hosts', read)], []))
    assert 'sensor-disabled' in client.sent[1][1]['message']


def test_credential_and_host_rotation_partition_ownership():
    a = NS(host='host', api_key='a')
    b = NS(host='host', api_key='b')
    assert service.owner_key(a, 'local', 'u') != service.owner_key(b, 'local', 'u')
    assert service.owner_key(a, 'local', 'u') != service.owner_key(a, 'remote', 'u')


def test_approval_in_quoted_or_prior_history_does_not_authorize():
    def execute(confirm_token: str, confirm: bool):
        pytest.fail('Unapproved action')
    tools = [tool('execute_admin_action', execute)]
    for messages in [[HumanMessage('Explain confirm_token signed')],
                     [HumanMessage('Approved — I confirm. confirm_token signed'), HumanMessage('Do not execute')]]:
        client = Toolkit([request('execute_admin_action', confirm_token='signed', confirm=True), FINAL])
        list(reasoning.run(client, tools, messages))
        assert 'human-confirmation-required' in client.sent[1][1]['message']


def test_missing_runtime_dependency_fails_without_fallback(monkeypatch):
    monkeypatch.setattr(service, '_server', lambda: (_ for _ in ()).throw(ImportError('private path')))
    row = wait(service.submit(DSS('x'), 'owner', 'ADMINTOOLKIT', 'one'), 'owner')
    assert row['status'] == 'failed'
    assert 'ImportError' in row['message'] and 'private path' not in row['message']


def test_retired_maps_cannot_reintroduce_nested_reasoning_for_any_capability():
    from atk_agent_common import tools_impl, actuator
    names = set(tools_impl.SENSOR_DESCRIPTIONS) | set(actuator.ACTIONS)
    assert len(names) == 64
    client = NS(settings={'agent_capability_providers': dict.fromkeys(names, 'cobuild')})
    assert all(capability_routing.selected(client, name) == 'existing' for name in names)


def test_autonomous_grant_revoked_after_plan_never_executes(monkeypatch):
    from atk_agent_common.triage import auto_remediate
    monkeypatch.setattr(auto_remediate.actuator, 'plan_admin_action', lambda *a, **kw:
                        {'canonicalTarget': {}, 'confirm_token': 'signed', 'plan': {}})
    monkeypatch.setattr(auto_remediate.actuator, 'execute_admin_action', lambda *a, **kw: pytest.fail('revoked'))
    summary = {'executed': [], 'skipped': [], 'totalFreedGB': 0, 'totalObjects': 0}
    result = auto_remediate.execute_candidate(None, {'enable_red_actions': True, 'master_password': 'private'},
                summary, {'host': 'local', 'action': 'log-cleanup'}, 'run', authorization_check=lambda: False)
    assert 'revoked' in result['reason']
