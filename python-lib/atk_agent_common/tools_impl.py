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

from . import domain_registry as _registry
from . import read_registry as _read_registry
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
                check = client.post('/api/hosts/check', json={'hostId': row['id']}, retry_safe=True)
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
                'topIssues': [shaping.pick(i, health_mod.ISSUE_PICK_KEYS)
                              for i in score['issues'][:top_n]],
                'whitelistSuppressed': score.get('whitelistSuppressed', 0),
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


# ── config-inspect (registry-backed: atk_agent_common.domain_registry) ──────
#
# One handler per registry row, uniform signature
#   (client, host, domain, name_filter, detail, top_n, page) -> dict
# The dispatcher merges the handler result into {host, domain}, applies the
# optional fields[] projection and the output budget. Adding a domain =
# adding a registry row + a handler here; the tool description, the 'list'
# manifest and the coverage contract all derive from the registry.


def _domain_connections(client, host, domain, name_filter, detail, top_n, page):
    flt = (name_filter or '').lower()
    data = client.get('/api/connections', host=host)
    details = data.get('connectionDetails') or []
    if flt:
        details = [c for c in details if flt in (c.get('name') or '').lower()
                   or flt in (c.get('type') or '').lower()]
    out = {'countsByType': data.get('connections'),
           'connections': details[:max(1, top_n * 2)]}
    if detail == 'health':
        from . import health as health_mod
        events = health_mod.fetch_connection_health(client, host)
        if events is None:
            out['healthError'] = ('connection health probe unavailable — the backend '
                                  'may still be scanning; retry in a few minutes')
            events = []
        if flt:
            events = [e for e in events if flt in (e.get('name') or '').lower()]
        failing = [e for e in events if e.get('status') == 'fail']
        out['healthProbe'] = {
            'probed': len(events),
            'failing': [shaping.pick(e, ('name', 'type', 'error', 'status')) for e in failing[:top_n]],
        }
    return out


def _domain_code_envs(client, host, domain, name_filter, detail, top_n, page):
    flt = (name_filter or '').lower()
    data = client.get('/api/code-envs', host=host, heavy=True,
                      progress_path='/api/code-envs/progress')
    envs = data.get('codeEnvs') or []
    if flt:
        # env-name substring OR exact project key: name_filter=<projectKey>
        # returns exactly the envs that project uses — the drill step the
        # project-codenv-* remediation route depends on.
        envs = [e for e in envs if flt in (e.get('name') or '').lower()
                or any(flt == str(k).lower() for k in (e.get('projectKeys') or []))]
    thresholds = client.get('/api/settings/threshold-defaults')
    # Same fallback as the scoring twins (health.py, userMatrix.ts) and
    # plugin.json's thresh_deprecated_python_prefixes default, so config_inspect
    # can't report "no deprecated envs" while instance_health flags them from the
    # same data on a host whose plugin settings were never saved.
    deprecated_prefixes = [p.strip() for p in
                           (thresholds.get('deprecatedPythonPrefixes') or '2.,3.6,3.7').split(',')
                           if p.strip()]

    def is_deprecated(env):
        v = str(env.get('version') or '')
        return any(v.startswith(p) for p in deprecated_prefixes)

    out = {'totals': {
        'totalEnvCount': data.get('totalEnvCount'),
        'analyzedEnvCount': len(data.get('codeEnvs') or []),
        'pythonVersionCounts': data.get('pythonVersionCounts'),
        'rVersionCounts': data.get('rVersionCounts'),
    }}
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
    return out


def _domain_plugins(client, host, domain, name_filter, detail, top_n, page):
    flt = (name_filter or '').lower()
    data = client.get('/api/plugins', host=host)
    details = data.get('pluginDetails') or []
    if flt:
        details = [p for p in details if flt in (p.get('id') or '').lower()
                   or flt in (p.get('label') or '').lower()]
    out = {'pluginsCount': data.get('pluginsCount'),
           'devPlugins': [p.get('id') for p in details if p.get('isDev')][:top_n],
           'plugins': [shaping.pick(p, ('id', 'installedVersion', 'label', 'isDev'))
                       for p in details[:max(1, top_n * 2)]]}
    if detail == 'usage':
        usages = client.get('/api/plugins/usages', host=host, heavy=True)
        by_plugin = usages.get('usagesByPlugin') or {}
        rows = [{'id': pid, 'projectsUsingCount': (u or {}).get('projectsUsingCount', 0)}
                for pid, u in by_plugin.items()
                if not flt or flt in pid.lower()]
        out['unusedPlugins'] = sorted(p['id'] for p in rows if not p['projectsUsingCount'])[:top_n * 2]
        out['mostUsed'] = shaping.top_rows(rows, 'projectsUsingCount', top_n)
    return out


def _domain_clusters(client, host, domain, name_filter, detail, top_n, page):
    flt = (name_filter or '').lower()
    data = client.get('/api/k8s-insights/clusters', host=host)
    rows = data.get('clusters') or []
    if flt:
        rows = [c for c in rows if flt in (c.get('id') or '').lower()
                or flt in (c.get('name') or '').lower()]
    out = {'totalDiscovered': data.get('totalDiscovered'),
           'unavailableCount': len(data.get('unavailable') or []),
           # Unavailable = no kubeconfig and not RUNNING: stale attachments, the
           # natural cluster-detach candidates — name them, don't just count them.
           'unavailable': [shaping.pick(c, ('id', 'state', 'type'))
                           for c in (data.get('unavailable') or [])[:max(1, top_n * 2)]],
           'clusters': [shaping.pick(c, ('id', 'name', 'state', 'architecture', 'type'))
                        for c in rows[:max(1, top_n * 2)]]}
    if detail == 'health':
        try:
            health = client.get('/api/k8s-insights/clusters/health', host=host, heavy=True)
            probes = health.get('clusters') or []
            if flt:
                probes = [p for p in probes if flt in (p.get('id') or '').lower()]
            out['reachability'] = {
                'ok': sum(1 for p in probes if p.get('ok')),
                'failing': [shaping.pick(p, ('id', 'errorClass', 'errorSummary'))
                            for p in probes if not p.get('ok')][:top_n],
            }
        except ToolkitError as exc:
            out['reachabilityError'] = exc.message
    return out


def _domain_llms(client, host, domain, name_filter, detail, top_n, page):
    flt = (name_filter or '').lower()
    data = client.get('/api/llms', host=host)
    llms = data.get('llms') or []
    if flt:
        llms = [l for l in llms if flt in (l.get('id') or '').lower()
                or flt in (l.get('connection') or '').lower()
                or flt in (l.get('model') or '').lower()]
    by_conn = {}
    for l in llms:
        by_conn.setdefault(l.get('connection') or '?', []).append(l.get('model'))
    out = {'llmCount': len(llms),
           'byConnection': {conn: models[:top_n] for conn, models in sorted(by_conn.items())}}
    if flt:
        out['matching'] = [shaping.pick(l, ('id', 'model', 'type', 'connection'))
                           for l in llms[:top_n]]
    return out


def _domain_projects(client, host, domain, name_filter, detail, top_n, page):
    # Resolve a project label to its KEY: the project-scoped domains all take
    # name_filter=<projectKey>, and the agent rarely knows the key.
    flt = (name_filter or '').lower()
    inv = client.get('/api/tools/admin-actions/inventory', host=host,
                     params={'domain': 'projects'})
    projects = inv.get('projects') or []
    if flt:
        projects = [p for p in projects if flt in (p.get('projectKey') or '').lower()
                    or flt in (p.get('name') or '').lower()]
    return {'projectCount': len(projects), 'projects': projects[:max(1, top_n * 2)]}


def _domain_project_scoped(client, host, domain, name_filter, detail, top_n, page):
    # Per-project listings backed by the admin-actions inventory GET;
    # name_filter carries the PROJECT KEY (required).
    project_key = (name_filter or '').strip()
    if not project_key:
        return {'error': {'code': 'bad-input',
                          'message': "domain %r needs name_filter=<projectKey> — find the "
                                     "key with domain='projects' (name_filter matches key "
                                     "or label)" % domain}}
    params = {'domain': domain, 'projectKey': project_key}
    if domain == 'datasets' and detail == 'usage':
        # lineage drill-down: producers/consumers from recipe IO, webapp/
        # scenario name-refs, unreferenced + delete-candidate rollups.
        params['detail'] = 'usage'
    inv = client.get('/api/tools/admin-actions/inventory', host=host,
                     params=params, heavy=params.get('detail') == 'usage')
    key = {'scenarios': 'scenarios', 'webapps': 'webapps',
           'notebooks': 'notebooks', 'jobs': 'jobs', 'datasets': 'datasets',
           'continuous-activities': 'activities'}[domain]
    out = {'projectKey': project_key}
    rows = inv.get(key) or []
    if domain == 'datasets' and detail == 'usage':
        out['summary'] = inv.get('summary')
        # rollups carry the verdict; row budget can be tighter than 2×top_n
        out[key] = rows[:max(1, top_n * 4)]
    else:
        size = max(1, top_n * 2)
        start = (page - 1) * size
        out[key] = rows[start:start + size]
        if start:
            out['page'] = page
    if inv.get('note'):
        out['note'] = inv['note']
    return out


def _domain_users(client, host, domain, name_filter, detail, top_n, page):
    # The RICH listing (/api/users): carries email + userProfile, so owner
    # logins resolve to addresses — the lean inventory projection does not.
    flt = (name_filter or '').lower()
    data = client.get('/api/users', host=host)
    users = data.get('users') or []
    if flt:
        users = [u for u in users if flt in (u.get('login') or '').lower()
                 or flt in (u.get('displayName') or '').lower()
                 or flt in (u.get('email') or '').lower()]
    size = max(1, top_n * 2)
    start = (page - 1) * size
    out = {'userStats': data.get('userStats'),
           'userCount': len(users),
           'disabled': [u.get('login') for u in users if not u.get('enabled', True)][:top_n],
           'noEmail': [u.get('login') for u in users
                       if not str(u.get('email') or '').strip()][:top_n],
           'users': [shaping.pick(u, ('login', 'displayName', 'email', 'enabled',
                                      'userProfile', 'groups'))
                     for u in users[start:start + size]]}
    if start:
        out['page'] = page
    return out


def _domain_api_keys(client, host, domain, name_filter, detail, top_n, page):
    inv = client.get('/api/tools/admin-actions/inventory', host=host,
                     params={'domain': 'api-keys'})
    return {'personal': (inv.get('personal') or [])[:max(1, top_n * 2)],
            'global': (inv.get('global') or [])[:max(1, top_n * 2)],
            'note': ('Key secrets are never shown. api-key-delete is IRREVERSIBLE; '
                     'the toolkit refuses its own key.')}


def _domain_connections_usage(client, host, domain, name_filter, detail, top_n, page):
    # Same memoized scan the health scorer and the Connections Insights page
    # use — one fetch path, no drift.
    from . import health as health_mod
    data = health_mod.fetch_connection_usages(client, host)
    if data is None:
        return {'error': {'code': 'scan-failed',
                          'message': 'The connection-usage scan did not complete — the '
                                     'backend may still be scanning; retry in a few '
                                     'minutes.'}}
    flt = (name_filter or '').lower()
    active = set(data.get('activeTriggerProjects') or [])

    def shape(rows, count_key):
        rows = sorted(rows or [], key=lambda r: -(r.get('projectCount') or 0))
        if flt:
            rows = [r for r in rows if flt in (r.get('name') or '').lower()]
        shaped = []
        for r in rows[:max(1, top_n)]:
            by_project = {}
            for p in r.get('projects') or []:
                pk = p.get('projectKey')
                row = by_project.setdefault(pk, {
                    'projectKey': pk, 'projectName': p.get('projectName'),
                    'owner': p.get('owner'), 'ownerEmail': p.get('ownerEmail'),
                    'objects': 0, 'activeTrigger': pk in active})
                row['objects'] += 1
            plist = sorted(by_project.values(), key=lambda x: -x['objects'])
            if flt:  # one named connection → full (paged) project list
                size = max(1, top_n * 2)
                start = (page - 1) * size
                plist = plist[start:start + size]
            else:
                plist = plist[:5]
            shaped.append({'name': r.get('name'), 'type': r.get('type'),
                           'projectCount': r.get('projectCount'),
                           count_key: r.get(count_key), 'projects': plist})
        return shaped

    out = {
        'datasetUsages': shape(data.get('datasetUsages'), 'datasetCount'),
        'llmUsages': shape(data.get('llmUsages'), 'recipeCount'),
        'activeTriggerProjects': sorted(active)[:top_n * 3],
        'scan': {'scannedProjectCount': data.get('scannedProjectCount'),
                 'failedProjectCount': data.get('failedProjectCount'),
                 'scanErrorCount': len(data.get('scanErrors') or [])},
        'localFilesystemUsageCount': len(data.get('localFilesystemUsages') or []),
    }
    if data.get('projectUrlBase'):
        out['projectUrlBase'] = data['projectUrlBase']
        out['linkNote'] = 'Project deep link = projectUrlBase + <projectKey> + /'
    if detail == 'fs':
        fs_rows = data.get('localFilesystemUsages') or []
        if flt:
            fs_rows = [r for r in fs_rows if flt in (r.get('connection') or '').lower()]
        out['localFilesystemUsages'] = fs_rows[:max(1, top_n * 2)]
    return out


def _domain_app_instances(client, host, domain, name_filter, detail, top_n, page):
    # Synchronous twin of the App Instances page sweep (macro-attributed
    # creator recipes; the public API strips them). Heavy: per-App_-recipe
    # settings fetches across every scannable project.
    data = client.get('/api/app-instances/summary', host=host, heavy=True)
    flt = (name_filter or '').lower()
    instances = data.get('instances') or []
    recipes = data.get('appRecipes') or []
    if flt:
        instances = [i for i in instances
                     if flt in (i.get('projectKey') or '').lower()
                     or flt in str(i.get('generatingAppId') or '').lower()
                     or flt in str(i.get('creatorFullId') or '').lower()]
        recipes = [r for r in recipes if flt in (r.get('fullId') or '').lower()
                   or flt in str(r.get('appId') or '').lower()]
    return {
        'apps': (data.get('apps') or [])[:top_n],
        'instanceCount': len(data.get('instances') or []),
        'instances': [shaping.pick(i, ('projectKey', 'generatingAppId',
                                       'creatorFullId', 'creatorProjectKey',
                                       'creatorRecipeName', 'isTemporary',
                                       'owner', 'lastModified'))
                      for i in instances[:max(1, top_n * 2)]],
        'keepInstanceOn': [shaping.pick(r, ('fullId', 'appId'))
                           for r in recipes if r.get('keepInstance') is True][:top_n],
        'attribution': data.get('attribution'),
        'orphanDeterminable': data.get('orphanDeterminable'),
        'orphanKeys': data.get('orphanKeys'),
        'attachedKeys': (data.get('attachedKeys') or [])[:max(1, top_n * 2)],
        'failedProjectCount': len(data.get('failedProjects') or []),
        'note': ('orphanKeys = instance projects whose creating App recipe no longer '
                 'exists (macro-attributed, never guessed from labels); cleanup = '
                 'project-delete (backup-first). orphanDeterminable=false means '
                 'UNKNOWN, not zero — never propose deletion then. keepInstanceOn '
                 'recipes are the CAUSE of instance sprawl; the flag is toggled on '
                 'the App Instances page, not by a catalogued action.'),
    }


def _compact_unknown(payload, top_n):
    """Defensive shape for macro payloads whose exact schema may drift: keep
    scalars, cap lists (+ report their true length), summarize large dicts."""
    out = {}
    for k, v in (payload or {}).items():
        if isinstance(v, list):
            out[k] = v[:top_n]
            if len(v) > top_n:
                out['%sCount' % k] = len(v)
        elif isinstance(v, dict) and len(v) > top_n * 2:
            out['%sKeys' % k] = sorted(str(x) for x in v)[:top_n]
            out['%sCount' % k] = len(v)
        else:
            out[k] = v
    return out


def _domain_adoption(client, host, domain, name_filter, detail, top_n, page):
    if detail in ('inventory', 'events'):
        data = client.get('/api/adoption/%s' % detail, host=host, heavy=True)
        if data.get('ok') is False:
            return {'error': {'code': 'adoption-%s-failed' % detail,
                              'message': str(data.get('error') or 'unavailable')[:300]}}
        return {'detail': detail, 'data': _compact_unknown(data, top_n)}
    data = client.get('/api/adoption', host=host, heavy=True)
    return {
        'totals': data.get('totals'),
        'licensing': data.get('licensing'),
        'profileCounts': data.get('profileCounts'),
        'repeatBuilders': data.get('repeatBuilders'),
        'monthlyTrend': (data.get('monthlyTrend') or [])[-12:],
        'builderStats': (data.get('builderStats') or [])[:top_n],
        'projectRowCount': len(data.get('projectRows') or []),
    }


def _domain_settings(client, host, domain, name_filter, detail, top_n, page):
    """Redacted read twin of settings-set: same families stripped, same secret
    regex masking scalars, so the agent can inspect exactly what it may
    mutate (plus the read-only operational families)."""
    from .policies import settings_paths as _sp
    raw = client.get('/api/settings/raw', host=host)
    if not isinstance(raw, dict) or not raw:
        return {'error': {'code': 'settings-unavailable',
                          'message': 'general settings payload unavailable'}}
    visible = {}
    for k, v in raw.items():
        kl = str(k).lower()
        if any(sub in kl for sub in _sp.BLOCKED_FIRST_SEGMENT_SUBSTRINGS):
            continue
        if _sp.BLOCKED_SEGMENT_RE.search(str(k)):
            continue
        visible[k] = v
    visible = _sp.redact_secrets(visible)
    flt = (name_filter or '').strip().lower()
    if flt:
        hits = {k: visible[k] for k in visible if flt in k.lower()}
        if not hits:
            return {'note': 'No top-level settings key contains %r.' % name_filter,
                    'keys': sorted(visible)}
        return {'matched': sorted(hits),
                'settings': _compact_unknown(hits, top_n)}
    container = visible.get('containerSettings') or {}
    if not isinstance(container, dict):
        container = {}
    execs = [e for e in (container.get('executionConfigs') or [])
             if isinstance(e, dict)]
    imp = visible.get('impersonation')
    return {
        'note': ('Redacted DSS general settings (secret values masked; '
                 'auth/SSO/security/licensing families stripped — the same '
                 'policy as settings-set). name_filter=<key substring> '
                 'returns full subtrees.'),
        'keys': sorted(visible),
        'cgroups': visible.get('cgroupSettings'),
        'limits': {'limits': visible.get('limits'),
                   'maxRunningActivities': visible.get('maxRunningActivities'),
                   'maxRunningActivitiesPerJob':
                       visible.get('maxRunningActivitiesPerJob')},
        'containerExec': {
            'defaultExecutionConfig': container.get('defaultExecutionConfig'),
            'configCount': len(execs),
            'configs': [dict(shaping.pick(e, ('name', 'type')),
                             kubernetesResources=((e.get('kubernetesRuntimeConfig') or {})
                                                  .get('kubernetesResources')))
                        for e in execs[:top_n]]},
        'spark': _compact_unknown(visible.get('sparkSettings') or {}, top_n),
        'internalDatabase': _compact_unknown(
            visible.get('internalDatabase') or {}, top_n),
        'impersonation': (imp.get('enabled') if isinstance(imp, dict) else imp),
    }


def _domain_cost_detail(client, host, domain, name_filter, detail, top_n, page):
    data = client.get('/api/cru', host=host, heavy=True)
    if not data.get('ok', True):
        return {'error': {'code': 'cru-unavailable',
                          'message': str(data.get('error') or 'CRU data unavailable')}}
    span = data.get('span') or {}
    out = {
        'span': shaping.pick(span, ('firstTs', 'lastTs', 'files', 'cruRecords')),
        'spanNote': ('Coverage = the instance\'s rolling audit-log retention; '
                     'older usage is not observable from this tool.'),
        'totals': data.get('totals'),
        'topProjects': shaping.top_rows(data.get('projects'), 'cpuH', top_n,
                                        keys=('projectKey', 'cpuH', 'memGBh',
                                              'llmUSD', 'llmTokens', 'records')),
        'byConnection': (data.get('connections') or [])[:top_n],
        'idleResources': (data.get('idleResources') or [])[:top_n],
        'llmModels': (data.get('llmModels') or [])[:top_n],
        'daily': (data.get('daily') or [])[-30:],
    }
    k8s = data.get('k8s') or {}
    if k8s.get('clusters'):
        out['k8sClusters'] = k8s['clusters'][:top_n]
    flt = (name_filter or '').strip().lower()
    if flt:
        row = next((p for p in data.get('projects') or []
                    if str(p.get('projectKey') or '').lower() == flt), None)
        if row is None:
            out['projectNote'] = ('No CRU rows for project %r in the covered span.'
                                  % name_filter)
        else:
            # nested byUser/byConnection/byModel breakdowns (server-capped)
            out['project'] = row
    return out


_DOMAIN_HANDLERS = {row['name']: globals()[row['handler']] for row in _registry.DOMAINS}


def config_inspect(client, host='local', domain='connections', detail=None,
                   name_filter=None, top_n=15, page=1, fields=None):
    """Registry-backed domain inspection. domain='list' returns the manifest;
    every other domain dispatches to its registered handler. `detail` unlocks
    per-domain drill-downs (see the manifest's detail lists)."""
    if domain in ('list', 'manifest'):
        return shaping.enforce_budget({
            'host': host or 'local',
            'domains': _registry.manifest(),
            'note': ("Call config_inspect(domain=<name>) to fetch one. heavy=true "
                     'domains run scans (minutes, possibly scan_running). fixActions '
                     'name the actuator actions that remediate findings there.')})
    handler = _DOMAIN_HANDLERS.get(domain)
    if handler is None:
        return {'error': {'code': 'bad-input',
                          'message': "domain must be one of: %s — or 'list' for the "
                                     'full manifest'
                                     % ', '.join(sorted(_DOMAIN_HANDLERS))}}
    try:
        page = max(1, int(page or 1))
    except (TypeError, ValueError):
        page = 1
    result = handler(client, host, domain, name_filter, detail, top_n, page)
    if 'error' in result:
        return result
    out = {'host': host or 'local', 'domain': domain}
    out.update(result)
    if fields:
        keep = {str(f) for f in fields} | {'host', 'domain', 'note', 'page',
                                           'truncated', 'truncation_note'}
        out = {k: v for k, v in out.items() if k in keep}
    return shaping.enforce_budget(out)


# ── log-errors / log-tail ────────────────────────────────────────────────────


def _raw_tail_text(client, host):
    """backend.log tail text (last ~100K chars). /api/logs/raw-tail is a JSON
    route ({text, chars}) — reading it as plain text would hand the model one
    giant JSON-escaped line."""
    data = client.get('/api/logs/raw-tail', host=host)
    if isinstance(data, dict):
        return data.get('text') or ''
    return str(data or '')


def log_errors(client, host='local', top_n=10, pattern=None, raw=False):
    """Grouped backend.log error signatures; optional raw-tail grep."""
    out = {'host': host or 'local'}
    if raw or pattern:
        text = _raw_tail_text(client, host)
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


def log_tail(client, host='local', lines=200, pattern=None, log='backend.log'):
    """Raw backend.log tail / grep (v1: backend.log only)."""
    if log != 'backend.log':
        return {'error': {'code': 'bad-input',
                          'message': "log %r is not available — this tool serves "
                                     "'backend.log' only (v1)." % log}}
    try:
        lines = max(1, min(int(lines or 200), 1000))
    except (TypeError, ValueError):
        lines = 200
    text = _raw_tail_text(client, host)
    all_lines = text.splitlines()
    out = {'host': host or 'local', 'log': log,
           'windowNote': 'Window = the last ~100K characters of the log '
                         '(%d lines available).' % len(all_lines)}
    if pattern:
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            return {'error': {'code': 'bad-input', 'message': 'Invalid pattern: %s' % exc}}
        hits = [l for l in all_lines if rx.search(l)]
        out['grep'] = {'pattern': pattern, 'matchCount': len(hits), 'lines': hits[-lines:]}
    else:
        out['lines'] = all_lines[-lines:]
    return shaping.enforce_budget(out)


# ── toolkit-get (registry-backed: atk_agent_common.read_registry) ───────────


def _paged_lists(payload, top_n, page):
    """Window every top-level list to the (page, top_n) slice, recording true
    lengths; summarize oversized dicts (the _compact_unknown doctrine, paged)."""
    out = {}
    start = (page - 1) * top_n
    for k, v in (payload or {}).items():
        if isinstance(v, list):
            window = v[start:start + top_n]
            out[k] = window
            if len(v) > len(window) or start:
                out['%sCount' % k] = len(v)
        elif isinstance(v, dict) and len(v) > top_n * 3:
            out['%sKeys' % k] = sorted(str(x) for x in v)[:top_n]
            out['%sCount' % k] = len(v)
        else:
            out[k] = v
    return out


_READ_ENDPOINTS = {row['name']: row for row in _read_registry.ENDPOINTS}


def toolkit_get(client, endpoint='list', host='local', params=None, fields=None,
                top_n=15, page=1):
    """Registry-backed read bridge. endpoint='list' returns the manifest;
    every other endpoint fetches its whitelisted backend route and windows
    the result (fields[] projection, top_n/page list paging, output budget)."""
    if endpoint in ('list', 'manifest'):
        return shaping.enforce_budget({
            'endpoints': _read_registry.manifest(),
            'note': ('Call toolkit_get(endpoint=<name>) to fetch one. heavy=true '
                     'endpoints run scans (minutes, possibly scan_running). '
                     'localOnly endpoints reject host≠local.')})
    row = _READ_ENDPOINTS.get(endpoint)
    if row is None:
        return {'error': {'code': 'bad-input',
                          'message': "endpoint must be one of: %s — or 'list' for "
                                     'the manifest' % ', '.join(sorted(_READ_ENDPOINTS))}}
    if row['local_only'] and (host or 'local') != 'local':
        return {'error': {'code': 'bad-input',
                          'message': 'endpoint %r is local-only — drop the host '
                                     'argument.' % endpoint}}
    params = params if isinstance(params, dict) else {}
    unknown = sorted(set(params) - set(row['params']))
    if unknown:
        return {'error': {'code': 'bad-input',
                          'message': 'endpoint %r does not accept param(s): %s. '
                                     'Allowed: %s'
                                     % (endpoint, ', '.join(unknown),
                                        ', '.join(row['params']) or '(none)')}}
    try:
        top_n = max(1, min(int(top_n or 15), 100))
    except (TypeError, ValueError):
        top_n = 15
    try:
        page = max(1, int(page or 1))
    except (TypeError, ValueError):
        page = 1
    data = client.get(row['path'], host=host, params=params or None,
                      heavy=row['heavy'], progress_path=row['progress_path'])
    if not isinstance(data, dict):
        data = {'data': data}
    out = {'host': host or 'local', 'endpoint': endpoint}
    out.update(_paged_lists(data, top_n, page))
    if page > 1:
        out['page'] = page
    if fields:
        keep = {str(f) for f in fields} | {'host', 'endpoint', 'page', 'note',
                                           'truncated', 'truncation_note'}
        out = {k: v for k, v in out.items() if k in keep or k.endswith('Count')}
    return shaping.enforce_budget(out)


# ── list-capabilities (meta) ─────────────────────────────────────────────────


def list_capabilities(client):
    """Ground-truth capability map: sensors + actions with their LIVE gate
    states, the master kill-switch, and the toolkit page map."""
    # Deferred imports: actuator/actions pull the whole action layer, which
    # tools_impl must not load at import time (webapp backend imports us).
    from . import action_gates
    from . import actions as actions_registry
    from . import actuator as actuator_mod
    from .remediation_map import AUTO_EXCLUDED
    gates = action_gates.gates(client)
    autonomous = action_gates.autonomous(client)
    sensors = [{'name': name, 'enabled': bool(gates.get(name, True)),
                'autonomous': bool(gates.get(name, True))
                and bool(autonomous.get(name, True))}
               for name in SENSOR_DESCRIPTIONS]
    local_only = set(actuator_mod._LOCAL_ONLY_ACTIONS)
    actions = [{'action': action,
                'mode': actions_registry.MODES[action],
                'risk': actions_registry.ALL_RISKS[action],
                'enabled': bool(gates.get(action, False)),
                'autoCapable': action not in AUTO_EXCLUDED,
                'autonomous': bool(gates.get(action, False))
                and action not in AUTO_EXCLUDED
                and bool(autonomous.get(action, False)),
                'batchable': action in actions_registry.BATCHABLE,
                'localOnly': action in local_only}
               for action in actuator_mod.ACTIONS]
    out = {
        'killSwitchOn': bool(client.settings.get('enable_red_actions')),
        'sensors': sensors,
        'actions': actions,
        'toolkitPages': dict(_read_registry.TOOLKIT_PAGES),
        'note': ('Sensors are read-only and need no confirmation. Disabled actions '
                 'can be enabled by an admin in Agents → Permissions. '
                 'autonomous=true means the NIGHTLY triage agent may plan and run '
                 'that capability without a human in the loop (admin-granted per '
                 'action there; autoCapable=false = python-run, never autonomous). '
                 'killSwitchOn=false means NO action can execute regardless of '
                 'per-action gates. toolkitPages maps webapp pages for pointing '
                 'users at the right screen.'),
    }
    # Deliberately roomier budget than data sensors: trimming this list would
    # make the ground-truth map lie about what exists.
    return shaping.enforce_budget(out, budget=shaping.MAX_OUTPUT_BYTES * 2)


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
            {'what': b.get('label'), 'bucketKey': b.get('name'),
             'location': b.get('location'), 'size': _fmt_bytes(b.get('bytes') or 0)}
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
        failing = []
        for p in probes:
            if p.get('ok'):
                continue
            row = shaping.pick(p, ('id', 'errorClass', 'errorSummary'))
            # DNS-dead endpoint ⇒ the cluster is gone and this is a stale
            # attachment — mechanically mappable to cluster-detach. Other
            # error classes (auth, timeout) need investigation first.
            if p.get('errorClass') == 'dns':
                row['suggestedAction'] = 'cluster-detach'
            failing.append(row)
        out['reachability'] = {
            'ok': sum(1 for p in probes if p.get('ok')),
            'failing': failing[:top_n],
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


def _config_inspect_description():
    """Generated from the domain registry — the model can only ever learn
    domains that actually exist (drift-proof by construction)."""
    names = '|'.join(row['name'] for row in _registry.DOMAINS)
    scoped = '/'.join(row['name'] for row in _registry.DOMAINS if row['project_scoped'])
    heavy = ', '.join(row['name'] for row in _registry.DOMAINS if row['heavy'])
    return (
        "Inspect one domain of a host's configuration/insights (host, domain=%s, "
        "detail, name_filter, top_n, page, fields[]). domain='list' returns the CHEAP "
        'manifest of every domain — summary, filters, detail modes, output fields and '
        'the actuator actions that can fix findings there; call it first when unsure. '
        "domain='projects' resolves a project label to its KEY (name_filter = "
        'key/label substring). For %s, name_filter is the PROJECT KEY (required). '
        "datasets detail='usage' adds flow lineage plus the 'unreferenced'/"
        "'deleteCandidates' rollups (grounding for dataset-delete). connections-usage "
        'rows carry per-project owner + ownerEmail (grounding for owner outreach). '
        'Heavy domains (%s) run scans — minutes, possibly scan_running. fields=[...] '
        'keeps only those top-level output keys; page walks long listings.'
        % (names, scoped, heavy))


# Read-only sensor catalog: {tool name: LLM-facing description}. The single
# source for agent_tools.build_langchain_tools AND the backend's Agent
# Settings catalog endpoint (which must not import langchain). Every name is
# a function in this module.
SENSOR_DESCRIPTIONS = {
    'list_hosts': (
        'List the DSS hosts this toolkit can reach (id, label, url). '
        'probe=true also checks reachability. Call this before targeting a non-local host.'),
    'instance_health': (
        'Health snapshot of one DSS host (host, sections list of system/sanity/java/issues/score, '
        'top_n, include_score). include_score=true adds the 0-100 UI health score but forces '
        'heavy scans (may return scan_running — retry later).'),
    'compute_cost': (
        'Compute + LLM cost from CRU audit records (host, group_by=project|user|context_type, '
        'top_n). Span limited to audit retention — check the span field.'),
    'config_inspect': _config_inspect_description(),
    'log_errors': (
        'Backend.log access (host, top_n): grouped error signatures by default; '
        'raw=true returns the raw log tail verbatim; pattern=<regex> greps the raw '
        'tail (case-insensitive). Use raw/pattern whenever the user asks to see or '
        'search backend.log itself.'),
    'log_tail': (
        'Raw backend.log tail (host, lines≤1000, pattern=<regex> to grep, log — v1 '
        "serves backend.log only). Window = the log's last ~100K characters. Use "
        'log_errors for grouped error signatures.'),
    'storage_footprint': (
        'Project storage totals, largest projects, inactive+large cleanup candidates '
        '(host, top_n, min_size_gb). Heavy scan — may return scan_running.'),
    'k8s_health': (
        'K8s clusters for a host: states + reachability sweep; cluster=<id> runs a deep audit.'),
    'db_health': (
        'RuntimeDB PostgreSQL health (host, view=overview|tables|per-project, connection, top_n).'),
    'toolkit_get': _read_registry.tool_description(),
    'list_capabilities': (
        'Ground-truth capability map: every sensor and admin action with its LIVE '
        'enablement gate state, the master kill-switch, and a map of every toolkit '
        'webapp page. Answer "can you X?" / "what can you do?" from this — never '
        "claim a capability is missing without checking it first."),
}
