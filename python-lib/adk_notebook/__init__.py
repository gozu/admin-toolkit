"""adk_notebook — shared library for the Admin Toolkit notebook "card printer".

Every card file under ``notebook-cards/`` does the SAME data fetch as the
matching webapp card, then renders it with rich instead of returning JSON. This
package is the reuse vehicle: the data plumbing (``client``, ``data``), the pure
parsers (``parse``, copied verbatim from backend.py) and the rich toolkit
(``ui``) all live here so each card file stays tiny.

In a DSS notebook (admin-toolkit code env)::

    import dataiku
    dataiku.use_plugin_libs("admin-toolkit")
    from adk_notebook import get_client, ui, data, parse, host_metrics

``client`` and ``data`` import ``dataiku``; ``parse`` and ``ui`` do not, so they
can be imported (and unit-tested) outside DSS. The dataiku-dependent names are
loaded lazily via ``__getattr__`` to keep that separation clean.
"""
from __future__ import annotations

import importlib

from . import parse  # pure stdlib — always safe to import

__all__ = [
    "parse",
    "ui",
    "client",
    "data",
    "get_client",
    "resolve_macro_project",
    "run_macro",
    "host_metrics",
    "process_metrics",
    "dbhealth",
    "image_cleaner",
    "k8s_insights",
]

_CLIENT_EXPORTS = {
    "get_client",
    "resolve_macro_project",
    "run_macro",
    "host_metrics",
    "process_metrics",
    "dbhealth",
    "image_cleaner",
    "k8s_insights",
}


def __getattr__(name: str):  # PEP 562 — lazy submodule / wrapper access
    # importlib.import_module (not ``from . import x``) avoids re-entering this
    # __getattr__ via the fromlist hasattr() check.
    if name in ("ui", "client", "data"):
        return importlib.import_module(f".{name}", __name__)
    if name in _CLIENT_EXPORTS:
        client = importlib.import_module(".client", __name__)
        return getattr(client, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
