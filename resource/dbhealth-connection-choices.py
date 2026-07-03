"""Shared plugin-level choices script (paramsPythonSetup). DSS calls it for
every SELECT param with getChoicesFromPython, passing the requested parameter
name in `payload`, so we branch on it:

- dataset_export_connection -> connections that can host a managed dataset
  (filesystem / cloud-object-store / SQL types), for "Save Tables as Datasets".
- agents_audit_postgres_connection -> PostgreSQL connections only, for the
  agents audit database (agents.agent_actions).
- dbhealth_connection (default) -> PostgreSQL connections only, for DB Health.

Both list shapes mirror the discovery proven in backend.py.
"""


# Connection types that can store a managed dataset (filesystem, cloud object
# stores, and SQL databases). LLM / web / other non-tabular connections are
# excluded. Superset is fine: only types actually present on the host appear.
_MANAGED_DATASET_CONN_TYPES = {
    "Filesystem", "HDFS", "S3", "EC2", "GCS", "Azure", "AzureBlob", "ADLS",
    "PostgreSQL", "MySQL", "MariaDB", "Snowflake", "BigQuery", "Redshift",
    "Greenplum", "Vertica", "Oracle", "SQLServer", "Synapse", "Teradata",
    "Databricks", "Athena", "Netezza", "SAPHANA", "DB2", "Exasol", "Kdb",
    "ClickHouse", "Trino", "Presto",
}


def _list_connections():
    import dataiku
    all_conns = dataiku.api_client().list_connections()
    if isinstance(all_conns, dict):
        return list(all_conns.items())
    return [(c.get("name"), c) for c in all_conns]


def do(payload, config, plugin_config, inputs):
    import logging

    param_name = ""
    if isinstance(payload, dict):
        param_name = payload.get("parameterName") or payload.get("name") or ""

    if param_name == "dataset_export_connection":
        choices = [{"value": "", "label": "(None — Save as Datasets disabled)"}]
        allow = _MANAGED_DATASET_CONN_TYPES
        type_filter = lambda t: t in allow  # noqa: E731
        log_tag = "dataset-export"
    elif param_name == "agents_audit_postgres_connection":
        choices = [{"value": "", "label": "(None — agents audit disabled)"}]
        type_filter = lambda t: t == "PostgreSQL"  # noqa: E731
        log_tag = "agents-audit"
    else:
        choices = [{"value": "", "label": "(None — DB Health disabled)"}]
        type_filter = lambda t: t == "PostgreSQL"  # noqa: E731
        log_tag = "dbhealth"

    try:
        for name, info in _list_connections():
            if not isinstance(info, dict):
                continue
            if not type_filter(info.get("type", "")):
                continue
            if name:
                choices.append({"value": name, "label": name})
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "[%s-connection-choices] list_connections failed: %s", log_tag, exc
        )

    return {"choices": choices}
