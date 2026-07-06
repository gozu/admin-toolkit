"""Pure implementations of every agent tool — the single source of truth.

Each function takes a ToolkitClient + validated inputs and returns a JSON-able
dict already shaped for model consumption (top-N, allowlisted keys, budget-
capped). tool.py adapters and the agents' in-process LangChain tools both call
these, so behavior can never diverge between the two surfaces.

Field names and parsing here follow shapes recorded live from the backend
(scripts/agents/verify_endpoints.py, 2026-07-02, tam-global v0.4.629) — e.g.
/api/overview serves human-formatted strings ('31 GB', '65,536', '46%') and
/api/java-memory serves the raw env-default.sh text.
"""

import re

from . import shaping
from .errors import ToolkitError

# ── parsing helpers for the overview's human-formatted strings ───────────────


def _num(text):
    """'65,536' → 65536; None on anything non-numeric ('Unlimited')."""
    try:
        return int(str(text).replace(',', '').strip())
    except (TypeError, ValueError):
        return None


def _pct(text):
    """'46%' → 46."""
    try:
        return int(str(text).strip().rstrip('%'))
    except (TypeError, ValueError):
        return None


def _xmx_mb(token):
    """'4g'/'2048m'/'512k' → MB."""
    m = re.match(r'^(\d+)([gmk])$', str(token).strip().lower())
    if not m:
        return None
    val, unit = int(m.group(1)), m.group(2)
    return val * 1024 if unit == 'g' else val if unit == 'm' else max(1, val // 1024)


_REAL_MOUNT_EXCLUDE = ('devtmpfs', 'tmpfs', 'overlay', 'shm', 'squashfs', 'efivarfs')


def _real_mounts(filesystem_rows):
    """Physical mounts only — the devtmpfs/tmpfs pseudo-rows would otherwise
    dominate any 'disk is full' reasoning."""
    out = []
    for row in filesystem_rows or []:
        fs = (row.get('Filesystem') or '').lower()
        if any(fs.startswith(x) for x in _REAL_MOUNT_EXCLUDE):
            continue
        out.append(row)
    return out


def _parse_java_memory(env_text):
    """BACKEND/FEK/JEK -Xmx out of env-default.sh (mirrors JavaMemoryParser.ts)."""
    keys = {'DKU_BACKEND_JAVA_OPTS': 'BACKEND', 'DKU_FEK_JAVA_OPTS': 'FEK', 'DKU_JEK_JAVA_OPTS': 'JEK'}
    settings = {}
    for line in (env_text or '').splitlines():
        m = re.match(r'^export\s+(\w+)="([^"]*)"', line.strip())
        if not m or m.group(1) not in keys:
            continue
        xmx = re.search(r'-Xmx(\d+[gmk])', m.group(2), re.IGNORECASE)
        if xmx:
            settings[keys[m.group(1)]] = xmx.group(1)
    return settings


# ── list-hosts ────────────────────────────────────────────────────────────────


def list_hosts(client, probe=False):
    hosts = client.list_hosts(force=True)
    rows = [{'id': h.get('id'), 'label': h.get('label'), 'url': h.get('url')} for h in hosts]
    if probe:
        for row in rows:
            try:
                check = client.post('/api/hosts/check', json={'hostId': row['id']})
                row['reachable'] = bool(check.get('ok'))
                row['toolkitPluginVersion'] = check.get('pluginVersion')
                row['macroProjectExists'] = check.get('adminToolkitProjectExists')
                if check.get('error'):
                    row['error'] = check['error']
            except ToolkitError as exc:
                row['reachable'] = False
                row['error'] = exc.message
    return shaping.enforce_budget({'hosts': rows, 'count': len(rows)})


# ── instance-health ───────────────────────────────────────────────────────────

_SECTIONS = ('system', 'sanity', 'java', 'issues', 'score')


def instance_health(client, host='local', sections=None, top_n=20, include_score=False):
    """Deterministic health snapshot: key metrics + threshold-derived issues,
    plus (opt-in — it forces the heavy code-envs + footprint scans) the same
    0-100 health score the toolkit UI shows, via the health.py port.
    """
    wanted = [s for s in (sections or _SECTIONS) if s in _SECTIONS] or list(_SECTIONS)
    if 'score' in (sections or ()):
        include_score = True
    if include_score:
        from . import health as health_mod
        from .errors import ScanTimeout
        try:
            score = health_mod.score_host(client, host=host)
            wanted_score = {
                'overall': score['overall'],
                'status': score['status'],
                'categoryScores': {c['category']: round(c['score'], 1) for c in score['categories']},
                'topIssues': [shaping.pick(i, ('severity', 'category', 'title', 'recommendation', 'value'))
                              for i in score['issues'][:top_n]],
            }
        except ScanTimeout as exc:
            wanted_score = exc.to_output()
    thresholds = client.get('/api/settings/threshold-defaults')
    overview = client.get('/api/overview', host=host)
    issues = []
    out = {'host': host or 'local', 'nodeId': (overview.get('instanceInfo') or {}).get('nodeId')}
    if include_score:
        out['healthScore'] = wanted_score

    mounts = _real_mounts(overview.get('filesystemInfo'))
    if 'system' in wanted:
        out['system'] = {
            'dssVersion': overview.get('dssVersion'),
            'os': overview.get('osInfo'),
            'pythonVersion': overview.get('pythonVersion'),
            'sparkVersion': overview.get('sparkVersion') or None,
            'cpu': overview.get('cpuCores'),
            'memory': shaping.pick(overview.get('memoryInfo') or {}, ('total', 'used', 'available', 'Swap')),
            'lastRestart': overview.get('lastRestartTime'),
            'filesystems': [shaping.pick(r, ('Filesystem', 'Mounted on', 'Size', 'Used', 'Available', 'Use%'))
                            for r in mounts],
            'openFilesLimit': (overview.get('systemLimits') or {}).get('open files'),
        }

    # threshold-derived issues from the overview (always computed — they feed
    # the issues section regardless of which display sections were asked for)
    warn_pct = thresholds.get('filesystemWarningPct', 70)
    crit_pct = thresholds.get('filesystemCriticalPct', 90)
    for row in mounts:
        use = _pct(row.get('Use%'))
        if use is None:
            continue
        if use >= crit_pct:
            issues.append({'severity': 'critical', 'category': 'system_capacity',
                           'title': 'Filesystem %s at %d%% (critical ≥ %d%%)'
                                    % (row.get('Mounted on'), use, crit_pct)})
        elif use >= warn_pct:
            issues.append({'severity': 'warning', 'category': 'system_capacity',
                           'title': 'Filesystem %s at %d%% (warning ≥ %d%%)'
                                    % (row.get('Mounted on'), use, warn_pct)})
    open_files = _num((overview.get('systemLimits') or {}).get('open files'))
    open_files_min = thresholds.get('openFilesMinimum', 65535)
    if open_files is not None and open_files < open_files_min:
        issues.append({'severity': 'warning', 'category': 'runtime_config',
                       'title': 'Open files limit %d below recommended %d' % (open_files, open_files_min)})

    if 'java' in wanted or 'issues' in wanted:
        java = {}
        try:
            java = _parse_java_memory(client.get_text('/api/java-memory', host=host))
        except ToolkitError as exc:
            out.setdefault('warnings', []).append('java-memory unavailable: %s' % exc.message)
        if 'java' in wanted:
            out['javaXmx'] = java or None
        heap_min = thresholds.get('javaHeapMinimumMB', 2048)
        for comp, token in (java or {}).items():
            mb = _xmx_mb(token)
            if mb is not None and mb < heap_min:
                issues.append({'severity': 'warning', 'category': 'system_capacity',
                               'title': 'Java %s heap %s (< %d MB minimum)' % (comp, token, heap_min)})

    if 'sanity' in wanted or 'issues' in wanted:
        try:
            sanity = client.get('/api/sanity-check', host=host)
            messages = sanity.get('messages') or []
            errors = [m for m in messages if (m.get('severity') or '').upper() in ('ERROR', 'SEVERE', 'FATAL')]
            warnings = [m for m in messages if (m.get('severity') or '').upper() == 'WARNING']
            if 'sanity' in wanted:
                grouped = {}
                for m in errors + warnings:
                    key = m.get('code') or m.get('title')
                    if key not in grouped:
                        grouped[key] = dict(shaping.pick(m, ('severity', 'title', 'code')), count=0)
                    grouped[key]['count'] += 1
                out['sanity'] = {
                    'maxSeverity': sanity.get('maxSeverity'),
                    'errorCount': len(errors),
                    'warningCount': len(warnings),
                    'topMessages': sorted(grouped.values(), key=lambda g: -g['count'])[:10],
                }
            seen_codes = {}
            for m in errors:
                key = m.get('code') or m.get('title')
                if key in seen_codes:
                    seen_codes[key]['count'] += 1
                    continue
                issue = {'severity': 'critical', 'category': 'sanity',
                         'title': m.get('title') or m.get('code'),
                         'detail': (m.get('message') or '')[:200], 'count': 1}
                seen_codes[key] = issue
                if len(issues) < top_n:
                    issues.append(issue)
        except ToolkitError as exc:
            out.setdefault('warnings', []).append('sanity-check unavailable: %s' % exc.message)

    if 'issues' in wanted:
        order = {'critical': 0, 'warning': 1}
        issues.sort(key=lambda i: order.get(i['severity'], 2))
        out['topIssues'] = issues[:top_n]
        out['issueCounts'] = {
            'critical': sum(1 for i in issues if i['severity'] == 'critical'),
            'warning': sum(1 for i in issues if i['severity'] == 'warning'),
        }
    return shaping.enforce_budget(out)


# ── adoption-metrics ─────────────────────────────────────────────────────────


def adoption_metrics(client, host='local', window_months=12, top_n=10):
    data = client.get('/api/adoption', host=host, heavy=True)
    if not data.get('ok', True):
        return {'host': host or 'local', 'error': {'code': 'adoption-unavailable',
                'message': str(data.get('error') or 'adoption data unavailable')}}
    trend = data.get('monthlyTrend') or []
    window = trend[-max(1, int(window_months)):]
    totals = data.get('totals') or {}

    def _delta(field):
        if len(window) >= 2:
            return window[-1].get(field, 0) - window[-2].get(field, 0)
        return None

    out = {
        'host': host or 'local',
        'totals': shaping.pick(totals, ('projectCount', 'activeProjectCount', 'builderCount',
                                        'commitCount', 'avgPeoplePerProject', 'automationCount',
                                        'inactiveThresholdDays')),
        'monthlyTrend': window,
        'latestMonthDelta': {'commits': _delta('commits'), 'activeBuilders': _delta('activeBuilders')},
        'repeatBuilders': data.get('repeatBuilders'),
        'topBuilders': shaping.top_rows(data.get('builderStats'), 'commits', top_n,
                                        keys=('login', 'displayName', 'commits', 'projectCount', 'activeMonths')),
        'topGroups': shaping.top_rows(data.get('groups'), 'commits', top_n,
                                      keys=('name', 'commits', 'builderCount', 'memberCount', 'projectCount')),
        'newUserCohorts': (data.get('cohorts') or [])[-max(1, int(window_months)):],
        'generatedAtMs': data.get('generatedAtMs'),
    }
    if totals.get('truncatedProjectCount'):
        out['note'] = ('%d projects hit the commit-page cap; their counts are floors (≥).'
                       % totals['truncatedProjectCount'])
    return shaping.enforce_budget(out)


# ── compute-cost ─────────────────────────────────────────────────────────────

_CRU_GROUPS = {'project': 'projects', 'user': 'users', 'context_type': 'contextTypes'}


def compute_cost(client, host='local', group_by='project', top_n=10):
    if group_by not in _CRU_GROUPS:
        return {'error': {'code': 'bad-input',
                          'message': 'group_by must be one of: %s' % ', '.join(sorted(_CRU_GROUPS))}}
    data = client.get('/api/cru', host=host, heavy=True)
    if not data.get('ok', True):
        return {'host': host or 'local', 'error': {'code': 'cru-unavailable',
                'message': str(data.get('error') or 'CRU data unavailable')}}
    span = data.get('span') or {}
    rows = data.get(_CRU_GROUPS[group_by]) or []
    key_map = {
        'project': ('projectKey', 'cpuH', 'memGBh', 'llmUSD', 'llmTokens', 'records'),
        'user': ('authIdentifier', 'cpuH', 'memGBh', 'llmUSD', 'records'),
        'context_type': ('type', 'cpuH', 'memGBh', 'records'),
    }[group_by]
    out = {
        'host': host or 'local',
        'totals': data.get('totals'),
        'span': {'firstTs': span.get('firstTs'), 'lastTs': span.get('lastTs'),
                 'auditFiles': span.get('files'), 'cruRecords': span.get('cruRecords')},
        'spanNote': ('Coverage = the instance\'s rolling audit-log retention; '
                     'older usage is not observable from this tool.'),
        'groupBy': group_by,
        'rows': shaping.top_rows(rows, 'cpuH', top_n, keys=key_map),
        'topLlmSpendUSD': shaping.top_rows(
            [r for r in (data.get('projects') or []) if r.get('llmUSD')],
            'llmUSD', min(top_n, 5), keys=('projectKey', 'llmUSD', 'llmTokens')),
    }
    return shaping.enforce_budget(out)


# ── config-inspect ───────────────────────────────────────────────────────────

_CONFIG_DOMAINS = ('connections', 'code-envs', 'plugins', 'llms')


def config_inspect(client, host='local', domain='connections', detail=None,
                   name_filter=None, top_n=15):
    """Per-domain configuration summaries. `detail` unlocks slower drill-downs:
    connections+health (probe), plugins+usage (project scan)."""
    if domain not in _CONFIG_DOMAINS:
        return {'error': {'code': 'bad-input',
                          'message': 'domain must be one of: %s' % ', '.join(_CONFIG_DOMAINS)}}
    flt = (name_filter or '').lower()
    out = {'host': host or 'local', 'domain': domain}

    if domain == 'connections':
        data = client.get('/api/connections', host=host)
        details = data.get('connectionDetails') or []
        if flt:
            details = [c for c in details if flt in (c.get('name') or '').lower()
                       or flt in (c.get('type') or '').lower()]
        out['countsByType'] = data.get('connections')
        out['connections'] = details[:max(1, top_n * 2)]
        if detail == 'health':
            events = []
            try:
                resp = client._do('GET', '/api/connections/health',
                                  host=client._effective_host('/api/connections/health', host),
                                  timeout=client.heavy_timeout, stream=True)
                client._raise_for_status(resp, '/api/connections/health', host)
                from . import sse as sse_mod
                import json as json_mod
                event, data_lines = None, []
                for raw in resp.iter_lines(decode_unicode=True):
                    line = (raw or '').strip('\r')
                    if line == '':
                        if data_lines:
                            try:
                                payload = json_mod.loads('\n'.join(data_lines))
                            except ValueError:
                                payload = None
                            if event == 'conn' and isinstance(payload, dict):
                                events.append(payload)
                        event, data_lines = None, []
                    elif line.startswith('event:'):
                        event = line[6:].strip()
                    elif line.startswith('data:'):
                        data_lines.append(line[5:].strip())
                resp.close()
            except ToolkitError as exc:
                out['healthError'] = exc.message
            if flt:
                events = [e for e in events if flt in (e.get('name') or '').lower()]
            failing = [e for e in events if e.get('status') == 'fail']
            out['healthProbe'] = {
                'probed': len(events),
                'failing': [shaping.pick(e, ('name', 'type', 'error', 'status')) for e in failing[:top_n]],
            }

    elif domain == 'code-envs':
        data = client.get('/api/code-envs', host=host, heavy=True,
                          progress_path='/api/code-envs/progress')
        envs = data.get('codeEnvs') or []
        if flt:
            envs = [e for e in envs if flt in (e.get('name') or '').lower()]
        thresholds = client.get('/api/settings/threshold-defaults')
        deprecated_prefixes = [p.strip() for p in
                               (thresholds.get('deprecatedPythonPrefixes') or '').split(',') if p.strip()]

        def is_deprecated(env):
            v = str(env.get('version') or '')
            return any(v.startswith(p) for p in deprecated_prefixes)

        out['totals'] = {
            'totalEnvCount': data.get('totalEnvCount'),
            'analyzedEnvCount': len(data.get('codeEnvs') or []),
            'pythonVersionCounts': data.get('pythonVersionCounts'),
            'rVersionCounts': data.get('rVersionCounts'),
        }
        # Per-item admin whitelist (false-positive doctrine): whitelisted envs
        # drop out of the finding lists; report only the suppressed count.
        from . import health as health_mod
        wl = health_mod._whitelist_lookup(health_mod.fetch_host_whitelist(client, host))
        deprecated_rows = [e for e in envs if is_deprecated(e)
                           and not wl('python-env-lifecycle', e.get('name'))]
        out['deprecatedPython'] = [shaping.pick(e, ('name', 'version', 'owner', 'usageCount', 'projectCount'))
                                   for e in deprecated_rows][:top_n]
        out['unused'] = [shaping.pick(e, ('name', 'version', 'owner', 'sizeBytes'))
                         for e in envs if not (e.get('usageCount') or 0)][:top_n]
        largest_rows = [e for e in envs if not wl('code-env-size', e.get('name'))]
        out['largest'] = shaping.top_rows(largest_rows, 'sizeBytes', top_n,
                                          keys=('name', 'version', 'sizeBytes', 'usageCount', 'projectCount'))
        if wl.matched:
            out['whitelistSuppressed'] = len(wl.matched)
        if flt:
            out['matching'] = [shaping.pick(e, ('name', 'version', 'owner', 'usageCount',
                                                'projectCount', 'sizeBytes', 'projectKeys'))
                               for e in envs[:top_n]]

    elif domain == 'plugins':
        data = client.get('/api/plugins', host=host)
        details = data.get('pluginDetails') or []
        if flt:
            details = [p for p in details if flt in (p.get('id') or '').lower()
                       or flt in (p.get('label') or '').lower()]
        out['pluginsCount'] = data.get('pluginsCount')
        out['devPlugins'] = [p.get('id') for p in details if p.get('isDev')][:top_n]
        out['plugins'] = [shaping.pick(p, ('id', 'installedVersion', 'label', 'isDev'))
                          for p in details[:max(1, top_n * 2)]]
        if detail == 'usage':
            usages = client.get('/api/plugins/usages', host=host, heavy=True)
            by_plugin = usages.get('usagesByPlugin') or {}
            rows = [{'id': pid, 'projectsUsingCount': (u or {}).get('projectsUsingCount', 0)}
                    for pid, u in by_plugin.items()
                    if not flt or flt in pid.lower()]
            out['unusedPlugins'] = sorted(p['id'] for p in rows if not p['projectsUsingCount'])[:top_n * 2]
            out['mostUsed'] = shaping.top_rows(rows, 'projectsUsingCount', top_n)

    elif domain == 'llms':
        data = client.get('/api/llms', host=host)
        llms = data.get('llms') or []
        if flt:
            llms = [l for l in llms if flt in (l.get('id') or '').lower()
                    or flt in (l.get('connection') or '').lower()
                    or flt in (l.get('model') or '').lower()]
        by_conn = {}
        for l in llms:
            by_conn.setdefault(l.get('connection') or '?', []).append(l.get('model'))
        out['llmCount'] = len(llms)
        out['byConnection'] = {conn: models[:top_n] for conn, models in sorted(by_conn.items())}
        if flt:
            out['matching'] = [shaping.pick(l, ('id', 'model', 'type', 'connection'))
                               for l in llms[:top_n]]

    return shaping.enforce_budget(out)


# ── log-errors ───────────────────────────────────────────────────────────────


def log_errors(client, host='local', top_n=10, pattern=None, raw=False):
    """Grouped backend.log error signatures; optional raw-tail grep."""
    out = {'host': host or 'local'}
    if raw or pattern:
        text = client.get_text('/api/logs/raw-tail', host=host)
        lines = text.splitlines()
        if pattern:
            import re as re_mod
            try:
                rx = re_mod.compile(pattern, re_mod.IGNORECASE)
            except re_mod.error as exc:
                return {'error': {'code': 'bad-input', 'message': 'Invalid pattern: %s' % exc}}
            hits = [l for l in lines if rx.search(l)]
            out['grep'] = {'pattern': pattern, 'matchCount': len(hits), 'lines': hits[-80:]}
        else:
            out['rawTail'] = lines[-80:]
        if raw and not pattern:
            return shaping.enforce_budget(out)
    data = client.get('/api/logs/errors', host=host)
    stats = data.get('logStats') or {}
    groups = []
    for err in (data.get('rawLogErrors') or [])[:top_n]:
        lines = err.get('data') or []
        first_error = next((l for l in lines if any(tag in l for tag in ('[ERROR]', '[FATAL]', '[SEVERE]', '[WARN]'))),
                           lines[0] if lines else '')
        groups.append({
            'timestamp': err.get('timestamp'),
            'signature': first_error[:300],
            'contextLineCount': len(lines),
        })
    out['stats'] = stats
    out['errorGroups'] = groups
    return shaping.enforce_budget(out)


# ── storage-footprint ────────────────────────────────────────────────────────


def _fmt_bytes(n):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return '%.2f %s' % (n, unit) if unit not in ('B', 'KB') else '%d %s' % (n, unit)
        n /= 1024.0


def storage_footprint(client, host='local', top_n=10, min_size_gb=0):
    data = client.get('/api/project-footprint', host=host, heavy=True,
                      progress_path='/api/project-footprint/progress')
    projects = data.get('projects') or []
    summary = data.get('summary') or {}
    rows = [p for p in projects if (p.get('totalGB') or 0) >= (min_size_gb or 0)]
    top = shaping.top_rows(rows, 'totalBytes', top_n,
                           keys=('projectKey', 'name', 'owner', 'totalGB', 'codeEnvCount',
                                 'managedDatasetsBytes', 'managedFoldersBytes', 'projectSizeHealth'))
    # Per-directory breakdown so size advisories can name the actual consumer
    # ("Web app runs /webappruns/X = 9.02 GB") instead of guessing.
    breakdown_by_key = {p.get('projectKey'): p.get('footprintBreakdown') for p in rows}
    for row in top:
        buckets = (breakdown_by_key.get(row.get('projectKey')) or {}).get('buckets') or []
        row['sizeBreakdown'] = [
            {'what': b.get('label'), 'location': b.get('location'),
             'size': _fmt_bytes(b.get('bytes') or 0)}
            for b in buckets[:3]]
    inactive_by_key = {}
    try:
        inactive = client.get('/api/tools/inactive-projects', host=host)
        inactive_by_key = {p.get('projectKey'): p.get('daysInactive')
                          for p in (inactive.get('projects') or [])}
    except ToolkitError:
        pass
    total_bytes = sum(p.get('totalBytes') or 0 for p in projects)
    candidates = []
    for p in projects:
        days = inactive_by_key.get(p.get('projectKey'))
        if days and (p.get('totalGB') or 0) >= 0.5:
            candidates.append({'projectKey': p.get('projectKey'), 'totalGB': round(p.get('totalGB') or 0, 2),
                               'daysInactive': days, 'owner': p.get('owner')})
    candidates.sort(key=lambda c: -(c['totalGB']))
    out = {
        'host': host or 'local',
        'totals': {
            'projectCount': summary.get('projectCount', len(projects)),
            'totalGB': round(total_bytes / (1024 ** 3), 1),
            'instanceAvgProjectGB': summary.get('instanceAvgProjectGB'),
            'inactiveProjectCount': len(inactive_by_key),
        },
        'topProjects': top,
        'cleanupCandidates': candidates[:top_n],
        'cleanupNote': 'cleanupCandidates = projects both inactive and ≥0.5GB — the best storage wins.',
    }
    return shaping.enforce_budget(out)


# ── k8s-health ───────────────────────────────────────────────────────────────


def k8s_health(client, host='local', cluster=None, top_n=10):
    """Cluster reachability sweep; with `cluster`, a deep audit of that cluster
    (pressure, unhealthy pods, rule findings) via the K8S Insights macro."""
    clusters = client.get('/api/k8s-insights/clusters', host=host)
    rows = clusters.get('clusters') or []
    out = {
        'host': host or 'local',
        'clusters': [shaping.pick(c, ('id', 'name', 'state', 'architecture')) for c in rows[:top_n * 2]],
        'unavailableCount': len(clusters.get('unavailable') or []),
        'totalDiscovered': clusters.get('totalDiscovered'),
    }
    try:
        health = client.get('/api/k8s-insights/clusters/health', host=host, heavy=True)
        probes = health.get('clusters') or []
        out['reachability'] = {
            'ok': sum(1 for p in probes if p.get('ok')),
            'failing': [shaping.pick(p, ('id', 'errorClass', 'errorSummary'))
                        for p in probes if not p.get('ok')][:top_n],
        }
    except ToolkitError as exc:
        out['reachabilityError'] = exc.message
    if cluster:
        audit = client.stream_final('/api/k8s-insights/stream', host=host,
                                    params={'clusterId': cluster})
        findings = audit.get('findings') or audit.get('rules') or []
        pods = audit.get('unhealthyPods') or []
        out['audit'] = shaping.enforce_budget({
            'clusterId': cluster,
            'findings': findings[:top_n * 2] if isinstance(findings, list) else findings,
            'unhealthyPods': pods[:top_n] if isinstance(pods, list) else pods,
            'nodes': audit.get('nodes'),
            'pressure': audit.get('pressure'),
        }, budget=shaping.MAX_OUTPUT_BYTES // 2)
    return shaping.enforce_budget(out)


# ── db-health ────────────────────────────────────────────────────────────────

_DB_VIEWS = ('overview', 'tables', 'per-project')


def db_health(client, host='local', view='overview', connection=None, top_n=10):
    if view not in _DB_VIEWS:
        return {'error': {'code': 'bad-input',
                          'message': 'view must be one of: %s' % ', '.join(_DB_VIEWS)}}
    conns = client.get('/api/tools/db-health/connections', host=host)
    connection = connection or conns.get('configuredConnection')
    if not connection:
        return {'error': {
            'code': 'no-connection',
            'message': 'No RuntimeDB connection configured for DB Health.',
            'remediation': ('Pass connection=<name> explicitly. PostgreSQL connections on this host: %s'
                            % ', '.join(c.get('name') for c in (conns.get('connections') or [])[:10])),
        }}
    data = client.get('/api/tools/db-health/%s' % view, host=host,
                      params={'connection': connection, 'limit': max(top_n * 3, 30)}, heavy=True)
    if data.get('needsPassword'):
        return {'error': {
            'code': 'db-password-required',
            'message': 'The DB Health connection needs a password the backend does not hold.',
            'remediation': 'An admin must set the DB password in the Admin Toolkit plugin settings (DB Health section).',
        }}
    out = {'host': host or 'local', 'view': view, 'connection': connection}
    if view == 'overview':
        out['overview'] = shaping.pick(data, ('dbSize', 'dbSizeBytes', 'version', 'tableCount',
                                              'totalDeadTuples', 'totalLiveTuples', 'canWrite', 'warnings'))
    elif view == 'tables':
        tables = data.get('tables') or []
        out['topByDeadTuples'] = shaping.top_rows(tables, 'deadTuples', top_n)
        out['tableCount'] = len(tables)
    else:
        rows = data.get('projects') or data.get('rows') or []
        out['rows'] = rows[:top_n]
    return shaping.enforce_budget(out)
