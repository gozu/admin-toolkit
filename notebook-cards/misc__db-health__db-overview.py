"""
Misc › DB Health › Database overview
Notebook version of the runtimedb overview card (size / version / table stats).
Source of truth: backend.py:11600 api_db_health_overview  ·  frontend DbHealthPage.tsx
Run inside a DSS Jupyter notebook (admin-toolkit code env). No API key / host needed.

Queries run through the dbhealth-query macro (psycopg2, dataiku service account).
Set CONNECTION to a DSS PostgreSQL connection name, or leave "" to inspect the
local dataiku runtimedb via the unix socket. PASSWORD is only needed if the
connection can't authenticate without one.
"""
import dataiku
dataiku.use_plugin_libs("admin-toolkit")
from adk_notebook import get_client, ui, data

CONNECTION = ""      # e.g. "runtimedb"; "" = local socket
PASSWORD = ""        # only if required


def fetch():
    client = get_client()
    conn = CONNECTION or None
    pw = PASSWORD or None
    out = {"warnings": []}
    size = data.db_rows(data.db_query(client,
        "SELECT pg_size_pretty(pg_database_size(current_database())) as db_size,"
        " pg_database_size(current_database()) as db_size_bytes,"
        " current_setting('server_version') as version", conn, pw))
    if size:
        out.update({"dbSize": size[0].get("db_size"), "dbSizeBytes": size[0].get("db_size_bytes"),
                    "version": size[0].get("version")})
    stats = data.db_rows(data.db_query(client,
        "SELECT count(*) as table_count, coalesce(sum(n_dead_tup),0) as total_dead,"
        " coalesce(sum(n_live_tup),0) as total_live FROM pg_stat_user_tables", conn, pw))
    if stats:
        out.update({"tableCount": stats[0].get("table_count"),
                    "totalDeadTuples": stats[0].get("total_dead"),
                    "totalLiveTuples": stats[0].get("total_live")})
    write = data.db_rows(data.db_query(client,
        "SELECT current_user as cu, current_setting('is_superuser') as su", conn, pw))
    out["canWrite"] = bool(write and write[0].get("su") == "on")
    out["queryError"] = None if size or stats else "Query failed — check CONNECTION / PASSWORD"
    return out


def render(payload):
    ui.header("Database Overview", "Misc › DB Health")
    if payload.get("queryError"):
        ui.note(payload["queryError"], "ERROR")
        return
    ui.kv_panel("Runtime DB", {
        "Size": payload.get("dbSize") or "—",
        "Version": payload.get("version") or "—",
        "Tables": payload.get("tableCount", 0),
        "Live tuples": f"{int(payload.get('totalLiveTuples') or 0):,}",
        "Dead tuples": (f"{int(payload.get('totalDeadTuples') or 0):,}",
                        "red" if int(payload.get("totalDeadTuples") or 0) > 100000 else "white"),
        "Write access": ("yes", "green") if payload.get("canWrite") else ("read-only", "yellow"),
    })


if __name__ != "__skip__":
    render(fetch())
