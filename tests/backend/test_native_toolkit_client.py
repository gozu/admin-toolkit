"""Native tools retain route gates and host isolation without external HTTP."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import conftest  # noqa: F401
import pytest
import requests
from flask import Flask, Response, g, jsonify, request, stream_with_context

from adk_backend.native_toolkit_client import NativeToolkitClient
from atk_agent_common.errors import RedLocked, ScanTimeout


@pytest.fixture
def app(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError('Native tools must not make HTTP requests')
    monkeypatch.setattr(requests.Session, 'request', no_network)
    app = Flask(__name__)

    @app.before_request
    def attach_host():
        g.host = request.headers.get('X-DSS-Host-Id', 'local')
        if request.path == '/api/write' and request.cookies.get('unlock') != 'yes':
            return jsonify(error='advanced-locked'), 403

    @app.get('/api/hosts')
    def hosts():
        return jsonify([{'id': 'local'}, {'id': 'remote'}])

    @app.get('/api/read')
    def read():
        return jsonify(host=g.host, query=request.args.get('q'))

    @app.post('/api/auth/red/unlock')
    def unlock():
        if request.json.get('password') != 'test-password':
            return jsonify(error='invalid-password'), 401
        response = jsonify(ok=True)
        response.set_cookie('unlock', 'yes', secure=True, httponly=True, path='/api')
        return response

    @app.post('/api/write')
    def write():
        return jsonify(host=g.host, value=request.json['value'])

    @app.get('/api/stream')
    def stream():
        def generate():
            yield 'event: progress\ndata: {}\n\n'
            yield 'event: done\ndata: {"host": "%s"}\n\n' % g.host
        return Response(stream_with_context(generate()), mimetype='text/event-stream')

    return app


def test_reads_and_parallel_host_contexts(app):
    client = NativeToolkitClient({}, app)
    with app.test_request_context('/chat', headers={'Cookie': 'browser=private'}):
        g.host = 'outer'
        with ThreadPoolExecutor(max_workers=2) as pool:
            local = pool.submit(client.get, '/api/read', params={'q': 'a & b'})
            remote = pool.submit(client.get, '/api/read', host='remote')
            assert local.result() == {'host': 'local', 'query': 'a & b'}
            assert remote.result()['host'] == 'remote'
        assert g.host == 'outer'
        assert not client.session.cookies


def test_unlock_cookies_and_write_gate(app):
    locked = NativeToolkitClient({}, app)
    with pytest.raises(RedLocked):
        locked.post('/api/write', json={'value': 1})
    client = NativeToolkitClient({'master_password': 'test-password'}, app)
    assert client.post('/api/write', host='remote', json={'value': 2}) == {
        'host': 'remote', 'value': 2}
    # A different component cannot inherit the unlocked state.
    with pytest.raises(RedLocked):
        locked.post('/api/write', json={'value': 3})


def test_sse_context_is_consumed_and_closed_on_worker(app):
    client = NativeToolkitClient({}, app)
    assert client.stream_final('/api/stream', host='remote') == {'host': 'remote'}
    assert client.get('/api/read')['host'] == 'local'


def test_timeout_does_not_retry_write(app):
    entered, finish, completed = Event(), Event(), Event()
    calls = []

    @app.post('/api/slow')
    def slow():
        calls.append(1)
        entered.set()
        finish.wait(5)
        completed.set()
        return jsonify(ok=True)

    client = NativeToolkitClient({'http_timeout_s': 0.05}, app)
    try:
        with pytest.raises(requests.exceptions.Timeout):
            client._do('POST', '/api/slow')
        assert entered.is_set()
        assert calls == [1]
    finally:
        finish.set()
        assert completed.wait(2)


def test_heavy_timeout_keeps_scan_running_contract(app):
    finish = Event()

    @app.get('/api/slow')
    def slow():
        finish.wait(5)
        return jsonify(ok=True)

    client = NativeToolkitClient({'heavy_timeout_s': 0.05}, app)
    try:
        with pytest.raises(ScanTimeout):
            client.get('/api/slow', heavy=True)
    finally:
        finish.set()


def test_native_builder_ignores_external_backend_and_needs_no_sdk(monkeypatch, app):
    from adk_backend import agent_native
    import dataiku

    def no_sdk():
        raise AssertionError('Native transport must not initialize SDK webapp auth')
    monkeypatch.setattr(dataiku, 'api_client', no_sdk)
    with app.app_context():
        client = agent_native.build_client({'backend_url': 'https://elsewhere.example'})
    assert isinstance(client, NativeToolkitClient)
    assert client.list_hosts() == [{'id': 'local'}, {'id': 'remote'}]


def test_real_backend_advanced_gate_still_blocks_native_calls(monkeypatch):
    from adk_backend import agent_native
    import backend

    monkeypatch.setattr(backend, '_resolve_client', lambda host: object())
    monkeypatch.setattr(backend, '_verify_red_token', lambda token: False)
    with backend.app.app_context():
        client = agent_native.build_client({})
    assert client.get('/__ping') == {'status': 'ok'}
    with pytest.raises(RedLocked):
        client.delete('/api/tools/code-env-cleaner/PYTHON/unused-test-name')
