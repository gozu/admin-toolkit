import copy
import json
import queue
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from flask import Flask

from adk_backend import product_analytics as analytics
from adk_backend.routes.settings import bp


@pytest.fixture
def setup(monkeypatch):
    raw = {'config': {}}
    settings = SimpleNamespace(get_raw=lambda: raw, save=Mock())
    client = SimpleNamespace(
        host='http://127.0.0.1:11000',
        get_plugin=lambda _: SimpleNamespace(get_settings=lambda: settings),
        get_general_settings=lambda: SimpleNamespace(get_raw=lambda: {'studioExternalUrl': 'https://tam-global.example.org'}),
    )
    monkeypatch.setattr(analytics, '_local_thread_client', lambda: client)
    monkeypatch.setattr(analytics, '_CONFIG', None)
    monkeypatch.setattr(analytics, '_CONFIG_AT', 0)
    monkeypatch.setattr(analytics, '_GENERATION', 0)
    monkeypatch.setattr(analytics, '_QUEUE', queue.Queue(maxsize=1000))
    monkeypatch.setattr(analytics, '_SEEN', {})
    monkeypatch.setattr(analytics, '_WORKER', SimpleNamespace(is_alive=lambda: True))
    return raw, settings, client


def batch(event='adtk_module_opened', **properties):
    return {'browser_id': str(uuid4()), 'session_id': str(uuid4()), 'events': [{
        'uuid': str(uuid4()), 'event': event, 'timestamp': datetime.now(timezone.utc).isoformat(),
        'host_id': 'customer-secret-host', 'properties': properties,
    }]}


@pytest.mark.parametrize('host, expected', [
    ('https://tam-global.dataiku.example', 'internal'), ('https://TAMGLOBAL.example:443/', 'internal'),
    ('https://akaos.example', 'internal'), ('https://customer.example', 'customer'),
    ('https://customer.example/akaos', 'customer'), ('https://not-akaos.example', 'customer'),
    ('https://tam-global-customer.example', 'customer'),
])
def test_classification(host, expected):
    assert analytics.classify_installation(host) == expected


def test_opt_out_survives_restart_drops_queue_and_never_resolves_identity(setup):
    raw, settings, _ = setup
    assert analytics.status() == {'enabled': True, 'configured': True, 'audience': 'internal'}
    secret = raw['config']['usage_analytics_secret']
    assert analytics.accept_batch(batch(), 'alice@example.org') == 1
    assert analytics.set_enabled(False)['enabled'] is False
    assert analytics._QUEUE.empty()
    analytics._CONFIG = None
    assert analytics.status()['enabled'] is False
    assert analytics.accept_batch(batch(), 'alice@example.org') == 0
    assert raw['config']['usage_analytics_secret'] == secret
    assert settings.save.call_count == 2


def test_stable_ids_no_pii_and_internal_origin_on_remote_host(setup):
    source = batch(module_id='projects', email='alice@example.org', name='secret project',
                   error='database password', url='https://customer.example', duration_ms=75,
                   audience='customer', plugin_version='forged', installation_id='forged')
    analytics.accept_batch(source, 'alice@example.org')
    _, first = analytics._QUEUE.get_nowait()
    analytics._CONFIG = None  # restart uses saved secret
    second_source = batch(module_id='projects')
    analytics.accept_batch(second_source, 'alice@example.org')
    _, second = analytics._QUEUE.get_nowait()
    props = first['properties']
    assert props['distinct_id'] == second['properties']['distinct_id']
    assert props['installation_id'] == second['properties']['installation_id']
    assert props['audience'] == 'internal'
    assert props['plugin_version'] == analytics.BUILD_VERSION
    assert props['identity_type'] == 'dss_user'
    assert props['$geoip_disable'] is True
    for secret in ['alice', 'customer-secret-host', 'secret project', 'password', 'forged', 'https://']:
        assert secret not in json.dumps(first)


def test_unidentified_browsers_are_distinct_and_retries_deduplicate(setup):
    first = batch()
    assert analytics.accept_batch(first, '__anonymous__') == 1
    assert analytics.accept_batch(first, '__anonymous__') == 0
    _, event1 = analytics._QUEUE.get_nowait()
    analytics.accept_batch(batch(), '__anonymous__')
    _, event2 = analytics._QUEUE.get_nowait()
    assert event1['properties']['distinct_id'] != event2['properties']['distinct_id']
    assert event1['properties']['identity_type'] == 'browser'


def test_configuration_failure_and_full_queue_do_not_block_product(setup, monkeypatch):
    monkeypatch.setattr(analytics, '_QUEUE', queue.Queue(maxsize=1))
    assert analytics.accept_batch(batch(), 'alice') == 1
    assert analytics.accept_batch(batch(), 'alice') == 0
    analytics._CONFIG = None
    monkeypatch.setattr(analytics, '_local_thread_client', Mock(side_effect=RuntimeError('offline')))
    assert analytics.status()['available'] is False
    assert analytics.accept_batch(batch(), 'alice') == 0


@pytest.mark.parametrize('bad', [None, [], {'events': 'wrong'}, {'events': [None]}, {'events': [{}]}])
def test_invalid_batches_are_rejected(setup, bad):
    with pytest.raises(ValueError):
        analytics.accept_batch(bad, 'alice')
    assert analytics._QUEUE.empty()


def test_batch_is_atomic_and_properties_are_bounded(setup):
    source = batch(duration_ms=float('inf'), module_id='projects', arbitrary={'secret': 'value'})
    invalid = copy.deepcopy(source['events'][0])
    invalid['event'] = ['wrong']
    source['events'].append(invalid)
    with pytest.raises(ValueError):
        analytics.accept_batch(source, 'alice')
    assert analytics._QUEUE.empty()
    source['events'].pop()
    analytics.accept_batch(source, 'alice')
    _, event = analytics._QUEUE.get_nowait()
    assert 'duration_ms' not in event['properties']
    assert 'arbitrary' not in event['properties']


def test_settings_and_capture_routes_are_local_and_update_is_guarded(setup, monkeypatch):
    from adk_backend.chat import identity
    monkeypatch.setattr(identity, 'resolve_chat_user', Mock(return_value='alice'))
    app = Flask(__name__)
    app.register_blueprint(bp)
    update = app.view_functions['settings.api_usage_update']
    assert update._admin_toolkit_advanced
    assert update._admin_toolkit_local_only
    with app.test_client() as client:
        assert client.post('/api/usage/config', json={'enabled': 'false'}).status_code == 400
        assert client.get('/api/usage/config').headers['Cache-Control'] == 'no-store'
        assert client.post('/api/usage/config', json={'enabled': False}).json['enabled'] is False
        result = client.post('/api/usage/events', json=batch(), headers={'X-DSS-Host-Id': 'broken-remote'})
        assert result.status_code == 202
        assert result.json['accepted'] == 0
        identity.resolve_chat_user.assert_not_called()


def test_sender_uses_posthog_batch_and_drops_disabled_generations(setup, monkeypatch):
    # Stop the worker after one drain without ever making a real network call.
    class StopWorker(BaseException):
        pass
    analytics.accept_batch(batch(), 'alice')
    real_get = analytics._QUEUE.get
    calls = 0
    def get(block=True, timeout=None):
        nonlocal calls
        if not block:
            return real_get(block=False)
        calls += 1
        if calls > 1:
            raise StopWorker()
        return real_get()
    monkeypatch.setattr(analytics._QUEUE, 'get', get)
    post = Mock(side_effect=RuntimeError('network unavailable'))
    monkeypatch.setattr(analytics.requests, 'post', post)
    with pytest.raises(StopWorker):
        analytics._send_loop()
    assert post.call_count == 1
    args, kwargs = post.call_args
    assert args[0] == 'https://us.i.posthog.com/batch/'
    assert kwargs['timeout'] == (2, 3)
    assert kwargs['allow_redirects'] is False
    assert 'headers' not in kwargs
    assert len(kwargs['json']['batch']) == 1
