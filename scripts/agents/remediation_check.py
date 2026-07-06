#!/usr/bin/env python3
"""Remediation-suite gate check: run the five new actuator actions through the
REAL plan/execute tool runtime and verify the safety doctrine end to end.

    .venv/bin/python scripts/agents/remediation_check.py [--project AGENTSSANDBOX]
        [--cluster-id <id>] [--red-on] [--allow-delete]

Default mode (red OFF expected):
  * every plannable action produces a plan with its expected evidence keys
    AND a confirm token,
  * policy engines refuse below the model (kubectl forbidden command,
    settings-set blacklisted path),
  * execute with a VALID token is still refused by the kill-switch.
--red-on mode additionally runs the safe subset live: a settings-set no-op
round-trip (newValue == current value, still exercises token + history), and
with --allow-delete a capped log-cleanup delete.
"""

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))

from test_tools import ensure_project, ensure_tool, get_client, run_tool  # noqa: E402

# plan-evidence keys that make each approval card reviewable
EXPECTED_EVIDENCE = {
    'log-cleanup': ('perRoot', 'totalReclaimableGB', 'capGB', 'note'),
    'docker-prune': ('estimatedReclaimableGB', 'dockerRootDir', 'sameFilesystemAsDssData', 'note'),
    'k8s-apply-fix': ('preview', 'warnings'),
    'code-env-consolidate': ('matchedRows', 'usageRows', 'note'),
    'settings-set': ('path', 'currentValue', 'proposedValue', 'note'),
}

RESULTS = []


def record(name, ok, detail='', skip=False):
    RESULTS.append((name, 'SKIP' if skip else ('PASS' if ok else 'FAIL'), detail))
    print('[%s] %s%s' % ('SKIP' if skip else ('PASS' if ok else 'FAIL'), name,
                         ' — ' + detail if detail else ''))


def tool_output(result):
    return ((result or {}).get('output') or {}) if isinstance(result, dict) else {}


def check_plan(handles, action, target, env_skip_markers=()):
    """Plan one action; PASS when evidence keys + confirm token are present.
    Returns the output dict (for execute checks), or None."""
    result = run_tool(handles['plan-admin-action'], 'plan %s' % action,
                      {'action': action, 'target': target})
    out = tool_output(result)
    err = out.get('error')
    if err:
        err_text = json.dumps(err, default=str)
        for marker in env_skip_markers:
            if marker in err_text:
                record('plan %s' % action, True,
                       'environment-dependent skip (%s)' % marker, skip=True)
                return None
        record('plan %s' % action, False, err_text[:300])
        return None
    plan = out.get('plan') or {}
    missing = [k for k in EXPECTED_EVIDENCE[action] if k not in plan]
    token = out.get('confirm_token')
    record('plan %s' % action, not missing and bool(token),
           ('missing evidence keys: %s' % missing if missing else '') +
           ('' if token else ' no confirm_token'))
    return out


def expect_refusal(name, result, *markers):
    """PASS when the tool output is an error mentioning one of the markers."""
    out = tool_output(result)
    err = json.dumps(out.get('error') or out, default=str).lower()
    hit = any(m in err for m in markers)
    record(name, hit, '' if hit else 'expected refusal mentioning %s, got: %s'
           % ('/'.join(markers), err[:240]))


def pick_code_env_pair(client):
    """Two same-language python envs for a dry-run consolidation plan."""
    envs = [e for e in client.list_code_envs() if (e.get('envLang') or '').upper() == 'PYTHON']
    names = sorted(e.get('envName') for e in envs if e.get('envName'))
    return (names[0], names[1]) if len(names) >= 2 else (None, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project', default='AGENTSSANDBOX')
    ap.add_argument('--cluster-id', default='',
                    help='DSS cluster id for the k8s-apply-fix preview plan (skipped without it)')
    ap.add_argument('--red-on', action='store_true',
                    help='kill switch is ON: also run the safe execute subset')
    ap.add_argument('--allow-delete', action='store_true',
                    help='with --red-on: really execute a capped log-cleanup delete')
    args = ap.parse_args()

    client = get_client()
    project = ensure_project(client, args.project)
    handles = {}
    for component in ('plan-admin-action', 'execute-admin-action'):
        handles[component], _ = ensure_tool(project, component)

    # ── plans (read-only / dry-run everywhere) ────────────────────────────
    log_out = check_plan(handles, 'log-cleanup',
                         {'roots': ['run'], 'minAgeDays': 3, 'maxDeleteGB': 1})
    check_plan(handles, 'docker-prune', {'mode': 'builder', 'keepStorageGB': 9999},
               env_skip_markers=('docker-permission', 'docker daemon', 'usage probe failed'))
    src_env, tgt_env = pick_code_env_pair(client)
    if src_env:
        check_plan(handles, 'code-env-consolidate',
                   {'sourceEnvName': src_env, 'targetEnvName': tgt_env, 'language': 'python'})
    else:
        record('plan code-env-consolidate', True, 'fewer than 2 python envs', skip=True)
    settings_out = check_plan(handles, 'settings-set',
                              {'path': 'studioExternalUrl', 'newValue': 'https://example.invalid'})
    if args.cluster_id:
        check_plan(handles, 'k8s-apply-fix',
                   {'clusterId': args.cluster_id,
                    'commands': ['get pods -n default -o json']})
    else:
        record('plan k8s-apply-fix (preview)', True, 'no --cluster-id', skip=True)

    # ── policy refusals: enforced below the model, relayed as errors ──────
    expect_refusal(
        'kubectl policy refusal (delete ns kube-system)',
        run_tool(handles['plan-admin-action'], 'plan k8s-apply-fix FORBIDDEN',
                 {'action': 'k8s-apply-fix',
                  'target': {'clusterId': args.cluster_id or 'any',
                             'commands': ['delete namespace kube-system --force']}}),
        'policy', 'refused', 'forbidden')
    expect_refusal(
        'settings-set blacklist refusal (security path)',
        run_tool(handles['plan-admin-action'], 'plan settings-set BLACKLISTED',
                 {'action': 'settings-set',
                  'target': {'path': 'security.enabled', 'newValue': False}}),
        'refused', 'blacklist', 'never agent-mutable')

    # ── kill-switch: a valid token must still be refused while red is OFF ─
    if not args.red_on:
        if settings_out and settings_out.get('confirm_token'):
            expect_refusal(
                'kill-switch refusal (settings-set, valid token)',
                run_tool(handles['execute-admin-action'], 'execute settings-set (red OFF)',
                         {'action': 'settings-set',
                          'target': settings_out.get('canonicalTarget'),
                          'confirm': True,
                          'confirm_token': settings_out['confirm_token']}),
                'kill', 'enable_red_actions', 'disabled', 'red actions')
        else:
            record('kill-switch refusal', False, 'no settings-set token to test with')

    # ── red ON: safe execute subset ───────────────────────────────────────
    if args.red_on:
        noop = check_plan(handles, 'settings-set', {'path': 'studioExternalUrl', 'newValue': None})
        current = (noop or {}).get('plan', {}).get('currentValue')
        noop = check_plan(handles, 'settings-set',
                          {'path': 'studioExternalUrl', 'newValue': current})
        if noop and noop.get('confirm_token'):
            result = run_tool(handles['execute-admin-action'], 'execute settings-set NO-OP',
                              {'action': 'settings-set', 'target': noop.get('canonicalTarget'),
                               'confirm': True, 'confirm_token': noop['confirm_token']})
            out = tool_output(result)
            record('execute settings-set no-op', out.get('status') == 'executed',
                   'status=%s auditId=%s' % (out.get('status'), out.get('auditId')))
        if args.allow_delete and log_out and log_out.get('confirm_token'):
            result = run_tool(handles['execute-admin-action'], 'execute log-cleanup (1 GB cap)',
                              {'action': 'log-cleanup', 'target': log_out.get('canonicalTarget'),
                               'confirm': True, 'confirm_token': log_out['confirm_token']})
            out = tool_output(result)
            record('execute log-cleanup capped', out.get('status') == 'executed',
                   'status=%s auditId=%s' % (out.get('status'), out.get('auditId')))

    failed = sum(1 for _, status, _ in RESULTS if status == 'FAIL')
    skipped = sum(1 for _, status, _ in RESULTS if status == 'SKIP')
    print('\n%d/%d passed (%d skipped)' % (len(RESULTS) - failed - skipped,
                                           len(RESULTS) - skipped, skipped))
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
