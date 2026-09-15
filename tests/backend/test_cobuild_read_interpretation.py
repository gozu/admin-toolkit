"""Read availability, authorization and asynchronous interpretation boundaries."""
import json
from concurrent.futures import Future
from types import SimpleNamespace

import conftest  # noqa: F401
import pytest

from atk_agent_common import capability_routing as routing
from atk_agent_common import read_interpretation as reads
from atk_agent_common.agent_runtime import _result_event
from adk_backend import cobuild_bridge as bridge


@pytest.mark.parametrize('name,args,wanted', [
    ('toolkit_get', {'endpoint': 'version'}, True),
    ('toolkit_get', {'endpoint': 'logs'}, False),
    ('toolkit_get', {'endpoint': 'version', 'fields': ['version']}, False),
    ('list_hosts', {'probe': False}, True), ('list_hosts', {'probe': True}, False),
    ('list_capabilities', {}, True), ('image-delete', {}, False),
])
def test_pilot_is_narrow(name, args, wanted):
    assert reads.supports(name, args) is wanted


def test_read_returns_before_inference_and_keeps_complete_output():
    effects = []
    class Client:
        settings = {}
        def get(self, path):
            return {'gates': {'list_hosts': True}}
        def post(self, path, host, json):
            effects.append(('interpret', host, json))
            return {'status': 'pending', 'turnId': 'a', 'viewTicket': 'ticket'}
    @routing.sensor
    def list_hosts(client, probe=False):
        effects.append('read')
        return {'count': 1, 'hosts': [{'id': 'a', 'url': 'https://private.invalid'}], 'partial': True}
    with routing.override('cobuild'):
        out = list_hosts(Client())
    assert effects[0] == 'read' and len(effects) == 2
    assert out['hosts'][0]['url'] == 'https://private.invalid' and out['partial']
    assert out['executionRoute']['interpretation']['status'] == 'pending'
    facts, _ = reads.evidence('list_hosts', out)
    assert facts == {'hasError': False, 'partial': True, 'count': 1, 'hostIds': ['a']}
    assert 'private.invalid' not in json.dumps(facts)
    events = _result_event('list_hosts', out, 10)
    assert events[1]['chunk']['eventKind'] == 'read_interpretation'


@pytest.mark.parametrize('permissions', [{'gates': {'list_hosts': False}}, {}, None])
def test_live_gate_failure_prevents_read(permissions):
    client = SimpleNamespace(settings={}, get=lambda p: permissions)
    @routing.sensor
    def list_hosts(client, probe=False):
        pytest.fail('disabled read executed')
    with routing.override('cobuild'), pytest.raises(routing.CapabilityRouteError):
        list_hosts(client)


def test_submission_failure_preserves_error_and_never_repeats_read():
    calls = []
    def fail(*args, **kwargs):
        raise TimeoutError('private URL and secret')
    client = SimpleNamespace(settings={}, get=lambda p: {'gates': {}}, post=fail)
    @routing.sensor
    def list_hosts(client, probe=False):
        calls.append(1)
        return {'error': {'message': 'unavailable'}, 'hosts': [], 'count': 0}
    with routing.override('cobuild'):
        out = list_hosts(client)
    assert calls == [1] and out['error']['message'] == 'unavailable'
    assert out['executionRoute']['interpretation']['status'] == 'failed'
    assert 'private URL' not in json.dumps(out)


def test_ticket_cannot_cross_hosts_read_operation_turns_or_submit_results(monkeypatch):
    future = Future()
    monkeypatch.setattr(bridge, '_TURNS', {})
    monkeypatch.setattr(bridge, '_pool', lambda: SimpleNamespace(submit=lambda *a: future))
    client = SimpleNamespace(host='https://instance.invalid')
    job = bridge.submit(client, 'kernel', 'project', 'list_hosts', 'interpret', {}, 'req',
                        result={'hosts': [], 'count': 0}, host='remote')
    assert bridge.status(job['turnId'], 'browser')['status'] == 'unknown'
    for host, ticket in [('local', job['viewTicket']), ('remote', 'wrong')]:
        assert bridge.read_status(client, host, job['turnId'], ticket)['status'] == 'unknown'
    assert bridge.read_status(client, 'remote', job['turnId'], job['viewTicket'])['status'] == 'pending'
    future.set_result({'status': 'completed', 'summary': 'No hosts.'})
    assert bridge.read_status(client, 'remote', job['turnId'], job['viewTicket'])['summary'] == 'No hosts.'
    with pytest.raises(ValueError):
        bridge.submit_result(job['turnId'], 'kernel', {'ok': True})
    bridge._TURNS[job['turnId']]['created'] -= 601
    assert bridge.read_status(client, 'remote', job['turnId'], job['viewTicket'])['status'] == 'unknown'


@pytest.mark.parametrize('mutation,category', [
    ({'facts': {'count': 0}}, 'mismatch/facts'),
    ({'request_id': 'other'}, 'mismatch/request_id'),
    ({'evidence_sha256': 'other'}, 'mismatch/evidence_sha256'),
    ({'summary': ''}, 'invalid-summary'),
    ({'summary': 42}, 'invalid-summary'),
    ('', 'empty-response'), ('not JSON', 'malformed-json'), ([], 'non-object'),
])
def test_invalid_interpretations_fail_with_specific_safe_diagnostics(mutation, category):
    facts, digest = reads.evidence('list_hosts', {'count': 1, 'hosts': [{'id': 'a'}]})
    good = {'type': 'read_interpretation', 'request_id': 'req',
            'evidence_sha256': digest, 'facts': facts, 'summary': 'One host.'}
    value = dict(good, **mutation) if isinstance(mutation, dict) else mutation
    text = value if isinstance(value, str) else json.dumps(value)
    response = SimpleNamespace(message=text, is_error=False)
    conversation = SimpleNamespace(conversation_id='conv', send_message=lambda *a, **k: response)
    client = SimpleNamespace(get_project=lambda p: SimpleNamespace(new_cobuild_conversation=lambda: conversation))
    with pytest.raises(ValueError, match=category):
        bridge.interpret_read(client, 'project', 'list_hosts', facts, digest, 'req')


def test_compact_interpretation_uses_one_message_and_preserves_fact_types():
    facts, digest = reads.evidence('toolkit_get', {'version': '1', 'runningVersion': '1', 'backendStale': False})
    prompts = []
    def send(prompt, allow_edit_project):
        assert allow_edit_project is False
        prompts.append(prompt)
        value = json.loads(prompt.split('\n', 1)[1])
        value['summary'] = 'Running version 1 is current.'
        return SimpleNamespace(message=json.dumps(value), is_error=False)
    conversation = SimpleNamespace(conversation_id='conv', send_message=send)
    client = SimpleNamespace(get_project=lambda p: SimpleNamespace(new_cobuild_conversation=lambda: conversation))
    out = bridge.interpret_read(client, 'project', 'toolkit_get', facts, digest, 'req')
    assert len(prompts) == 1 and out['facts']['backendStale'] is False
    assert out['summary'] == 'Running version 1 is current.'


def test_pilot_shares_worker_capacity_with_old_bridge(monkeypatch):
    monkeypatch.setattr(bridge, '_TURNS', {str(i): {'future': Future(), 'created': 0} for i in range(8)})
    with pytest.raises(ValueError, match='capacity'):
        bridge.submit(None, 'owner', 'project', 'list_hosts', 'interpret', {}, 'req',
                      result={'hosts': [], 'count': 0})
