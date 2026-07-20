"""LLM planning pass (triage/auto_agent): the propose_fix enforcement matrix
— the model is never trusted, every refusal is code-level — plus early-out
statuses and the shared-executor delegation contract."""

import conftest  # noqa: F401  (installs the python-lib path + DSS stubs)

import json

import pytest

from atk_agent_common import remediation_map
from atk_agent_common.triage import auto_agent, auto_remediate


_SETTINGS = {'auto_remediate_max_gb': 20, 'auto_remediate_max_objects': 25,
             'auto_remediate_remote_hosts': False,
             'enable_red_actions': True, 'master_password': 'x'}
_ROWS = [{'host': 'local', 'score': 40, 'topIssues': [{'id': 'disk-critical-/data'}]}]


def _fresh_summary(**over):
    base = {'enabled': ['log-cleanup'], 'paused': False, 'remoteHosts': False,
            'executed': [], 'skipped': [], 'totalFreedGB': 0.0, 'totalObjects': 0}
    base.update(over)
    return base


def _run_planner(monkeypatch, calls, summary, autonomous_actions,
                 flagged=('local',), execute_stub=None, settings=None):
    """Run the planner with the model loop replaced by a scripted list of
    propose_fix invocations; returns (planner_status, tool_results)."""
    results = []

    def fake_execute(client, live_settings, summary_arg, cand, run_id,
                     tier='deterministic', agent_name='triage-auto', llm_id=None):
        if execute_stub is not None:
            return execute_stub(cand, summary_arg, tier=tier,
                                agent_name=agent_name, llm_id=llm_id)
        done = {'host': cand['host'], 'action': cand['action'],
                'findingId': cand.get('issueId'), 'tier': tier,
                'freedGB': 0.5, 'auditId': 99}
        summary_arg['executed'].append(done)
        return done

    def fake_loop(llm, tools, messages, trace=None, max_iterations=None):
        propose = {t.name: t for t in tools}['propose_fix']
        for call in calls:
            results.append(json.loads(propose.func(**call)))
        yield {'chunk': {'text': 'planner done'}}

    monkeypatch.setattr(auto_agent.auto_remediate, 'execute_candidate', fake_execute)
    from atk_agent_common import agent_runtime, agent_tools, native_loop
    monkeypatch.setattr(agent_runtime, 'build_llm', lambda llm_id: object())
    monkeypatch.setattr(agent_tools, 'build_langchain_tools',
                        lambda client, names=None, autonomous_only=False: [])
    monkeypatch.setattr(native_loop, 'run_native_loop', fake_loop)
    status = auto_agent.run_llm_planner(
        client=None, settings=settings or _SETTINGS, rows=_ROWS,
        flagged=list(flagged), summary=summary,
        autonomous_actions=autonomous_actions, run_id='r1', llm_id='llm:x')
    return status, results


# ── early-out statuses ───────────────────────────────────────────────────────

def test_early_out_statuses():
    assert auto_agent.run_llm_planner(None, _SETTINGS, [], [], _fresh_summary(),
                                      {'log-cleanup'}, 'r1', 'llm:x') \
        == {'status': 'nothing-flagged'}
    assert auto_agent.run_llm_planner(None, _SETTINGS, _ROWS, ['local'],
                                      _fresh_summary(), {'log-cleanup'}, 'r1', '') \
        == {'status': 'no-llm'}
    assert auto_agent.run_llm_planner(None, _SETTINGS, _ROWS, ['local'],
                                      _fresh_summary(), set(), 'r1', 'llm:x') \
        == {'status': 'no-autonomous-actions'}
    # A forged map naming ONLY python-run is still "nothing granted".
    assert auto_agent.run_llm_planner(None, _SETTINGS, _ROWS, ['local'],
                                      _fresh_summary(), {'python-run'}, 'r1', 'llm:x') \
        == {'status': 'no-autonomous-actions'}


def test_planner_crash_is_a_status_not_an_exception(monkeypatch):
    from atk_agent_common import agent_runtime, agent_tools
    monkeypatch.setattr(agent_tools, 'build_langchain_tools',
                        lambda client, names=None, autonomous_only=False: [])
    monkeypatch.setattr(agent_runtime, 'build_llm',
                        lambda llm_id: (_ for _ in ()).throw(RuntimeError('no mesh')))
    status = auto_agent.run_llm_planner(None, _SETTINGS, _ROWS, ['local'],
                                        _fresh_summary(), {'log-cleanup'},
                                        'r1', 'llm:x')
    assert status['status'] == 'error' and 'no mesh' in status['error']


# ── propose_fix enforcement matrix ───────────────────────────────────────────

def _call(action='log-cleanup', host='local', finding='disk-critical-/data',
          **extra):
    return dict({'host': host, 'action': action, 'finding_id': finding,
                 'reasoning': 'because'}, **extra)


def test_unknown_action_refused(monkeypatch):
    summary = _fresh_summary()
    status, results = _run_planner(monkeypatch, [_call(action='rm-rf-slash')],
                                   summary, {'log-cleanup'})
    # A refused call still counts as a proposal — the digest's
    # "N proposal(s), X executed, Y refused" must add up.
    assert status == {'status': 'ran', 'proposals': 1, 'executed': 0, 'refused': 1}
    assert 'unknown action' in results[0]['error']['message']
    assert summary['skipped'][0]['tier'] == 'llm'


def test_python_run_refused_even_with_forged_grant(monkeypatch):
    # The grant set is code-filtered, but even a hand-injected member is
    # refused by the AUTO_EXCLUDED check before the grant check.
    summary = _fresh_summary()
    _status, results = _run_planner(
        monkeypatch, [_call(action='python-run')], summary,
        {'log-cleanup', 'python-run'})
    assert 'can never run autonomously' in results[0]['error']['message']
    assert 'python-run' in remediation_map.AUTO_EXCLUDED


def test_non_autonomous_action_refused(monkeypatch):
    summary = _fresh_summary()
    _status, results = _run_planner(monkeypatch, [_call(action='docker-prune')],
                                    summary, {'log-cleanup'})
    assert 'no Autonomous grant' in results[0]['error']['message']


def test_non_flagged_host_refused(monkeypatch):
    summary = _fresh_summary()
    _status, results = _run_planner(monkeypatch, [_call(host='prod-9')],
                                    summary, {'log-cleanup'})
    assert 'not flagged tonight' in results[0]['error']['message']


def test_dedupe_across_both_tiers(monkeypatch):
    # Already executed by the deterministic tier → the repeat is refused.
    summary = _fresh_summary(executed=[{'host': 'local', 'action': 'log-cleanup',
                                        'tier': 'deterministic'}])
    _status, results = _run_planner(monkeypatch, [_call()], summary, {'log-cleanup'})
    assert 'already handled' in results[0]['error']['message']
    # And within the planner's own proposals.
    summary = _fresh_summary()
    status, results = _run_planner(monkeypatch, [_call(), _call()],
                                   summary, {'log-cleanup'})
    assert results[0].get('status') == 'executed'
    assert 'already handled' in results[1]['error']['message']
    assert status == {'status': 'ran', 'proposals': 2, 'executed': 1, 'refused': 1}


def test_proposal_cap(monkeypatch):
    monkeypatch.setattr(auto_agent, 'MAX_PROPOSALS', 2)
    grants = {'log-cleanup', 'docker-prune', 'connection-test'}
    calls = [_call(action='log-cleanup'), _call(action='docker-prune'),
             _call(action='connection-test')]
    summary = _fresh_summary()
    status, results = _run_planner(monkeypatch, calls, summary, grants)
    assert 'proposal cap' in results[2]['error']['message']
    # The over-cap call is still tallied as a (refused) proposal.
    assert status['proposals'] == 3 and status['executed'] == 2 \
        and status['refused'] == 1


def test_delegation_carries_tier_agent_and_llm(monkeypatch):
    captured = {}

    def stub(cand, summary_arg, tier=None, agent_name=None, llm_id=None):
        captured.update(cand=cand, tier=tier, agent_name=agent_name, llm_id=llm_id)
        done = {'host': cand['host'], 'action': cand['action'], 'tier': tier,
                'freedGB': 1.0, 'auditId': 7, 'detail': 'ok'}
        summary_arg['executed'].append(done)
        return done

    summary = _fresh_summary()
    _status, results = _run_planner(
        monkeypatch, [_call(target={'minAgeDays': 3})], summary, {'log-cleanup'},
        execute_stub=stub)
    assert captured['tier'] == 'llm'
    assert captured['agent_name'] == auto_agent.AGENT_NAME == 'triage-llm'
    assert captured['llm_id'] == 'llm:x'
    assert captured['cand']['target'] == {'minAgeDays': 3}
    assert captured['cand']['reasoning'] == 'because'
    assert results[0]['status'] == 'executed' and results[0]['auditId'] == 7


def test_executor_skip_reported_not_counted_as_executed(monkeypatch):
    def stub(cand, summary_arg, tier=None, agent_name=None, llm_id=None):
        skipped = {'host': cand['host'], 'action': cand['action'], 'tier': tier,
                   'reason': 'cumulative auto_remediate_max_objects cap (25) reached'}
        summary_arg['skipped'].append(skipped)
        return skipped

    summary = _fresh_summary()
    status, results = _run_planner(monkeypatch, [_call()], summary, {'log-cleanup'},
                                   execute_stub=stub)
    assert results[0]['status'] == 'skipped'
    assert 'cap' in results[0]['reason']
    assert status == {'status': 'ran', 'proposals': 1, 'executed': 0, 'refused': 0}


# ── execute_candidate budget/tier behavior (shared executor) ─────────────────

def test_execute_candidate_object_budget_and_tier_tag(monkeypatch):
    monkeypatch.setattr(
        auto_remediate.actuator, 'plan_admin_action',
        lambda *a, **k: {'canonicalTarget': {}, 'confirm_token': 't', 'plan': {}})
    summary = {'executed': [], 'skipped': [], 'totalFreedGB': 0.0, 'totalObjects': 25}
    entry = auto_remediate.execute_candidate(
        None, dict(_SETTINGS), summary,
        {'host': 'local', 'action': 'log-cleanup', 'issueId': 'f1', 'target': {},
         'reasoning': 'r'},
        'r1', tier='llm', agent_name='triage-llm')
    assert 'auto_remediate_max_objects' in entry['reason']
    assert entry['tier'] == 'llm' and entry['reasoning'] == 'r'
    assert summary['skipped'] == [entry] and summary['executed'] == []


def test_execute_candidate_remote_policy(monkeypatch):
    summary = {'executed': [], 'skipped': [], 'totalFreedGB': 0.0, 'totalObjects': 0}
    entry = auto_remediate.execute_candidate(
        None, dict(_SETTINGS), summary,
        {'host': 'remote-1', 'action': 'connection-test', 'issueId': 'f1',
         'target': {'name': 'c1'}}, 'r1', tier='llm', agent_name='triage-llm')
    assert 'remote-host remediation is OFF' in entry['reason']
    # local-only action refused per-candidate when remote IS allowed
    settings = dict(_SETTINGS, auto_remediate_remote_hosts=True)
    entry = auto_remediate.execute_candidate(
        None, settings, summary,
        {'host': 'remote-1', 'action': 'log-cleanup', 'issueId': 'f1',
         'target': {}}, 'r1', tier='llm', agent_name='triage-llm')
    assert 'LOCAL-ONLY' in entry['reason']
