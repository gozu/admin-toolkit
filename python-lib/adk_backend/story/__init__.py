"""Story — Postgres-persisted, scheduled analytics (experimental).

Pure-logic package: nothing here may import flask (or any request-scoped
toolkit module) at module level, so the python-runnables/ macros can import
it and the tests can run without DSS.
"""
