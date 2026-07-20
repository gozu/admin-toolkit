"""Auto-remediation tiers of the daily triage loop.

An admin grants SPECIFIC capabilities autonomy via the per-action Autonomous
flags in Agents → Permissions (`agent_autonomous_gates`; default: nothing
granted = tiers off), and can pause everything with one switch
(`auto_remediate_enabled`) without losing the per-action grants. Two tiers
share this module's executor and one budget pool:

  • deterministic (agent='triage-auto', run first = budget priority): the
    proven finding→build_target mappings from remediation_map, no LLM in the
    decision;
  • LLM-planned (agent='triage-llm', triage/auto_agent.py): a planning pass
    that reviews the flagged findings after the sweep and may propose ANY
    autonomous-granted action through execute_candidate below.

Every run goes through the SAME plan → execute path as a human-approved
action: kill-switch, per-action gate (independently re-checked at plan AND
execute — autonomy is an additional layer, never a bypass), HMAC token,
policy enforcement in the macro, audit row. Nothing is silent — candidates
that cannot run land in `skipped` with a tier-tagged reason, surfaced in the
digest.

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
        import re as _re
        pretty = _re.sub(r'(?<=\d)(GB|MB|kB|B)$', r' \1', str(result['totalReclaimed']))
        return 'docker reported %s reclaimed from the builder/image cache' % pretty
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


def execute_candidate(client, settings, summary, cand, run_id,
                      tier='deterministic', agent_name='triage-auto', llm_id=None):
    """Run ONE autonomous candidate through the shared plan → execute path,
    appending a tier-tagged entry to summary['executed'] or ['skipped'] and
    updating the shared GB/object budget accounting. Both tiers funnel here.

    `cand` = {'host', 'action', 'issueId', 'target' (dict | [dicts] | None),
    'reasoning'?}. plan/execute still independently re-check the enablement
    gate + HMAC token — autonomy is an additional layer, never a bypass.
    Returns the appended entry."""
    host = cand.get('host')
    action = cand['action']
    entry = {'host': host, 'action': action, 'findingId': cand.get('issueId'),
             'tier': tier}
    if cand.get('reasoning'):
        entry['reasoning'] = str(cand['reasoning'])[:400]

    def skip(reason):
        skipped = dict(entry, reason=reason)
        summary['skipped'].append(skipped)
        return skipped

    remote = host not in _LOCAL_HOSTS
    if remote and not bool(settings.get('auto_remediate_remote_hosts')):
        return skip('remote-host remediation is OFF (Permissions → Autonomous agent)')
    if remote and action in set(actuator._LOCAL_ONLY_ACTIONS):
        return skip('%s is LOCAL-ONLY — cannot run on a remote host' % action)
    if not settings.get('enable_red_actions'):
        return skip('enable_red_actions master kill-switch is OFF (would have run)')
    if not settings.get('master_password'):
        return skip('no master password configured — cannot mint a confirm token')

    max_gb = float(settings.get('auto_remediate_max_gb') or 20)
    max_objects = int(settings.get('auto_remediate_max_objects') or 25)
    target = cand.get('target')
    batch = isinstance(target, list)
    if target is None:
        target = {}
    try:
        plan = actuator.plan_admin_action(
            client, host=host, action=action,
            target=None if batch else target,
            targets=target if batch else None)
    except ToolkitError as exc:
        return skip('plan failed: %s' % exc.message)
    if plan.get('error'):
        return skip('plan refused: %s' % plan['error'].get('message'))
    estimated = _estimated_gb(action, plan.get('plan') or {})
    if summary['totalFreedGB'] + estimated > max_gb:
        return skip('cumulative cap: ~%.1f GB estimated would exceed the %s GB '
                    'auto_remediate_max_gb cap' % (estimated, max_gb))
    if summary['totalObjects'] >= max_objects:
        return skip('cumulative auto_remediate_max_objects cap (%d) reached' % max_objects)
    try:
        result = actuator.execute_admin_action(
            client, host=host, action=action,
            target=plan['canonicalTarget'], confirm_flag=True,
            confirm_token=plan['confirm_token'], agent_name=agent_name,
            llm_id=llm_id, provenance={'runId': run_id, 'findingId': cand.get('issueId'),
                                       'tier': tier})
    except ToolkitError as exc:
        return skip('execute failed: %s' % exc.message)
    # Batch runs report 'partial' when some targets fail — for a probe
    # like connection-test a still-failing connection is a RESULT, not
    # a tier failure, so partial batches count as executed.
    status_ok = result.get('status') in ('ok', 'partial') and not result.get('error')
    if not status_ok:
        reason = (result.get('error') or {}).get('message') \
            or str((result.get('result') or {}).get('error') or result.get('status'))
        return skip('execute refused/failed: %s' % reason)
    freed = _freed_gb(action, result.get('result'))
    objects = _objects(action, result.get('result'))
    summary['totalFreedGB'] = round(summary['totalFreedGB'] + freed, 3)
    summary['totalObjects'] += objects
    done = dict(entry, freedGB=round(freed, 3), auditId=result.get('auditId'))
    detail = _detail(action, result.get('result'))
    if detail:
        done['detail'] = detail
    effect = _effect(action, result.get('result'))
    if effect:
        done['effect'] = effect
    if result.get('auditId') is None:
        # An autonomous action MUST leave an audit row — this is loud.
        done['warning'] = ('AUDIT ROW MISSING for an autonomous action — the fix ran '
                           'but was not recorded (triage connection down?). '
                           'Investigate before the next sweep.')
    summary['executed'].append(done)
    return done


def run_auto_remediation(client, settings, rows, run_id, autonomous_actions=None):
    """Execute the deterministic tier's auto-fixes for the sweep's scored
    hosts. Returns the digest-ready summary the LLM tier then appends to:

    {'enabled': [...], 'paused': bool, 'remoteHosts': bool,
     'executed': [{host, action, findingId, tier, freedGB, auditId, detail?,
                   reasoning?, warning?}],
     'skipped': [{host, action?, findingId?, tier?, reason}],
     'totalFreedGB', 'totalObjects'}

    `autonomous_actions` = the LIVE per-action Autonomous grants (set of
    action names); default derives from the settings snapshot
    (agent_autonomous_gates, CSV-seeded by config.resolve) so offline runs
    and tests stay client-free.
    """
    if autonomous_actions is None:
        auto_map = settings.get('agent_autonomous_gates') or {}
        autonomous_actions = {a for a, v in auto_map.items() if v}
    enabled = (set(autonomous_actions) & set(actuator.ACTIONS)) \
        - remediation_map.AUTO_EXCLUDED
    paused = not settings.get('auto_remediate_enabled', True)
    allow_remote = bool(settings.get('auto_remediate_remote_hosts'))
    summary = {'enabled': sorted(enabled), 'paused': paused,
               'remoteHosts': allow_remote, 'executed': [], 'skipped': [],
               'totalFreedGB': 0.0, 'totalObjects': 0}
    if not enabled or paused:
        return summary

    for row in rows:
        host = row.get('host')
        issues = row.get('topIssues') or []
        candidates = remediation_map.auto_candidates(issues, enabled, settings)
        if not candidates:
            continue
        if host not in _LOCAL_HOSTS and not allow_remote:
            # One aggregated skip per host keeps the digest readable;
            # execute_candidate re-checks per candidate for the LLM tier.
            summary['skipped'].append({
                'host': host, 'tier': 'deterministic',
                'reason': 'remote-host remediation is OFF (Permissions → '
                'Autonomous agent) — %d candidate fix(es) not run on this host'
                % len(candidates)})
            continue
        for cand in candidates:
            execute_candidate(client, settings, summary, dict(cand, host=host), run_id)
    return summary
