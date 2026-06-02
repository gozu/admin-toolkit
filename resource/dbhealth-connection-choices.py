"""Populate the DB Health "RuntimeDB Connection" dropdown with the host's
PostgreSQL connections, plus a leading "(None)" choice that disables DB Health.

Mirrors the discovery shape proven in backend.py:_list_pg_connections().
"""


def do(payload, config, plugin_config, inputs):
    import logging

    choices = [{"value": "", "label": "(None — DB Health disabled)"}]
    try:
        import dataiku
        client = dataiku.api_client()
        all_conns = client.list_connections()
        items = (
            all_conns.items()
            if isinstance(all_conns, dict)
            else [(c.get("name"), c) for c in all_conns]
        )
        for name, info in items:
            if not isinstance(info, dict):
                continue
            if info.get("type", "") != "PostgreSQL":
                continue
            if name:
                choices.append({"value": name, "label": name})
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "[dbhealth-connection-choices] list_connections failed: %s", exc
        )

    return {"choices": choices}
