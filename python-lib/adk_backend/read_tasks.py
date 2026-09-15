"""One inbound request, existing read handlers, one background interpretation.

Internal GET dispatch runs the normal Flask hooks, including host selection and
advanced-route gates. There is no arbitrary endpoint, mutation or prompt input.
"""
import hashlib
import inspect
import threading
import time
import uuid

from flask import current_app, g, request

from adk_backend.clients import ThreadPoolExecutor
from atk_agent_common import capability_routing, read_tasks, tools_impl
from atk_agent_common.errors import BackendError
from atk_agent_common.read_interpretation import canonical

_SLOTS = threading.BoundedSemaphore(4)
_GETS = frozenset({
    '/api/hosts', '/api/mode', '/api/version', '/api/overview',
    '/api/settings/threshold-defaults', '/api/connections', '/api/plugins',
    '/api/tools/admin-actions/inventory', '/api/k8s-insights/clusters',
    '/api/k8s-insights/clusters/health',
})


class ReadAccessError(Exception):
    def __init__(self, status, payload):
        self.status, self.payload = status, payload


class LocalReadClient:
    """Execute only the read handlers used by the bounded task vocabulary."""
    def __init__(self, app, headers, host, config, gates, autonomous):
        self.app, self.headers, self.host = app, headers, host
        allow = config.get('host_allowlist') or ''
        self.allowlist = [v.strip() for v in allow.split(',') if v.strip()]
        self.settings = {'enable_red_actions': str(config.get('enable_red_actions', True)).lower()
                         in ('true', '1', 'yes')}
        self._task_gate_maps = (gates, autonomous)

    def get(self, path, host='local', params=None, **kwargs):
        if path not in _GETS or host not in (None, '', 'local', self.host):
            raise BackendError('Read task attempted an unsupported endpoint or host.')
        headers = dict(self.headers)
        if host not in (None, '', 'local'):
            headers['X-DSS-Host-Id'] = host
        else:
            headers.pop('X-DSS-Host-Id', None)
        # A separate application context keeps nested g.client from replacing
        # the parent request's selected host. Full dispatch retains all hooks.
        with self.app.app_context(), self.app.test_request_context(
                path, method='GET', headers=headers, query_string=params):
            response = self.app.full_dispatch_request()
            try:
                if response.status_code >= 300:
                    if response.status_code in (401, 403, 409):
                        raise ReadAccessError(response.status_code, response.get_json() or {'error': 'read-access-denied'})
                    raise BackendError('Read endpoint returned HTTP %s.' % response.status_code)
                payload = response.get_json()
                if payload is None:
                    raise BackendError('Read endpoint did not return JSON.')
                return payload
            finally:
                response.close()

    def list_hosts(self, force=False):
        rows = self.get('/api/hosts')
        return [r for r in rows if not self.allowlist or r.get('id') in self.allowlist]


def run(body, config, gates, autonomous, owner):
    from adk_backend import cobuild_bridge
    if not isinstance(body, dict):
        raise ValueError('Expected a task object.')
    operations = body.get('operations')
    if not isinstance(operations, list) or not 1 <= len(operations) <= 6:
        raise ValueError('A read task needs between one and six operations.')
    host = getattr(g, 'host_id', 'local')
    normalized = []
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError('Expected an operation object.')
        name, arguments = operation.get('name'), operation.get('arguments', {})
        if not isinstance(name, str) or not read_tasks.supports(name, arguments):
            raise ValueError('Unsupported read task operation or arguments.')
        if not gates.get(name, True):
            raise PermissionError('Capability is disabled in Agent Permissions.')
        if arguments.get('host', host) != host:
            raise ValueError('All task operations must target the selected host.')
        fn = getattr(tools_impl, name)
        bound = inspect.signature(fn).bind(None, **arguments)
        bound.apply_defaults()
        args = {k: v for k, v in bound.arguments.items() if k != 'client'}
        if 'host' in args:
            args['host'] = host
        normalized.append((name, args))
    app = current_app._get_current_object()
    headers = {k: request.headers[k] for k in ('Cookie', 'Authorization') if k in request.headers}
    client = LocalReadClient(app, headers, host, config, gates, autonomous)
    if client.allowlist and host not in client.allowlist:
        raise PermissionError('Host is outside the configured agent allowlist.')
    if not _SLOTS.acquire(blocking=False):
        raise ValueError('Read task capacity is full; retry after a task finishes.')
    started = time.monotonic()

    def execute(operation):
        name, arguments = operation
        try:
            with capability_routing.override('existing'):
                data = getattr(tools_impl, name)(client, **arguments)
        except ReadAccessError:
            raise
        except Exception as exc:
            data = {'error': {'code': 'read-task-failed', 'message': 'Read failed (%s).' % type(exc).__name__}}
        return {'name': name, 'data': data}

    try:
        if len(normalized) == 1:
            results = [execute(normalized[0])]
        else:
            with ThreadPoolExecutor(max_workers=min(3, len(normalized))) as pool:
                results = list(pool.map(execute, normalized))
    finally:
        _SLOTS.release()
    data_seconds = time.monotonic() - started
    projected = {'reads': [dict(name=name, facts=read_tasks.facts(name, args, result['data']))
                           for (name, args), result in zip(normalized, results)]}
    digest = hashlib.sha256(canonical(projected).encode()).hexdigest()
    try:
        job = cobuild_bridge.submit_read_facts(
            g.client, owner, str(config.get('agent_cobuild_project') or 'ADMINTOOLKIT'),
            'read task', projected, digest, uuid.uuid4().hex, host)
    except Exception as exc:
        job = {'status': 'failed', 'message': 'Interpretation unavailable (%s); read data retained.' % type(exc).__name__}
    route = {'provider': 'cobuild', 'executor': 'adtk', 'variant': 'read-task',
             'host': host, 'submittedAt': time.time(), 'dataSeconds': round(data_seconds, 3),
             'returnSeconds': round(time.monotonic() - started, 3), 'interpretation': job,
             'messageExchanges': 1, 'readCount': len(results)}
    return {'results': results, 'executionRoute': route}
