"""Agent tools must authenticate to protected DSS webapps in both runtimes."""

import json
from types import SimpleNamespace

import conftest  # noqa: F401
import dataiku
import pytest
import requests
from requests.auth import HTTPBasicAuth

from atk_agent_common.client import ToolkitClient
from atk_agent_common.errors import BackendAuthenticationError

EXTERNAL = 'https://dss.example'
INTERNAL = 'http://127.0.0.1:10000'
WEBAPP = '/web-apps-backends/ADMIN/toolkit'


def install_sdk(monkeypatch, auth='ticket', external=EXTERNAL):
    session = requests.Session()
    if auth == 'ticket':
        session.headers['X-DKU-APITicket'] = 'test-ticket'
    else:
        session.auth = HTTPBasicAuth('test-key', '')
    session.verify = '/test/dss-ca.pem'
    seen = []
    backend = SimpleNamespace(base_url=INTERNAL + WEBAPP + '/', session=session)

    def project(key):
        seen.append(key)

        def webapp(key):
            seen.append(key)
            return SimpleNamespace(get_backend_client=lambda: backend)
        return SimpleNamespace(get_webapp=webapp)

    client = SimpleNamespace(host=INTERNAL, get_project=project,
                             get_general_settings=lambda: SimpleNamespace(
                                 get_raw=lambda: {'studioExternalUrl': external}))
    monkeypatch.setattr(dataiku, 'api_client', lambda: client)
    return session, seen


@pytest.mark.parametrize('auth', ['ticket', 'key'])
def test_protected_host_list_uses_sdk_credentials(monkeypatch, auth):
    sdk_session, seen = install_sdk(monkeypatch, auth)
    client = ToolkitClient({'backend_url': EXTERNAL + WEBAPP})
    calls = []

    def send(request, **kwargs):
        calls.append(request)
        authenticated = (request.headers.get('X-DKU-APITicket') == 'test-ticket'
                         or request.headers.get('Authorization') == 'Basic dGVzdC1rZXk6')
        resp = requests.Response()
        resp.status_code = 200 if authenticated else 401
        resp._content = json.dumps([{'id': 'local'}] if authenticated else {}).encode()
        assert kwargs['allow_redirects'] is False
        return resp

    monkeypatch.setattr(client.session, 'send', send)
    assert client.list_hosts() == [{'id': 'local'}]
    assert calls[0].url == INTERNAL + WEBAPP + '/api/hosts'
    assert seen == ['ADMIN', 'toolkit']
    assert client.session.verify == '/test/dss-ca.pem'
    assert client.session is not sdk_session
    client.session.cookies.set('unlock', 'test')
    assert not sdk_session.cookies
    assert sdk_session.headers['Connection'] != 'close'


def test_tls_opt_out_preserved(monkeypatch):
    install_sdk(monkeypatch)
    client = ToolkitClient({'backend_url': EXTERNAL + WEBAPP, 'verify_tls': False})
    assert client.session.verify is False


def test_wrong_instance_setting_fails_closed(monkeypatch):
    install_sdk(monkeypatch)
    with pytest.raises(BackendAuthenticationError, match='does not match'):
        ToolkitClient({'backend_url': 'https://other.example' + WEBAPP})


def test_sdk_failure_does_not_expose_secret_or_fall_back(monkeypatch):
    def fail():
        raise RuntimeError('secret-ticket')
    monkeypatch.setattr(dataiku, 'api_client', fail)
    with pytest.raises(BackendAuthenticationError) as exc:
        ToolkitClient({'backend_url': EXTERNAL + WEBAPP})
    assert 'secret-ticket' not in str(exc.value.to_output())


@pytest.mark.parametrize('payload', [b'<html>Not authenticated</html>', b'[]', b'{}'])
@pytest.mark.parametrize('verb', ['get', 'get_text', 'stream_final'])
def test_401_is_structured_without_unlock_or_retry(monkeypatch, payload, verb):
    client = ToolkitClient({'backend_url': 'http://backend.test'})
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url))
        resp = requests.Response()
        resp.status_code = 401
        resp._content = payload
        return resp

    monkeypatch.setattr(client.session, 'request', request)
    with pytest.raises(BackendAuthenticationError) as exc:
        getattr(client, verb)('/api/hosts')
    assert len(calls) == 1
    output = exc.value.to_output()['error']
    assert output['code'] == 'backend-authentication-failed'
    assert 'does not establish' in output['message']


def test_redirect_is_not_followed(monkeypatch):
    client = ToolkitClient({'backend_url': 'http://backend.test'})

    def request(method, url, **kwargs):
        assert kwargs['allow_redirects'] is False
        resp = requests.Response()
        resp.status_code = 302
        resp.headers['Location'] = 'https://other.example'
        return resp

    monkeypatch.setattr(client.session, 'request', request)
    with pytest.raises(BackendAuthenticationError, match='redirected'):
        client.list_hosts()
