"""ToolkitClient HTTP resilience: bare ConnectionError is retried ONCE for
reads (and opt-in idempotent writes), never for plain writes, and never for a
ConnectTimeout. These lock the fixes for the ~13ms host-unreachable flapping
seen live: pooled keep-alive sockets die during long inter-tool LLM pauses, so
the client sends Connection: close and retries the one instant failure.

Clause order is load-bearing: ConnectTimeout subclasses BOTH Timeout and
ConnectionError, and must re-raise (as a Timeout) without ever sleep-retrying.
"""

import conftest  # noqa: F401  (installs the python-lib path + DSS stubs)

import pytest
import requests

from atk_agent_common import client as client_mod
from atk_agent_common.client import ToolkitClient
from atk_agent_common.errors import UnreachableHost


class _Resp:
    status_code = 200


def _client(monkeypatch):
    c = ToolkitClient({'backend_url': 'http://backend.test'})
    sleeps = {'n': 0}

    def fake_sleep(_):
        sleeps['n'] += 1

    monkeypatch.setattr(client_mod.time, 'sleep', fake_sleep)
    return c, sleeps


def _install_request(monkeypatch, c, seq):
    """seq: list of Exception instances (raise) or _Resp (return)."""
    state = {'i': 0}

    def fake_request(method, url, **kw):
        i = state['i']
        state['i'] += 1
        item = seq[i]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(c.session, 'request', fake_request)
    return state


def test_get_retried_once_then_succeeds(monkeypatch):
    c, sleeps = _client(monkeypatch)
    state = _install_request(monkeypatch, c,
                             [requests.exceptions.ConnectionError('boom'), _Resp()])
    resp = c._do('GET', '/api/x')
    assert resp.status_code == 200
    assert state['i'] == 2  # initial + one retry
    assert sleeps['n'] == 1


def test_post_not_retried(monkeypatch):
    c, sleeps = _client(monkeypatch)
    state = _install_request(monkeypatch, c,
                             [requests.exceptions.ConnectionError('boom')])
    with pytest.raises(UnreachableHost) as ei:
        c._do('POST', '/api/x')
    assert state['i'] == 1  # no retry
    assert sleeps['n'] == 0
    assert 'NOT auto-retried' in ei.value.message


def test_retry_safe_post_is_retried(monkeypatch):
    c, sleeps = _client(monkeypatch)
    state = _install_request(monkeypatch, c,
                             [requests.exceptions.ConnectionError('boom'), _Resp()])
    resp = c._do('POST', '/api/x', retry_safe=True)
    assert resp.status_code == 200
    assert state['i'] == 2
    assert sleeps['n'] == 1


def test_get_gives_up_after_one_retry(monkeypatch):
    c, sleeps = _client(monkeypatch)
    state = _install_request(monkeypatch, c,
                             [requests.exceptions.ConnectionError('a'),
                              requests.exceptions.ConnectionError('b')])
    with pytest.raises(UnreachableHost) as ei:
        c._do('GET', '/api/x')
    assert state['i'] == 2  # initial + exactly one retry, then give up
    assert sleeps['n'] == 1
    assert 'auto-retried once' in ei.value.message


def test_connect_timeout_never_retried(monkeypatch):
    # ConnectTimeout is a subclass of both Timeout and ConnectionError; the
    # Timeout clause must win so it is re-raised and never sleep-retried.
    c, sleeps = _client(monkeypatch)
    state = _install_request(monkeypatch, c,
                             [requests.exceptions.ConnectTimeout('slow')])
    with pytest.raises(requests.exceptions.Timeout):
        c._do('GET', '/api/x')
    assert state['i'] == 1
    assert sleeps['n'] == 0


def test_session_sends_connection_close(monkeypatch):
    c, _ = _client(monkeypatch)
    assert c.session.headers.get('Connection') == 'close'
