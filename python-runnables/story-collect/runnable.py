"""Plugin macro: Story hub collector (scheduled by 'Story: collect analytics').

Runs ONLY on the hub. Flow per run:
  1. load StoryConfig from plugin settings (fails fast when no connection);
  2. ensure/migrate the `story` Postgres schema;
  3. list hosts (local + remote-dss-host presets, optionally filtered);
  4. run_collection() — per-(host, source) isolated transactions with durable
     cursors and failure rows in story.ingest_runs;
  5. RAISE with a per-source summary if anything failed, so the scenario
     outcome is FAILED and the mail reporter emails the alert address.

Per-source booleans exist so an hourly audit-only scenario can be added later
without new code.
"""
import json

from dataiku.runnables import Runnable


def _bool(value, default=True):
    if value is None or value == '':
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('true', '1', 'yes', 'y')


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        # Deferred imports: dataiku/psycopg2 exist on the host; keep module
        # import cheap so DSS can introspect the runnable.
        import dataiku
        from db_adapter import load_story_config
        from adk_backend.story import collect, db, hosts as story_hosts
        from adk_backend.story.schema import ensure_schema

        client = dataiku.api_client()
        cfg = load_story_config(client=client)
        if not cfg.connection_name:
            raise RuntimeError(
                "Story is not configured: select a PostgreSQL connection in "
                "plugin settings → 'Story (experimental)'.")

        sources = []
        if _bool(self.config.get('collect_audit')):
            sources.append('audit')
        if _bool(self.config.get('collect_license')):
            sources.append('license')
        if _bool(self.config.get('collect_inventory')):
            sources.append('inventory')
        if not sources:
            raise RuntimeError('story-collect: every source is disabled — nothing to do')

        try:
            lookback_override = int(self.config.get('lookback_days') or 0)
        except (TypeError, ValueError):
            lookback_override = 0
        if lookback_override > 0:
            from dataclasses import replace
            cfg = replace(cfg, audit_lookback_days=lookback_override)

        host_csv = (self.config.get('hosts') or '').strip()
        only_ids = [h for h in host_csv.split(',') if h.strip()] if host_csv else None
        host_list = story_hosts.list_story_hosts(local_client=client, only_ids=only_ids)

        conn = db.connect(cfg.connection_name, client=client)
        try:
            ensure_schema(conn)
            status = collect.run_collection(conn, host_list, cfg, sources)
        finally:
            conn.close()

        if not status['ok']:
            failed = ['%s/%s: %s' % (r['host'], r['source'], r['error'])
                      for r in status['results'] if r['status'] != 'ok']
            # Durable detail lives in story.ingest_runs; this raise is what
            # flips the scenario outcome to FAILED → failure email.
            raise RuntimeError(
                'Story collection failed for %d unit(s): %s'
                % (status['failures'], ' | '.join(failed)[:1500]))

        return json.dumps(status)
