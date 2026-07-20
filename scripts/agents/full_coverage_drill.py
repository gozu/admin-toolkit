#!/usr/bin/env python3
"""Full agent-coverage drill against a LIVE toolkit backend (the faithful
webapp surface: /api/agents/chat SSE — the exact route the Agents page uses).

Three phases:

  read    Every config_inspect domain, every toolkit_get endpoint and every
          standalone sensor gets exercised through real chat turns (batched
          3-4 per turn). PASS = the expected tool_call event appeared and the
          turn produced no error event.
  issues  Ground truth first: the Python health scorer (score-parity wiring)
          plus the cached module endpoints enumerate every finding family
          currently PRESENT on the instance. Each family gets a chat turn
          asking the agent to investigate + plan (or declare the documented
          manual gap). PASS = plan event for remediable families / honest
          manual guidance (no plan) for documented gaps.
  execute With --execute, plans whose action is in the safe reversible
          whitelist are approved through chat (the UI's approval message
          verbatim, confirm_token included). PASS = execution event ok:true
          with an auditId.

Gates: the script unlocks Agentic Actions (master password file), snapshots
the gate map, enables what the drill needs, and ALWAYS restores the snapshot.

    .venv/bin/python scripts/agents/full_coverage_drill.py \
        --base https://<host>/web-apps-backends/<PROJECT>/<webappId> \
        --password-file masterpass.key [--phase all] [--execute] \
        [--report drill_report.json]
"""

import argparse
import json
import pathlib
import re
import sys
import time

import requests

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'python-lib'))

from atk_agent_common import config, health, remediation_map  # noqa: E402
from atk_agent_common import domain_registry, read_registry  # noqa: E402
from atk_agent_common.client import ToolkitClient  # noqa: E402

DEFAULT_BASE = ("https://tam-global.fe-aws.dkucloud-dev.com"
                "/web-apps-backends/DIAG_PARSER_BRANCH1/Gv9CLFn")

STANDALONE_SENSORS = (
    ('list_hosts', 'Call list_hosts (no probe). Report how many hosts you see.'),
    ('list_capabilities', 'Call list_capabilities. Report how many actions are '
                          'currently gate-enabled.'),
    ('instance_health', "Call instance_health for host 'local' with sections "
                        "['system','sanity','java'] (include_score=false). "
                        'Report total RAM and the sanity max severity.'),
    ('compute_cost', "Call compute_cost grouped by project. Report the top "
                     'project by CPU-hours.'),
    ('log_errors', 'Call log_errors (grouped mode). Report the most frequent '
                   'error signature.'),
    ('log_tail', "Call log_tail with pattern 'ERROR' and lines=50. Report one "
                 'matching line verbatim.'),
    ('storage_footprint', 'Call storage_footprint. Report the largest project '
                          'and its size.'),
    ('k8s_health', 'Call k8s_health (no cluster deep-dive). Report cluster '
                   'count and states.'),
    ('db_health', "Call db_health view='overview'. Report the runtime DB size "
                  'or the reason it is unavailable.'),
)

# Reversible / harmless actions the execute phase may approve automatically.
SAFE_EXECUTE_ACTIONS = {'connection-test', 'connection-index', 'db-analyze',
                        'cluster-pods-cleanup', 'notebook-kernels-shutdown'}


def sse_events(resp):
    """Minimal SSE parser → (event, payload) tuples."""
    event, data = None, []
    for raw in resp.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        line = raw.strip('\r')
        if line.startswith('event:'):
            event = line[6:].strip()
        elif line.startswith('data:'):
            data.append(line[5:].strip())
        elif line == '' and event is not None:
            try:
                payload = json.loads('\n'.join(data) or '{}')
            except ValueError:
                payload = {}
            yield event, payload
            event, data = None, []


class Drill:
    def __init__(self, base, password, report_path, runtime=None):
        self.base = base.rstrip('/')
        self.session = requests.Session()
        self.password = password
        self.runtime = runtime  # None = server default (dataiku since 0.4.777)
        self.report = {'base': base, 'runtime': runtime or 'server-default',
                       'read': [], 'issues': [], 'execute': []}
        self.report_path = report_path
        self.client = ToolkitClient(config.resolve(
            {'backend_url': self.base, 'heavy_timeout_s': 900}))
        agents = self.session.get(self.base + '/api/agents', timeout=60).json()
        rows = agents.get('agents') or []
        if not rows:
            raise SystemExit('no agent provisioned at %s' % base)
        self.agent_id = rows[0]['id']
        print('agent: %s (%s)' % (rows[0]['name'], self.agent_id))
        self._gate_snapshot = None

    # ── plumbing ────────────────────────────────────────────────────────────

    def chat(self, messages, timeout=900):
        """One streamed turn. Returns dict(text, events, error, done)."""
        out = {'text': [], 'events': [], 'error': None, 'done': None}
        body = {'agentId': self.agent_id, 'messages': messages}
        if self.runtime:
            # Explicit override: pins the runtime AND (for 'dataiku') disables
            # the pre-stream native fallback — deterministic drill results.
            body['runtime'] = self.runtime
        resp = self.session.post(
            self.base + '/api/agents/chat',
            json=body,
            stream=True, timeout=(30, timeout))
        resp.raise_for_status()
        for event, payload in sse_events(resp):
            if event == 'chunk':
                out['text'].append(str(payload.get('text') or ''))
            elif event == 'agent_event':
                out['events'].append((str(payload.get('eventKind') or ''),
                                      payload.get('eventData') or {}))
            elif event == 'done':
                out['done'] = payload
            elif event == 'error':
                out['error'] = str(payload.get('message') or 'stream error')
        out['text'] = ''.join(out['text'])
        return out

    def tool_calls(self, result, name=None):
        calls = [d for k, d in result['events'] if k == 'tool_call']
        if name:
            calls = [c for c in calls if str(c.get('name')) == name]
        return calls

    def unlock(self):
        r = self.session.post(self.base + '/api/auth/red/unlock',
                              json={'password': self.password}, timeout=30)
        if r.status_code != 200:
            raise SystemExit('red unlock failed: %s %s' % (r.status_code, r.text[:200]))
        print('agentic actions unlocked (expires %s)' % r.json().get('expiresAt'))

    def gates(self):
        return self.session.get(self.base + '/api/agents/action-settings',
                                timeout=30).json()

    def set_gates(self, updates):
        r = self.session.post(self.base + '/api/agents/action-settings/update',
                              json={'gates': updates}, timeout=30)
        if r.status_code != 200:
            raise SystemExit('gate update failed: %s %s' % (r.status_code, r.text[:300]))

    def enable_gates(self, actions):
        current = self.gates()
        self._gate_snapshot = {row['action']: bool(row['enabled'])
                               for row in current.get('actions') or []}
        updates = {a: True for a in actions if not self._gate_snapshot.get(a, False)}
        if updates:
            print('enabling gates for drill: %s' % ', '.join(sorted(updates)))
            self.set_gates(updates)

    def restore_gates(self):
        if not self._gate_snapshot:
            return
        live = {row['action']: bool(row['enabled'])
                for row in self.gates().get('actions') or []}
        reverts = {a: v for a, v in self._gate_snapshot.items() if live.get(a) != v}
        if reverts:
            print('restoring gate snapshot: %s' % ', '.join(sorted(reverts)))
            self.set_gates(reverts)
        self._gate_snapshot = None

    def save(self):
        if self.report_path:
            pathlib.Path(self.report_path).write_text(
                json.dumps(self.report, indent=2, default=str))

    # ── phase: read coverage ────────────────────────────────────────────────

    def _read_cases(self):
        pk = self._sample_project_key()
        cases = []
        for row in domain_registry.DOMAINS:
            name = row['name']
            extra = ''
            if row['project_scoped']:
                extra = " with name_filter='%s'" % pk
            cases.append(('domain:%s' % name, 'config_inspect',
                          {'domain': name},
                          "Call config_inspect with domain='%s'%s on the local "
                          'host. Report one concrete fact from the result.'
                          % (name, extra)))
        for row in read_registry.ENDPOINTS:
            cases.append(('endpoint:%s' % row['name'], 'toolkit_get',
                          {'endpoint': row['name']},
                          "Call toolkit_get with endpoint='%s' on the local "
                          'host. Report one concrete number from the result.'
                          % row['name']))
        for name, prompt in STANDALONE_SENSORS:
            cases.append(('sensor:%s' % name, name, {}, prompt))
        return cases

    def _sample_project_key(self):
        try:
            data = self.client.get('/api/projects', host='local')
            rows = data if isinstance(data, list) else (data.get('projects') or [])
            for p in rows:
                key = p.get('projectKey') or p.get('key')
                if key:
                    return str(key)
        except Exception:
            pass
        return 'ADMINTOOLKIT'

    def _case_called(self, case, result):
        _, tool, want_args, _ = case
        for call in self.tool_calls(result, tool):
            args = call.get('args') or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            if all(str(args.get(k)) == str(v) for k, v in want_args.items()):
                return True
        return False

    def run_read(self, batch_size=4):
        cases = self._read_cases()
        print('\n=== READ PHASE: %d cases ===' % len(cases))
        pending = list(cases)
        batches = [pending[i:i + batch_size]
                   for i in range(0, len(pending), batch_size)]
        retry = []
        for i, batch in enumerate(batches):
            prompt = ('Coverage drill — perform EACH of the following, one '
                      'tool call after another, then summarize one fact per '
                      'item (numbered). Do not skip any item. Retry once with '
                      'corrected arguments if a call errors; if a heavy scan '
                      "returns scan_running, say 'scan_running' for that item."
                      '\n\n' + '\n'.join('%d. %s' % (j + 1, c[3])
                                         for j, c in enumerate(batch)))
            result = self.chat([{'role': 'user', 'content': prompt}])
            for case in batch:
                ok = self._case_called(case, result)
                scan_running = ('scan_running' in result['text'].lower())
                status = 'PASS' if ok and not result['error'] else (
                    'SCAN_RUNNING' if ok and scan_running else 'RETRY')
                if status == 'RETRY':
                    retry.append(case)
                else:
                    self.report['read'].append(
                        {'case': case[0], 'status': status})
                print('  [%s] %s' % (status, case[0]))
            if result['error']:
                print('  turn error: %s' % result['error'])
            self.save()
        for case in retry:
            result = self.chat([{'role': 'user', 'content': case[3]}])
            ok = self._case_called(case, result) and not result['error']
            status = 'PASS' if ok else 'FAIL'
            self.report['read'].append({'case': case[0], 'status': status,
                                        'error': result['error'],
                                        'text': result['text'][-400:]})
            print('  retry [%s] %s' % (status, case[0]))
            self.save()

    # ── phase: issue resolution ─────────────────────────────────────────────

    def _health_findings(self):
        """Ground truth: same wiring as score_parity.py."""
        host = 'local'
        overview = self.client.get('/api/overview', host=host)
        raw_settings = self.client.get('/api/settings/raw', host=host)
        java_text = self.client.get_text('/api/java-memory', host=host)
        code_envs = self.client.get('/api/code-envs', host=host, heavy=True)
        footprint = self.client.get('/api/project-footprint', host=host, heavy=True)
        thresholds = self.client.get('/api/settings/threshold-defaults')
        whitelist = health.fetch_host_whitelist(self.client, host)
        sanity = health.fetch_sanity_messages(self.client, host)
        conn_health = health.fetch_connection_health(self.client, host)
        from atk_agent_common.tools_impl import _parse_java_memory
        parsed = health.build_parsed_data(
            overview, raw_settings, _parse_java_memory(java_text), code_envs,
            footprint, sanity_messages=sanity, connection_health=conn_health)
        score = health.calculate_health_score(parsed, thresholds,
                                              whitelist=whitelist)
        return score.get('issues') or []

    def _module_findings(self):
        """Cached module endpoints → extra finding families present."""
        out = []
        probes = (
            ('connection-audit', '/api/connections/audit', None,
             lambda d: sum(1 for r in (d.get('connections') or d.get('audit') or [])
                           if (r.get('configIssues') or r.get('issues')))),
            ('cru-idle-resources', '/api/cru', None,
             lambda d: len(d.get('idleResources') or [])),
            ('db-bloat', '/api/tools/db-health/overview', None,
             lambda d: 1 if d.get('ok', True) and d.get('tables') else 0),
            ('churn-dormant-seats', '/api/users/churn', None,
             lambda d: len(d.get('users') or d.get('accounts') or [])),
            ('llm-audit-flagged', '/api/llm-audit', None,
             lambda d: sum((d.get('summary') or {}).get(k, 0)
                           for k in ('obsolete', 'ripoff'))),
        )
        for fam, path, params, counter in probes:
            try:
                data = self.client.get(path, host='local', params=params)
                n = counter(data if isinstance(data, dict) else {})
                if n:
                    out.append({'id': fam, 'title': '%s (%d present)' % (fam, n),
                                'severity': 'info'})
            except Exception as exc:
                print('  (module probe %s skipped: %s)' % (fam, str(exc)[:120]))
        return out

    def run_issues(self, limit=0):
        print('\n=== ISSUES PHASE ===')
        findings = self._health_findings()
        families = {}
        for f in findings:
            fam = re.sub(r'(-group)?$', '', str(f.get('id')))
            families.setdefault(fam, f)
        for f in self._module_findings():
            families.setdefault(f['id'], f)
        rows = sorted(families.items())
        if limit:
            rows = rows[:limit]
        print('finding families present: %d' % len(rows))
        for fam, f in rows:
            gap = remediation_map.is_documented_gap(fam)
            specs = remediation_map.remediations_for(fam)
            expect = 'manual' if gap or not specs else 'plan'
            prompt = (
                "Finding '%s' (%s, severity %s) is present on this instance. "
                'Investigate it with your sensors, then: if one of your '
                'catalogued actions can remediate it, produce the plan '
                '(plan_admin_action) and WAIT for approval — do NOT execute. '
                'If it is NOT auto-remediable, say so explicitly and give the '
                'exact manual steps. Cite concrete evidence.'
                % (fam, str(f.get('title'))[:160], f.get('severity')))
            result = self.chat([{'role': 'user', 'content': prompt}])
            plans = [d for k, d in result['events'] if k == 'plan']
            items = [d for k, d in result['events'] if k == 'action_items']
            text_lower = result['text'].lower()
            said_manual = any(w in text_lower for w in
                              ('manual', 'cannot be automated', 'not auto',
                               'no catalogued action', "can't remediate"))
            if expect == 'plan':
                ok = bool(plans or items)
                verdict = 'PASS' if ok else 'FAIL'
            else:
                ok = not plans and said_manual
                verdict = 'PASS' if ok else ('OVERREACH' if plans else 'FAIL')
            self.report['issues'].append({
                'family': fam, 'expected': expect, 'verdict': verdict,
                'plans': [{'action': p.get('action'),
                           'confirmToken': p.get('confirm_token'),
                           'target': p.get('target') or p.get('canonicalTarget')}
                          for p in plans],
                'error': result['error'], 'summary': result['text'][-500:]})
            print('  [%s] %s (expected %s; %d plan(s), %d item batch(es))'
                  % (verdict, fam, expect, len(plans), len(items)))
            self.save()

    # ── phase: execute (safe subset) ────────────────────────────────────────

    def run_execute(self):
        print('\n=== EXECUTE PHASE (safe reversible subset) ===')
        for row in self.report['issues']:
            for plan in row.get('plans') or []:
                action = str(plan.get('action') or '')
                token = plan.get('confirmToken') or ''
                if action not in SAFE_EXECUTE_ACTIONS or not token:
                    continue
                approval = ('Approved — I confirm. Execute the planned %s on '
                            'host local with the exact planned target, '
                            'confirm=true and confirm_token %s. Report the '
                            'outcome and the auditId.' % (action, token))
                history = [
                    {'role': 'user', 'content': 'Re-issue and execute the '
                     'approved plan below.'},
                    {'role': 'user', 'content': approval},
                ]
                result = self.chat(history)
                execs = [d for k, d in result['events'] if k == 'execution']
                ok = any(str(e.get('status')) == 'ok' for e in execs)
                self.report['execute'].append(
                    {'action': action, 'ok': ok,
                     'auditIds': [e.get('auditId') for e in execs],
                     'error': result['error'],
                     'summary': result['text'][-400:]})
                print('  [%s] %s auditIds=%s'
                      % ('PASS' if ok else 'FAIL', action,
                         [e.get('auditId') for e in execs]))
                self.save()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=DEFAULT_BASE)
    ap.add_argument('--password-file', default='masterpass.key')
    ap.add_argument('--phase', default='all',
                    choices=('all', 'read', 'issues', 'execute'))
    ap.add_argument('--limit', type=int, default=0,
                    help='cap issue families (0 = all)')
    ap.add_argument('--execute', action='store_true',
                    help='approve + execute the safe reversible subset')
    ap.add_argument('--runtime', choices=('native', 'dataiku'), default=None,
                    help='per-turn runtime override (default: the server '
                         "default — 'dataiku' since 0.4.777; an explicit "
                         "'dataiku' also disables the native fallback)")
    ap.add_argument('--report', default='scripts/agents/.coverage_drill.json')
    args = ap.parse_args()

    password = pathlib.Path(args.password_file).read_text().strip()
    drill = Drill(args.base, password, args.report, runtime=args.runtime)
    drill.unlock()
    try:
        if args.phase in ('all', 'issues', 'execute') or args.execute:
            # Plan-time gating: a disabled action refuses at PLAN, so the
            # issues phase needs every action remediation_map can propose.
            need = set(SAFE_EXECUTE_ACTIONS)
            for _glob, specs in remediation_map.REMEDIATIONS:
                for spec in specs or ():
                    need.add(spec['action'])
            drill.enable_gates(sorted(need))
        if args.phase in ('all', 'read'):
            drill.run_read()
        if args.phase in ('all', 'issues'):
            drill.run_issues(limit=args.limit)
        if args.execute and args.phase in ('all', 'execute'):
            drill.run_execute()
    finally:
        drill.restore_gates()
        drill.save()
    reads = [r['status'] for r in drill.report['read']]
    issues = [r['verdict'] for r in drill.report['issues']]
    print('\n=== SUMMARY ===')
    print('read: %d PASS / %d other / %d total'
          % (reads.count('PASS'), len(reads) - reads.count('PASS'), len(reads)))
    print('issues: %d PASS / %d other / %d total'
          % (issues.count('PASS'), len(issues) - issues.count('PASS'),
             len(issues)))
    if drill.report['execute']:
        ex = [r['ok'] for r in drill.report['execute']]
        print('execute: %d ok / %d total' % (sum(ex), len(ex)))
    bad = (len(reads) - reads.count('PASS') - reads.count('SCAN_RUNNING')
           + sum(1 for v in issues if v not in ('PASS',)))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
