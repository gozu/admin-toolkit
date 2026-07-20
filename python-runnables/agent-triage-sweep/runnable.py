"""Plugin macro: daily agent health triage (scheduled by 'Agents — Daily
health triage').

Flow per run:
  1. resolve plugin settings (backend URL, triage connection, threshold);
  2. deterministic sweep — health.py scores every host, no LLM in ranking;
  3. one LLM Mesh completion per flagged host drafts a recommendation,
     grounded ONLY in that host's issues + signals;
  4. persist rows to agents.agent_triage_daily (upsert on day+host);
  5. deterministic auto-remediation tier (mapped finding→target fixes over
     the admin's per-action Autonomous grants — budget priority);
  6. LLM planning pass (triage/auto_agent): may propose ANY
     autonomous-granted action for the flagged hosts, same executor, same
     shared budgets; its crash never loses the deterministic summary;
  7. snapshot zip, then the digest email via the configured mail channel;
  8. RAISE if any host errored, so the scenario reporter fires.

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
without limits, >1h retry storms) it MUST be the subject of the recommendation. \
Whitelist-suppressed findings are already absent from the data below — treat every issue \
you see as live. Structure: one sentence of diagnosis, one concrete \
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
        if not settings.get('master_password'):
            try:
                if (client.get('/api/hosts/keys/status') or {}).get('configured'):
                    config_warning = ('A remote host API key is encrypted but no master password '
                                      'is set in the Admin Toolkit plugin settings; '
                                      'remote hosts will fail after any backend restart.')
            except Exception:
                pass
        hosts = [h.strip() for h in (self.config.get('hosts') or '').split(',') if h.strip()] or None
        threshold = settings.get('triage_score_threshold', 75)
        run_id = 'triage-%d' % int(time.time())
        self._run_id = run_id

        snapshot_enabled = _bool(self.config.get('snapshot_enabled'), default=True)
        payload_sink = {} if snapshot_enabled else None
        result = sweep.sweep_fleet(client, hosts=hosts, score_threshold=threshold,
                                   payload_sink=payload_sink)
        rows = result['hosts']

        # "vs yesterday" deltas for the digest — decoration, never a dependency.
        previous = store.fetch_previous_scores(settings['triage_connection'])
        for row in rows:
            prev_score = previous.get(row['host'])
            if isinstance(prev_score, (int, float)) and isinstance(row.get('score'), (int, float)):
                row['previousScore'] = prev_score

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

        # Auto-remediation tiers (admin-granted Autonomous capabilities only;
        # failures become a digest warning, never a sweep failure). The LIVE
        # grants beat the kernel-start snapshot — an admin toggle this
        # afternoon applies tonight without a kernel recycle.
        auto_summary = None
        auto_error = None
        if not _bool(self.config.get('skip_auto_remediate')):
            try:
                from atk_agent_common import action_gates, actuator
                from atk_agent_common.triage import auto_remediate
                autonomous_actions = {a for a in actuator.ACTIONS
                                      if action_gates.action_autonomous(client, a)}
                auto_summary = auto_remediate.run_auto_remediation(
                    client, settings, rows, run_id,
                    autonomous_actions=autonomous_actions)
            except Exception as exc:
                auto_error = '%s: %s' % (type(exc).__name__, str(exc)[:300])
            # LLM planning pass — separate guard: a planner crash keeps the
            # deterministic summary intact (run_llm_planner itself returns an
            # error status rather than raising, this is the belt).
            if auto_summary is not None and not auto_summary.get('paused') \
                    and not _bool(self.config.get('skip_llm')):
                try:
                    from atk_agent_common.triage import auto_agent
                    auto_summary['llmPlanner'] = auto_agent.run_llm_planner(
                        client, settings, rows, result['flagged'], auto_summary,
                        autonomous_actions, run_id, llm_id)
                except Exception as exc:
                    auto_summary['llmPlanner'] = {
                        'status': 'error',
                        'error': '%s: %s' % (type(exc).__name__, str(exc)[:300])}

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
        """Branded HTML digest (atk_agent_common.triage.digest); the plain-text
        twin ships as the fallback body when the HTML send fails."""
        import dataiku
        from atk_agent_common.triage import digest
        from atk_agent_common.triage.provision import MACRO_PROJECT_KEY, resolve_mail_channel
        client = dataiku.api_client()
        channel_id = resolve_mail_channel(client, settings.get('triage_mail_channel') or '')
        channel = client.get_messaging_channel(channel_id)

        ctx = self._digest_context(settings, result, rows, config_warning,
                                   snapshot_error, auto_summary, auto_error)
        subject = digest.build_subject(ctx)
        recipient = settings['triage_recipient']
        try:
            channel.send(MACRO_PROJECT_KEY, [recipient], subject,
                         digest.render_digest_html(ctx), plain_text=False)
        except TypeError:
            # Older dataikuapi without plain_text kwarg — HTML is its default.
            channel.send(MACRO_PROJECT_KEY, [recipient], subject,
                         digest.render_digest_html(ctx))
        except Exception:
            # Never lose the report over markup: retry as plain text.
            channel.send(MACRO_PROJECT_KEY, [recipient], subject,
                         digest.render_digest_text(ctx), plain_text=True)

    def _digest_context(self, settings, result, rows, config_warning, snapshot_error,
                        auto_summary, auto_error):
        import dataiku
        host_labels = {}
        try:
            from atk_agent_common.client import ToolkitClient
            client = ToolkitClient(settings)
            for h in client.list_hosts() or []:
                if h.get('id'):
                    host_labels[h['id']] = h.get('label') or h.get('name') or h['id']
        except Exception:
            pass
        version = None
        try:
            plugin = dataiku.api_client().get_plugin('admin-toolkit')
            version = (plugin.get_settings().get_raw() or {}).get('version') \
                or (getattr(plugin, 'get_info', lambda: {})() or {}).get('version')
        except Exception:
            pass
        toolkit_url = None
        backend_url = settings.get('backend_url') or ''
        if '/web-apps-backends/' in backend_url:
            # public webapp UI = same base with the backend prefix swapped out
            base, _, tail = backend_url.partition('/web-apps-backends/')
            toolkit_url = '%s/public-webapps/%s' % (base, tail)
        return {
            'dateLabel': time.strftime('%A, %B %-d'),
            'timeLabel': time.strftime('%H:%M server time'),
            'runId': getattr(self, '_run_id', None),
            'threshold': result['scoreThreshold'],
            'version': version,
            'llmEnabled': bool(settings.get('default_llm_id')),
            'maxGb': float(settings.get('auto_remediate_max_gb') or 20),
            'hostLabels': host_labels,
            'hosts': rows,
            'flagged': result['flagged'],
            'autoSummary': auto_summary,
            'autoError': auto_error,
            'configWarning': config_warning,
            'snapshotError': snapshot_error,
            'toolkitUrl': toolkit_url,
        }
