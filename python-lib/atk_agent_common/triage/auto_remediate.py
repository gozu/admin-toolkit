"""Auto-remediation tier of the daily triage loop.

An admin opts SPECIFIC actions into autonomous execution via the
`auto_remediate_actions` plugin CSV (default empty = tier off), and can pause
the whole tier with one switch (`auto_remediate_enabled`) without losing the
per-action selection. Only actions the remediation map marks `auto: True`
(reversible, capped, whitelist-safe) can ever run here, and every run goes
through the SAME plan → execute path as a human-approved action: kill-switch,
per-action gate, HMAC token, policy enforcement in the macro, audit row
(agent='triage-auto'). Nothing is silent — candidates that cannot run land in
`skipped` with a reason, surfaced in the digest.

Remote hosts are covered when `auto_remediate_remote_hosts` is on (default
off): non-LOCAL-ONLY actions then run against flagged remote hosts through
the same policy stack; LOCAL-ONLY actions (log-cleanup, docker-prune, …) are
skipped there with an explicit reason.
"""

from .. import actuator, remediation_map
from ..errors import ToolkitError

_LOCAL_HOSTS = (None, '', 'local')

# result-key → GB extractors per action (batch rows handled separately)
_GB_KEYS = {
    'log-cleanup': ('totalReclaimedGB', 1.0),
    'job-logs-cleanup': ('totalReclaimedGB', 1.0),
    'docker-prune': ('totalReclaimedBytes', 1.0 / (1000 ** 3)),
}


def _estimated_gb(action, plan):
    if action in ('log-cleanup', 'job-logs-cleanup'):
        return float(plan.get('totalReclaimableGB') or 0)
    if action == 'docker-prune':
        return float(plan.get('estimatedReclaimableGB') or 0)
    return 0.0


def _single_freed_gb(action, result):
    if not isinstance(result, dict):
        return 0.0
    key, factor = _GB_KEYS.get(action, (None, 0))
    if not key:
        return 0.0
    return float(result.get(key) or 0) * factor


def _freed_gb(action, result):
    if not isinstance(result, dict):
        return 0.0
    if result.get('batch'):
        return sum(_single_freed_gb(action, (row.get('result') or {}))
                   for row in result.get('perTarget') or [] if row.get('status') == 'ok')
    return _single_freed_gb(action, result)


def _objects(action, result):
    if not isinstance(result, dict):
        return 1
    if result.get('batch'):
        return int(result.get('okCount') or 0) or 1
    if action == 'log-cleanup' or action == 'job-logs-cleanup':
        return int(result.get('totalDeletedFiles') or 0) or 1
    if action == 'notebook-kernels-shutdown':
        return int(result.get('shutdownCount') or result.get('count') or 0) or 1
    return 1


def _conn_test_counts(result):
    """(recovered, still_failing) from a single or batched connection-test."""
    if result.get('batch'):
        passed = failed = 0
        for row in result.get('perTarget') or []:
            inner = row.get('result') or {}
            if row.get('status') == 'ok' and inner.get('connectionOK') is True:
                passed += 1
            else:
                failed += 1
        return passed, failed
    return (1, 0) if result.get('connectionOK') is True else (0, 1)


def _detail(action, result):
    """One short human line about what actually happened — feeds the digest."""
    if not isinstance(result, dict):
        return None
    if action == 'connection-test':
        passed, failed = _conn_test_counts(result)
        return '%d connection(s) recovered, %d still failing' % (passed, failed)
    if result.get('batch'):
        ok, err = result.get('okCount') or 0, result.get('errorCount') or 0
        return '%d of %d target(s) succeeded' % (ok, ok + err)
    if action == 'notebook-kernels-shutdown':
        n = result.get('shutdownCount') or result.get('count')
        return '%s kernel(s) shut down' % n if n is not None else None
    if action == 'docker-prune' and result.get('totalReclaimed'):
        return 'docker reported %s reclaimed from the builder/image cache' \
            % result['totalReclaimed']
    if action in ('log-cleanup', 'job-logs-cleanup') and result.get('totalDeletedFiles'):
        noun = 'aged job directories' if action == 'job-logs-cleanup' else 'rotated log files'
        return '%s %s removed' % (result['totalDeletedFiles'], noun)
    return None


def _effect(action, result):
    """'fixed' | 'no-effect' | None — lets the digest style a zero-effect
    probe (connection still broken) differently from a real fix."""
    if not isinstance(result, dict) or action != 'connection-test':
        return None
    passed, _failed = _conn_test_counts(result)
    return 'fixed' if passed else 'no-effect'


def run_auto_remediation(client, settings, rows, run_id):
    """Execute admin-opted auto-fixes for the sweep's scored hosts. Returns a
    digest-ready summary:

    {'enabled': [...], 'paused': bool, 'remoteHosts': bool,
     'executed': [{host, action, findingId, freedGB, auditId, detail?,
                   warning?}],
     'skipped': [{host, action?, findingId?, reason}],
     'totalFreedGB', 'totalObjects'}
    """
    enabled = set(settings.get('auto_remediate_actions') or [])
    paused = not settings.get('auto_remediate_enabled', True)
    allow_remote = bool(settings.get('auto_remediate_remote_hosts'))
    summary = {'enabled': sorted(enabled), 'paused': paused,
               'remoteHosts': allow_remote, 'executed': [], 'skipped': [],
               'totalFreedGB': 0.0, 'totalObjects': 0}
    if not enabled or paused:
        return summary

    max_gb = float(settings.get('auto_remediate_max_gb') or 20)
    max_objects = int(settings.get('auto_remediate_max_objects') or 25)
    local_only_actions = set(actuator._LOCAL_ONLY_ACTIONS)

    for row in rows:
        host = row.get('host')
        issues = row.get('topIssues') or []
        candidates = remediation_map.auto_candidates(issues, enabled, settings)
        if not candidates:
            continue
        remote = host not in _LOCAL_HOSTS
        if remote and not allow_remote:
            summary['skipped'].append({
                'host': host, 'reason': 'remote-host remediation is OFF (Permissions → '
                'Autonomous agent) — %d candidate fix(es) not run on this host'
                % len(candidates)})
            continue
        for cand in candidates:
            entry = {'host': host, 'action': cand['action'], 'findingId': cand['issueId']}
            if remote and cand['action'] in local_only_actions:
                summary['skipped'].append(dict(entry, reason='%s is LOCAL-ONLY — cannot '
                                               'run on a remote host' % cand['action']))
                continue
            if not settings.get('enable_red_actions'):
                summary['skipped'].append(dict(entry, reason='enable_red_actions master '
                                               'kill-switch is OFF (would have run)'))
                continue
            if not settings.get('master_password'):
                summary['skipped'].append(dict(entry, reason='no master password '
                                               'configured — cannot mint a confirm token'))
                continue
            target = cand['target']
            batch = isinstance(target, list)
            try:
                plan = actuator.plan_admin_action(
                    client, host=host, action=cand['action'],
                    target=None if batch else target,
                    targets=target if batch else None)
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
            # Batch runs report 'partial' when some targets fail — for a probe
            # like connection-test a still-failing connection is a RESULT, not
            # a tier failure, so partial batches count as executed.
            status_ok = result.get('status') in ('ok', 'partial') and not result.get('error')
            if not status_ok:
                reason = (result.get('error') or {}).get('message') \
                    or str((result.get('result') or {}).get('error') or result.get('status'))
                summary['skipped'].append(dict(entry, reason='execute refused/failed: %s' % reason))
                continue
            freed = _freed_gb(cand['action'], result.get('result'))
            objects = _objects(cand['action'], result.get('result'))
            summary['totalFreedGB'] = round(summary['totalFreedGB'] + freed, 3)
            summary['totalObjects'] += objects
            done = dict(entry, freedGB=round(freed, 3), auditId=result.get('auditId'))
            detail = _detail(cand['action'], result.get('result'))
            if detail:
                done['detail'] = detail
            effect = _effect(cand['action'], result.get('result'))
            if effect:
                done['effect'] = effect
            if result.get('auditId') is None:
                # An autonomous action MUST leave an audit row — this is loud.
                done['warning'] = ('AUDIT ROW MISSING for an autonomous action — the fix ran '
                                   'but was not recorded (triage connection down?). '
                                   'Investigate before the next sweep.')
            summary['executed'].append(done)
    return summary
