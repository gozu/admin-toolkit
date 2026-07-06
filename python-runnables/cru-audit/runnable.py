"""Plugin macro: parse Compute Resource Usage (CRU) from the host audit logs.

Read-only. Runs as the `dataiku` service account (impersonate=false) so it can
read <DIP_HOME>/run/audit/audit.log* regardless of webapp impersonation. Backs
the "Cost / CRU" module.

Parses ALL CRU resource kinds (rules from CRU.md — do not re-derive):
- Filter to logger == "dku.audit.compute-resource-usage".
- Metrics are cumulative & monotonic ⇒ take max(metric) per CRU `id`, over ALL
  msgTypes (update/complete/start), then sum by dimension.
- LOCAL_PROCESS.localProcess: vmRSSTotalMBS → GB·h (/1024/3600),
  cpuTotalMS → CPU·h (/1000/3600). Attribute via context.
- SQL_QUERY.sqlQuery: statementExecutionTime (DB-engine compute) vs totalTime
  (wall incl. fetch) vs fetchedRowCount. Attribute via own `context` when
  present (newer DSS) else join connectionUsageId → SQL_CONNECTION.id context
  (older DSS); else UNATTRIBUTED.
- SINGLE_K8S_JOB / SPARK_K8S_JOB: reserved cost = memoryRequestMB × lifetime
  (endTime, else last-seen). Actuals come from the pod census join.
- LLM_USAGE.llmUsage: estimatedCostUSD (real $), tokens, cache hit/miss, per
  model. Full context block → directly attributable.
- kubernetes-cluster-usage-status pod census: integrate memoryMB and
  cpuCurrentMillis over MEASURED gaps between snapshots per pod (cadence is
  not documented — never assume 60 s). Attribution via pod annotations
  (original case), falling back to labels (lowercased).
- Daily activity series: per-day DELTAS of each cumulative metric (bucketed at
  the timestamp where the increase was observed).
- Idle-resource finder: ids with high GB·h and ~0 CPU·h
  (WEBAPP_BACKEND/JUPYTER_NOTEBOOK_KERNEL) = "reaper" candidates.

Streams line-by-line; never loads a whole file. Handles a .gz suffix via gzip.
"""
import glob
import gzip
import io
import json
import os
from datetime import datetime

from dataiku.runnables import Runnable

# Idle-resource ("reaper") thresholds: resident memory worth flagging at near-zero CPU.
_IDLE_MIN_GBH = 1.0
_IDLE_MAX_CPUH = 0.05
_IDLE_CTX_TYPES = ('WEBAPP_BACKEND', 'JUPYTER_NOTEBOOK_KERNEL')
_IDLE_LIMIT = 25

# Census integration: ignore gaps longer than this between two snapshots of the
# same pod (collector outage / log rotation hole), so one hole doesn't fabricate
# hours of residency.
_CENSUS_MAX_GAP_MS = 15 * 60 * 1000

# Payload caps (keep the JSON result bounded on big instances).
_DETAIL_ROWS = 12
_CONNECTION_ROWS = 40
_MODEL_ROWS = 24
_PROCESS_ROWS = 15

_ANNOT_PROJECT = 'dataiku.com/dku-project-key'
_ANNOT_EXEC_TYPE = 'dataiku.com/dku-execution-type'
_ANNOT_SUBMITTER = 'dataiku.com/dku-exec-submitter'
_LABEL_NODE = 'dataiku.com/dku-node-id'
_LABEL_EXEC_ID = 'dataiku.com/dku-execution-id'


def _open_lines(path):
    if path.endswith('.gz'):
        return io.TextIOWrapper(gzip.open(path, 'rb'), encoding='utf-8', errors='replace')
    return open(path, 'r', encoding='utf-8', errors='replace')


def _ts_ms(obj):
    """Epoch ms from the audit line ISO timestamp; None if unparseable.

    Must be timezone-aware: this epoch is compared against epoch-ms fields
    inside CRU records (startTime/endTime), so a naive-local parse would skew
    K8s job lifetimes by the host UTC offset.
    """
    ts = obj.get('timestamp')
    if not ts:
        return None
    try:
        # "2026-01-01T00:00:00.000+0000"
        return int(datetime.strptime(ts, '%Y-%m-%dT%H:%M:%S.%f%z').timestamp() * 1000)
    except Exception:
        return None


def _day(ts_iso):
    """YYYY-MM-DD bucket from the audit line ISO timestamp string."""
    return ts_iso[:10] if ts_iso and len(ts_iso) >= 10 else None


class _Daily(object):
    """Per-day delta accumulator for the activity timeline."""

    def __init__(self):
        self.days = {}

    def add(self, day, field, value):
        if not day or not value:
            return
        d = self.days.setdefault(day, {
            'memGBh': 0.0, 'cpuH': 0.0, 'llmUSD': 0.0,
            'sqlExecS': 0.0, 'sqlQueries': 0, 'k8sGBh': 0.0,
        })
        d[field] += value

    def rows(self):
        return [dict(date=k, **v) for k, v in sorted(self.days.items())]


def _parse_audit(audit_dir, max_files=0):
    files = sorted(glob.glob(os.path.join(audit_dir, 'audit.log*')))
    if max_files and max_files > 0:
        files = files[:max_files]

    # Per-CRU-id state (max-per-id dedup for cumulative metrics)
    local = {}     # id -> {maxMem, maxCpu, projectKey, authIdentifier, ctxType, commandName}
    sqlconn = {}   # id -> {projectKey, authIdentifier, connection}
    sqlq = {}      # id -> {connUsageId, connection, maxExec, maxTotal, maxRows,
                   #        projectKey, authIdentifier, ctxType}
    k8s = {}       # id -> {execId, cluster, config, memReqMB, cpuReq, start, lastSeen,
                   #        end, spark, projectKey, authIdentifier, ctxType}
    llm = {}       # id -> {maxUSD, ptok, ctok, queries, cacheHit, cacheMiss, compMS,
                   #        projectKey, authIdentifier, model, llmId, llmType, connection}

    # Census state: pod key -> integration accumulators
    pods = {}      # key -> {lastTs, memMBs, cpuMilliMs, memMB, project, execType,
                   #        submitter, nodeId, cluster, execId, snaps}
    census_msgs = 0

    daily = _Daily()

    first_ts = None
    last_ts = None
    lines_scanned = 0
    files_read = 0

    for path in files:
        try:
            fh = _open_lines(path)
        except OSError:
            continue
        files_read += 1
        with fh:
            for line in fh:
                lines_scanned += 1
                is_cru = 'compute-resource-usage' in line
                if not is_cru:
                    continue
                is_census = 'kubernetes-cluster-usage-status' in line
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get('logger') != 'dku.audit.compute-resource-usage':
                    continue
                ts_iso = obj.get('timestamp')
                if ts_iso:
                    if first_ts is None or ts_iso < first_ts:
                        first_ts = ts_iso
                    if last_ts is None or ts_iso > last_ts:
                        last_ts = ts_iso
                day = _day(ts_iso)
                msg = obj.get('message') or {}

                if is_census:
                    census_msgs += 1
                    ts_ms = _ts_ms(obj)
                    cluster = msg.get('clusterId') or 'unknown'
                    for pod in ((msg.get('podsStatus') or {}).get('pods') or []):
                        if not isinstance(pod, dict):
                            continue
                        _ingest_pod(pods, daily, day, ts_ms, cluster, pod)
                    continue

                cru = msg.get('computeResourceUsage')
                if not isinstance(cru, dict):
                    continue
                cid = cru.get('id')
                if not cid:
                    continue
                ctype = cru.get('type')
                ctx = cru.get('context') or {}

                if ctype == 'LOCAL_PROCESS':
                    lp = cru.get('localProcess') or {}
                    mem = lp.get('vmRSSTotalMBS') or 0
                    cpu = lp.get('cpuTotalMS') or 0
                    e = local.get(cid)
                    if e is None:
                        local[cid] = {
                            'maxMem': mem, 'maxCpu': cpu,
                            'projectKey': ctx.get('projectKey'),
                            'authIdentifier': ctx.get('authIdentifier'),
                            'ctxType': ctx.get('type'),
                            'commandName': lp.get('commandName'),
                        }
                        daily.add(day, 'memGBh', mem / 1024.0 / 3600.0)
                        daily.add(day, 'cpuH', cpu / 1000.0 / 3600.0)
                    else:
                        if mem > e['maxMem']:
                            daily.add(day, 'memGBh', (mem - e['maxMem']) / 1024.0 / 3600.0)
                            e['maxMem'] = mem
                        if cpu > e['maxCpu']:
                            daily.add(day, 'cpuH', (cpu - e['maxCpu']) / 1000.0 / 3600.0)
                            e['maxCpu'] = cpu

                elif ctype == 'SQL_CONNECTION':
                    # Attribution anchor for older-DSS SQL_QUERY records.
                    e = sqlconn.get(cid)
                    if e is None:
                        sqlconn[cid] = {
                            'projectKey': ctx.get('projectKey'),
                            'authIdentifier': ctx.get('authIdentifier'),
                            'connection': (cru.get('sqlConnection') or {}).get('connection'),
                        }
                    elif ctx and not e.get('projectKey'):
                        e['projectKey'] = ctx.get('projectKey')
                        e['authIdentifier'] = ctx.get('authIdentifier')

                elif ctype == 'SQL_QUERY':
                    q = cru.get('sqlQuery') or {}
                    ex = q.get('statementExecutionTime') or 0
                    tt = cru.get('totalTime') or 0
                    rows = q.get('fetchedRowCount') or 0
                    e = sqlq.get(cid)
                    if e is None:
                        sqlq[cid] = {
                            'connUsageId': q.get('connectionUsageId'),
                            'connection': q.get('connection'),
                            'maxExec': ex, 'maxTotal': tt, 'maxRows': rows,
                            'projectKey': ctx.get('projectKey'),
                            'authIdentifier': ctx.get('authIdentifier'),
                            'ctxType': ctx.get('type'),
                        }
                        daily.add(day, 'sqlExecS', ex / 1000.0)
                        daily.add(day, 'sqlQueries', 1)
                    else:
                        if ex > e['maxExec']:
                            daily.add(day, 'sqlExecS', (ex - e['maxExec']) / 1000.0)
                            e['maxExec'] = ex
                        if tt > e['maxTotal']:
                            e['maxTotal'] = tt
                        if rows > e['maxRows']:
                            e['maxRows'] = rows
                        if ctx and not e.get('projectKey'):
                            e['projectKey'] = ctx.get('projectKey')
                            e['authIdentifier'] = ctx.get('authIdentifier')
                            e['ctxType'] = ctx.get('type')

                elif ctype in ('SINGLE_K8S_JOB', 'SPARK_K8S_JOB'):
                    spark = ctype == 'SPARK_K8S_JOB'
                    blk = cru.get('sparkK8SJob' if spark else 'singleK8SJob') or {}
                    ts_ms = _ts_ms(obj)
                    e = k8s.get(cid)
                    if e is None:
                        k8s[cid] = {
                            'execId': blk.get('executionId'),
                            'cluster': blk.get('k8sClusterId'),
                            'config': blk.get('sparkConfigName' if spark else 'containerConfigName'),
                            'memReqMB': blk.get('memoryRequestMB') or 0,
                            'cpuReq': blk.get('cpuRequest'),
                            'start': cru.get('startTime'),
                            'end': cru.get('endTime'),
                            'lastSeen': ts_ms,
                            'spark': spark,
                            'projectKey': ctx.get('projectKey'),
                            'authIdentifier': ctx.get('authIdentifier'),
                            'ctxType': ctx.get('type'),
                            'startDay': day,
                        }
                    else:
                        if cru.get('endTime'):
                            e['end'] = cru.get('endTime')
                        if ts_ms and (e['lastSeen'] is None or ts_ms > e['lastSeen']):
                            e['lastSeen'] = ts_ms

                elif ctype == 'LLM_USAGE':
                    lu = cru.get('llmUsage') or {}
                    usd = lu.get('estimatedCostUSD') or 0
                    e = llm.get(cid)
                    if e is None:
                        llm[cid] = {
                            'maxUSD': usd,
                            'ptok': lu.get('totalPromptTokens') or 0,
                            'ctok': lu.get('totalCompletionTokens') or 0,
                            'queries': lu.get('totalQueries') or 0,
                            'cacheHit': lu.get('cacheHitQueries') or 0,
                            'cacheMiss': lu.get('cacheMissQueries') or 0,
                            'compMS': lu.get('totalComputationTimeMS') or 0,
                            'projectKey': ctx.get('projectKey'),
                            'authIdentifier': ctx.get('authIdentifier'),
                            'model': lu.get('llmModel'),
                            'llmId': lu.get('llmId'),
                            'llmType': lu.get('llmType'),
                            'connection': lu.get('connection'),
                        }
                        daily.add(day, 'llmUSD', usd)
                    elif usd > e['maxUSD']:
                        daily.add(day, 'llmUSD', usd - e['maxUSD'])
                        e['maxUSD'] = usd
                        e['ptok'] = lu.get('totalPromptTokens') or 0
                        e['ctok'] = lu.get('totalCompletionTokens') or 0
                        e['queries'] = lu.get('totalQueries') or 0
                        e['cacheHit'] = lu.get('cacheHitQueries') or 0
                        e['cacheMiss'] = lu.get('cacheMissQueries') or 0
                        e['compMS'] = lu.get('totalComputationTimeMS') or 0

    return _aggregate(
        files=files, files_read=files_read, lines_scanned=lines_scanned,
        first_ts=first_ts, last_ts=last_ts,
        local=local, sqlconn=sqlconn, sqlq=sqlq, k8s=k8s, llm=llm,
        pods=pods, census_msgs=census_msgs, daily=daily,
    )


def _ingest_pod(pods, daily, day, ts_ms, cluster, pod):
    labels = pod.get('labels') or {}
    annots = pod.get('annotations') or {}
    key = '%s|%s|%s' % (cluster, pod.get('namespace') or '', pod.get('name') or '')
    mem = pod.get('memoryMB') or 0
    cpu_milli = pod.get('cpuCurrentMillis') or 0
    e = pods.get(key)
    if e is None:
        pods[key] = {
            'lastTs': ts_ms,
            'memMBs': 0.0, 'cpuMilliS': 0.0, 'memMB': mem, 'cpuMilli': cpu_milli,
            'memReqMB': pod.get('memoryRequestMB') or 0,
            # annotations keep original case; labels are lowercased by k8s rules
            'project': annots.get(_ANNOT_PROJECT) or labels.get('dataiku.com/dku-project-key'),
            'execType': (annots.get(_ANNOT_EXEC_TYPE)
                         or (labels.get('dataiku.com/dku-execution-type') or '').upper() or None),
            'submitter': annots.get(_ANNOT_SUBMITTER) or labels.get('dataiku.com/dku-exec-submitter'),
            'nodeId': labels.get(_LABEL_NODE),
            'cluster': cluster,
            'execId': labels.get(_LABEL_EXEC_ID),
            'snaps': 1,
        }
        return
    e['snaps'] += 1
    if ts_ms and e['lastTs'] and ts_ms > e['lastTs']:
        gap = ts_ms - e['lastTs']
        if gap <= _CENSUS_MAX_GAP_MS:
            # integrate the PREVIOUS observation over the measured gap
            gap_s = gap / 1000.0
            e['memMBs'] += e['memMB'] * gap_s
            e['cpuMilliS'] += e['cpuMilli'] * gap_s
            daily.add(day, 'k8sGBh', (e['memMB'] * gap_s) / 1024.0 / 3600.0)
    if ts_ms and (e['lastTs'] is None or ts_ms > e['lastTs']):
        e['lastTs'] = ts_ms
    e['memMB'] = mem
    e['cpuMilli'] = cpu_milli


def _aggregate(files, files_read, lines_scanned, first_ts, last_ts,
               local, sqlconn, sqlq, k8s, llm, pods, census_msgs, daily):
    def gbh(mbs):
        return mbs / 1024.0 / 3600.0

    def cpuh(ms):
        return ms / 1000.0 / 3600.0

    projects = {}
    users = {}
    ctx_types = {}
    proj_detail = {}

    _PROJ_ZERO = {
        'memGBh': 0.0, 'cpuH': 0.0, 'llmUSD': 0.0, 'llmTokens': 0,
        'sqlExecS': 0.0, 'sqlTotalS': 0.0, 'sqlRows': 0, 'sqlQueries': 0,
        'k8sReservedGBh': 0.0, 'k8sActualGBh': 0.0, 'k8sCpuCoreH': 0.0, 'k8sJobs': 0,
        'records': 0,
    }

    def _proj(pk):
        pk = pk or 'NONE'
        p = projects.get(pk)
        if p is None:
            p = dict(projectKey=pk, **_PROJ_ZERO)
            projects[pk] = p
        return p

    def _user(u):
        u = u or 'NONE'
        r = users.get(u)
        if r is None:
            r = dict(authIdentifier=u, **_PROJ_ZERO)
            users[u] = r
        return r

    def _detail(pk):
        return proj_detail.setdefault(pk or 'NONE', {
            'byUser': {}, 'byCtx': {}, 'byConnection': {}, 'byModel': {}})

    def _det_row(d, key):
        r = d.get(key)
        if r is None:
            r = {'memGBh': 0.0, 'cpuH': 0.0, 'llmUSD': 0.0, 'sqlExecS': 0.0,
                 'k8sGBh': 0.0, 'records': 0}
            d[key] = r
        return r

    # ---- LOCAL_PROCESS ----
    idle = []
    top_procs = []
    for cid, e in local.items():
        memgbh = gbh(e['maxMem'])
        cpu_h = cpuh(e['maxCpu'])
        p = _proj(e['projectKey'])
        p['memGBh'] += memgbh
        p['cpuH'] += cpu_h
        p['records'] += 1
        u = _user(e['authIdentifier'])
        u['memGBh'] += memgbh
        u['cpuH'] += cpu_h
        u['records'] += 1
        ct = ctx_types.setdefault(e['ctxType'] or 'NONE', {
            'type': e['ctxType'] or 'NONE', 'memGBh': 0.0, 'cpuH': 0.0, 'records': 0})
        ct['memGBh'] += memgbh
        ct['cpuH'] += cpu_h
        ct['records'] += 1
        det = _detail(e['projectKey'])
        du = _det_row(det['byUser'], e['authIdentifier'] or 'NONE')
        du['memGBh'] += memgbh
        du['cpuH'] += cpu_h
        du['records'] += 1
        dc = _det_row(det['byCtx'], e['ctxType'] or 'NONE')
        dc['memGBh'] += memgbh
        dc['cpuH'] += cpu_h
        dc['records'] += 1
        if memgbh >= _IDLE_MIN_GBH and cpu_h < _IDLE_MAX_CPUH and e['ctxType'] in _IDLE_CTX_TYPES:
            idle.append({
                'id': cid, 'projectKey': e['projectKey'] or 'NONE',
                'contextType': e['ctxType'], 'memGBh': memgbh, 'cpuH': cpu_h})
        if memgbh >= 0.5 or cpu_h >= 0.05:
            top_procs.append({
                'id': cid, 'projectKey': e['projectKey'] or 'NONE',
                'contextType': e['ctxType'] or 'NONE',
                'commandName': (e.get('commandName') or '')[:80],
                'memGBh': memgbh, 'cpuH': cpu_h})

    # ---- SQL: join queries to connections, aggregate by connection + project ----
    connections = {}
    sql_unattributed = {'queries': 0, 'execS': 0.0, 'totalS': 0.0, 'rows': 0}
    for cid, q in sqlq.items():
        pk = q.get('projectKey')
        auth = q.get('authIdentifier')
        if not pk:
            anchor = sqlconn.get(q.get('connUsageId') or '')
            if anchor:
                pk = anchor.get('projectKey')
                auth = auth or anchor.get('authIdentifier')
        exec_s = q['maxExec'] / 1000.0
        total_s = q['maxTotal'] / 1000.0
        rows = q['maxRows']
        conn = q.get('connection') or 'unknown'
        c = connections.setdefault(conn, {
            'connection': conn, 'queries': 0, 'execS': 0.0, 'totalS': 0.0,
            'rows': 0, 'projects': {}})
        c['queries'] += 1
        c['execS'] += exec_s
        c['totalS'] += total_s
        c['rows'] += rows
        if pk:
            c['projects'][pk] = c['projects'].get(pk, 0.0) + exec_s
            p = _proj(pk)
            p['sqlExecS'] += exec_s
            p['sqlTotalS'] += total_s
            p['sqlRows'] += rows
            p['sqlQueries'] += 1
            p['records'] += 1
            det = _detail(pk)
            dcn = _det_row(det['byConnection'], conn)
            dcn['sqlExecS'] += exec_s
            dcn['records'] += 1
            if auth:
                u = _user(auth)
                u['sqlExecS'] += exec_s
                u['sqlTotalS'] += total_s
                u['sqlQueries'] += 1
                u['records'] += 1
                du = _det_row(det['byUser'], auth)
                du['sqlExecS'] += exec_s
                du['records'] += 1
        else:
            sql_unattributed['queries'] += 1
            sql_unattributed['execS'] += exec_s
            sql_unattributed['totalS'] += total_s
            sql_unattributed['rows'] += rows

    conn_list = []
    for c in connections.values():
        top = sorted(c['projects'].items(), key=lambda kv: kv[1], reverse=True)[:3]
        c['topProjects'] = [{'projectKey': k, 'execS': v} for k, v in top]
        del c['projects']
        # fetch-bound vs engine-bound signal: share of wall time NOT spent in the engine
        c['fetchOverheadPct'] = (
            100.0 * (1.0 - c['execS'] / c['totalS']) if c['totalS'] > 0 else 0.0)
        conn_list.append(c)
    conn_list.sort(key=lambda r: r['execS'], reverse=True)

    # ---- K8s jobs (reserved) ----
    clusters = {}
    k8s_exec_index = {}
    for cid, e in k8s.items():
        start = e.get('start')
        end = e.get('end') or e.get('lastSeen')
        lifetime_ms = max(0, (end - start)) if (start and end) else 0
        reserved_gbh = (e['memReqMB'] / 1024.0) * (lifetime_ms / 3600000.0)
        pk = e.get('projectKey')
        p = _proj(pk)
        p['k8sReservedGBh'] += reserved_gbh
        p['k8sJobs'] += 1
        p['records'] += 1
        u = _user(e.get('authIdentifier'))
        u['k8sReservedGBh'] += reserved_gbh
        u['k8sJobs'] += 1
        u['records'] += 1
        cl = clusters.setdefault(e.get('cluster') or 'unknown', {
            'clusterId': e.get('cluster') or 'unknown', 'jobs': 0, 'sparkJobs': 0,
            'reservedGBh': 0.0})
        cl['jobs'] += 1
        if e['spark']:
            cl['sparkJobs'] += 1
        cl['reservedGBh'] += reserved_gbh
        if e.get('execId'):
            k8s_exec_index[e['execId']] = pk

    # ---- K8s pod census (actuals) ----
    nodes = {}
    exec_types = {}
    census_pods = len(pods)
    for key, e in pods.items():
        actual_gbh = e['memMBs'] / 1024.0 / 3600.0
        cpu_core_h = e['cpuMilliS'] / 1000.0 / 3600.0
        nd = nodes.setdefault(e.get('nodeId') or 'unknown', {
            'nodeId': e.get('nodeId') or 'unknown', 'actualGBh': 0.0,
            'cpuCoreH': 0.0, 'pods': 0})
        nd['actualGBh'] += actual_gbh
        nd['cpuCoreH'] += cpu_core_h
        nd['pods'] += 1
        et = exec_types.setdefault(e.get('execType') or 'UNKNOWN', {
            'type': e.get('execType') or 'UNKNOWN', 'actualGBh': 0.0,
            'cpuCoreH': 0.0, 'pods': 0})
        et['actualGBh'] += actual_gbh
        et['cpuCoreH'] += cpu_core_h
        et['pods'] += 1
        pk = e.get('project') or k8s_exec_index.get(e.get('execId') or '')
        if pk:
            p = _proj(pk)
            p['k8sActualGBh'] += actual_gbh
            p['k8sCpuCoreH'] += cpu_core_h
            det = _detail(pk)
            dc = _det_row(det['byCtx'], e.get('execType') or 'UNKNOWN')
            dc['k8sGBh'] += actual_gbh
            dc['records'] += 1
        if e.get('submitter'):
            u = _user(e['submitter'])
            u['k8sActualGBh'] += actual_gbh
            u['k8sCpuCoreH'] += cpu_core_h

    # ---- LLM ----
    models = {}
    for cid, e in llm.items():
        usd = e['maxUSD']
        p = _proj(e['projectKey'])
        p['llmUSD'] += usd
        p['llmTokens'] += (e['ptok'] + e['ctok'])
        p['records'] += 1
        u = _user(e['authIdentifier'])
        u['llmUSD'] += usd
        u['records'] += 1
        det = _detail(e['projectKey'])
        du = _det_row(det['byUser'], e['authIdentifier'] or 'NONE')
        du['llmUSD'] += usd
        du['records'] += 1
        mkey = e.get('llmId') or e.get('model') or 'unknown'
        m = models.setdefault(mkey, {
            'llmId': mkey, 'model': e.get('model') or 'unknown',
            'llmType': e.get('llmType') or '', 'connection': e.get('connection') or '',
            'usd': 0.0, 'ptok': 0, 'ctok': 0, 'queries': 0,
            'cacheHit': 0, 'cacheMiss': 0, 'compS': 0.0})
        m['usd'] += usd
        m['ptok'] += e['ptok']
        m['ctok'] += e['ctok']
        m['queries'] += e['queries']
        m['cacheHit'] += e['cacheHit']
        m['cacheMiss'] += e['cacheMiss']
        m['compS'] += e['compMS'] / 1000.0
        dm = _det_row(det['byModel'], e.get('model') or 'unknown')
        dm['llmUSD'] += usd
        dm['records'] += 1

    # ---- attach per-project drilldowns (bounded) ----
    for pk, det in proj_detail.items():
        p = projects.get(pk)
        if not p:
            continue
        p['byUser'] = _top_rows(det['byUser'], 'authIdentifier',
                                lambda r: r['memGBh'] + r['llmUSD'] + r['sqlExecS'] / 3600.0)
        p['byContextType'] = _top_rows(det['byCtx'], 'type',
                                       lambda r: r['memGBh'] + r['k8sGBh'])
        p['byConnection'] = _top_rows(det['byConnection'], 'connection',
                                      lambda r: r['sqlExecS'])
        p['byModel'] = _top_rows(det['byModel'], 'model', lambda r: r['llmUSD'])

    proj_list = sorted(projects.values(),
                       key=lambda r: (r['memGBh'] + r['k8sActualGBh']), reverse=True)
    user_list = sorted(users.values(), key=lambda r: r['cpuH'], reverse=True)
    ctx_list = sorted(ctx_types.values(), key=lambda r: r['memGBh'], reverse=True)
    idle.sort(key=lambda r: r['memGBh'], reverse=True)
    top_procs.sort(key=lambda r: r['memGBh'], reverse=True)

    model_list = sorted(models.values(), key=lambda r: r['usd'], reverse=True)[:_MODEL_ROWS]
    node_list = sorted(nodes.values(), key=lambda r: r['actualGBh'], reverse=True)
    exec_type_list = sorted(exec_types.values(), key=lambda r: r['actualGBh'], reverse=True)
    cluster_list = sorted(clusters.values(), key=lambda r: r['reservedGBh'], reverse=True)

    class_totals = {
        'local': {
            'memGBh': sum(r['memGBh'] for r in proj_list),
            'cpuH': sum(r['cpuH'] for r in proj_list),
            'records': len(local),
        },
        'sql': {
            'queries': len(sqlq),
            'execS': sum(c['execS'] for c in conn_list),
            'totalS': sum(c['totalS'] for c in conn_list),
            'rows': sum(c['rows'] for c in conn_list),
            'connections': len(conn_list),
            'unattributed': sql_unattributed,
        },
        'k8s': {
            'jobs': len(k8s),
            'sparkJobs': sum(1 for e in k8s.values() if e['spark']),
            'reservedGBh': sum(c['reservedGBh'] for c in cluster_list),
            'actualGBh': sum(n['actualGBh'] for n in node_list),
            'cpuCoreH': sum(n['cpuCoreH'] for n in node_list),
            'censusSnapshots': census_msgs,
            'censusPods': census_pods,
        },
        'llm': {
            'usd': sum(m['usd'] for m in models.values()),
            'ptok': sum(m['ptok'] for m in models.values()),
            'ctok': sum(m['ctok'] for m in models.values()),
            'queries': sum(m['queries'] for m in models.values()),
            'cacheHit': sum(m['cacheHit'] for m in models.values()),
            'cacheMiss': sum(m['cacheMiss'] for m in models.values()),
            'records': len(llm),
        },
    }

    return {
        'ok': True,
        'span': {
            'firstTs': first_ts, 'lastTs': last_ts, 'files': len(files),
            'filesRead': files_read, 'linesScanned': lines_scanned,
            'cruRecords': len(local) + len(llm) + len(sqlq) + len(k8s),
        },
        'totals': {
            'memGBh': class_totals['local']['memGBh'],
            'cpuH': class_totals['local']['cpuH'],
            'llmUSD': class_totals['llm']['usd'],
            'sqlExecS': class_totals['sql']['execS'],
            'k8sReservedGBh': class_totals['k8s']['reservedGBh'],
            'k8sActualGBh': class_totals['k8s']['actualGBh'],
            'projectCount': len([p for p in proj_list if p['projectKey'] != 'NONE']),
            'userCount': len([u for u in user_list if u['authIdentifier'] != 'NONE']),
        },
        'classTotals': class_totals,
        'projects': proj_list,
        'users': user_list,
        'contextTypes': ctx_list,
        'connections': conn_list[:_CONNECTION_ROWS],
        'llmModels': model_list,
        'k8s': {
            'clusters': cluster_list,
            'nodes': node_list,
            'execTypes': exec_type_list,
        },
        'idleResources': idle[:_IDLE_LIMIT],
        'topProcesses': top_procs[:_PROCESS_ROWS],
        'daily': daily.rows(),
    }


def _top_rows(d, key_name, score):
    rows = [dict({key_name: k}, **v) for k, v in d.items()]
    rows.sort(key=score, reverse=True)
    return rows[:_DETAIL_ROWS]


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        dip_home = os.environ.get('DIP_HOME') or os.environ.get('DKU_DIP_HOME')
        if not dip_home:
            return json.dumps({'ok': False, 'error': 'DIP_HOME not set on host'})
        audit_dir = os.path.join(dip_home, 'run', 'audit')
        try:
            max_files = int(self.config.get('max_files') or 0)
        except (TypeError, ValueError):
            max_files = 0
        try:
            result = _parse_audit(audit_dir, max_files=max_files)
        except Exception as exc:
            return json.dumps({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:240]}'})
        result['auditDir'] = audit_dir
        return json.dumps(result)
