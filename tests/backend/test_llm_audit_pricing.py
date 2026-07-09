"""Pricing-catalog fetch failures must not kill the LLM audit.

Air-gapped or TLS-intercepted instances (corporate proxies with a private CA)
cannot reach the LiteLLM pricing URL — the audit must still list models, with
verdicts degrading to 'unknown' instead of the endpoint 500ing with zero rows.
"""

import pytest

import llm_audit
from adk_backend import caching
from adk_backend.routes import llm_tools


@pytest.fixture(autouse=True)
def _clear_pricing_cache():
    caching._CACHE.clear()
    caching._CACHE_INFLIGHT.clear()
    caching._CACHE_INFLIGHT_ERRORS.clear()
    yield


def _collect_events(events):
    def add_event(step, message, level='info', project_key=None):
        events.append((step, level, message))
    return add_event


def test_pricing_fetch_failure_degrades_to_empty_lookup(monkeypatch):
    def _boom(timeout=30):
        raise llm_audit.PricingFetchError(
            '<urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed>')

    monkeypatch.setattr(llm_tools.llm_audit, 'build_lookup', _boom)
    events = []
    lookup, fetched_at, error = llm_tools._fetch_pricing_lookup(_collect_events(events))
    assert lookup == {}
    assert fetched_at is None
    assert 'CERTIFICATE_VERIFY_FAILED' in error
    assert any(step == 'pricing_fetch_failed' and level == 'warn' for step, level, _ in events)


def test_llm_audit_route_returns_rows_when_pricing_unreachable(monkeypatch):
    """The customer-reported failure: TLS-intercepted network → the endpoint
    must still 200 with the scanned model rows, flagging pricingError."""
    from flask import Flask, g

    app = Flask(__name__)
    app.register_blueprint(llm_tools.bp)

    class _FakeProject:
        def list_llms(self):
            return [{
                'id': 'openai:conn:gpt-4o',
                'type': 'OPENAI',
                'connection': 'conn',
                'model': 'gpt-4o',
                'friendlyName': 'GPT-4o',
            }]

        def __getattr__(self, name):
            # Reference-scan asset fetches are per-asset guarded — let them fall
            # back to empty rather than defining every list_* method here.
            raise AttributeError(name)

    class _FakeClient:
        def list_connections(self):
            return {}

        def list_projects(self):
            return [{'projectKey': 'P1', 'name': 'P One'}]

        def get_project(self, project_key):
            return _FakeProject()

    def _boom(timeout=30):
        raise llm_audit.PricingFetchError(
            '<urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed>')

    monkeypatch.setattr(llm_tools.llm_audit, 'build_lookup', _boom)

    with app.test_request_context('/api/llm-audit'):
        g.client = _FakeClient()
        resp = llm_tools.api_llm_audit()

    assert resp.status_code == 200
    payload = resp.get_json()
    assert 'CERTIFICATE_VERIFY_FAILED' in (payload['pricingError'] or '')
    assert len(payload['rows']) == 1
    assert payload['rows'][0]['status'] == 'unknown'
    assert 'CERTIFICATE_VERIFY_FAILED' in payload['summary']['pricingError']


def test_pricing_fetch_success_passes_lookup_through(monkeypatch):
    monkeypatch.setattr(
        llm_tools.llm_audit, 'build_lookup', lambda timeout=30: {'gpt-4o': {'input': 1}})
    events = []
    lookup, fetched_at, error = llm_tools._fetch_pricing_lookup(_collect_events(events))
    assert lookup == {'gpt-4o': {'input': 1}}
    assert error is None
    assert fetched_at
    assert any(step == 'pricing_ready' for step, _, _ in events)
