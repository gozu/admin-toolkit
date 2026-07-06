"""Shared plugin-level choices script (paramsPythonSetup). DSS calls it for
every SELECT param with getChoicesFromPython, passing the requested parameter
name in `payload`, so we branch on it:

- dataset_export_connection -> connections that can host a managed dataset
  (filesystem / cloud-object-store / SQL types), for "Save Tables as Datasets".
- archive_folder_connection -> connections that can host a managed folder,
  for the auto-created 'admin-toolkit-archive' backup/export destination.
- agents_audit_postgres_connection / triage_connection -> PostgreSQL
  connections only (agents audit database / daily triage store).
- chat_db_connection -> PostgreSQL / SQLServer connections (Agent Hub parity),
  for the optional server-side agents chat persistence.
- default_llm_id -> LLM Mesh completion models (same enumeration as the
  webapp's /api/llms report picker: project.list_llms(), RAG excluded).
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

# Connection types that can host a managed folder (file-like stores only).
# The per-connection allowManagedFolders flag is checked on top of this in
# the archive_folder_connection branch — DSS refuses folder creation on
# connections where an admin disabled it (e.g. filesystem_managed).
_MANAGED_FOLDER_CONN_TYPES = {
    "Filesystem", "HDFS", "S3", "EC2", "GCS", "Azure", "AzureBlob", "ADLS",
    "FTP", "SFTP", "SCP", "SSH",
}


def _list_connections():
    import dataiku
    all_conns = dataiku.api_client().list_connections()
    if isinstance(all_conns, dict):
        return list(all_conns.items())
    return [(c.get("name"), c) for c in all_conns]


def _llm_choices(config):
    """LLM Mesh completion models, tried on ADMINTOOLKIT first (the plugin's
    macro project) then any readable project — list_llms is project-scoped."""
    import dataiku
    client = dataiku.api_client()
    choices = [{"value": "", "label": "(None — set per agent)"}]
    llms = None
    try:
        keys = client.list_project_keys()
    except Exception:
        keys = []
    ordered = (["ADMINTOOLKIT"] if "ADMINTOOLKIT" in keys else []) + \
              [k for k in keys if k != "ADMINTOOLKIT"]
    for pk in ordered[:10]:
        try:
            llms = client.get_project(pk).list_llms()
            if llms:
                break
        except Exception:
            continue
    seen = set()
    for llm in llms or []:
        if llm.get("type") == "RETRIEVAL_AUGMENTED":
            continue
        llm_id = llm.get("id")
        if not llm_id or llm_id in seen:
            continue
        seen.add(llm_id)
        choices.append({"value": llm_id, "label": llm.get("friendlyName") or llm_id})
    # Keep the stored value selectable even if enumeration missed it.
    current = ((config or {}).get("default_llm_id") or "").strip()
    if current and current not in seen:
        choices.append({"value": current, "label": "%s (current)" % current})
    return choices


def do(payload, config, plugin_config, inputs):
    import logging

    param_name = ""
    if isinstance(payload, dict):
        param_name = payload.get("parameterName") or payload.get("name") or ""

    if param_name == "default_llm_id":
        try:
            return {"choices": _llm_choices(config)}
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "[default-llm-choices] list_llms failed: %s", exc
            )
            return {"choices": [{"value": "", "label": "(None — set per agent)"}]}

    if param_name == "dataset_export_connection":
        choices = [{"value": "", "label": "(None — Save as Datasets disabled)"}]
        allow = _MANAGED_DATASET_CONN_TYPES
        type_filter = lambda t, info=None: t in allow  # noqa: E731
        log_tag = "dataset-export"
    elif param_name == "archive_folder_connection":
        choices = [{"value": "", "label": "(None — pick folders manually)"}]
        allow = _MANAGED_FOLDER_CONN_TYPES

        def type_filter(t, info=None):
            return t in allow and (info or {}).get("allowManagedFolders", True)

        log_tag = "archive-folder"
    elif param_name == "agents_audit_postgres_connection":
        choices = [{"value": "", "label": "(None — agents audit disabled)"}]
        type_filter = lambda t, info=None: t == "PostgreSQL"  # noqa: E731
        log_tag = "agents-audit"
    elif param_name == "triage_connection":
        choices = [{"value": "", "label": "(None — triage loop disabled)"}]
        type_filter = lambda t, info=None: t == "PostgreSQL"  # noqa: E731
        log_tag = "triage"
    elif param_name == "chat_db_connection":
        # Agent Hub parity: chat persistence supports PostgreSQL + SQL Server.
        choices = [{"value": "", "label": "(None — select a connection)"}]
        type_filter = lambda t, info=None: t in ("PostgreSQL", "SQLServer")  # noqa: E731
        log_tag = "chat-db"
    else:
        choices = [{"value": "", "label": "(None — DB Health disabled)"}]
        type_filter = lambda t, info=None: t == "PostgreSQL"  # noqa: E731
        log_tag = "dbhealth"

    try:
        for name, info in _list_connections():
            if not isinstance(info, dict):
                continue
            if not type_filter(info.get("type", ""), info):
                continue
            if name:
                choices.append({"value": name, "label": name})
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "[%s-connection-choices] list_connections failed: %s", log_tag, exc
        )

    return {"choices": choices}
