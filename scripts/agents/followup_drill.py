#!/usr/bin/env python3
"""Follow-up to full_coverage_drill: answer the agent's clarifying questions
(the drill's single-turn FAILs) and drive REAL approved executions for the
safe, reversible remediations of genuine findings.

    .venv/bin/python scripts/agents/followup_drill.py [--base URL]
        [--password-file masterpass.key] [--report scripts/agents/.followup.json]
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from full_coverage_drill import DEFAULT_BASE, Drill  # noqa: E402

GATES = ('connection-test', 'docker-prune', 'project-clear-webapp-runs',
         'settings-set', 'project-set-cluster', 'code-env-consolidate')


def approval_for(plan):
    return ('Approved — I confirm. Execute the planned %s on host %s with the '
            'exact planned target, confirm=true and confirm_token %s. Report '
            'the outcome and the auditId.'
            % (plan.get('action'), plan.get('host') or 'local',
               plan.get('confirm_token')))


def turn(drill, history, content):
    history = history + [{'role': 'user', 'content': content}]
    result = drill.chat(history)
    history = history + [{'role': 'assistant', 'content': result['text']}]
    plans = [d for k, d in result['events'] if k == 'plan']
    execs = [d for k, d in result['events'] if k == 'execution']
    items = [d for k, d in result['events'] if k == 'action_items']
    return history, result, plans, execs, items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=DEFAULT_BASE)
    ap.add_argument('--password-file', default='masterpass.key')
    ap.add_argument('--report', default='scripts/agents/.followup_drill.json')
    args = ap.parse_args()

    password = pathlib.Path(args.password_file).read_text().strip()
    drill = Drill(args.base, password, None)
    drill.unlock()
    out = {'cases': []}

    def record(name, verdict, detail):
        out['cases'].append({'case': name, 'verdict': verdict, 'detail': detail})
        print('[%s] %s — %s' % (verdict, name, str(detail)[:220]))
        pathlib.Path(args.report).write_text(json.dumps(out, indent=2, default=str))

    try:
        drill.enable_gates(sorted(GATES))

        # 1. connection-broken-unverified → answer the scoping question, then
        #    approve the connection-test re-probe (green, zero mutation).
        h, r, plans, _, _ = turn(drill, [], (
            "Finding 'connection-broken-unverified' is present. Investigate "
            'and produce a remediation plan; if you need scoping input, ask.'))
        if not plans:
            h, r, plans, _, _ = turn(drill, h, (
                'Option 2 — plan the re-probe: connection-test across the '
                'failing/unverified connections. Produce the plan now.'))
        if plans:
            h, r2, _, execs, _ = turn(drill, h, approval_for(plans[0]))
            ok = any(str(e.get('status')) == 'ok' for e in execs)
            record('connection-broken-unverified → connection-test EXECUTED',
                   'PASS' if ok else 'FAIL',
                   {'auditIds': [e.get('auditId') for e in execs],
                    'tail': r2['text'][-200:]})
        else:
            record('connection-broken-unverified', 'FAIL', r['text'][-300:])

        # 2. cap-data-mount-full → docker-prune (amber, capped, auto-eligible)
        #    executed for real on the genuine data-mount-pressure finding.
        h, r, plans, _, _ = turn(drill, [], (
            "Finding 'cap-data-mount-full': the data mount is above the "
            'critical threshold. Produce the docker-prune plan (builder cache '
            'mode, default caps) and WAIT.'))
        if plans:
            h, r2, _, execs, _ = turn(drill, h, approval_for(plans[0]))
            ok = any(str(e.get('status')) == 'ok' for e in execs)
            record('cap-data-mount-full → docker-prune EXECUTED',
                   'PASS' if ok else 'FAIL',
                   {'auditIds': [e.get('auditId') for e in execs],
                    'tail': r2['text'][-200:]})
        else:
            record('cap-data-mount-full docker-prune', 'FAIL', r['text'][-300:])

        # 3. project-size-high → project-clear-webapp-runs executed for real.
        h, r, plans, _, _ = turn(drill, [], (
            "Finding 'project-size-high-group': investigate which project the "
            'toolkit flags and produce a project-clear-webapp-runs plan for '
            'the single largest flagged project. WAIT for approval.'))
        if plans:
            h, r2, _, execs, _ = turn(drill, h, approval_for(plans[0]))
            ok = any(str(e.get('status')) == 'ok' for e in execs)
            record('project-size-high → clear-webapp-runs EXECUTED',
                   'PASS' if ok else 'FAIL',
                   {'auditIds': [e.get('auditId') for e in execs],
                    'tail': r2['text'][-200:]})
        else:
            record('project-size-high clear-webapp-runs', 'FAIL', r['text'][-300:])

        # 4. features-disabled-few → answer the follow-up; PLAN only, then
        #    explicitly reject (no config flips on a customer-shaped host).
        h, r, plans, _, _ = turn(drill, [], (
            "Finding 'features-disabled-few' is present. Which disabled "
            'feature would you re-enable first and why? Produce the '
            'settings-set plan for exactly that one and WAIT.'))
        if not plans:
            h, r, plans, _, _ = turn(drill, h,
                                     'Yes — plan it for your top recommendation.')
        verdict = 'PASS' if plans else 'FAIL'
        record('features-disabled-few → settings-set PLAN (not executed)',
               verdict, {'actions': [p.get('action') for p in plans],
                         'tail': r['text'][-200:]})
        if plans:
            turn(drill, h, 'Rejected — do not execute. Leave the setting as is.')

        # 5. WARN_CLUSTERS_NONE_SELECTED → plan project-set-cluster, reject.
        h, r, plans, _, _ = turn(drill, [], (
            "Sanity warning WARN_CLUSTERS_NONE_SELECTED_PROJECT flags projects "
            'without an explicit cluster. Plan project-set-cluster for ONE '
            'flagged project, pointing it at the instance default cluster. WAIT.'))
        if not plans:
            h, r, plans, _, _ = turn(drill, h, (
                'Pick the first flagged project and the default cluster id you '
                'see in settings; produce the plan.'))
        verdict = 'PASS' if plans else 'FAIL'
        record('clusters-none-selected → project-set-cluster PLAN (not executed)',
               verdict, {'actions': [p.get('action') for p in plans],
                         'tail': r['text'][-200:]})
        if plans:
            turn(drill, h, 'Rejected — do not execute.')

        # 6. project-codenv-info → is restraint the honest answer?
        h, r, plans, _, items = turn(drill, [], (
            "Finding 'project-codenv-info-group' (projects with 2 code envs, "
            'info severity). Is action actually warranted here? If yes, '
            'propose the checklist; if not, say why not in one paragraph.'))
        record('project-codenv-info judgment', 'PASS',
               {'plans': len(plans), 'itemBatches': len(items),
                'tail': r['text'][-300:]})
    finally:
        drill.restore_gates()
    print('\nfollow-up complete: %d cases' % len(out['cases']))


if __name__ == '__main__':
    main()
