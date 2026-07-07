"""Python port of the admin-toolkit UI health score.

Ported from resource/frontend/src/hooks/useHealthScore.ts @ 87eaa67 (v0.4.629,
1011 lines) — `calculateHealthScore` plus the LIVE-mode ParsedData assembly the
UI performs around it (useApiDataLoader: `{...overview}` spread; phase2:
JavaMemoryParser + GeneralSettingsParser). Parity is enforced by
scripts/agents/score_parity.py (±2 pts vs the real TS run on live payloads).

Faithfully-ported live-mode quirks (do NOT "fix" these without changing the TS
first, or parity breaks):
  * `systemLimits['Max open files']` never exists live (the overview serves
    'open files') → the open-files factor always scores 100.
  * `enabledSettings['User Isolation']` never exists live (raw settings keys
    are camelCase) → the user-isolation factor never fires.
  * scoreFilesystem reads ALL mounts (incl. tmpfs) but skips rows whose
    parsed usage is <= 0 or > 100.
(The historical `'No' is truthy` cgroups quirk was FIXED on both twins in the
TAM-rubric recalibration: anything but an explicit 'Yes' counts as disabled.)
"""

import math
import re

# Calibrated against the TAM severity rubric (docs/agent-workflows/
# severity-rubric.md): infra/runtime dominate; security_isolation is
# zero-weighted (its cgroups penalties moved into runtime_config).
_DEFAULT_WEIGHTS = {
    'system_capacity': 0.30,
    'runtime_config': 0.30,
    'code_envs': 0.15,
    'project_footprint': 0.15,
    'version_currency': 0.10,
    'security_isolation': 0.0,
}

# Rubric size lines (interview C26/E46): code env >5GB, project >10GB.
_LARGE_CODE_ENV_GB = 5
_LARGE_PROJECT_GB = 10

# Rules honoring the per-item admin whitelist (false-positive doctrine).
WHITELISTABLE_RULES = ('project-size', 'project-code-envs', 'code-env-size',
                       'python-env-lifecycle', 'disk-usage', 'connection-broken',
                       'exec-config-resources', 'sanity-check')

# Issue keys safe to relay to LLM consumers (triage sweep + instance_health
# score). whitelistRule/whitelistItems stay OUT — suppression happens upstream,
# so those keys would only annotate LIVE findings with hedging fuel. items and
# details are the plain enrichment fields action targets are built from.
ISSUE_PICK_KEYS = ('id', 'severity', 'category', 'title', 'recommendation',
                   'description', 'value', 'items', 'details')


def _whitelist_lookup(entries):
    """entries: [{rule, item, ...}] → membership predicate (rule, item).
    Tracks matches on `check.matched` so the score can report how many
    findings the whitelist suppressed (count only, never the items)."""
    keys = {'%s %s' % (e.get('rule'), e.get('item')) for e in (entries or [])}
    matched = set()

    def check(rule, item):
        key = '%s %s' % (rule, item)
        if key in keys:
            matched.add(key)
            return True
        return False

    check.matched = matched
    return check

_SEVERITY_ORDER = {'critical': 0, 'warning': 1, 'info': 2, 'good': 3}


# ── TS helper ports ──────────────────────────────────────────────────────────


def _parse_memory_to_mb(value):
    m = re.match(r'^(\d+)([gmk]?)$', str(value or '').lower())
    if not m:
        return 0
    num, unit = int(m.group(1)), m.group(2)
    if unit == 'g':
        return num * 1024
    if unit == 'k':
        return num / 1024
    return num


def _parse_percentage(value):
    try:
        return int(str(value or '').replace('%', '')) or 0
    except ValueError:
        return 0


def _parse_memory_size_to_gb(value):
    m = re.match(r'^(\d+)\s*(kB|MB|GB|B)?$', str(value or ''), re.IGNORECASE)
    if not m:
        return 0
    num = int(m.group(1))
    unit = (m.group(2) or 'kB').lower()
    return {'gb': num, 'mb': num / 1024, 'kb': num / (1024 * 1024),
            'b': num / (1024 ** 3)}.get(unit, num / (1024 * 1024))


def _combine_enabled_scores(components, default_score=100):
    active = [c for c in components if c['enabled'] and isinstance(c['score'], (int, float))
              and math.isfinite(c['score']) and c['weight'] > 0]
    if not active:
        return default_score
    weight_sum = sum(c['weight'] for c in active)
    if weight_sum <= 0:
        return default_score
    weighted = sum(c['score'] * c['weight'] for c in active) / weight_sum
    return max(0, min(100, weighted))


def _issue(id, category, severity, title, recommendation='', **extra):
    out = {'id': id, 'category': category, 'severity': severity, 'title': title}
    if recommendation:
        out['recommendation'] = recommendation
    out.update(extra)
    return out


# ── factor scorers (1:1 with the TS functions) ───────────────────────────────


def _score_python_version(code_envs, dss_version, t, is_whitelisted):
    """Lifecycle-aware Python scorer (port of the TS scorePythonVersion):
    in-use deprecated env → 20/critical; in-use 3.8 on DSS>=14 → 60/warning;
    unreferenced deprecated env → info delete candidate, no score drag."""
    if not code_envs:
        return 75, []
    prefixes = [p.strip() for p in
                str((t or {}).get('deprecatedPythonPrefixes') or '2.,3.6,3.7').split(',')
                if p.strip()]
    m = re.match(r'\d+', str(dss_version or ''))
    dss_major = int(m.group(0)) if m else 0

    def deprecated(version):
        return any(str(version or '').startswith(p) for p in prefixes)

    def in_use(env):
        count = env.get('usageCount')
        return count > 0 if isinstance(count, (int, float)) else True

    def names(envs):
        return [str(e.get('name')) for e in envs]

    python_envs = [e for e in code_envs if e.get('language') != 'r'
                   and not is_whitelisted('python-env-lifecycle', e.get('name'))]
    deprecated_in_use = [e for e in python_envs if deprecated(e.get('version')) and in_use(e)]
    plan_in_use = [e for e in python_envs
                   if str(e.get('version') or '').startswith('3.8') and in_use(e)] \
        if dss_major >= 14 else []
    deprecated_unused = [e for e in python_envs if deprecated(e.get('version')) and not in_use(e)]

    issues = []
    score = 100

    def preview(envs, with_version=True):
        names = ['%s (%s)' % (e.get('name'), e.get('version')) if with_version
                 else str(e.get('name')) for e in envs[:5]]
        more = ' and %d more' % (len(envs) - 5) if len(envs) > 5 else ''
        return ', '.join(names) + more

    if deprecated_in_use:
        score = 20
        n = len(deprecated_in_use)
        issues.append(_issue(
            'python-lifecycle-critical', 'version_currency', 'critical',
            '%d in-use code env%s on deprecated Python' % (n, 's' if n > 1 else ''),
            'Migrate the projects using these environments to a supported Python version now.',
            description='%s. These Python versions are deprecated by Dataiku — projects using '
                        'them must migrate now.' % preview(deprecated_in_use),
            value=n, threshold='not %s' % '/'.join(prefixes),
            whitelistRule='python-env-lifecycle', whitelistItems=names(deprecated_in_use)))
    elif plan_in_use:
        score = 60
        n = len(plan_in_use)
        issues.append(_issue(
            'python-lifecycle-plan', 'version_currency', 'warning',
            '%d in-use code env%s on Python 3.8 (deprecated in DSS 14)' % (n, 's' if n > 1 else ''),
            'Plan the migration to a supported Python version before the removal release.',
            description='%s. DSS 14 deprecates Python 3.8; support will be removed in a later '
                        'release.' % preview(plan_in_use, with_version=False),
            value=n,
            whitelistRule='python-env-lifecycle', whitelistItems=names(plan_in_use)))

    if deprecated_unused:
        n = len(deprecated_unused)
        issues.append(_issue(
            'python-lifecycle-cleanup', 'version_currency', 'info',
            '%d unreferenced code env%s on deprecated Python' % (n, 's' if n > 1 else ''),
            'Delete these unused environments (backup-first via the code-env cleaner).',
            description='%s. Nothing references these environments — they are delete '
                        'candidates, not migration work.' % preview(deprecated_unused),
            value=n,
            whitelistRule='python-env-lifecycle', whitelistItems=names(deprecated_unused)))

    return score, issues


def _score_spark_version(spark_settings):
    if not spark_settings:
        return 75, None
    version = spark_settings.get('Spark Version')
    if not version or not isinstance(version, str):
        return 75, None
    m = re.match(r'^(\d+)', version)
    if not m:
        return 75, None
    if int(m.group(1)) < 3:
        return 50, _issue('spark-version-old', 'version_currency', 'warning',
                          'Spark %s is outdated' % version,
                          'Upgrade to Spark 3.x for better performance and features.', value=version)
    return 100, None


def _score_memory_availability(memory_info):
    if not memory_info:
        return 75, None
    total_str = memory_info.get('MemTotal') or memory_info.get('total')
    avail_str = memory_info.get('MemAvailable') or memory_info.get('available')
    if not total_str or not avail_str:
        return 75, None
    total_gb = _parse_memory_size_to_gb(total_str)
    avail_gb = _parse_memory_size_to_gb(avail_str)
    if total_gb <= 0:
        return 75, None
    pct = avail_gb / total_gb * 100
    if pct < 10:
        return 30, _issue('memory-critical', 'system_capacity', 'critical',
                          'Memory critically low (%.0f%% available)' % pct,
                          'Investigate memory usage, consider adding more RAM or reducing load.',
                          value='%.0f%%' % pct)
    if pct < 30:
        return 70, _issue('memory-low', 'system_capacity', 'warning',
                          'Memory running low (%.0f%% available)' % pct,
                          'Monitor memory usage and consider scaling resources.', value='%.0f%%' % pct)
    return 100, None


def _score_filesystem(filesystem_info, is_whitelisted):
    if not filesystem_info:
        return 75, []
    worst = 100
    issues = []
    for fs in filesystem_info:
        usage = _parse_percentage(fs.get('Use%'))
        mount = fs.get('Mounted on') or fs.get('Filesystem')
        if usage > 100 or usage <= 0:
            continue
        if is_whitelisted('disk-usage', str(mount)):
            continue
        available = 100 - usage
        if available < 10:
            worst = min(worst, 30)
            issues.append(_issue('disk-critical-%s' % mount, 'system_capacity', 'critical',
                                 'Disk %d%% full on %s' % (usage, mount),
                                 'Free up disk space or expand storage immediately.',
                                 value='%d%%' % usage,
                                 whitelistRule='disk-usage', whitelistItems=[str(mount)]))
        elif available < 20:
            worst = min(worst, 70)
            issues.append(_issue('disk-warning-%s' % mount, 'system_capacity', 'warning',
                                 'Disk %d%% used on %s' % (usage, mount),
                                 'Monitor disk usage and plan for cleanup or expansion.',
                                 value='%d%%' % usage,
                                 whitelistRule='disk-usage', whitelistItems=[str(mount)]))
    return worst, issues


def _feature_details(disabled_features):
    """[{name, settingsPath, proposedValue, sensitive?}] for the issue — the
    exact settings-set targets an agent needs (cap 10). Entries without a
    settings path (e.g. Deployer Client mode) carry name only."""
    details = []
    for name, entry in list(disabled_features.items())[:10]:
        row = {'name': name}
        path = (entry or {}).get('settingsPath')
        if path:
            row['settingsPath'] = path
            row['proposedValue'] = (entry or {}).get('proposedValue', True)
        if (entry or {}).get('sensitive'):
            row['sensitive'] = True
        details.append(row)
    return details


def _score_disabled_features(disabled_features):
    if not disabled_features:
        return 100, None
    count = len(disabled_features)
    if count == 0:
        return 100, None
    details = _feature_details(disabled_features)
    if count <= 2:
        return 80, _issue('features-disabled-few', 'runtime_config', 'info',
                          '%d feature%s disabled' % (count, 's' if count > 1 else ''),
                          'Review disabled features to ensure they are intentionally disabled.',
                          value=count, details=details)
    if count <= 5:
        return 60, _issue('features-disabled-several', 'runtime_config', 'warning',
                          '%d features disabled' % count,
                          'Review disabled features and enable those needed for your use case.',
                          value=count, details=details)
    return 40, _issue('features-disabled-many', 'runtime_config', 'warning',
                      '%d features disabled' % count,
                      'Review disabled features list and discuss with your admin or Dataiku support.',
                      value=count, details=details)


def _score_security_settings(parsed):
    """Port of scoreSecuritySettings incl. the remaining JS quirks (see module
    docstring). The cgroups check is the FIXED version: anything but an
    explicit 'Yes' counts as disabled (issues live in runtime_config)."""
    issues = []
    total = 100
    checks = 0
    enabled_settings = parsed.get('enabledSettings')
    if enabled_settings:
        impersonation = enabled_settings.get('User Isolation')  # never present live
        if impersonation is not None:
            checks += 1
            if not impersonation:
                total -= 25
                issues.append(_issue('impersonation-disabled', 'security_isolation', 'warning',
                                     'User isolation disabled',
                                     'Consider enabling user isolation for better security in multi-user environments.'))
    cgroup_settings = parsed.get('cgroupSettings')
    if cgroup_settings is not None:  # {} is truthy in JS ⇒ `if (parsedData.cgroupSettings)` passes for {}
        checks += 1
        if str(cgroup_settings.get('Enabled') or '') != 'Yes':
            total -= 15
            issues.append(_issue('cgroups-disabled', 'runtime_config', 'warning',
                                 'CGroups not enabled',
                                 'Enable CGroups memory limits for kernels and jobs.',
                                 description='CGroups resource limits are not configured — '
                                             'runaway kernels/jobs can take down the host.'))
        empty_targets = cgroup_settings.get('Empty Target Types')
        if empty_targets and str(empty_targets).strip() != '':
            total -= 20
            issues.append(_issue('cgroups-empty-targets', 'runtime_config', 'warning',
                                 'CGroups empty target types',
                                 'Configure cgroup settings for all target types.'))
    system_limits = parsed.get('systemLimits')
    if system_limits:
        max_open = system_limits.get('Max open files')  # never present live ('open files')
        if max_open:
            checks += 1
            m = re.match(r'\d+', str(max_open))  # parseInt semantics: leading digits only
            limit = int(m.group(0)) if m else 0
            if limit < 65535:
                total -= 20
                issues.append(_issue('open-files-low', 'system_capacity', 'critical',
                                     'Open files limit too low (%d)' % limit,
                                     'Increase the open files limit in system configuration.',
                                     value=limit))
    if checks == 0:
        return 75, []
    return max(0, total), issues


def _score_runtime_database(general_settings):
    """Port of the cap-aware TS scorer: settings loaded but no PostgreSQL
    connection type ⇒ embedded H2 ⇒ score 0 + cap-runtime-db critical."""
    if not general_settings:
        return 75, None
    db_type = ((general_settings.get('internalDatabase') or {})
               .get('connection') or {}).get('type')
    if db_type == 'PostgreSQL':
        return 100, None
    label = db_type or 'internal H2'
    return 0, _issue('cap-runtime-db', 'runtime_config', 'critical',
                     'Runtime database is %s, not PostgreSQL' % label,
                     'Migrate the DSS runtime database to PostgreSQL immediately.',
                     value=label, threshold='PostgreSQL')


def _score_java_memory(java_memory_settings):
    if not java_memory_settings:
        return 75, []
    issues = []
    total = 100
    checks = 0
    for key, name in (('BACKEND', 'Backend'), ('JEK', 'JEK'), ('FEK', 'FEK')):
        value = java_memory_settings.get(key)
        if value:
            checks += 1
            mb = _parse_memory_to_mb(value)
            if 0 < mb < 2048:
                total -= 15
                issues.append(_issue('java-memory-%s' % key.lower(), 'runtime_config', 'warning',
                                     '%s heap < 2GB (%s)' % (name, value),
                                     'Increase %s heap size in install.ini or environment settings.' % name,
                                     value=value))
    if checks == 0:
        return 75, []
    return max(0, total), issues


def _normalize_code_env_risk(count):
    if count <= 1:
        return 0.0
    if count == 2:
        return 0.45
    if count == 3:
        return 0.75
    return 1.0


def _normalize_project_size_index(total_gb, avg_gb):
    if total_gb >= 40:
        return 1.0
    abs_norm = math.log1p(min(max(total_gb, 0), 40)) / math.log1p(40)
    ratio = total_gb / max(avg_gb, 0.1)
    rel_norm = math.log1p(min(max(ratio, 0), 4)) / math.log1p(4)
    return max(0.0, min(1.0, 0.6 * abs_norm + 0.4 * rel_norm))


def _group_issue(id, category, severity, count, noun_phrase, detail, recommendation, names,
                 whitelist_rule=None, whitelist_items=None):
    preview = ', '.join(names[:5])
    more = ' and %d more' % (len(names) - 5) if len(names) > 5 else ''
    plural = 's' if count > 1 else ''
    extra = {}
    if whitelist_rule and whitelist_items:
        extra = {'whitelistRule': whitelist_rule, 'whitelistItems': whitelist_items}
    return _issue(id, category, severity,
                  ('%d project%s ' % (count, plural)) + noun_phrase,
                  recommendation, description='%s%s. %s' % (preview, more, detail), **extra)


def _score_code_env_size(code_envs, is_whitelisted):
    """Rubric C26: a single code env >5GB is a finding (whitelist-subject)."""
    if not code_envs:
        return 100, []
    sized = [e for e in code_envs
             if isinstance(e.get('sizeBytes'), (int, float)) and e['sizeBytes'] > 0]
    if not sized:
        return 100, []
    large = [e for e in sized
             if e['sizeBytes'] / (1024 ** 3) > _LARGE_CODE_ENV_GB
             and not is_whitelisted('code-env-size', e.get('name'))]
    if not large:
        return 100, []
    preview = ', '.join('%s (%.1fGB)' % (e.get('name'), e['sizeBytes'] / (1024 ** 3))
                        for e in large[:5])
    more = ' and %d more' % (len(large) - 5) if len(large) > 5 else ''
    n = len(large)
    return max(40, 100 - n * 15), [_issue(
        'code-env-size-group', 'code_envs', 'warning',
        '%d code env%s over %dGB' % (n, 's' if n > 1 else '', _LARGE_CODE_ENV_GB),
        'Slim the environment (or whitelist it if the size is legitimate, e.g. CUDA).',
        description='%s%s. Environments this large are usually over-pinned or carry unused '
                    'heavy packages.' % (preview, more),
        value=n, threshold='<=%dGB' % _LARGE_CODE_ENV_GB,
        whitelistRule='code-env-size', whitelistItems=[str(e.get('name')) for e in large])]


def _score_code_env_complexity(project_footprint, is_whitelisted):
    if not project_footprint:
        return 75, []
    risks, issues = [], []
    critical, critical_keys, warning, info = [], [], [], []
    for row in project_footprint:
        if is_whitelisted('project-code-envs', row.get('projectKey')):
            continue
        count = row.get('codeEnvCount') or 0
        risks.append(_normalize_code_env_risk(count))
        if count >= 4:
            critical.append('%s (%d)' % (row.get('projectKey'), count))
            critical_keys.append(row.get('projectKey'))
        elif count == 3:
            warning.append(row.get('projectKey'))
        elif count == 2:
            info.append(row.get('projectKey'))
    if critical:
        issues.append(_group_issue('project-codenv-critical-group', 'code_envs', 'critical',
                                   len(critical), 'have 4+ code envs',
                                   'Each extra code environment multiplies size, fragility, deployment time, and failure surface.',
                                   'Consolidate toward a single code environment per project.', critical,
                                   whitelist_rule='project-code-envs', whitelist_items=critical_keys))
    if warning:
        issues.append(_group_issue('project-codenv-warning-group', 'code_envs', 'warning',
                                   len(warning), 'have 3 code envs',
                                   'Multiple code environments increase maintenance overhead and drift risk.',
                                   'Reduce project code environments to 1-2, ideally 1.', warning,
                                   whitelist_rule='project-code-envs', whitelist_items=warning))
    if info:
        issues.append(_group_issue('project-codenv-info-group', 'code_envs', 'info',
                                   len(info), 'have 2 code envs',
                                   'Two code environments already increase rebuild and deployment complexity.',
                                   'Consolidate to a single environment when possible.', info,
                                   whitelist_rule='project-code-envs', whitelist_items=info))
    avg_risk = sum(risks) / len(risks) if risks else 0
    return max(0, min(100, 100 * (1 - avg_risk))), issues


def _score_project_size_pressure(project_footprint, summary, is_whitelisted):
    if not project_footprint:
        return 75, []
    avg_gb = (summary or {}).get('instanceAvgProjectGB')
    if avg_gb is None:
        avg_gb = sum((r.get('totalBytes') or 0) / (1024 ** 3) for r in project_footprint) / len(project_footprint)
    risks, issues = [], []
    huge, huge_keys, large, large_keys, critical, high = [], [], [], [], [], []
    for row in project_footprint:
        if is_whitelisted('project-size', row.get('projectKey')):
            continue
        total_gb = row.get('totalGB')
        if total_gb is None:
            total_gb = (row.get('totalBytes') or 0) / (1024 ** 3)
        size_risk = row.get('projectSizeIndex')
        if not isinstance(size_risk, (int, float)):
            size_risk = _normalize_project_size_index(total_gb, avg_gb)
        risks.append(size_risk)
        if total_gb >= 40:
            huge.append('%s (%.1fGB)' % (row.get('projectKey'), total_gb))
            huge_keys.append(row.get('projectKey'))
            continue
        if total_gb > _LARGE_PROJECT_GB:
            large.append('%s (%.1fGB)' % (row.get('projectKey'), total_gb))
            large_keys.append(row.get('projectKey'))
        health = row.get('projectSizeHealth')
        if health == 'angry-red':
            critical.append(row.get('projectKey'))
        elif health == 'red':
            high.append(row.get('projectKey'))
    if huge:
        issues.append(_group_issue('project-size-huge-group', 'project_footprint', 'critical',
                                   len(huge), 'exceed 40GB',
                                   'Project size above 40GB is a severe storage and operational risk.',
                                   'Prioritize cleanup or archival for these projects.', huge,
                                   whitelist_rule='project-size', whitelist_items=huge_keys))
    if large:
        issues.append(_group_issue('project-size-large-group', 'project_footprint', 'warning',
                                   len(large), 'exceed %dGB' % _LARGE_PROJECT_GB,
                                   'Projects this large usually hide accumulating webapp logs or '
                                   'filesystem data that belongs on block storage.',
                                   'Inspect what fills each project (or whitelist it if the size '
                                   'is legitimate).', large,
                                   whitelist_rule='project-size', whitelist_items=large_keys))
    if critical:
        issues.append(_group_issue('project-size-critical-group', 'project_footprint', 'critical',
                                   len(critical), 'have critical relative size',
                                   'These projects are significantly larger than peers on this instance.',
                                   'Review managed data/folders and archive or purge stale assets.', critical,
                                   whitelist_rule='project-size', whitelist_items=critical))
    if high:
        issues.append(_group_issue('project-size-high-group', 'project_footprint', 'warning',
                                   len(high), 'have high project size',
                                   'These projects are above instance norm and add storage pressure.',
                                   'Review large managed datasets/folders for cleanup.', high,
                                   whitelist_rule='project-size', whitelist_items=high))
    avg_risk = sum(risks) / len(risks) if risks else 0
    return max(0, min(100, 100 * (1 - avg_risk))), issues


def _score_connection_health(health, dataset_usages, llm_usages, is_whitelisted):
    """Port of scoreConnectionHealth: broken actively-used connections (rubric
    always-lead critical). Issues only — cap-connection-broken clamps via the
    existing cap logic. Usage arrays None ⇒ the usage scan did not run ⇒
    failing connections surface as 'unverified'. Health rows carry no recency,
    so only "currently failing" is knowable (documented rubric deviation)."""
    failing = [c for c in (health or []) if c.get('status') == 'fail'
               and not is_whitelisted('connection-broken', c.get('name'))]
    if not failing:
        return []

    def preview(names):
        more = ' and %d more' % (len(names) - 5) if len(names) > 5 else ''
        return ', '.join(names[:5]) + more

    if dataset_usages is None and llm_usages is None:
        names = [str(c.get('name')) for c in failing]
        n = len(failing)
        return [_issue(
            'connection-broken-unverified', 'connections', 'warning',
            '%d connection%s failing their test (usage unverified)' % (n, 's' if n > 1 else ''),
            'Run the usage scan on the Connections → Insights page to confirm impact.',
            description='%s. The connection usage scan has not completed, so it is unknown '
                        'whether projects actively depend on these connections.' % preview(names),
            value=n, items=names,
            whitelistRule='connection-broken', whitelistItems=names)]

    ds_by_name = {u.get('name'): u for u in (dataset_usages or [])}
    llm_by_name = {u.get('name'): u for u in (llm_usages or [])}
    used, used_preview, unused = [], [], []
    for conn in failing:
        name = str(conn.get('name'))
        ds = ds_by_name.get(name) or {}
        llm = llm_by_name.get(name) or {}
        project_count = (ds.get('projectCount') or 0) + (llm.get('projectCount') or 0)
        object_count = ((ds.get('datasetCount') or 0) + (ds.get('recipeCount') or 0)
                        + (llm.get('datasetCount') or 0) + (llm.get('recipeCount') or 0))
        if project_count > 0 or object_count > 0:
            used.append(name)
            used_preview.append('%s (%d project%s)'
                                % (name, project_count, '' if project_count == 1 else 's'))
        else:
            unused.append(name)

    issues = []
    if used:
        n = len(used)
        issues.append(_issue(
            'cap-connection-broken', 'connections', 'critical',
            '%d actively-used connection%s failing their test' % (n, 's' if n > 1 else ''),
            'Repair these connections immediately (credentials, network, or endpoint).',
            description='%s. Projects actively depend on these connections — datasets and '
                        'recipes on them are broken for every user right now.'
                        % preview(used_preview),
            value=n, items=used,
            whitelistRule='connection-broken', whitelistItems=used))
    if unused:
        n = len(unused)
        issues.append(_issue(
            'connection-broken-unused', 'connections', 'info',
            '%d unused connection%s failing their test' % (n, 's' if n > 1 else ''),
            'Repair or delete these unused connections.',
            description='%s. No project references these connections — a failing test alone '
                        'is low-impact mess.' % preview(unused),
            value=n, items=unused,
            whitelistRule='connection-broken', whitelistItems=unused))
    return issues


def _is_set_number(v):
    """TS `typeof v === 'number'` — bools are not numbers there."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _score_exec_config_resources(configs, is_whitelisted):
    """Port of scoreExecConfigResources: K8s exec configs missing memory
    requests/limits (unset OR <=0 = "not set", rules/dss_drift.py semantics).
    CPU-missing is description-only — it neither counts nor escalates."""
    def unset(v):
        return not _is_set_number(v) or v <= 0

    k8s_configs = [c for c in configs if str(c.get('type') or '').upper() == 'KUBERNETES']
    offending = [c for c in k8s_configs
                 if (unset(c.get('memRequestMB')) or unset(c.get('memLimitMB')))
                 and not is_whitelisted('exec-config-resources', c.get('name'))]
    if not offending:
        return 100, []
    cpu_missing = sum(1 for c in offending
                      if unset(c.get('cpuRequest')) or unset(c.get('cpuLimit')))
    names = [str(c.get('name')) for c in offending]
    preview = ', '.join(names[:5])
    more = ' and %d more' % (len(names) - 5) if len(names) > 5 else ''
    cpu_note = ' %d of them also lack CPU requests/limits.' % cpu_missing if cpu_missing else ''
    n = len(offending)
    return max(30, 100 - n * 25), [_issue(
        'exec-config-resources-group', 'runtime_config', 'warning',
        '%d Kubernetes exec config%s without memory requests/limits' % (n, 's' if n > 1 else ''),
        'Set memRequestMB and memLimitMB on each containerized execution config.',
        description='%s%s. Containers on these configs run with unbounded memory — the '
                    'scheduler cannot protect the node, so a single heavy job can evict or '
                    'OOM-kill its neighbors.%s' % (preview, more, cpu_note),
        value=n, whitelistRule='exec-config-resources', whitelistItems=names)]


_SANITY_MAX_ISSUES = 5
_SANITY_DESC_MAX = 280


def _score_sanity_check(messages, is_whitelisted):
    """Port of scoreSanityCheck: one issue per distinct surviving ERROR code
    (warning) / WARNING code (info), ERRORs first, stable code sort, capped at
    5. Severity comes from the whitelist-FILTERED messages, never from raw
    sanityCheckMaxSeverity."""
    surviving = [m for m in messages
                 if m.get('severity') in ('ERROR', 'WARNING')
                 and not is_whitelisted('sanity-check', str(m.get('code')))]
    if not surviving:
        return 100, []

    def by_code(severity):
        grouped = {}
        for m in surviving:
            if m.get('severity') != severity:
                continue
            code = str(m.get('code'))
            if code in grouped:
                grouped[code]['count'] += 1
            else:
                grouped[code] = {'first': m, 'count': 1}
        return sorted(grouped.items())

    error_codes = by_code('ERROR')
    warning_codes = by_code('WARNING')
    issues = []

    def push(code, entry, kind):
        if len(issues) >= _SANITY_MAX_ISSUES:
            return
        raw = str(entry['first'].get('message') or entry['first'].get('details') or '')
        description = raw[:_SANITY_DESC_MAX] + '…' if len(raw) > _SANITY_DESC_MAX else raw
        issues.append(_issue(
            'sanity-%s-%s' % (kind, code), 'runtime_config',
            'warning' if kind == 'error' else 'info',
            'Sanity check %s: %s' % (kind, entry['first'].get('title') or code),
            'Review this finding on the DSS sanity check (Administration → Maintenance).',
            description=description, value=entry['count'],
            whitelistRule='sanity-check', whitelistItems=[code]))

    for code, entry in error_codes:
        push(code, entry, 'error')
    for code, entry in warning_codes:
        push(code, entry, 'warning')

    return (40 if error_codes else 75), issues


# ── live-mode ParsedData assembly (ports of the loader + parsers) ────────────

_CGROUP_SKIP_KEYS = {
    'enabled', 'cgroupsVersion', 'cgroupsV2Controllers', 'hierarchiesMountPoint',
    'cgroups', 'jobExecutionKernels', 'edaRecipes', 'metricsChecks', 'deploymentHooks',
    'devLambdaServer', 'customPythonDataAccessComponents',
}


def _parse_cgroup_settings(raw):
    """Port of GeneralSettingsParser.parseCgroupsSettings (fields the score reads)."""
    cgroups = (raw or {}).get('cgroupSettings')
    if not cgroups:
        return {}
    out = {'Enabled': 'Yes' if cgroups.get('enabled') is True else 'No'}
    empty_names = []
    for key, value in cgroups.items():
        if key in _CGROUP_SKIP_KEYS:
            continue
        if isinstance(value, dict) and 'targets' in value and isinstance(value['targets'], list):
            if not value['targets']:
                empty_names.append(key)
    if empty_names:
        out['Empty Target Types'] = ', '.join(empty_names)
    return out


def _check_disabled_features(raw):
    """Port of GeneralSettingsParser.checkDisabledFeatures — only the feature
    NAMES matter for scoring (the score counts keys). Each entry also carries
    the exact general-settings path (+ proposedValue True) so agents can name
    a concrete settings-set target; impersonation is marked `sensitive`
    (flipping it has UIF-wide consequences — never a casual toggle)."""
    d = raw or {}
    disabled = {}

    def add(name, settings_path=None, sensitive=False):
        entry = {'status': 'Disabled'}
        if settings_path:
            entry['settingsPath'] = settings_path
            entry['proposedValue'] = True
        if sensitive:
            entry['sensitive'] = True
        disabled[name] = entry

    ai = d.get('aiDrivenAnalyticsSettings')
    if ai:
        if 'enabled' in ai:
            if ai.get('enabled') is False:
                add('AI Assistants', 'aiDrivenAnalyticsSettings.enabled')
        else:
            if not ai.get('prepareAICompletionEnabled'):
                add('AI: Prepare Completion', 'aiDrivenAnalyticsSettings.prepareAICompletionEnabled')
            if not ai.get('aiGenerateSQLEnabled'):
                add('AI: Generate SQL', 'aiDrivenAnalyticsSettings.aiGenerateSQLEnabled')
            if not ai.get('aiExplanationsEnabled'):
                add('AI: Explanations', 'aiDrivenAnalyticsSettings.aiExplanationsEnabled')
            if not ai.get('storiesAIEnabled'):
                add('AI: Stories', 'aiDrivenAnalyticsSettings.storiesAIEnabled')
    if (d.get('codeAssistantSettings') or {}).get('codeAssistantEnabled') is False:
        add('Code Assistant', 'codeAssistantSettings.codeAssistantEnabled')
    if (d.get('askDataikuSettings') or {}).get('enabled') is False:
        add('Ask Dataiku', 'askDataikuSettings.enabled')
    if (d.get('sparkSettings') or {}).get('sparkEnabled') is False:
        add('Spark', 'sparkSettings.sparkEnabled')
    if (d.get('containerSettings') or {}).get('cdeEnabled') is False:
        add('Container Execution (CDE)', 'containerSettings.cdeEnabled')
    if (d.get('containerSettings') or {}).get('k8sEnabled') is False:
        add('Kubernetes', 'containerSettings.k8sEnabled')
    if (d.get('cgroupSettings') or {}).get('enabled') is False:
        add('CGroups', 'cgroupSettings.enabled')
    if (d.get('governIntegrationSettings') or {}).get('enabled') is False:
        add('Govern Integration', 'governIntegrationSettings.enabled')
    if (d.get('popularDatasetsSettings') or {}).get('enablePopularDatasets') is False:
        add('Popular Datasets', 'popularDatasetsSettings.enablePopularDatasets')
    if (d.get('impersonation') or {}).get('enabled') is False:
        add('Impersonation', 'impersonation.enabled', sensitive=True)
    mode = (d.get('deployerClientSettings') or {}).get('mode')
    if mode and mode != 'LOCAL':
        disabled['Deployer Client'] = {'status': 'Mode: %s' % mode}
    return disabled


def _extract_exec_resource_configs(raw_settings):
    """Port of utils/execResources.ts extractExecResourceConfigs: absent or
    malformed executionConfigs ⇒ None (skip semantics); present-but-empty ⇒ []
    (scores 100). Resource fields are FLAT on each config."""
    container = (raw_settings or {}).get('containerSettings') or {}
    configs = container.get('executionConfigs')
    if not isinstance(configs, list):
        return None

    def num(v):
        return v if _is_set_number(v) else None

    out = []
    for cfg in configs:
        if not isinstance(cfg, dict):
            continue
        out.append({
            'name': str(cfg.get('name')) if cfg.get('name') is not None else '',
            'type': str(cfg.get('type')) if cfg.get('type') is not None else None,
            'memRequestMB': num(cfg.get('memRequestMB')),
            'memLimitMB': num(cfg.get('memLimitMB')),
            'cpuRequest': num(cfg.get('cpuRequest')),
            'cpuLimit': num(cfg.get('cpuLimit')),
        })
    return out


def build_parsed_data(overview, raw_settings, java_memory_settings, code_envs_payload,
                      footprint_payload, sanity_messages=None, connection_health=None,
                      connection_dataset_usages=None, connection_llm_usages=None):
    """Assemble the ParsedData subset calculateHealthScore reads, exactly as the
    live UI loader does. The new-input kwargs default to None ⇒ key absent
    (= TS `undefined` = the corresponding score component silently skips)."""
    parsed = dict(overview or {})  # initialData = {...overview}
    if (overview or {}).get('sparkVersion'):
        parsed['sparkSettings'] = {'Spark Version': overview['sparkVersion']}
    parsed['javaMemorySettings'] = java_memory_settings or {}
    parsed['generalSettings'] = raw_settings or {}
    parsed['enabledSettings'] = {k: v for k, v in (raw_settings or {}).items()
                                 if isinstance(v, bool)}
    parsed['cgroupSettings'] = _parse_cgroup_settings(raw_settings)
    parsed['disabledFeatures'] = _check_disabled_features(raw_settings)
    parsed['codeEnvs'] = (code_envs_payload or {}).get('codeEnvs') or []
    parsed['projectFootprint'] = (footprint_payload or {}).get('projects') or []
    parsed['projectFootprintSummary'] = (footprint_payload or {}).get('summary') or {}
    exec_configs = _extract_exec_resource_configs(raw_settings)
    if exec_configs is not None:
        parsed['execResourceConfigs'] = exec_configs
    if sanity_messages is not None:
        parsed['sanityCheck'] = sanity_messages
    if connection_health is not None:
        parsed['connectionHealth'] = connection_health
    if connection_dataset_usages is not None:
        parsed['connectionDatasetUsages'] = connection_dataset_usages
    if connection_llm_usages is not None:
        parsed['connectionLlmUsages'] = connection_llm_usages
    return parsed


# ── the score itself ─────────────────────────────────────────────────────────


def calculate_health_score(parsed, thresholds=None, whitelist=None):
    """Port of calculateHealthScore with all factor toggles enabled (the
    default), weights from threshold settings when provided, and the per-item
    finding whitelist applied inside the scorers."""
    t = thresholds or {}
    is_whitelisted = _whitelist_lookup(whitelist)
    weights = dict(_DEFAULT_WEIGHTS)
    for cat, key in (('code_envs', 'weightCodeEnvs'), ('project_footprint', 'weightProjectFootprint'),
                     ('system_capacity', 'weightSystemCapacity'),
                     ('security_isolation', 'weightSecurityIsolation'),
                     ('version_currency', 'weightVersionCurrency'),
                     ('runtime_config', 'weightRuntimeConfig')):
        if key in t:
            weights[cat] = t[key]

    categories = []
    all_issues = []

    # VERSION CURRENCY
    py_score, py_issues = _score_python_version(parsed.get('codeEnvs'), parsed.get('dssVersion'),
                                                t, is_whitelisted)
    spark_score, spark_issue = _score_spark_version(parsed.get('sparkSettings'))
    vc_score = _combine_enabled_scores([
        {'enabled': True, 'score': py_score, 'weight': 0.7},
        {'enabled': True, 'score': spark_score, 'weight': 0.3},
    ])
    vc_issues = list(py_issues) + ([spark_issue] if spark_issue else [])
    categories.append({'category': 'version_currency', 'label': 'Version Currency',
                       'score': vc_score, 'weight': weights['version_currency'], 'issues': vc_issues})
    all_issues.extend(vc_issues)

    # SYSTEM CAPACITY
    mem_score, mem_issue = _score_memory_availability(parsed.get('memoryInfo'))
    fs_score, fs_issues = _score_filesystem(parsed.get('filesystemInfo'), is_whitelisted)
    _, sec_issues = _score_security_settings(parsed)
    open_files_issue = next((i for i in sec_issues if i['id'] == 'open-files-low'), None)
    open_files_score = 30 if open_files_issue else 100
    sc_score = _combine_enabled_scores([
        {'enabled': True, 'score': mem_score, 'weight': 0.4},
        {'enabled': True, 'score': fs_score, 'weight': 0.4},
        {'enabled': True, 'score': open_files_score, 'weight': 0.2},
    ])
    sc_issues = list(fs_issues)
    if mem_issue:
        sc_issues.append(mem_issue)
    if open_files_issue:
        sc_issues.append(open_files_issue)

    # Cap rules on the data mount (rubric A5/A6): DIP_HOME on NFS; data mount
    # >= dataMountCriticalPct full. dipHomeStorage absent (older remote
    # toolkits) ⇒ the rules silently skip.
    dip_home = parsed.get('dipHomeStorage') or {}
    if dip_home:
        fs_type = str(dip_home.get('fsType') or '').lower()
        if fs_type.startswith('nfs'):
            sc_issues.append(_issue(
                'cap-diphome-nfs', 'system_capacity', 'critical',
                'DIP_HOME is on NFS (%s)' % dip_home.get('fsType'),
                'Move DIP_HOME to local or block storage.',
                description='The DSS data directory (%s) sits on an NFS mount (%s). NFS under '
                            'DIP_HOME causes pervasive performance and locking problems.'
                            % (dip_home.get('path') or 'DIP_HOME', dip_home.get('mount') or '?'),
                value=dip_home.get('fsType')))
        data_mount_critical = t.get('dataMountCriticalPct', 75)
        used_pct = dip_home.get('usedPct')
        if isinstance(used_pct, (int, float)) and used_pct >= data_mount_critical:
            sc_issues.append(_issue(
                'cap-data-mount-full', 'system_capacity', 'critical',
                'Data mount %d%% full (%s)' % (used_pct, dip_home.get('mount') or 'DIP_HOME'),
                'Free space now (job logs, exports, large managed folders) or expand the volume.',
                description='The mount holding DIP_HOME is at %d%% — past the %d%% critical '
                            'line. DSS misbehaves unpredictably when the data disk fills.'
                            % (used_pct, data_mount_critical),
                value='%d%%' % used_pct, threshold='<%d%%' % data_mount_critical))

    categories.append({'category': 'system_capacity', 'label': 'System Capacity',
                       'score': sc_score, 'weight': weights['system_capacity'], 'issues': sc_issues})
    all_issues.extend(sc_issues)

    # SECURITY ISOLATION (0-weight by default; issues still surface)
    iso_issue = next((i for i in sec_issues if i['id'] == 'impersonation-disabled'), None)
    cg_disabled = next((i for i in sec_issues if i['id'] == 'cgroups-disabled'), None)
    cg_empty = next((i for i in sec_issues if i['id'] == 'cgroups-empty-targets'), None)
    si_issues = []
    si_score = 100
    if iso_issue:
        si_score -= 25
        si_issues.append(iso_issue)
    si_score = max(0, si_score)
    categories.append({'category': 'security_isolation', 'label': 'Security Isolation',
                       'score': si_score, 'weight': weights['security_isolation'], 'issues': si_issues})
    all_issues.extend(si_issues)

    # CODE ENVIRONMENTS
    ce_score, ce_issues = _score_code_env_complexity(parsed.get('projectFootprint'), is_whitelisted)
    ces_score, ces_issues = _score_code_env_size(parsed.get('codeEnvs'), is_whitelisted)
    ce_combined = _combine_enabled_scores([
        {'enabled': True, 'score': ce_score, 'weight': 0.7},
        {'enabled': True, 'score': ces_score, 'weight': 0.3},
    ])
    ce_all = list(ce_issues) + list(ces_issues)
    categories.append({'category': 'code_envs', 'label': 'Code Envs',
                       'score': ce_combined, 'weight': weights['code_envs'], 'issues': ce_all})
    all_issues.extend(ce_all)

    # PROJECT FOOTPRINT
    pf_score, pf_issues = _score_project_size_pressure(parsed.get('projectFootprint'),
                                                       parsed.get('projectFootprintSummary'),
                                                       is_whitelisted)
    categories.append({'category': 'project_footprint', 'label': 'Project Footprint',
                       'score': pf_score, 'weight': weights['project_footprint'], 'issues': pf_issues})
    all_issues.extend(pf_issues)

    # RUNTIME CONFIG (cgroups component relocated here from security_isolation)
    df_score, df_issue = _score_disabled_features(parsed.get('disabledFeatures'))
    jm_score, jm_issues = _score_java_memory(parsed.get('javaMemorySettings'))
    rd_score, rd_issue = _score_runtime_database(parsed.get('generalSettings'))
    cgroups_score = 100
    rc_issues = []
    if cg_disabled:
        cgroups_score -= 60
        rc_issues.append(cg_disabled)
    if cg_empty:
        cgroups_score -= 20
        rc_issues.append(cg_empty)
    cgroups_score = max(0, cgroups_score)

    # Exec-config resources + DSS sanity check (rubric-mandated inputs).
    # Input absent (old payloads, zip mode, sanity 501) ⇒ component disabled ⇒
    # the 4×0.20 legacy components renormalize back to the old 4×0.25 behavior.
    exec_configs = parsed.get('execResourceConfigs')
    exec_enabled = exec_configs is not None
    er_score, er_issues = (_score_exec_config_resources(exec_configs, is_whitelisted)
                           if exec_enabled else (100, []))
    sanity_msgs = parsed.get('sanityCheck')
    sanity_enabled = sanity_msgs is not None
    sn_score, sn_issues = (_score_sanity_check(sanity_msgs, is_whitelisted)
                           if sanity_enabled else (100, []))

    rc_score = _combine_enabled_scores([
        {'enabled': True, 'score': df_score, 'weight': 0.20},
        {'enabled': True, 'score': jm_score, 'weight': 0.20},
        {'enabled': True, 'score': rd_score, 'weight': 0.20},
        {'enabled': True, 'score': cgroups_score, 'weight': 0.20},
        {'enabled': exec_enabled, 'score': er_score, 'weight': 0.10},
        {'enabled': sanity_enabled, 'score': sn_score, 'weight': 0.10},
    ])
    rc_issues.extend(jm_issues)
    if df_issue:
        rc_issues.append(df_issue)
    if rd_issue:
        rc_issues.append(rd_issue)
    if exec_enabled:
        rc_issues.extend(er_issues)
    if sanity_enabled:
        rc_issues.extend(sn_issues)

    # Cap rule (rubric A1): impersonation on but cgroups not configured.
    impersonation_on = ((parsed.get('generalSettings') or {}).get('impersonation')
                        or {}).get('enabled') is True
    if cg_disabled and impersonation_on:
        rc_issues.append(_issue(
            'cap-cgroups-missing', 'runtime_config', 'critical',
            'Multi-user isolation is on but cgroups are not configured',
            'Configure cgroup memory limits for kernels and jobs now.',
            description='User isolation (impersonation) is enabled but cgroup resource limits '
                        'are not — a single runaway kernel or job can take down the host for '
                        'every user.'))

    categories.append({'category': 'runtime_config', 'label': 'Runtime Config',
                       'score': rc_score, 'weight': weights['runtime_config'], 'issues': rc_issues})
    all_issues.extend(rc_issues)

    # CONNECTIONS (issues only — category stays zero-weighted, no score bar;
    # cap-connection-broken clamps via the cap logic below)
    if parsed.get('connectionHealth') is not None:
        # Usage data counts as "present" only once the usage scan COMPLETED —
        # the UI twin checks connectionUsageLoading; this twin never sets it,
        # so absent-lifecycle means the arrays are authoritative.
        usage_loading = parsed.get('connectionUsageLoading')
        usage_ready = parsed.get('connectionDatasetUsages') is not None and (
            usage_loading is None or usage_loading.get('phase') == 'done')
        ds_usages = parsed.get('connectionDatasetUsages') if usage_ready else None
        llm_usages = (parsed.get('connectionLlmUsages') if parsed.get('connectionLlmUsages')
                      is not None else []) if usage_ready else None
        all_issues.extend(_score_connection_health(parsed.get('connectionHealth'),
                                                   ds_usages, llm_usages, is_whitelisted))

    overall = sum(c['score'] * c['weight'] for c in categories)

    unique, seen = [], set()
    for issue in all_issues:
        if issue['id'] not in seen:
            seen.add(issue['id'])
            unique.append(issue)

    # Critical cap (interview K100): any cap-* issue clamps the overall score
    # into the critical band.
    capped = any(i['id'].startswith('cap-') for i in unique)
    if capped:
        overall = min(overall, t.get('healthCriticalCapScore', 49))

    unique.sort(key=lambda i: _SEVERITY_ORDER.get(i['severity'], 9))

    critical_below = t.get('healthCriticalBelow', 50)
    warning_below = t.get('healthWarningBelow', 80)
    status = 'healthy'
    if overall < critical_below:
        status = 'critical'
    elif overall < warning_below:
        status = 'warning'

    return {
        'overall': round(overall),
        'status': status,
        'categories': categories,
        'issues': unique,
        'criticalCount': sum(1 for i in unique if i['severity'] == 'critical'),
        'warningCount': sum(1 for i in unique if i['severity'] == 'warning'),
        'infoCount': sum(1 for i in unique if i['severity'] == 'info'),
        'capped': capped,
        'whitelistSuppressed': len(is_whitelisted.matched),
    }


# ── one-call convenience for tools/triage ────────────────────────────────────


def fetch_host_whitelist(client, host='local'):
    """Whitelist entries applying to `host` (hub-stored; host='*' matches all).
    Best-effort: an older backend without /api/whitelist yields []."""
    try:
        entries = (client.get('/api/whitelist') or {}).get('entries') or []
    except Exception:
        return []
    eff = host or 'local'
    return [e for e in entries if (e.get('host') or 'local') in (eff, '*')]


def fetch_sanity_messages(client, host='local'):
    """Messages from DSS's own sanity check, or None (= component skips) on
    any failure — including the 501 the backend serves on DSS < 14.4."""
    try:
        data = client.get('/api/sanity-check', host=host)
        msgs = (data or {}).get('messages')
        return msgs if isinstance(msgs, list) else None
    except Exception:
        return None


def fetch_connection_health(client, host='local', read_timeout=120, wall_timeout=300):
    """Per-connection rows from the (epoch-memoized) health SSE, or None (=
    component skips) on any failure or wall-budget overrun. Rows carry
    `status` ('ok'/'fail'/'skipped') — never an `ok` key. Proxies may buffer
    SSE, hence the read/wall budgets: the sweep must never block on this."""
    import json as json_mod
    import time as time_mod
    path = '/api/connections/health'
    try:
        resp = client._do('GET', path, host=client._effective_host(path, host),
                          timeout=read_timeout, stream=True)
        client._raise_for_status(resp, path, host)
        rows = []
        started = time_mod.time()
        event, data_lines = None, []
        try:
            for raw in resp.iter_lines(decode_unicode=True):
                if time_mod.time() - started > wall_timeout:
                    return None
                line = (raw or '').strip('\r')
                if line == '':
                    if data_lines:
                        try:
                            payload = json_mod.loads('\n'.join(data_lines))
                        except ValueError:
                            payload = None
                        if event == 'conn' and isinstance(payload, dict):
                            rows.append(payload)
                        elif event == 'error':
                            return None
                    event, data_lines = None, []
                elif line.startswith('event:'):
                    event = line[6:].strip()
                elif line.startswith('data:'):
                    data_lines.append(line[5:].strip())
        finally:
            resp.close()
        return rows
    except Exception:
        return None


def fetch_connection_usages(client, host='local'):
    """Final payload of the (epoch-memoized) usages SSE ({datasetUsages,
    llmUsages, ...}), or None on any failure."""
    try:
        return client.stream_final('/api/connections/usages', host=host)
    except Exception:
        return None


def score_host(client, host='local', collect=None):
    """Fetch every score input from the backend for `host` and compute the
    score. Heavy inputs (code-envs, footprint) may raise ScanTimeout — callers
    surface that as scan_running. Sanity + connection health are best-effort
    (None ⇒ their components skip); the expensive connection-usage scan runs
    ONLY when at least one connection test failed (escalate-on-demand), so a
    single memoized scan enriches every failing connection.

    `collect`: optional dict the caller passes to receive the raw fetched
    payloads (snapshot zips) — filled on success only."""
    from .tools_impl import _parse_java_memory

    overview = client.get('/api/overview', host=host)
    thresholds = client.get('/api/settings/threshold-defaults')
    raw_settings = client.get('/api/settings/raw', host=host)
    try:
        java = _parse_java_memory(client.get_text('/api/java-memory', host=host))
    except Exception:
        java = {}
    code_envs = client.get('/api/code-envs', host=host, heavy=True,
                           progress_path='/api/code-envs/progress')
    footprint = client.get('/api/project-footprint', host=host, heavy=True,
                           progress_path='/api/project-footprint/progress')
    sanity = fetch_sanity_messages(client, host)
    conn_health = fetch_connection_health(client, host)
    usages = None
    ds_usages = llm_usages = None
    if conn_health is not None and any(c.get('status') == 'fail' for c in conn_health):
        usages = fetch_connection_usages(client, host)
        if usages is not None:
            ds_usages = usages.get('datasetUsages') or []
            llm_usages = usages.get('llmUsages') or []
    whitelist = fetch_host_whitelist(client, host)
    parsed = build_parsed_data(overview, raw_settings, java, code_envs, footprint,
                               sanity_messages=sanity, connection_health=conn_health,
                               connection_dataset_usages=ds_usages,
                               connection_llm_usages=llm_usages)
    score = calculate_health_score(parsed, thresholds, whitelist=whitelist)
    if collect is not None:
        collect.update({
            'overview': overview,
            'settings-raw': raw_settings,
            'java-memory': java,
            'code-envs': code_envs,
            'project-footprint': footprint,
            'sanity': sanity,
            'connection-health': conn_health,
            'connection-usages': usages,
            'whitelist': whitelist,
            'score': score,
        })
    return score
