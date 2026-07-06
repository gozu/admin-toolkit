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
mount >=75%% full, recently-broken active connection, deprecated Python in use, exec configs \
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

        snapshot_enabled = _bool(self.config.get('snapshot_enabled'), default=True)
        payload_sink = {} if snapshot_enabled else None
        result = sweep.sweep_fleet(client, hosts=hosts, score_threshold=threshold,
                                   payload_sink=payload_sink)
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

        # Auto-remediation tier (admin-opted actions only; failures become a
        # digest warning, never a sweep failure).
        auto_summary = None
        auto_error = None
        if not _bool(self.config.get('skip_auto_remediate')):
            try:
                from atk_agent_common.triage import auto_remediate
                auto_summary = auto_remediate.run_auto_remediation(client, settings, rows, run_id)
            except Exception as exc:
                auto_error = '%s: %s' % (type(exc).__name__, str(exc)[:300])

        # Snapshot zip (schema-free record of every scan payload the sweep
        # consumed). Failures become a digest warning, never a sweep failure.
        snapshot_info = None
        snapshot_error = None
        if snapshot_enabled:
            try:
                snapshot_info = self._write_snapshot(payload_sink, result, rows, run_id)
            except Exception as exc:
                snapshot_error = '%s: %s' % (type(exc).__name__, str(exc)[:200])

        digest_error = None
        if not _bool(self.config.get('skip_email')) and settings.get('triage_recipient'):
            try:
                self._send_digest(settings, result, rows, config_warning,
                                  snapshot_error=snapshot_error,
                                  auto_summary=auto_summary, auto_error=auto_error)
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
            'snapshot': snapshot_info,
            'snapshotError': snapshot_error,
            'autoRemediation': auto_summary,
            'autoRemediationError': auto_error,
        }
        if errored:
            raise RuntimeError('Triage completed with host errors: %s — summary: %s'
                               % (errored, json.dumps(summary, default=str)))
        return json.dumps(summary, default=str)

    def _write_snapshot(self, payload_sink, result, rows, run_id):
        """One zip of all raw scan payloads (+ per-host triage row) per run,
        into the configured managed folder (empty ⇒ find-or-create the
        default) in the scenario's project."""
        import dataiku
        from atk_agent_common import snapshot
        from atk_agent_common.triage.provision import MACRO_PROJECT_KEY
        for row in rows:
            payload_sink.setdefault(row['host'], {})['triage-row'] = row
        try:
            import os
            import atk_agent_common
            plugin_json = os.path.join(os.path.dirname(atk_agent_common.__file__),
                                       '..', '..', 'plugin.json')
            with open(plugin_json) as fh:
                agents_version = (json.load(fh) or {}).get('version')
        except Exception:
            agents_version = None
        stamp = time.strftime('%y%m%d%H%M')
        manifest = {
            'generatedAt': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            'stamp': stamp,
            'runId': run_id,
            'hosts': [r['host'] for r in rows],
            'flagged': result['flagged'],
            'scoreThreshold': result['scoreThreshold'],
            'agentsPluginVersion': agents_version,
        }
        project = dataiku.api_client().get_project(self.project_key or MACRO_PROJECT_KEY)
        return snapshot.write_snapshot(project, payload_sink, manifest, stamp,
                                       folder_ref=self.config.get('snapshot_folder') or '')

    def _send_digest(self, settings, result, rows, config_warning=None, snapshot_error=None,
                     auto_summary=None, auto_error=None):
        import dataiku
        from atk_agent_common.triage.provision import MACRO_PROJECT_KEY, resolve_mail_channel
        client = dataiku.api_client()
        channel_id = resolve_mail_channel(client, settings.get('triage_mail_channel') or '')
        channel = client.get_messaging_channel(channel_id)
        lines = ['Daily DSS fleet health triage (threshold %s):' % result['scoreThreshold'], '']
        if config_warning:
            lines += ['CONFIG WARNING: %s' % config_warning, '']
        if snapshot_error:
            lines += ['SNAPSHOT WARNING: snapshot zip failed: %s' % snapshot_error, '']
        lines += self._auto_remediation_lines(auto_summary, auto_error)
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

    @staticmethod
    def _auto_remediation_lines(auto_summary, auto_error):
        """Digest section for the auto-remediation tier — every executed fix
        (freed GB + audit row id) and every skip with its reason. Silent only
        when the tier is off and nothing errored."""
        if auto_error:
            return ['AUTO-REMEDIATION WARNING: tier crashed: %s' % auto_error, '']
        if not auto_summary or not auto_summary.get('enabled'):
            return []
        lines = ['Auto-remediation (opted-in: %s):' % ', '.join(auto_summary['enabled'])]
        for done in auto_summary.get('executed') or []:
            lines.append('  ✓ %s %s (finding %s) — freed %.2f GB, audit #%s'
                         % (done['host'], done['action'], done.get('findingId'),
                            done.get('freedGB') or 0, done.get('auditId')))
            if done.get('warning'):
                lines.append('    !! %s' % done['warning'])
        for skip in auto_summary.get('skipped') or []:
            lines.append('  – %s %s: %s' % (skip.get('host'),
                                            skip.get('action') or '(all)', skip.get('reason')))
        if not (auto_summary.get('executed') or auto_summary.get('skipped')):
            lines.append('  (no matching findings today)')
        lines += ['  Total freed: %.2f GB across %d object(s).'
                  % (auto_summary.get('totalFreedGB') or 0, auto_summary.get('totalObjects') or 0), '']
        return lines
