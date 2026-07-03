"""Plugin macro: daily agent health triage (scheduled by 'Agents — Daily
health triage').

Flow per run:
  1. resolve plugin settings (backend URL, triage connection, threshold);
  2. deterministic sweep — health.py scores every host, no LLM in ranking;
  3. one LLM Mesh completion per flagged host drafts a recommendation,
     grounded ONLY in that host's issues + signals;
  4. persist rows to agents.agent_triage_daily (upsert on day+host);
  5. email a digest via the configured mail channel;
  6. RAISE if any host errored, so the scenario reporter fires.

The sweep doubles as the fleet-wide heavy-cache pre-warmer: after it runs,
agent tool calls hit warm code-envs/footprint caches all day.
"""
import json
import time

from dataiku.runnables import Runnable


def _bool(value, default=False):
    if value is None or value == '':
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('true', '1', 'yes', 'y')


RECOMMENDATION_PROMPT = """You are drafting ONE health recommendation for a Dataiku DSS instance; the \
instance ADMIN reads it in tomorrow's digest. Ground every statement in the JSON below — do \
not invent metrics or issues. Severity doctrine: only medium+ findings matter; when an \
always-lead critical is present (H2 runtime DB, DIP_HOME on NFS, missing cgroups, data \
mount >=75% full, recently-broken active connection, deprecated Python in use, exec configs \
without limits, >1h retry storms) it MUST be the subject of the recommendation. Ignore \
whitelist-suppressed findings. Structure: one sentence of diagnosis, one concrete \
recommended action (the single highest-impact next step), one sentence of evidence (cite \
issue ids or log signatures). Max 120 words, no preamble.

Host data:
%s"""


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config or {}

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        import dataiku
        from atk_agent_common import config as config_mod
        from atk_agent_common.client import ToolkitClient
        from atk_agent_common.triage import store, sweep

        settings = config_mod.resolve(self.plugin_config)
        if not settings.get('triage_connection'):
            raise RuntimeError('Agent triage is not configured: set the triage PostgreSQL '
                               'connection in the Admin Toolkit Agents plugin settings.')
        client = ToolkitClient(settings)
        # Preflight: encrypted host keys with no configured password only work
        # while the backend still caches a key from an interactive UI unlock —
        # any backend restart then breaks remote hosts. Surface it every run.
        config_warning = None
        if not settings.get('host_keys_password'):
            try:
                if (client.get('/api/hosts/keys/status') or {}).get('configured'):
                    config_warning = ('A remote host API key is encrypted but host_keys_password '
                                      'is not set in the Admin Toolkit Agents plugin settings; '
                                      'remote hosts will fail after any backend restart.')
            except Exception:
                pass
        hosts = [h.strip() for h in (self.config.get('hosts') or '').split(',') if h.strip()] or None
        threshold = settings.get('triage_score_threshold', 75)
        run_id = 'triage-%d' % int(time.time())

        result = sweep.sweep_fleet(client, hosts=hosts, score_threshold=threshold)
        rows = result['hosts']

        llm_id = settings.get('default_llm_id')
        if not _bool(self.config.get('skip_llm')) and llm_id and result['flagged']:
            from atk_agent_common.triage.provision import MACRO_PROJECT_KEY
            dss = dataiku.api_client()
            llm = dss.get_project(self.project_key or MACRO_PROJECT_KEY).get_llm(llm_id)
            for row in rows:
                if row['host'] not in result['flagged']:
                    continue
                try:
                    completion = llm.new_completion()
                    completion.with_message(RECOMMENDATION_PROMPT % json.dumps(row, default=str)[:8000])
                    resp = completion.execute()
                    row['recommendation'] = (resp.text or '').strip() if resp.success else \
                        '[LLM draft failed: %s]' % getattr(resp, 'errorMessage', 'unknown')
                except Exception as exc:
                    row['recommendation'] = '[LLM draft failed: %s: %s]' % (type(exc).__name__, str(exc)[:150])

        written = store.persist_sweep(settings['triage_connection'], rows, run_id, llm_id=llm_id)

        digest_error = None
        if not _bool(self.config.get('skip_email')) and settings.get('triage_recipient'):
            try:
                self._send_digest(settings, result, rows, config_warning)
            except Exception as exc:
                digest_error = '%s: %s' % (type(exc).__name__, str(exc)[:200])

        errored = [r['host'] for r in rows if r.get('status') == 'error']
        summary = {
            'runId': run_id,
            'hostsScored': len(rows),
            'flagged': result['flagged'],
            'errored': errored,
            'errors': {r['host']: r.get('error') for r in rows if r.get('status') == 'error'},
            'rowsWritten': written,
            'digestError': digest_error,
            'configWarning': config_warning,
        }
        if errored:
            raise RuntimeError('Triage completed with host errors: %s — summary: %s'
                               % (errored, json.dumps(summary, default=str)))
        return json.dumps(summary, default=str)

    def _send_digest(self, settings, result, rows, config_warning=None):
        import dataiku
        from atk_agent_common.triage.provision import MACRO_PROJECT_KEY, resolve_mail_channel
        client = dataiku.api_client()
        channel_id = resolve_mail_channel(client, settings.get('triage_mail_channel') or '')
        channel = client.get_messaging_channel(channel_id)
        lines = ['Daily DSS fleet health triage (threshold %s):' % result['scoreThreshold'], '']
        if config_warning:
            lines += ['CONFIG WARNING: %s' % config_warning, '']
        for row in rows:
            score = row.get('score')
            lines.append('%s — %s (%s)' % (row['host'],
                                           ('score %s' % score) if score is not None else 'no score',
                                           row.get('status')))
            if row.get('error'):
                lines.append('  ! %s' % json.dumps(row['error'], default=str)[:300])
            if row.get('recommendation'):
                lines.append('  → %s' % row['recommendation'])
        body = '\n'.join(lines)
        channel.send(MACRO_PROJECT_KEY, [settings['triage_recipient']],
                     '[Admin Toolkit / Agents] Daily fleet health triage', body)
