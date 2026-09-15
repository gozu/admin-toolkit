"""Fused reads preserve deterministic output and request security boundaries."""
import json
import threading
from concurrent.futures import Future
from types import SimpleNamespace

import conftest  # noqa: F401
import pytest
from flask import Flask, g, jsonify, request

from adk_backend import cobuild_bridge as bridge, read_tasks as tasks
from adk_backend.routes import agent_gates
from atk_agent_common import capability_routing as routing, read_tasks, tools_impl


@pytest.fixture
def app(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(agent_gates.bp)
    monkeypatch.setattr(agent_gates, '_plugin_config', lambda: {})
    @app.before_request
    def attach():
        g.host_id = request.headers.get('X-DSS-Host-Id', 'local')
        g.client = SimpleNamespace(host=g.host_id)
        if request.path == '/api/plugins' and request.headers.get('Authorization') != 'test-ticket':
            return jsonify(error='locked'), 403
    @app.route('/api/version')
    def version():
        return jsonify(version='1', runningVersion='1', backendStale=False)
    @app.route('/api/hosts')
    def hosts():
        return jsonify([{'id': 'local', 'url': 'private-url', 'label': 'private-label'}])
    @app.route('/api/plugins')
    def plugins():
        return jsonify(pluginsCount=1, pluginDetails=[{'id': 'private-plugin'}])
    return app


def test_batch_keeps_data_and_submits_one_allowlisted_summary(app, monkeypatch):
    jobs = []
    def submit(*args):
        jobs.append(args)
        return {'status': 'pending', 'turnId': 'job', 'viewTicket': 'ticket'}
    monkeypatch.setattr(bridge, 'submit_read_facts', submit)
    response = app.test_client().post('/api/agents/read-task', headers={'Authorization': 'test-ticket'}, json={
        'operations': [{'name': 'list_hosts'}, {'name': 'config_inspect', 'arguments': {'domain': 'plugins'}}]})
    assert response.status_code == 200
    out = response.get_json()
    assert out['results'][0]['data']['hosts'][0]['url'] == 'private-url'
    assert out['results'][1]['data']['plugins'][0]['id'] == 'private-plugin'
    assert out['executionRoute']['readCount'] == 2
    assert len(jobs) == 1
    assert 'private-' not in json.dumps(jobs[0][4])


@pytest.mark.parametrize('operation', [
    {'name': 'image-delete'}, {'name': 'list_hosts', 'arguments': {'probe': True}},
    {'name': 'toolkit_get', 'arguments': {'endpoint': 'logs'}},
    {'name': 'config_inspect', 'arguments': {'domain': 'plugins', 'top_n': 10000}},
    {'name': 'k8s_health', 'arguments': {'cluster': 'deep-scan'}},
    {'name': 'config_inspect', 'arguments': {'domain': 'projects', 'host': 'other'}},
])
def test_invalid_batch_runs_nothing(app, monkeypatch, operation):
    monkeypatch.setattr(tools_impl, 'list_hosts', lambda *a, **k: pytest.fail('read executed'))
    response = app.test_client().post('/api/agents/read-task', json={
        'operations': [{'name': 'list_hosts'}, operation]})
    assert response.status_code == 400


@pytest.mark.parametrize('gates,status', [('not-json', 503), ('[]', 503), ('{"list_hosts":"false"}', 503),
                                         ('{"list_hosts":false}', 403)])
def test_permissions_fail_closed_before_read(app, monkeypatch, gates, status):
    monkeypatch.setattr(agent_gates, '_plugin_config', lambda: {'agent_action_gates': gates})
    monkeypatch.setattr(tools_impl, 'list_hosts', lambda *a, **k: pytest.fail('read executed'))
    assert app.test_client().post('/api/agents/read-task', json={
        'operations': [{'name': 'list_hosts'}]}).status_code == status


def test_nested_dispatch_preserves_access_gate_and_forwards_authorization(app, monkeypatch):
    monkeypatch.setattr(bridge, 'submit_read_facts', lambda *a: {'status': 'pending'})
    body = {'operations': [{'name': 'config_inspect', 'arguments': {'domain': 'plugins'}}]}
    assert app.test_client().post('/api/agents/read-task', json=body).status_code == 403
    assert app.test_client().post('/api/agents/read-task', json=body,
                                  headers={'Authorization': 'test-ticket'}).status_code == 200


def test_nested_dispatch_restores_parent_client_and_target_host(app):
    with app.test_request_context('/', headers={'X-DSS-Host-Id': 'remote'}):
        g.client, g.host_id = 'parent', 'remote'
        client = tasks.LocalReadClient(app, {'Authorization': 'test-ticket'}, 'remote', {}, {}, {})
        assert client.get('/api/plugins', host='remote')['pluginsCount'] == 1
        assert g.client == 'parent' and g.host_id == 'remote'
        with pytest.raises(Exception, match='unsupported'):
            client.get('/api/plugins', host='other')


def test_reads_execute_concurrently_and_interpretation_failure_retains_results(app, monkeypatch):
    barrier = threading.Barrier(2)
    def list_hosts(client, probe=False):
        barrier.wait(timeout=3)
        return {'hosts': [], 'count': 0}
    def list_capabilities(client):
        barrier.wait(timeout=3)
        return {'sensors': [], 'actions': [], 'toolkitPages': {}}
    def failed(*args):
        raise RuntimeError('secret')
    monkeypatch.setattr(tools_impl, 'list_hosts', list_hosts)
    monkeypatch.setattr(tools_impl, 'list_capabilities', list_capabilities)
    monkeypatch.setattr(bridge, 'submit_read_facts', failed)
    response = app.test_client().post('/api/agents/read-task', json={'operations': [
        {'name': 'list_hosts'}, {'name': 'list_capabilities'}]})
    out = response.get_json()
    assert all('error' not in row['data'] for row in out['results'])
    assert out['executionRoute']['interpretation']['status'] == 'failed'
    assert 'secret' not in json.dumps(out)


def test_sensor_uses_one_task_request_and_keeps_raw_output():
    calls = []
    def task(operations, host):
        calls.append((operations, host))
        return {'results': [{'data': {'hosts': [], 'count': 0}}],
                'executionRoute': {'variant': 'read-task', 'interpretation': {'status': 'pending'}}}
    client = SimpleNamespace(read_task=task)
    with routing.override('cobuild'):
        out = tools_impl.list_hosts(client)
    assert len(calls) == 1 and out['count'] == 0
    assert out['executionRoute']['variant'] == 'read-task'


def test_stream_wakes_on_completion_and_checks_ticket(app, monkeypatch):
    future = Future()
    monkeypatch.setattr(bridge, '_TURNS', {})
    monkeypatch.setattr(bridge, '_pool', lambda: SimpleNamespace(submit=lambda *a: future))
    job = bridge.submit_read_facts(SimpleNamespace(host='local'), 'owner', 'project',
                                   'read task', {}, 'digest', 'req')
    bad = app.test_client().post('/api/agents/cobuild-read-stream', json=dict(job, viewTicket='bad'))
    assert b'unknown' in bad.data and b'digest' not in bad.data
    future.set_result({'status': 'completed', 'summary': 'Ready.'})
    good = app.test_client().post('/api/agents/cobuild-read-stream', json=job)
    assert good.mimetype == 'text/event-stream'
    assert b'event: done' in good.data and b'Ready.' in good.data


def test_health_evidence_does_not_include_raw_configuration():
    facts = read_tasks.facts('config_inspect', {'domain': 'connections'}, {
        'connections': [{'name': 'private', 'password': 'secret'}], 'partial': True})
    assert facts['connectionsReturned'] == 1 and facts['partial'] is True
    assert 'secret' not in json.dumps(facts) and 'private' not in json.dumps(facts)
