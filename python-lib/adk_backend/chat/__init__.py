"""Server-side chat persistence for the Agents module.

Ported from Dataiku's Agent Hub plugin (v1.4.2) storage layer: Flask-SQLAlchemy
models (used as a bare declarative base — no init_app), SQLite in the webapp's
workload folder (LOCAL) or a DSS SQL connection (REMOTE, PostgreSQL/MSSQL),
JSON-as-TEXT columns and zlib-compressed traces. See chat/db.py for lifecycle.
"""
