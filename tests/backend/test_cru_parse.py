"""Unit tests for the cru-audit macro parser (all CRU resource kinds)."""

import importlib.util
import json
import os
import sys
import types

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def _load_runnable():
    if 'dataiku.runnables' not in sys.modules:
        runnables = types.ModuleType('dataiku.runnables')

        class Runnable:
            pass

        runnables.Runnable = Runnable  # type: ignore[attr-defined]
        sys.modules.setdefault('dataiku', types.ModuleType('dataiku'))
        sys.modules['dataiku'].runnables = runnables  # type: ignore[attr-defined]
        sys.modules['dataiku.runnables'] = runnables
    path = os.path.join(_ROOT, 'python-runnables', 'cru-audit', 'runnable.py')
    spec = importlib.util.spec_from_file_location('cru_runnable', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


runnable = _load_runnable()


def _line(ts, msg_type, cru):
    return json.dumps({
        'severity': 'INFO',
        'logger': 'dku.audit.compute-resource-usage',
        'topic': 'compute-resource-usage',
        'message': {'auditTopic': 'compute-resource-usage',
                    'msgType': msg_type,
                    'computeResourceUsage': cru},
        'timestamp': ts,
    })


def _census_line(ts, cluster, pods):
    return json.dumps({
        'severity': 'INFO',
        'logger': 'dku.audit.compute-resource-usage',
        'topic': 'compute-resource-usage',
        'message': {'auditTopic': 'compute-resource-usage',
                    'msgType': 'kubernetes-cluster-usage-status',
                    'clusterId': cluster,
                    'podsStatus': {'pods': pods}},
        'timestamp': ts,
    })


@pytest.fixture()
def audit_dir(tmp_path):
    lines = []

    # -- LOCAL_PROCESS: cumulative metrics, max-per-id; two updates + a second id
    ctx = {'projectKey': 'PROJ_A', 'authIdentifier': 'alice', 'type': 'WEBAPP_BACKEND'}
    lines.append(_line('2026-06-01T10:00:00.000+0000', 'compute-resource-usage-update', {
        'id': 'lp1', 'type': 'LOCAL_PROCESS', 'context': ctx,
        'localProcess': {'vmRSSTotalMBS': 1024 * 3600, 'cpuTotalMS': 0}}))
    lines.append(_line('2026-06-02T10:00:00.000+0000', 'compute-resource-usage-update', {
        'id': 'lp1', 'type': 'LOCAL_PROCESS', 'context': ctx,
        'localProcess': {'vmRSSTotalMBS': 3 * 1024 * 3600, 'cpuTotalMS': 7200000}}))
    lines.append(_line('2026-06-01T11:00:00.000+0000', 'compute-resource-usage-update', {
        'id': 'lp2', 'type': 'LOCAL_PROCESS',
        'context': {'projectKey': 'PROJ_B', 'authIdentifier': 'bob', 'type': 'JOB_ACTIVITY'},
        'localProcess': {'vmRSSTotalMBS': 2 * 1024 * 3600, 'cpuTotalMS': 3600000}}))

    # -- SQL: query with NO context, joined via SQL_CONNECTION anchor
    lines.append(_line('2026-06-01T10:00:00.000+0000', 'compute-resource-usage-start', {
        'id': 'conn1', 'type': 'SQL_CONNECTION',
        'context': {'projectKey': 'PROJ_A', 'authIdentifier': 'alice'},
        'sqlConnection': {'connection': 'wh1'}}))
    lines.append(_line('2026-06-01T10:00:01.000+0000', 'compute-resource-usage-start', {
        'id': 'q1', 'type': 'SQL_QUERY',
        'sqlQuery': {'connectionUsageId': 'conn1', 'connection': 'wh1', 'query': 'SELECT 1'}}))
    lines.append(_line('2026-06-01T10:00:05.000+0000', 'compute-resource-usage-complete', {
        'id': 'q1', 'type': 'SQL_QUERY', 'totalTime': 4000,
        'sqlQuery': {'connectionUsageId': 'conn1', 'connection': 'wh1',
                     'statementExecutionTime': 1000, 'fetchedRowCount': 42}}))
    # -- SQL: query WITH its own context (newer DSS), no anchor needed
    lines.append(_line('2026-06-01T10:01:00.000+0000', 'compute-resource-usage-complete', {
        'id': 'q2', 'type': 'SQL_QUERY', 'totalTime': 2000,
        'context': {'projectKey': 'PROJ_B', 'authIdentifier': 'bob', 'type': 'JOB_ACTIVITY'},
        'sqlQuery': {'connectionUsageId': 'connX', 'connection': 'wh1',
                     'statementExecutionTime': 500, 'fetchedRowCount': 7}}))
    # -- SQL: unattributable query (anchor never seen)
    lines.append(_line('2026-06-01T10:02:00.000+0000', 'compute-resource-usage-complete', {
        'id': 'q3', 'type': 'SQL_QUERY', 'totalTime': 1000,
        'sqlQuery': {'connectionUsageId': 'gone', 'connection': 'wh2',
                     'statementExecutionTime': 800, 'fetchedRowCount': 5}}))

    # -- K8s job: 1024MB request, 30-minute lifetime => 0.5 reserved GB·h
    start_ms = 1780308000000  # 2026-06-01T18:00:00Z
    lines.append(_line('2026-06-01T18:00:00.000+0000', 'compute-resource-usage-start', {
        'id': 'k1', 'type': 'SINGLE_K8S_JOB',
        'context': {'projectKey': 'PROJ_A', 'authIdentifier': 'alice', 'type': 'JOB_ACTIVITY'},
        'singleK8SJob': {'executionId': 'exec-1', 'containerConfigName': 'eks',
                         'memoryRequestMB': 1024, 'cpuRequest': -1,
                         'k8sClusterId': 'clus1'},
        'startTime': start_ms}))
    lines.append(_line('2026-06-01T18:30:00.000+0000', 'compute-resource-usage-complete', {
        'id': 'k1', 'type': 'SINGLE_K8S_JOB',
        'context': {'projectKey': 'PROJ_A', 'authIdentifier': 'alice', 'type': 'JOB_ACTIVITY'},
        'singleK8SJob': {'executionId': 'exec-1', 'containerConfigName': 'eks',
                         'memoryRequestMB': 1024, 'cpuRequest': -1,
                         'k8sClusterId': 'clus1'},
        'startTime': start_ms, 'endTime': start_ms + 30 * 60 * 1000}))

    # -- census: same pod at t and t+60s → integrate 1024MB over 60s
    pod = {'phase': 'Running', 'memoryMB': 1024, 'cpuCurrentMillis': 500,
           'namespace': 'ns', 'name': 'pod-1',
           'annotations': {'dataiku.com/dku-project-key': 'PROJ_A',
                           'dataiku.com/dku-execution-type': 'JUPYTER_NOTEBOOK_KERNEL',
                           'dataiku.com/dku-exec-submitter': 'alice'},
           'labels': {'dataiku.com/dku-node-id': 'node-1',
                      'dataiku.com/dku-execution-id': 'exec-9'}}
    lines.append(_census_line('2026-06-01T12:00:00.000+0000', 'clus1', [pod]))
    lines.append(_census_line('2026-06-01T12:01:00.000+0000', 'clus1', [pod]))
    # a >15min gap must NOT integrate
    lines.append(_census_line('2026-06-01T13:00:00.000+0000', 'clus1', [pod]))

    # -- LLM: two updates of the same id, cumulative USD
    llm_ctx = {'projectKey': 'PROJ_A', 'authIdentifier': 'alice',
               'type': 'WEBAPP_BACKEND', 'webappId': 'w1'}
    lines.append(_line('2026-06-01T14:00:00.000+0000', 'compute-resource-usage-update', {
        'id': 'llm1', 'type': 'LLM_USAGE', 'context': llm_ctx,
        'llmUsage': {'estimatedCostUSD': 0.10, 'totalPromptTokens': 100,
                     'totalCompletionTokens': 10, 'totalQueries': 1,
                     'cacheHitQueries': 0, 'cacheMissQueries': 1,
                     'totalComputationTimeMS': 1000, 'llmModel': 'gpt-x',
                     'llmId': 'openai:c:gpt-x', 'llmType': 'OPENAI',
                     'connection': 'c'}}))
    lines.append(_line('2026-06-02T14:00:00.000+0000', 'compute-resource-usage-update', {
        'id': 'llm1', 'type': 'LLM_USAGE', 'context': llm_ctx,
        'llmUsage': {'estimatedCostUSD': 0.25, 'totalPromptTokens': 250,
                     'totalCompletionTokens': 25, 'totalQueries': 2,
                     'cacheHitQueries': 1, 'cacheMissQueries': 1,
                     'totalComputationTimeMS': 2500, 'llmModel': 'gpt-x',
                     'llmId': 'openai:c:gpt-x', 'llmType': 'OPENAI',
                     'connection': 'c'}}))

    # noise: non-CRU line + malformed line
    lines.append(json.dumps({'logger': 'dku.audit.other', 'message': {}}))
    lines.append('{not json')

    (tmp_path / 'audit.log').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return str(tmp_path)


@pytest.fixture()
def result(audit_dir):
    return runnable._parse_audit(audit_dir)


def test_local_process_max_per_id(result):
    proj = {p['projectKey']: p for p in result['projects']}
    # lp1: max 3 GB·h mem, 2 CPU·h
    assert proj['PROJ_A']['memGBh'] == pytest.approx(3.0)
    assert proj['PROJ_A']['cpuH'] == pytest.approx(2.0)
    assert proj['PROJ_B']['memGBh'] == pytest.approx(2.0)
    assert proj['PROJ_B']['cpuH'] == pytest.approx(1.0)


def test_sql_join_and_context_paths(result):
    proj = {p['projectKey']: p for p in result['projects']}
    # q1 joined via conn1 anchor → PROJ_A
    assert proj['PROJ_A']['sqlExecS'] == pytest.approx(1.0)
    assert proj['PROJ_A']['sqlRows'] == 42
    # q2 attributed via its own context → PROJ_B
    assert proj['PROJ_B']['sqlExecS'] == pytest.approx(0.5)
    # q3 unattributed
    una = result['classTotals']['sql']['unattributed']
    assert una['queries'] == 1
    assert una['execS'] == pytest.approx(0.8)
    conns = {c['connection']: c for c in result['connections']}
    assert conns['wh1']['queries'] == 2
    assert conns['wh1']['execS'] == pytest.approx(1.5)
    assert conns['wh1']['totalS'] == pytest.approx(6.0)
    assert 0 < conns['wh1']['fetchOverheadPct'] < 100


def test_k8s_reserved_and_census_actuals(result):
    proj = {p['projectKey']: p for p in result['projects']}
    # 1 GB request × 0.5h lifetime
    assert proj['PROJ_A']['k8sReservedGBh'] == pytest.approx(0.5)
    assert proj['PROJ_A']['k8sJobs'] == 1
    # census: only the 60s gap integrates (the 59-minute one is dropped):
    # 1024MB × 60s = 1/60 GB·h
    assert proj['PROJ_A']['k8sActualGBh'] == pytest.approx(1.0 / 60.0, rel=1e-6)
    nodes = {n['nodeId']: n for n in result['k8s']['nodes']}
    assert nodes['node-1']['pods'] == 1
    assert nodes['node-1']['actualGBh'] == pytest.approx(1.0 / 60.0, rel=1e-6)
    # cpu: 500 millicores over 60s = 500*60 milli·s → /1000/3600 core·h
    assert nodes['node-1']['cpuCoreH'] == pytest.approx(500 * 60 / 1000 / 3600, rel=1e-6)
    et = {t['type']: t for t in result['k8s']['execTypes']}
    assert 'JUPYTER_NOTEBOOK_KERNEL' in et


def test_llm_max_per_id(result):
    proj = {p['projectKey']: p for p in result['projects']}
    assert proj['PROJ_A']['llmUSD'] == pytest.approx(0.25)
    assert proj['PROJ_A']['llmTokens'] == 275
    models = {m['llmId']: m for m in result['llmModels']}
    m = models['openai:c:gpt-x']
    assert m['usd'] == pytest.approx(0.25)
    assert m['cacheHit'] == 1


def test_daily_deltas(result):
    daily = {d['date']: d for d in result['daily']}
    # lp1 day1: 1 GB·h; day2 delta: 2 GB·h. lp2 day1: 2 GB·h.
    assert daily['2026-06-01']['memGBh'] == pytest.approx(3.0)
    assert daily['2026-06-02']['memGBh'] == pytest.approx(2.0)
    # llm: 0.10 day1, 0.15 delta day2
    assert daily['2026-06-01']['llmUSD'] == pytest.approx(0.10)
    assert daily['2026-06-02']['llmUSD'] == pytest.approx(0.15)
    assert daily['2026-06-01']['sqlQueries'] == 3


def test_drilldowns_present(result):
    proj = {p['projectKey']: p for p in result['projects']}
    a = proj['PROJ_A']
    users = {r['authIdentifier'] for r in a['byUser']}
    assert 'alice' in users
    assert any(r['connection'] == 'wh1' for r in a['byConnection'])
    assert any(r['model'] == 'gpt-x' for r in a['byModel'])
    ctx_types = {r['type'] for r in a['byContextType']}
    assert 'WEBAPP_BACKEND' in ctx_types


def test_top_processes_enriched(result):
    procs = {p['id']: p for p in result['topProcesses']}
    lp1 = procs['lp1']
    assert lp1['authIdentifier'] == 'alice'
    # seen on both days of the fixture
    assert lp1['firstDay'] == '2026-06-01'
    assert lp1['lastDay'] == '2026-06-02'
    assert lp1['memGBh'] == pytest.approx(3.0)


def test_span_and_class_totals(result):
    assert result['ok'] is True
    ct = result['classTotals']
    assert ct['local']['records'] == 2
    assert ct['sql']['queries'] == 3
    assert ct['k8s']['jobs'] == 1
    assert ct['llm']['records'] == 1
    assert result['span']['cruRecords'] == 2 + 3 + 1 + 1
