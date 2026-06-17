"""Plugin macro: parse Compute Resource Usage (CRU) from the host audit logs.

Read-only. Runs as the `dataiku` service account (impersonate=false) so it can
read <DIP_HOME>/run/audit/audit.log* regardless of webapp impersonation. Backs
the "Cost / CRU" module.

Aggregation rules (from CRU.md — do not re-derive):
- Filter to logger == "dku.audit.compute-resource-usage".
- Metrics are cumulative & monotonic ⇒ take max(metric) per CRU `id`, over ALL
  msgTypes (update/complete/start), then sum by dimension.
- LOCAL_PROCESS.localProcess: vmRSSTotalMBS → GB·h (/1024/3600),
  cpuTotalMS → CPU·h (/1000/3600). Attribute via context.{projectKey,authIdentifier,type}.
- LLM_USAGE.llmUsage: estimatedCostUSD (real $), tokens. Full context block →
  directly attributable, no join.
- Idle-resource finder: ids with high GB·h and ~0 CPU·h
  (WEBAPP_BACKEND/JUPYTER_NOTEBOOK_KERNEL) = "reaper" candidates.

Streams line-by-line; never loads a whole file. Handles a .gz suffix via gzip.
"""
import glob
import gzip
import io
import json
import os

from dataiku.runnables import Runnable

# Idle-resource ("reaper") thresholds: resident memory worth flagging at near-zero CPU.
_IDLE_MIN_GBH = 1.0
_IDLE_MAX_CPUH = 0.05
_IDLE_CTX_TYPES = ('WEBAPP_BACKEND', 'JUPYTER_NOTEBOOK_KERNEL')
_IDLE_LIMIT = 25


def _open_lines(path):
    if path.endswith('.gz'):
        return io.TextIOWrapper(gzip.open(path, 'rb'), encoding='utf-8', errors='replace')
    return open(path, 'r', encoding='utf-8', errors='replace')


def _parse_audit(audit_dir, max_files=0):
    files = sorted(glob.glob(os.path.join(audit_dir, 'audit.log*')))
    if max_files and max_files > 0:
        files = files[:max_files]

    local = {}  # CRU id -> {maxMem, maxCpu, projectKey, authIdentifier, ctxType}
    llm = {}    # CRU id -> {maxUSD, ptok, ctok, queries, projectKey, authIdentifier, model}

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
                if 'compute-resource-usage' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get('logger') != 'dku.audit.compute-resource-usage':
                    continue
                msg = obj.get('message') or {}
                cru = msg.get('computeResourceUsage')
                if not isinstance(cru, dict):
                    continue
                ts = obj.get('timestamp')
                if ts:
                    if first_ts is None or ts < first_ts:
                        first_ts = ts
                    if last_ts is None or ts > last_ts:
                        last_ts = ts
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
                        }
                    else:
                        if mem > e['maxMem']:
                            e['maxMem'] = mem
                        if cpu > e['maxCpu']:
                            e['maxCpu'] = cpu
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
                            'projectKey': ctx.get('projectKey'),
                            'authIdentifier': ctx.get('authIdentifier'),
                            'model': lu.get('llmModel'),
                        }
                    elif usd > e['maxUSD']:
                        e['maxUSD'] = usd
                        e['ptok'] = lu.get('totalPromptTokens') or 0
                        e['ctok'] = lu.get('totalCompletionTokens') or 0
                        e['queries'] = lu.get('totalQueries') or 0

    def gbh(mbs):
        return mbs / 1024.0 / 3600.0

    def cpuh(ms):
        return ms / 1000.0 / 3600.0

    projects = {}
    users = {}
    ctx_types = {}
    # Per-project sub-breakdowns (by user / by context.type) backing the
    # leaderboard drilldown panel. Kept separate from the flat rollups above.
    proj_detail = {}

    def _proj(pk):
        return projects.setdefault(pk or 'NONE', {
            'projectKey': pk or 'NONE', 'memGBh': 0.0, 'cpuH': 0.0,
            'llmUSD': 0.0, 'llmTokens': 0, 'records': 0})

    def _detail(pk):
        return proj_detail.setdefault(pk or 'NONE', {'byUser': {}, 'byCtx': {}})

    def _user(u):
        return users.setdefault(u or 'NONE', {
            'authIdentifier': u or 'NONE', 'memGBh': 0.0, 'cpuH': 0.0,
            'llmUSD': 0.0, 'records': 0})

    idle = []
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
        # per-project breakdown
        det = _detail(e['projectKey'])
        du = det['byUser'].setdefault(e['authIdentifier'] or 'NONE', {'memGBh': 0.0, 'cpuH': 0.0, 'records': 0})
        du['memGBh'] += memgbh
        du['cpuH'] += cpu_h
        du['records'] += 1
        dc = det['byCtx'].setdefault(e['ctxType'] or 'NONE', {'memGBh': 0.0, 'cpuH': 0.0, 'records': 0})
        dc['memGBh'] += memgbh
        dc['cpuH'] += cpu_h
        dc['records'] += 1
        if memgbh >= _IDLE_MIN_GBH and cpu_h < _IDLE_MAX_CPUH and e['ctxType'] in _IDLE_CTX_TYPES:
            idle.append({
                'id': cid, 'projectKey': e['projectKey'] or 'NONE',
                'contextType': e['ctxType'], 'memGBh': memgbh, 'cpuH': cpu_h})

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
        du = det['byUser'].setdefault(e['authIdentifier'] or 'NONE', {'memGBh': 0.0, 'cpuH': 0.0, 'records': 0})
        du.setdefault('llmUSD', 0.0)
        du['llmUSD'] += usd
        du['records'] += 1

    # Attach top contributors per project (rows are bounded to keep payload small).
    for pk, det in proj_detail.items():
        p = projects.get(pk)
        if not p:
            continue
        by_user = sorted(
            ({'authIdentifier': u, **vals} for u, vals in det['byUser'].items()),
            key=lambda r: r['memGBh'] + r.get('llmUSD', 0.0), reverse=True)
        by_ctx = sorted(
            ({'type': c, **vals} for c, vals in det['byCtx'].items()),
            key=lambda r: r['memGBh'], reverse=True)
        p['byUser'] = by_user[:12]
        p['byContextType'] = by_ctx[:12]

    proj_list = sorted(projects.values(), key=lambda r: r['memGBh'], reverse=True)
    user_list = sorted(users.values(), key=lambda r: r['cpuH'], reverse=True)
    ctx_list = sorted(ctx_types.values(), key=lambda r: r['memGBh'], reverse=True)
    idle.sort(key=lambda r: r['memGBh'], reverse=True)

    total_mem = sum(r['memGBh'] for r in proj_list)
    total_cpu = sum(r['cpuH'] for r in proj_list)
    total_usd = sum(r['llmUSD'] for r in proj_list)

    return {
        'ok': True,
        'span': {
            'firstTs': first_ts, 'lastTs': last_ts, 'files': len(files),
            'filesRead': files_read, 'linesScanned': lines_scanned,
            'cruRecords': len(local) + len(llm),
        },
        'totals': {
            'memGBh': total_mem, 'cpuH': total_cpu, 'llmUSD': total_usd,
            'projectCount': len([p for p in proj_list if p['projectKey'] != 'NONE']),
            'userCount': len([u for u in user_list if u['authIdentifier'] != 'NONE']),
        },
        'projects': proj_list,
        'users': user_list,
        'contextTypes': ctx_list,
        'idleResources': idle[:_IDLE_LIMIT],
    }


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
