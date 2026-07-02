"""Plugin macro: aggregate the host audit logs into per-day Story rows.

Read-only and DB-free by design: the same adk_backend.story.aggregate code
runs here on every host (local hub and remotes). Runs as the `dataiku`
service account (impersonate=false) so it can read <DIP_HOME>/run/audit/.

Returns the aggregate payload as JSON — the hub's story-collect macro is the
only writer to Postgres.
"""
import json
import os

from dataiku.runnables import Runnable

from adk_backend.story.aggregate import aggregate_audit_dir


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        dip_home = os.environ.get('DIP_HOME') or os.environ.get('DKU_DIP_HOME')
        if not dip_home:
            return json.dumps({'ok': False, 'error': 'DIP_HOME not set on host'})
        audit_dir = os.path.join(dip_home, 'run', 'audit')

        since_day = (self.config.get('since_day') or '').strip() or None
        try:
            lookback_days = int(self.config.get('lookback_days') or 14)
        except (TypeError, ValueError):
            lookback_days = 14
        try:
            max_files = int(self.config.get('max_files') or 0)
        except (TypeError, ValueError):
            max_files = 0

        try:
            result = aggregate_audit_dir(
                audit_dir,
                since_day=since_day,
                lookback_days=lookback_days,
                max_files=max_files,
            )
        except Exception as exc:
            return json.dumps({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:240]}'})
        result['auditDir'] = audit_dir
        return json.dumps(result)
