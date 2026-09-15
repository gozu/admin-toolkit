"""Execution path parity and boundary tests; no real mutations or model calls."""
import json
from types import SimpleNamespace

import conftest  # noqa: F401
import pytest

from atk_agent_common import capability_routing as routing, tools_impl, actions
from adk_backend import cobuild_bridge as bridge
from adk_backend.routes import agent_gates

KNOWN = set(tools_impl.SENSOR_DESCRIPTIONS) | set(actions.SHAPES)


def response(value, **kwargs):
    return SimpleNamespace(message=json.dumps(value), is_error=False,
                           is_confirmation_request=False, is_question_request=False, **kwargs)


@pytest.mark.parametrize('name', sorted(KNOWN))
def test_all_capabilities_validate_exact_handoff_and_reject_drift(name):
    args = {'target': {'name': 'disposable-fixture', 'enabled': True}}
    phase = 'read' if name in tools_impl.SENSOR_DESCRIPTIONS else 'execute'
    value = {'type': 'tool_request', 'name': name, 'phase': phase,
             'request_id': 'request-a', 'arguments': args}
    assert bridge.validate_response(response(value), name, phase, args, 'request-a') == ''
    for changed in [dict(value, name='other'), dict(value, request_id='request-b'),
                    dict(value, arguments={'target': {'name': 'other', 'enabled': True}}),
                    dict(value, arguments={'target': {'name': 'disposable-fixture', 'enabled': 1}})]:
        with pytest.raises(ValueError):
            bridge.validate_response(response(changed), name, phase, args, 'request-a')


def test_provider_switch_never_changes_permissions():
    changed = routing.merge_providers({}, dict.fromkeys(KNOWN, 'cobuild'), KNOWN)
    sensors, rows = agent_gates._catalog({}, {}, changed)
    assert len(sensors) + len(rows) == 64
    assert all(s['provider'] == 'cobuild' and s['enabled'] for s in sensors)
    assert all(a['provider'] == 'cobuild' and not a['enabled'] and not a['autonomous'] for a in rows)
    with pytest.raises(ValueError):
        routing.merge_providers({}, {'unknown': 'cobuild'}, KNOWN)
    with pytest.raises(ValueError):
        routing.merge_providers({}, {'instance_health': 'automatic'}, KNOWN)


class Client:
    settings = {}


def test_existing_route_is_unchanged_and_makes_no_inference_call(monkeypatch):
    monkeypatch.setattr(routing, '_wait', lambda *a: pytest.fail('unexpected Cobuild call'))
    @routing.sensor
    def sample(client, host='local', limit=1):
        return {'host': host, 'rows': list(range(limit))}
    assert sample(Client(), limit=2) == {'host': 'local', 'rows': [0, 1]}


def test_cobuild_route_executes_once_and_preserves_output(monkeypatch):
    calls = []
    def transport(client, payload, host):
        calls.append((payload, host))
        return {'status': 'completed', 'turnId': 'turn-a', 'conversationId': 'conv-a',
                'seconds': 1, 'summary': 'Observed two rows.'}
    monkeypatch.setattr(routing, '_wait', transport)
    effects = []
    @routing.sensor
    def sample(client, host='local', limit=2):
        effects.append(host)
        return {'rows': [1, 2], 'confirm_token': 'must-not-reach-cobuild'}
    with routing.override('cobuild'):
        out = sample(Client(), host='fixture')
    assert effects == ['fixture']
    assert out['rows'] == [1, 2]
    assert out['executionRoute']['executor'] == 'adtk'
    assert out['executionRoute']['resultAcknowledged'] is True
    assert calls[0][1] == calls[1][1] == 'fixture'
    assert 'confirm_token' not in calls[1][0]['result']
    assert 'must-not-reach-cobuild' not in json.dumps(calls)


def test_failed_request_does_not_execute_or_fall_back(monkeypatch):
    def fail(*args):
        raise routing.CapabilityRouteError('Credits unavailable')
    monkeypatch.setattr(routing, '_wait', fail)
    @routing.sensor
    def sample(client):
        pytest.fail('operation must not execute')
    with routing.override('cobuild'), pytest.raises(routing.CapabilityRouteError):
        sample(Client())


def test_failed_ack_preserves_actual_outcome_and_never_repeats_execution(monkeypatch):
    def fail(*args):
        raise routing.CapabilityRouteError('Explanation failed')
    monkeypatch.setattr(routing, '_wait', fail)
    out = routing.finish_operation(Client(), {'status': 'ok', 'auditId': 1},
                                   {'provider': 'cobuild', 'turnId': 'turn-a'})
    assert out['status'] == 'ok' and out['auditId'] == 1
    assert out['executionRoute']['resultAcknowledged'] is False


def test_provider_overrides_do_not_leak_between_calls():
    client = Client()
    with routing.override('cobuild'):
        assert routing.selected(client, 'instance_health') == 'cobuild'
        with routing.override('existing'):
            assert routing.selected(client, 'instance_health') == 'existing'
        assert routing.selected(client, 'instance_health') == 'cobuild'
    assert routing.selected(client, 'instance_health') == 'existing'


def test_abort_text_is_not_successful_handoff():
    aborted = SimpleNamespace(message='Operation was aborted by the user.', is_error=False,
                              is_confirmation_request=False, is_question_request=False)
    with pytest.raises(ValueError):
        bridge.validate_response(aborted, 'list_hosts', 'read', {}, 'id')


def test_route_owner_isolated_by_host_and_caller():
    client = SimpleNamespace(host='https://instance.invalid')
    assert bridge.owner_key(client, 'a', 'session') != bridge.owner_key(client, 'b', 'session')
    assert bridge.owner_key(client, 'a', 'session') != bridge.owner_key(client, 'a', 'other')


def test_unrelated_turn_does_not_evict_long_running_operation(monkeypatch):
    from concurrent.futures import Future
    conversation = object()

    def submit(fn, *args):
        future = Future()
        future.set_result({'_conversation': conversation})
        return future

    now = [0]
    monkeypatch.setattr(bridge, '_TURNS', {})
    monkeypatch.setattr(bridge, '_pool', lambda: SimpleNamespace(submit=submit))
    monkeypatch.setattr(bridge.time, 'monotonic', lambda: now[0])
    operation = bridge.submit(None, 'owner', 'project', 'cluster-start', 'execute', {}, 'id')
    now[0] = 1800
    bridge.submit(None, 'owner', 'project', 'list_hosts', 'read', {}, 'other')
    result = bridge.submit_result(operation['turnId'], 'owner', {'ok': True})
    assert result['status'] == 'pending'
    assert bridge.submit_result(operation['turnId'], 'owner', {'ok': True}) == result
    now[0] = 5500
    bridge.submit(None, 'owner', 'project', 'list_hosts', 'read', {}, 'third')
    assert operation['turnId'] not in bridge._TURNS
