"""Shared thread-local context for the Admin Toolkit backend.

Holds the active host id, per-thread DSS clients and the benchmark recorder.
Owned here (lowest layer) because caching, utils and clients all read it;
backend.py re-exports it via adk_backend.clients.
"""

import threading

_THREAD_LOCAL = threading.local()
