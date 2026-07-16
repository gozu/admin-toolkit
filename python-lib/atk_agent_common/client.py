"""ToolkitClient — THE HTTP wrapper over the admin-toolkit webapp backend.

One requests.Session per component instance; its cookie jar holds both unlock
cookies (HttpOnly restricts browser JS, not requests). Fleet-awareness is a
header: X-DSS-Host-Id on every non-local call, with the host id validated
against GET /api/hosts ∩ the configured allowlist, so a hallucinated host id
dies here with the valid ids in the error instead of a confusing 502.

Unlocks are lazy with a single retry per request:
  403 on a red route          → POST /api/auth/red/unlock {password} → retry
  409 remote-keys-locked      → POST /api/hosts/keys/unlock {password} → retry
  409 macro-project-missing   → MacroProjectMissing (needs a human, no retry)
A post-unlock 403/409 means the configured password no longer matches
(rotation) → locked error, never a loop.

Heavy endpoints block up to heavy_timeout_s; on timeout the caller gets
ScanTimeout carrying live progress — the backend coalesces in-flight scans, so
"re-invoke later" hits a warm cache instead of restarting work.
"""

import time

import requests

from .errors import (BackendError, MacroProjectMissing, RedLocked,
                     RemoteKeysLocked, ScanTimeout, UnknownHost, UnreachableHost)

_HOSTS_CACHE_TTL_S = 60


class ToolkitClient:
    def __init__(self, settings):
        self.settings = settings
        self.base_url = (settings.get('backend_url') or '').rstrip('/')
        if not self.base_url:
            raise BackendError(
                'No Admin Toolkit backend URL configured.',
                remediation='Set "Backend base URL" in the Admin Toolkit Agents plugin '
                            'settings (or install the Admin Toolkit webapp so it can be discovered).')
        self.session = requests.Session()
        # Fresh connection per call. Agent turns idle 10-60s between tool calls
        # and the backend restarts on every plugin deploy, so pooled keep-alive
        # sockets go stale and the next call dies with an instant ConnectionError
        # (→ host-unreachable). A new connection costs ~ms against multi-second
        # backend queries — a trade we always want here.
        self.session.headers['Connection'] = 'close'
        self.session.verify = settings.get('verify_tls', True)
        self.timeout = settings.get('http_timeout_s', 30)
        self.heavy_timeout = settings.get('heavy_timeout_s', 900)
        self._hosts_cache = None
        self._hosts_cache_ts = 0.0
        self._red_unlocked = False
        self._keys_unlocked = False

    # ── hosts ────────────────────────────────────────────────────────────────
    def list_hosts(self, force=False):
        """[{id,label,url}] from the backend, filtered by the allowlist."""
        now = time.time()
        if not force and self._hosts_cache is not None and now - self._hosts_cache_ts < _HOSTS_CACHE_TTL_S:
            return self._hosts_cache
        hosts = self._request('GET', '/api/hosts', host=None)
        allow = self.settings.get('host_allowlist') or []
        if allow:
            hosts = [h for h in hosts if h.get('id') in allow]
        self._hosts_cache = hosts
        self._hosts_cache_ts = now
        return hosts

    def validate_host(self, host):
        host = (host or 'local').strip()
        valid = [h.get('id') for h in self.list_hosts()]
        if host not in valid:
            raise UnknownHost(host, valid)
        return host

    # ── public verbs ─────────────────────────────────────────────────────────
    def get(self, path, host='local', params=None, heavy=False, progress_path=None):
        return self._request('GET', path, host=host, params=params,
                             heavy=heavy, progress_path=progress_path)

    def post(self, path, host='local', json=None, red=False, params=None, retry_safe=False):
        return self._request('POST', path, host=host, json=json, red=red, params=params,
                             retry_safe=retry_safe)

    def delete(self, path, host='local', json=None, red=True, params=None, headers=None):
        return self._request('DELETE', path, host=host, json=json, red=red,
                             params=params, extra_headers=headers)

    def stream_final(self, path, host='local', params=None, timeout=None):
        """Consume an SSE endpoint and return the final ('done'/'error') payload.
        LBs may buffer SSE — callers should prefer a blocking variant when one
        exists and treat this as the long-runner path."""
        from . import sse as sse_mod
        eff_host = self._effective_host(path, host)
        resp = self._do('GET', path, host=eff_host, params=params,
                        timeout=timeout or self.heavy_timeout, stream=True)
        self._raise_for_status(resp, path, host)
        try:
            event, payload = sse_mod.read_final_event(resp, done_events=('done', 'error'))
        finally:
            resp.close()
        if event == 'error':
            code = (payload or {}).get('error')
            if code == 'macro-project-missing':
                raise MacroProjectMissing('The ADMINTOOLKIT macro project is missing on host %r.' % host)
            raise BackendError('Stream %s failed: %s' % (path, code))
        if payload is None:
            raise BackendError('Stream %s ended without a final event.' % path)
        return payload

    def get_text(self, path, host='local'):
        """GET returning raw text (e.g. /api/java-memory serves a shell file)."""
        resp = self._do('GET', path, host=self._effective_host(path, host), timeout=self.timeout)
        self._raise_for_status(resp, path, host)
        return resp.text

    # ── internals ────────────────────────────────────────────────────────────
    def _effective_host(self, path, host):
        """Validated host id, or None for local/host-independent routes."""
        if host in (None, '', 'local'):
            return None
        if path.startswith(('/api/hosts', '/api/auth')):
            return None  # host-registry + unlock routes are local by definition
        return self.validate_host(host)

    def _headers(self, host, extra=None):
        headers = {'X-DSS-Host-Id': host} if host else {}
        if extra:
            headers.update(extra)
        return headers

    def _do(self, method, path, host=None, params=None, json=None, timeout=None,
            stream=False, extra_headers=None, retry_safe=False):
        # Retry a bare ConnectionError once: with Connection: close the pool is
        # already fresh-per-call, but a socket can still die mid-flight, and the
        # failure is instant (no work happened server-side). Retry only reads,
        # or writes the caller has flagged idempotent (retry_safe). GETs/HEADs
        # are always safe; POST/DELETE are NOT retried unless opted in.
        url = self.base_url + path
        retryable = retry_safe or method in ('GET', 'HEAD')
        for attempt in (1, 2):
            try:
                return self.session.request(
                    method, url, params=params, json=json, stream=stream,
                    headers=self._headers(host, extra_headers), timeout=timeout or self.timeout)
            # ConnectTimeout subclasses BOTH Timeout and ConnectionError — this
            # clause MUST stay first so a connect timeout is never sleep-retried.
            except requests.exceptions.Timeout:
                raise
            except requests.exceptions.ConnectionError:
                if retryable and attempt == 1:
                    time.sleep(0.5)
                    continue
                if retryable:
                    raise UnreachableHost(
                        'The Admin Toolkit backend refused the connection. An instant connection '
                        'failure usually means a stale socket or the backend restarting (happens on '
                        'every plugin deploy). Already auto-retried once.',
                        remediation='Retry now — a fresh connection usually succeeds. If it persists '
                                    'past ~1 minute, an admin should check the webapp in DSS.')
                raise UnreachableHost(
                    'The Admin Toolkit backend refused the connection, and this call was NOT '
                    'auto-retried: this call may mutate state — verify whether it took effect '
                    '(read the relevant inventory/audit) before re-executing.',
                    remediation='Check whether the change landed before retrying; the webapp may be '
                                'restarting (happens on every plugin deploy).')
            except requests.exceptions.RequestException as exc:
                raise UnreachableHost(
                    'The Admin Toolkit backend did not respond (%s).' % type(exc).__name__,
                    remediation='The webapp backend may be restarting; retry in ~1 minute. '
                                'If it persists, an admin should check the webapp in DSS.')

    def _request(self, method, path, host='local', params=None, json=None,
                 heavy=False, red=False, progress_path=None, extra_headers=None,
                 retry_safe=False):
        eff_host = self._effective_host(path, host)
        timeout = self.heavy_timeout if heavy else self.timeout
        retried_red = retried_keys = False
        while True:
            try:
                resp = self._do(method, path, host=eff_host, params=params, json=json,
                                timeout=timeout, extra_headers=extra_headers,
                                retry_safe=retry_safe)
            except requests.exceptions.Timeout:
                if heavy:
                    raise ScanTimeout(
                        'The scan behind %s is still running after %ss.' % (path, timeout),
                        progress=self._fetch_progress(progress_path, eff_host))
                raise UnreachableHost('Timed out after %ss calling %s.' % (timeout, path))

            if resp.status_code < 400:
                return self._parse_json(resp, path)

            code, body = self._error_body(resp)

            if resp.status_code == 403 and not retried_red:
                self._unlock_red()  # raises RedLocked if impossible
                retried_red = True
                continue
            if resp.status_code == 409 and code == 'remote-keys-locked' and not retried_keys:
                self._unlock_keys()
                retried_keys = True
                continue
            self._raise_mapped(resp.status_code, code, body, path, host, red,
                               retried_red=retried_red, retried_keys=retried_keys)

    def _raise_for_status(self, resp, path, host):
        if resp.status_code >= 400:
            code, body = self._error_body(resp)
            self._raise_mapped(resp.status_code, code, body, path, host, red=False,
                               retried_red=False, retried_keys=False)

    def _raise_mapped(self, status, code, body, path, host, red, retried_red, retried_keys):
        message = (body or {}).get('message') or (body or {}).get('detail') or ''
        if status == 403:
            raise RedLocked('Advanced Actions stayed locked after unlocking (%s). The configured '
                            'password may have been rotated.' % (message or 'HTTP 403'))
        if status == 409 and code == 'remote-keys-locked':
            raise RemoteKeysLocked('Remote host keys stayed locked (%s).' % (message or 'HTTP 409'))
        if status == 409 and code == 'macro-project-missing':
            raise MacroProjectMissing(message or 'The ADMINTOOLKIT macro project is missing on host %r.' % host)
        if status == 502 or code == 'host-unreachable':
            raise UnreachableHost(message or 'Host %r is unreachable.' % host,
                                  detail={'hostId': (body or {}).get('hostId', host)})
        raise BackendError('HTTP %s from %s%s' % (status, path, (': ' + message) if message else ''),
                           detail={'code': code} if code else None)

    def _error_body(self, resp):
        try:
            body = resp.json()
            return (body.get('error') if isinstance(body, dict) else None), body
        except ValueError:
            return None, None

    def _parse_json(self, resp, path):
        try:
            return resp.json()
        except ValueError:
            raise BackendError('Non-JSON response from %s (content-type %s).'
                               % (path, resp.headers.get('Content-Type', '?')))

    def _fetch_progress(self, progress_path, host):
        if not progress_path:
            return None
        try:
            resp = self._do('GET', progress_path, host=host, timeout=10)
            if resp.status_code < 400:
                data = resp.json()
                # Whole event streams are noise in agent context — keep the
                # status and the last few steps only.
                if isinstance(data, dict) and isinstance(data.get('events'), list):
                    events = data['events']
                    return {'status': data.get('status'),
                            'lastEvents': [{'step': e.get('step'), 'message': e.get('message')}
                                           for e in events[-3:]],
                            'eventCount': len(events)}
                return data
        except Exception:
            pass
        return None

    # ── unlocks ──────────────────────────────────────────────────────────────
    def _unlock_red(self):
        password = self.settings.get('master_password') or ''
        if not password:
            raise RedLocked('This action needs the Advanced Actions unlock, and no master password '
                            'is configured in the plugin settings.')
        resp = self._do('POST', '/api/auth/red/unlock', json={'password': password}, timeout=self.timeout)
        if resp.status_code == 200:
            self._red_unlocked = True
            return
        code, body = self._error_body(resp)
        if code == 'not-configured':
            raise RedLocked('The Admin Toolkit has no master password configured, so red '
                            'actions are impossible on this backend.',
                            remediation='An admin must set the master password in the Admin Toolkit plugin settings.')
        raise RedLocked('The configured master password was rejected (%s).'
                        % ((body or {}).get('message') or 'HTTP %s' % resp.status_code))

    def _unlock_keys(self):
        password = self.settings.get('master_password') or ''
        if not password:
            raise RemoteKeysLocked('This host\'s API key is encrypted and no master password is '
                                   'configured in the plugin settings.')
        resp = self._do('POST', '/api/hosts/keys/unlock', json={'password': password}, timeout=self.timeout)
        if resp.status_code == 200:
            self._keys_unlocked = True
            return
        code, body = self._error_body(resp)
        if code == 'not-configured':
            raise RemoteKeysLocked('The backend reports no encrypted host keys — the 409 likely '
                                   'came from a stale preset; retry or check the host preset.')
        raise RemoteKeysLocked('The configured master password was rejected by the host-keys gate (%s).'
                               % ((body or {}).get('message') or 'HTTP %s' % resp.status_code))
