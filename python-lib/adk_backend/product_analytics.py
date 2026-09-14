"""Explicit product events, installation-wide opt-out, and pseudonymous IDs.

No SDK autocapture: the browser sends a small allowlisted vocabulary through
our backend. A bounded, best-effort queue keeps PostHog off the request path.
All configuration/identity belongs to the installation serving the webapp,
never the managed host selected in the host picker.
"""
import hashlib
import hmac
import json
import math
import queue
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

from adk_backend.build_info import BUILD_VERSION
from adk_backend.clients import _local_thread_client

EVENTS = frozenset({
    'adtk_activity',
    'adtk_webapp_opened', 'adtk_module_opened', 'adtk_results_viewed',
    'adtk_scan_started', 'adtk_scan_completed', 'adtk_scan_failed',
    'adtk_scan_cancelled', 'adtk_scan_results_viewed',
    'adtk_snapshot_created', 'adtk_comparison_completed',
})
_UUID = re.compile(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$')
_SLUG = re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]{0,79}$')
_LOCK = threading.RLock()
_CONFIG = None
_CONFIG_AT = 0.0
_QUEUE = queue.Queue(maxsize=1000)
_WORKER = None
_GENERATION = 0
_SEEN = {}  # bounded request deduplication; PostHog also receives event UUIDs


def classify_installation(url):
    """Exact first DNS label, not a substring match on customer URLs."""
    name = (urlsplit(url if '://' in url else 'https://' + url).hostname or '').lower()
    return 'internal' if name.split('.')[0] in {'tam-global', 'tamglobal', 'akaos'} else 'customer'


def _pseudonym(secret, kind, value):
    return hmac.new(secret.encode(), (kind + ':' + value).encode(), hashlib.sha256).hexdigest()


def _read_config():
    global _CONFIG, _CONFIG_AT, _GENERATION
    with _LOCK:
        if _CONFIG is not None and time.monotonic() - _CONFIG_AT < 10:
            return dict(_CONFIG)
        client = _local_thread_client()
        settings = client.get_plugin('admin-toolkit').get_settings()
        config = settings.get_raw().get('config', {})
        # A missing value means the product default: enabled. Do not coerce
        # strings with bool('false'), and do not reset a saved opt-out.
        enabled = config.get('usage_analytics_enabled', True) not in (False, 'false', '0', 0)
        packaged = json.loads(Path(__file__).with_name('product_analytics_config.json').read_text())
        token = packaged.get('project_token', '')
        api_host = packaged.get('api_host', '')
        configured = bool(token.startswith('phc_') and api_host in {
            'https://us.i.posthog.com', 'https://eu.i.posthog.com',
        })
        secret = config.get('usage_analytics_secret', '')
        if enabled and configured and not secret:
            secret = secrets.token_hex(32)
            settings.get_raw().setdefault('config', {})['usage_analytics_secret'] = secret
            settings.save()
        external = str(client.get_general_settings().get_raw().get('studioExternalUrl') or '')
        # API clients can use loopback, so prefer DSS's canonical external URL.
        installation_url = external or str(getattr(client, 'host', '') or '')
        value = {'enabled': enabled, 'configured': configured, 'secret': secret,
                 'audience': classify_installation(installation_url),
                 'api_host': api_host, 'token': token,
                 'installation_id': _pseudonym(secret, 'installation', installation_url) if secret else ''}
        if _CONFIG and (_CONFIG['enabled'] != enabled or _CONFIG['secret'] != secret):
            _GENERATION += 1
        _CONFIG, _CONFIG_AT = value, time.monotonic()
        return dict(value)


def status():
    try:
        config = _read_config()
        return {key: config[key] for key in ('enabled', 'configured', 'audience')}
    except Exception:
        # Analytics must never break the admin tool or send without reading
        # the administrator's opt-out. The UI distinguishes this from disabled.
        return {'enabled': False, 'configured': False, 'available': False}


def set_enabled(enabled):
    global _CONFIG_AT, _GENERATION
    if not isinstance(enabled, bool):
        raise ValueError('enabled must be a boolean')
    with _LOCK:
        settings = _local_thread_client().get_plugin('admin-toolkit').get_settings()
        settings.get_raw().setdefault('config', {})['usage_analytics_enabled'] = enabled
        settings.save()
        _GENERATION += 1
        _CONFIG_AT = float('-inf')
        if _CONFIG is not None:
            _CONFIG['enabled'] = enabled
        while True:
            try:
                _QUEUE.get_nowait()
            except queue.Empty:
                break
    return status()


def _properties(raw):
    """Project into an allowlist. Never forward arbitrary strings or objects."""
    if not isinstance(raw, dict):
        raise ValueError('properties must be an object')
    out = {}
    for key in ('module_id', 'scan_key'):
        value = raw.get(key)
        if isinstance(value, str) and _SLUG.fullmatch(value):
            out[key] = value
    for key in ('visit_id', 'scan_id'):
        value = raw.get(key)
        if isinstance(value, str) and _UUID.fullmatch(value):
            out[key] = value
    for key in ('duration_ms',):
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            out[key] = max(0, min(round(value), 86_400_000))
    for key in ('is_empty', 'start_observed'):
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    enums = {
        'trigger': {'automatic', 'manual', 'on_demand', 'unknown'},
        'results_state': {'partial', 'complete'},
        'format': {'diagnostic_bundle', 'snapshot'},
        'data_source': {'api', 'zip'},
    }
    for key, choices in enums.items():
        if isinstance(raw.get(key), str) and raw[key] in choices:
            out[key] = raw[key]
    return out


def accept_batch(body, user_id):
    """Called in request context; returns accepted count, never waits for PostHog."""
    global _WORKER
    if not isinstance(body, dict) or not isinstance(body.get('events'), list) or len(body['events']) > 50:
        raise ValueError('events must be an array of at most 50 events')
    browser_id = body.get('browser_id', '')
    session_id = body.get('session_id', '')
    if not isinstance(browser_id, str) or not _UUID.fullmatch(browser_id):
        raise ValueError('invalid browser_id')
    if not isinstance(session_id, str) or not _UUID.fullmatch(session_id):
        raise ValueError('invalid session_id')
    # Validate the whole batch before enqueueing anything.
    clean = []
    now = datetime.now(timezone.utc)
    for event in body['events']:
        if not isinstance(event, dict) or not isinstance(event.get('event'), str) or event['event'] not in EVENTS:
            raise ValueError('unknown event')
        event_id = event.get('uuid', '')
        if not isinstance(event_id, str) or not _UUID.fullmatch(event_id):
            raise ValueError('invalid event uuid')
        try:
            timestamp = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))
            if timestamp.tzinfo is None or abs((now - timestamp).total_seconds()) > 3600:
                raise ValueError('timestamp outside collection window')
        except (KeyError, TypeError, AttributeError, ValueError):
            raise ValueError('invalid event timestamp') from None
        props = _properties(event.get('properties', {}))
        host = event.get('host_id', 'local')
        if not isinstance(host, str) or len(host) > 256:
            raise ValueError('invalid host_id')
        clean.append((event_id, event['event'], timestamp.isoformat(), props, host))
    try:
        config = _read_config()
    except Exception:
        return 0
    if not config['enabled'] or not config['configured']:
        return 0
    from adk_backend.chat.identity import ANONYMOUS_USER
    identified = bool(user_id and user_id != ANONYMOUS_USER)
    kind = 'dss_user' if identified else 'browser'
    distinct_id = _pseudonym(config['secret'], kind, user_id if identified else browser_id)
    accepted = 0
    with _LOCK:
        if not _CONFIG or not _CONFIG['enabled'] or config['secret'] != _CONFIG['secret']:
            return 0
        for event_id, name, timestamp, props, host in clean:
            dedup = (distinct_id, event_id)
            if dedup in _SEEN:
                continue
            props.update({
                'distinct_id': distinct_id,
                'installation_id': config['installation_id'],
                'audience': config['audience'], 'identity_type': kind,
                'plugin_version': BUILD_VERSION, 'schema_version': 1,
                'target_host_id': _pseudonym(config['secret'], 'target', host),
                '$session_id': session_id, '$geoip_disable': True,
                '$process_person_profile': False,
            })
            item = {'uuid': event_id, 'event': name, 'timestamp': timestamp, 'properties': props}
            try:
                _QUEUE.put_nowait((_GENERATION, item))
            except queue.Full:
                break
            if len(_SEEN) >= 4096:
                _SEEN.clear()
            _SEEN[dedup] = True
            accepted += 1
        if accepted and (_WORKER is None or not _WORKER.is_alive()):
            _WORKER = threading.Thread(target=_send_loop, name='toolkit-product-analytics', daemon=True)
            _WORKER.start()
    return accepted


def _send_loop():
    while True:
        generation, first = _QUEUE.get()
        batch = [(generation, first)]
        while len(batch) < 50:
            try:
                batch.append(_QUEUE.get_nowait())
            except queue.Empty:
                break
        try:
            # Explicit local client: analytics describes the serving installation,
            # and must not inherit a managed target from an arbitrary thread.
            config = _read_config()
            with _LOCK:
                events = [item for epoch, item in batch if epoch == _GENERATION]
            if config['enabled'] and config['configured'] and events:
                # No cookies, browser headers, IPs, URLs, payloads or exception
                # text. No retries/backlog on disk if the network is unavailable.
                requests.post(config['api_host'] + '/batch/', json={
                    'api_key': config['token'], 'batch': events,
                }, timeout=(2, 3), allow_redirects=False)
        except Exception:
            pass
