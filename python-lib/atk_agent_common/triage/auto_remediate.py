"""Auto-remediation tier of the daily triage loop.

An admin opts SPECIFIC actions into autonomous execution via the
`auto_remediate_actions` plugin CSV (default empty = tier off). Only actions
the remediation map marks `auto: True` (log-cleanup, docker-prune — reversible,
capped, whitelist-safe) can ever run here, and every run goes through the SAME
plan → execute path as a human-approved action: kill-switch, HMAC token, policy
enforcement in the macro, audit row (agent='triage-auto'). Nothing is silent —
candidates that cannot run land in `skipped` with a reason, surfaced in the
digest.
"""

from .. import actuator, remediation_map
from ..errors import ToolkitError

_LOCAL_HOSTS = (None, '', 'local')


def _estimated_gb(action, plan):
    if action == 'log-cleanup':
        return float(plan.get('totalReclaimableGB') or 0)
    if action == 'docker-prune':
        return float(plan.get('estimatedReclaimableGB') or 0)
    return 0.0


def _freed_gb(action, result):
    if not isinstance(result, dict):
        return 0.0
    if action == 'log-cleanup':
        return float(result.get('totalReclaimedGB') or 0)
    if action == 'docker-prune':
        return float(result.get('totalReclaimedBytes') or 0) / (1000 ** 3)
    return 0.0


def _objects(action, result):
    if isinstance(result, dict) and action == 'log-cleanup':
        return int(result.get('totalDeletedFiles') or 0)
    return 1


def run_auto_remediation(client, settings, rows, run_id):
    """Execute admin-opted auto-fixes for the sweep's scored hosts (v1: local
    host only). Returns a digest-ready summary:

    {'enabled': [...], 'executed': [{host, action, findingId, freedGB,
    auditId, warning?}], 'skipped': [{host, action?, findingId?, reason}],
    'totalFreedGB', 'totalObjects'}
    """
    enabled = set(settings.get('auto_remediate_actions') or [])
    summary = {'enabled': sorted(enabled), 'executed': [], 'skipped': [],
               'totalFreedGB': 0.0, 'totalObjects': 0}
    if not enabled:
        return summary

    max_gb = float(settings.get('auto_remediate_max_gb') or 20)
    max_objects = int(settings.get('auto_remediate_max_objects') or 25)

    for row in rows:
        host = row.get('host')
        issues = row.get('topIssues') or []
        candidates = remediation_map.auto_candidates(issues, enabled, settings)
        if not candidates:
            continue
        if host not in _LOCAL_HOSTS:
            summary['skipped'].append({
                'host': host, 'reason': 'auto-remediation is LOCAL-ONLY in v1 — %d candidate '
                'fix(es) not run on this remote host' % len(candidates)})
            continue
        for cand in candidates:
            entry = {'host': host, 'action': cand['action'], 'findingId': cand['issueId']}
            if not settings.get('enable_red_actions'):
                summary['skipped'].append(dict(entry, reason='enable_red_actions master '
                                               'kill-switch is OFF (would have run)'))
                continue
            if not settings.get('master_password'):
                summary['skipped'].append(dict(entry, reason='no master password '
                                               'configured — cannot mint a confirm token'))
                continue
            try:
                plan = actuator.plan_admin_action(client, host=host, action=cand['action'],
                                                  target=cand['target'])
            except ToolkitError as exc:
                summary['skipped'].append(dict(entry, reason='plan failed: %s' % exc.message))
                continue
            if plan.get('error'):
                summary['skipped'].append(dict(entry, reason='plan refused: %s'
                                               % plan['error'].get('message')))
                continue
            estimated = _estimated_gb(cand['action'], plan.get('plan') or {})
            if summary['totalFreedGB'] + estimated > max_gb:
                summary['skipped'].append(dict(entry, reason='cumulative cap: ~%.1f GB estimated '
                                               'would exceed the %s GB auto_remediate_max_gb cap'
                                               % (estimated, max_gb)))
                continue
            if summary['totalObjects'] >= max_objects:
                summary['skipped'].append(dict(entry, reason='cumulative auto_remediate_max_objects '
                                               'cap (%d) reached' % max_objects))
                continue
            try:
                result = actuator.execute_admin_action(
                    client, host=host, action=cand['action'],
                    target=plan['canonicalTarget'], confirm_flag=True,
                    confirm_token=plan['confirm_token'], agent_name='triage-auto',
                    llm_id=None, provenance={'runId': run_id, 'findingId': cand['issueId']})
            except ToolkitError as exc:
                summary['skipped'].append(dict(entry, reason='execute failed: %s' % exc.message))
                continue
            if result.get('error') or result.get('status') != 'ok':
                reason = (result.get('error') or {}).get('message') \
                    or str((result.get('result') or {}).get('error') or result.get('status'))
                summary['skipped'].append(dict(entry, reason='execute refused/failed: %s' % reason))
                continue
            freed = _freed_gb(cand['action'], result.get('result'))
            objects = _objects(cand['action'], result.get('result'))
            summary['totalFreedGB'] = round(summary['totalFreedGB'] + freed, 3)
            summary['totalObjects'] += objects
            done = dict(entry, freedGB=round(freed, 3), auditId=result.get('auditId'))
            if result.get('auditId') is None:
                # An autonomous action MUST leave an audit row — this is loud.
                done['warning'] = ('AUDIT ROW MISSING for an autonomous action — the fix ran '
                                   'but was not recorded (triage connection down?). '
                                   'Investigate before the next sweep.')
            summary['executed'].append(done)
    return summary
