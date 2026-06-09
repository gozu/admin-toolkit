"""Stub out DSS-only modules so backend.py can be imported in a plain pytest env."""

import os
import sys
import types

import pytest

# python-lib must resolve before (and alongside) the webapps path the test
# modules add, so `import adk_backend` works under pytest like it does in DSS.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(_ROOT, 'python-lib'))


def _install_dataiku_stub():
    if 'dataiku' in sys.modules:
        return
    mod = types.ModuleType('dataiku')

    class _FakeClient:
        def get_general_settings(self):
            class _S:
                def get_raw(self_inner):
                    return {}
            return _S()

    mod.api_client = lambda: _FakeClient()  # type: ignore[attr-defined]
    sys.modules['dataiku'] = mod


def _install_dateutil_stub():
    # Very small stub — only .parser.isoparse is used elsewhere in backend.py,
    # none of our adapter tests touch it, but the import must succeed.
    if 'dateutil' in sys.modules:
        return
    du = types.ModuleType('dateutil')
    parser_mod = types.ModuleType('dateutil.parser')
    from datetime import datetime as _dt

    def _isoparse(s):
        return _dt.fromisoformat(str(s).replace('Z', '+00:00'))

    parser_mod.isoparse = _isoparse  # type: ignore[attr-defined]
    parser_mod.parse = _isoparse  # type: ignore[attr-defined]
    tz_mod = types.ModuleType('dateutil.tz')
    tz_mod.tzlocal = lambda: None  # type: ignore[attr-defined]
    du.parser = parser_mod  # type: ignore[attr-defined]
    du.tz = tz_mod  # type: ignore[attr-defined]
    sys.modules['dateutil'] = du
    sys.modules['dateutil.parser'] = parser_mod
    sys.modules['dateutil.tz'] = tz_mod


_install_dataiku_stub()
_install_dateutil_stub()


@pytest.fixture(autouse=True)
def _reset_backend_singletons_between_tests():
    yield
    backend = sys.modules.get('backend')
    if backend is None:
        return
    try:
        from adk_backend import caching as _adk_caching
        backend._CACHE.clear()
        _adk_caching._CACHE_INFLIGHT.clear()
        _adk_caching._CACHE_INFLIGHT_ERRORS.clear()
        if hasattr(backend, '_FOOTPRINT_STATES'):
            backend._FOOTPRINT_STATES.clear()
            backend._FOOTPRINT_STATES['local'] = backend._new_footprint_state()
        backend._THREAD_LOCAL.__dict__.clear()
    except Exception:
        pass
