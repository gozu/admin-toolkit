"""Per-capability Existing/Cobuild routing, shared by every agent surface.

Cobuild requests an exact, bounded ADTK operation. ADTK remains the executor
and authority for measurements, plans, approvals and mutations. This is an
explicit bridge, not a claim that Cobuild has native host-administration tools.
No provider failure silently falls back to the existing route.
"""
import contextlib
import contextvars
import functools
import inspect
import json
import time
import uuid

from .errors import ToolkitError
from .read_interpretation import supports as compact_read_supported

PARAM = 'agent_capability_providers'
PROVIDERS = ('existing', 'cobuild')
_OVERRIDE = contextvars.ContextVar('atk_capability_provider_override', default=None)
_NESTED = contextvars.ContextVar('atk_capability_nested_operation', default=False)
_UNCONFIGURED = object()


class CapabilityRouteError(ToolkitError):
    code = 'capability-route-failed'
    remediation = ('Review the Cobuild result or select Existing for this capability '
                   'in Agents → Permissions. No automatic fallback was performed.')


def parse_providers(raw):
    if raw in (None, ''):
        return {}
    value = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(value, dict) or any(v not in PROVIDERS for v in value.values()):
        raise ValueError('providers must be a map of capability names to existing or cobuild')
    return dict(value)


def merge_providers(current, updates, known):
    updates = parse_providers(updates)
    unknown = set(updates) - set(known)
    if unknown:
        raise ValueError('unknown capabilities: ' + ', '.join(sorted(unknown)))
    return dict(parse_providers(current), **updates)


@contextlib.contextmanager
def override(provider):
    """Test-run-local override; never changes a customer's persisted selection."""
    if provider not in PROVIDERS:
        raise ValueError('unknown capability provider')
    token = _OVERRIDE.set(provider)
    try:
        yield
    finally:
        _OVERRIDE.reset(token)


def selected(client, name):
    if _NESTED.get():
        return 'existing'
    if _OVERRIDE.get() is not None:
        return _OVERRIDE.get()
    if getattr(client, 'settings', {}).get(PARAM, _UNCONFIGURED) is _UNCONFIGURED:
        return 'existing'  # Older clients retain their unchanged behavior.
    now = time.monotonic()
    cached = getattr(client, '_capability_provider_cache', None)
    if isinstance(cached, tuple) and now - cached[0] < 30:
        return cached[1].get(name, 'existing')
    raw = client.settings.get(PARAM)
    try:
        live = client.get('/api/agents/action-settings') or {}
        if isinstance(live.get('providers'), dict):
            raw = live['providers']
    except Exception:
        pass  # Same configured route; the later Cobuild call still fails explicitly.
    try:
        providers = parse_providers(raw)
    except (ValueError, TypeError) as exc:
        raise CapabilityRouteError('Invalid capability route configuration.') from exc
    client._capability_provider_cache = (now, providers)
    return providers.get(name, 'existing')


def _wait(client, payload, host):
    started = time.monotonic()
    result = client.post('/api/agents/cobuild-turn', host=host, json=payload)
    while result.get('status') == 'pending':
        if time.monotonic() - started > 240:
            raise CapabilityRouteError(
                'Cobuild is still pending after 240 seconds. The ADTK operation has '
                'not run; DSS-side inference may still finish.',
                detail={'turnId': result.get('turnId'), 'outcome': 'pending'})
        time.sleep(1)
        result = client.get('/api/agents/cobuild-turn/' + result['turnId'], host=host)
    if result.get('status') != 'completed':
        raise CapabilityRouteError(result.get('message') or 'Cobuild did not complete.',
                                   detail={'turnId': result.get('turnId'),
                                           'outcome': result.get('status', 'unknown')})
    return result


def request_operation(client, capability, phase, arguments, host='local'):
    """Obtain a validated Cobuild request before any ADTK operation executes.

    Confirmation tokens/passwords never enter this payload. Host routing and
    exact arguments are fixed by the caller, not writable by the model.
    """
    if selected(client, capability) != 'cobuild':
        return None
    result = _wait(client, {'capability': capability, 'phase': phase,
                            'arguments': _without_credentials(arguments),
                            'requestId': uuid.uuid4().hex}, host)
    return {'provider': 'cobuild', 'transport': 'dataiku-cobuild-sdk',
            'executor': 'adtk', 'phase': phase, 'turnId': result['turnId'],
            'conversationId': result.get('conversationId'),
            'seconds': result.get('seconds'), 'reason': result.get('reason', ''),
            'creditUsage': 'not-reported'}


def _without_credentials(value):
    if isinstance(value, dict):
        return {k: _without_credentials(v) for k, v in value.items()
                if not any(s in k.lower().replace('_', '')
                           for s in ('password', 'confirmtoken', 'apikey', 'secret', 'authorization'))}
    if isinstance(value, (list, tuple)):
        return [_without_credentials(v) for v in value]
    return value


def finish_operation(client, result, route, host='local'):
    if route is None:
        return result
    route = dict(route)
    try:
        finished = _wait(client, {'phase': 'result', 'turnId': route['turnId'],
                                  'result': _without_credentials(result)}, host)
        route['summary'] = finished.get('summary', '')
        route['resultAcknowledged'] = True
        route['seconds'] = (route.get('seconds') or 0) + (finished.get('seconds') or 0)
    except ToolkitError as exc:
        # The operation already ran. Never turn a failed explanation into a
        # retryable execution failure or run an executor twice.
        route['resultAcknowledged'] = False
        route['explanationError'] = exc.message
    return attach(result, route)


def attach(result, route):
    if route is not None and isinstance(result, dict):
        result = dict(result, executionRoute=route)
    return result


def sensor(fn):
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def routed(client, *args, **kwargs):
        bound = signature.bind(client, *args, **kwargs)
        bound.apply_defaults()
        arguments = {k: v for k, v in bound.arguments.items() if k != 'client'}
        from .read_tasks import supports as task_supported
        if (task_supported(fn.__name__, arguments) and callable(getattr(client, 'read_task', None))
                and selected(client, fn.__name__) == 'cobuild'):
            started = time.monotonic()
            task = client.read_task([{'name': fn.__name__, 'arguments': arguments}],
                                    host=arguments.get('host') or 'local')
            result = task['results'][0]['data']
            route = dict(task['executionRoute'], returnSeconds=round(time.monotonic() - started, 3))
            return attach(result, route)
        if compact_read_supported(fn.__name__, arguments) and selected(client, fn.__name__) == 'cobuild':
            return _compact_read(fn, client, args, kwargs, arguments)
        route = request_operation(client, fn.__name__, 'read', arguments,
                                  host=arguments.get('host') or 'local')
        token = _NESTED.set(True)
        try:
            result = fn(client, *args, **kwargs)
        finally:
            _NESTED.reset(token)
        return finish_operation(client, result, route, arguments.get('host') or 'local')
    return routed


def _compact_read(fn, client, args, kwargs, arguments):
    started = time.monotonic()
    host = arguments.get('host') or 'local'
    # Replace the old request-phase server gate with a live check BEFORE the
    # read. Do not infer authorization from a cached provider selection.
    permissions = client.get('/api/agents/action-settings')
    gates = permissions.get('gates') if isinstance(permissions, dict) else None
    if not isinstance(gates, dict):
        raise CapabilityRouteError('Live capability permissions are unavailable; read not run.')
    if not gates.get(fn.__name__, True):
        raise CapabilityRouteError('Capability is disabled in Agent Permissions; read not run.')
    token = _NESTED.set(True)
    try:
        result = fn(client, *args, **kwargs)
    finally:
        _NESTED.reset(token)
    route = {'provider': 'cobuild', 'transport': 'dataiku-cobuild-sdk', 'executor': 'adtk',
             'variant': 'compact-read', 'phase': 'read', 'host': host,
             'dataSeconds': round(time.monotonic() - started, 3),
             'submittedAt': time.time(), 'creditUsage': 'not-reported',
             'note': 'Data is ready independently. Cobuild interpretation is separate from the chat answer.'}
    try:
        job = client.post('/api/agents/cobuild-turn', host=host, json={
            'capability': fn.__name__, 'phase': 'interpret', 'arguments': arguments,
            'requestId': uuid.uuid4().hex, 'result': _without_credentials(result)})
        if job.get('status') != 'pending' or not job.get('viewTicket'):
            raise CapabilityRouteError('Cobuild interpretation was not queued.')
        route['interpretation'] = job
    except Exception as exc:
        # Read already completed; never repeat it or hide it on inference failure.
        route['interpretation'] = {'status': 'failed',
                                   'message': 'Interpretation submission failed (%s).' % type(exc).__name__}
    route['returnSeconds'] = round(time.monotonic() - started, 3)
    return attach(result, route)
