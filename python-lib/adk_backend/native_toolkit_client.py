"""Native tools dispatch through Flask, inside the already admitted webapp.

All route hooks, host routing, unlocks and action gates still run. No browser
cookies are copied and no unauthenticated HTTP listener is created. Each call
gets a separate Flask request context on a bounded worker pool so the calling
chat context and parallel tools cannot contaminate each other's host state.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from email.message import Message
from threading import BoundedSemaphore
import time

import requests
from requests.cookies import MockRequest, MockResponse
from werkzeug.test import Client

from atk_agent_common.client import ToolkitClient
from atk_agent_common.errors import BackendError

_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix='atk-native-route')
_SLOTS = BoundedSemaphore(8)
_BASE = 'https://toolkit.internal'


class NativeToolkitClient(ToolkitClient):
    def __init__(self, settings, app):
        self.app = app
        super().__init__(dict(settings, backend_url=_BASE))

    def _authenticate_backend(self):
        # The caller is already inside this webapp. DSS authentication remains
        # on the external chat route; internal calls still pass every Flask gate.
        pass

    def _do(self, method, path, host=None, params=None, json=None, timeout=None,
            stream=False, extra_headers=None, retry_safe=False):
        if not path.startswith('/') or path.startswith('//'):
            raise BackendError('Native toolkit calls require a local route path.')
        prepared = self.session.prepare_request(requests.Request(
            method, _BASE + path, params=params, json=json,
            headers=self._headers(host, extra_headers)))
        limit = timeout or self.timeout
        deadline = time.monotonic() + limit
        if not _SLOTS.acquire(timeout=limit):
            raise requests.exceptions.Timeout('Native toolkit route workers are busy.')
        try:
            future = _POOL.submit(self._dispatch, prepared)
        except Exception:
            _SLOTS.release()
            raise
        # Timed-out routes continue just like an HTTP backend scan. Keep their
        # slot occupied until completion; never accumulate an unbounded queue
        # and never retry writes whose outcome is unknown.
        future.add_done_callback(lambda _: _SLOTS.release())
        try:
            response, cookie_headers = future.result(timeout=max(0, deadline - time.monotonic()))
        except FutureTimeout:
            raise requests.exceptions.Timeout('Native toolkit route timed out.') from None
        except Exception as exc:
            raise BackendError('Native toolkit route failed (%s).' % type(exc).__name__) from None
        self.session.cookies.extract_cookies(MockResponse(cookie_headers), MockRequest(prepared))
        return response

    def _dispatch(self, prepared):
        # use_cookies=False: the component's requests jar owns unlock cookies.
        # Buffer SSE here: native consumers only need its final event, and the
        # generator must be consumed/closed on the worker owning its context.
        response = Client(self.app, use_cookies=False).open(
            prepared.path_url, method=prepared.method, base_url=_BASE,
            headers=dict(prepared.headers), data=prepared.body,
            buffered=True, follow_redirects=False)
        try:
            result = requests.Response()
            result.status_code = response.status_code
            result.headers = requests.structures.CaseInsensitiveDict(response.headers)
            result.encoding = requests.utils.get_encoding_from_headers(result.headers)
            result.url = prepared.url
            result.request = prepared
            result._content = response.get_data()
            result._content_consumed = True
            cookie_headers = Message()
            for value in response.headers.getlist('Set-Cookie'):
                cookie_headers.add_header('Set-Cookie', value)
            return result, cookie_headers
        finally:
            response.close()
