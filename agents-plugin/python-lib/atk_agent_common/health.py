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
  * `cgroupSettings['Enabled']` is the STRING 'Yes'/'No'; 'No' is truthy in
    JS, so the cgroups-disabled penalty only fires when the raw settings have
    no cgroupSettings key at all (parser returns {} → key undefined → falsy).
  * scoreFilesystem reads ALL mounts (incl. tmpfs) but skips rows whose
    parsed usage is <= 0 or > 100.
"""

import math
import re

_DEFAULT_WEIGHTS = {
    'code_envs': 0.35,
    'project_footprint': 0.30,
    'system_capacity': 0.15,
    'security_isolation': 0.10,
    'version_currency': 0.05,
    'runtime_config': 0.05,
}

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


def _python_version_supported(version):
    m = re.search(r'(\d+)\.(\d+)', str(version or ''))
    if not m:
        return False
    return int(m.group(1)) >= 3 and int(m.group(2)) >= 10


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


def _score_python_version(code_envs):
    if not code_envs:
        return 75, None
    total = len(code_envs)
    supported = sum(1 for e in code_envs if _python_version_supported(e.get('version')))
    unsupported = total - supported
    pct = supported / total * 100
    if pct >= 90:
        return 100, None
    if pct >= 70:
        return 80, _issue('python-versions-aging', 'version_currency', 'info',
                          '%d of %d code envs on older Python' % (unsupported, total),
                          'Consider upgrading older code environments to Python 3.10 or later.',
                          value='%.0f%%' % pct)
    if pct >= 50:
        return 60, _issue('python-versions-old', 'version_currency', 'warning',
                          '%d of %d code envs on older Python' % (unsupported, total),
                          'Upgrade code environments to Python 3.10 or later.', value='%.0f%%' % pct)
    if pct >= 30:
        return 40, _issue('python-versions-critical', 'version_currency', 'warning',
                          '%d of %d code envs on older Python' % (unsupported, total),
                          'Prioritize upgrading code environments to Python 3.10 or later.',
                          value='%.0f%%' % pct)
    return 20, _issue('python-versions-critical', 'version_currency', 'critical',
                      '%d of %d code envs on unsupported Python' % (unsupported, total),
                      'Upgrade code environments to Python 3.10 or later ASAP.', value='%.0f%%' % pct)


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


def _score_filesystem(filesystem_info):
    if not filesystem_info:
        return 75, []
    worst = 100
    issues = []
    for fs in filesystem_info:
        usage = _parse_percentage(fs.get('Use%'))
        mount = fs.get('Mounted on') or fs.get('Filesystem')
        if usage > 100 or usage <= 0:
            continue
        available = 100 - usage
        if available < 10:
            worst = min(worst, 30)
            issues.append(_issue('disk-critical-%s' % mount, 'system_capacity', 'critical',
                                 'Disk %d%% full on %s' % (usage, mount),
                                 'Free up disk space or expand storage immediately.',
                                 value='%d%%' % usage))
        elif available < 20:
            worst = min(worst, 70)
            issues.append(_issue('disk-warning-%s' % mount, 'system_capacity', 'warning',
                                 'Disk %d%% used on %s' % (usage, mount),
                                 'Monitor disk usage and plan for cleanup or expansion.',
                                 value='%d%%' % usage))
    return worst, issues


def _score_disabled_features(disabled_features):
    if not disabled_features:
        return 100, None
    count = len(disabled_features)
    if count == 0:
        return 100, None
    if count <= 2:
        return 80, _issue('features-disabled-few', 'runtime_config', 'info',
                          '%d feature%s disabled' % (count, 's' if count > 1 else ''),
                          'Review disabled features to ensure they are intentionally disabled.',
                          value=count)
    if count <= 5:
        return 60, _issue('features-disabled-several', 'runtime_config', 'warning',
                          '%d features disabled' % count,
                          'Review disabled features and enable those needed for your use case.',
                          value=count)
    return 40, _issue('features-disabled-many', 'runtime_config', 'warning',
                      '%d features disabled' % count,
                      'Review disabled features list and discuss with your admin or Dataiku support.',
                      value=count)


def _score_security_settings(parsed):
    """Port of scoreSecuritySettings incl. the JS truthiness quirks (see module
    docstring): 'No' is truthy; missing dicts skip checks entirely."""
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
        if not cgroup_settings.get('Enabled'):  # 'Yes'/'No' both truthy; only missing/'' fires
            total -= 15
            issues.append(_issue('cgroups-disabled', 'security_isolation', 'info',
                                 'CGroups not enabled',
                                 'Consider enabling CGroups for better resource isolation.'))
        empty_targets = cgroup_settings.get('Empty Target Types')
        if empty_targets and str(empty_targets).strip() != '':
            total -= 20
            issues.append(_issue('cgroups-empty-targets', 'security_isolation', 'warning',
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
    db_type = (((general_settings or {}).get('internalDatabase') or {})
               .get('connection') or {}).get('type')
    if not db_type:
        return 75, None
    if db_type == 'PostgreSQL':
        return 100, None
    return 40, _issue('runtime-db-not-postgres', 'runtime_config', 'warning',
                      'Runtime database is %s, not PostgreSQL' % db_type,
                      'Migrate the DSS runtime database to PostgreSQL.', value=db_type)


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


def _group_issue(id, category, severity, count, noun_phrase, detail, recommendation, names):
    preview = ', '.join(names[:5])
    more = ' and %d more' % (len(names) - 5) if len(names) > 5 else ''
    plural = 's' if count > 1 else ''
    return _issue(id, category, severity,
                  ('%d project%s ' % (count, plural)) + noun_phrase,
                  recommendation, description='%s%s. %s' % (preview, more, detail))


def _score_code_env_complexity(project_footprint):
    if not project_footprint:
        return 75, []
    risks, issues = [], []
    critical, warning, info = [], [], []
    for row in project_footprint:
        count = row.get('codeEnvCount') or 0
        risks.append(_normalize_code_env_risk(count))
        if count >= 4:
            critical.append('%s (%d)' % (row.get('projectKey'), count))
        elif count == 3:
            warning.append(row.get('projectKey'))
        elif count == 2:
            info.append(row.get('projectKey'))
    if critical:
        issues.append(_group_issue('project-codenv-critical-group', 'code_envs', 'critical',
                                   len(critical), 'have 4+ code envs',
                                   'Each extra code environment multiplies size, fragility, deployment time, and failure surface.',
                                   'Consolidate toward a single code environment per project.', critical))
    if warning:
        issues.append(_group_issue('project-codenv-warning-group', 'code_envs', 'warning',
                                   len(warning), 'have 3 code envs',
                                   'Multiple code environments increase maintenance overhead and drift risk.',
                                   'Reduce project code environments to 1-2, ideally 1.', warning))
    if info:
        issues.append(_group_issue('project-codenv-info-group', 'code_envs', 'info',
                                   len(info), 'have 2 code envs',
                                   'Two code environments already increase rebuild and deployment complexity.',
                                   'Consolidate to a single environment when possible.', info))
    avg_risk = sum(risks) / len(risks) if risks else 0
    return max(0, min(100, 100 * (1 - avg_risk))), issues


def _score_project_size_pressure(project_footprint, summary):
    if not project_footprint:
        return 75, []
    avg_gb = (summary or {}).get('instanceAvgProjectGB')
    if avg_gb is None:
        avg_gb = sum((r.get('totalBytes') or 0) / (1024 ** 3) for r in project_footprint) / len(project_footprint)
    risks, issues = [], []
    huge, critical, high = [], [], []
    for row in project_footprint:
        total_gb = row.get('totalGB')
        if total_gb is None:
            total_gb = (row.get('totalBytes') or 0) / (1024 ** 3)
        size_risk = row.get('projectSizeIndex')
        if not isinstance(size_risk, (int, float)):
            size_risk = _normalize_project_size_index(total_gb, avg_gb)
        risks.append(size_risk)
        if total_gb >= 40:
            huge.append('%s (%.1fGB)' % (row.get('projectKey'), total_gb))
            continue
        health = row.get('projectSizeHealth')
        if health == 'angry-red':
            critical.append(row.get('projectKey'))
        elif health == 'red':
            high.append(row.get('projectKey'))
    if huge:
        issues.append(_group_issue('project-size-huge-group', 'project_footprint', 'critical',
                                   len(huge), 'exceed 40GB',
                                   'Project size above 40GB is a severe storage and operational risk.',
                                   'Prioritize cleanup or archival for these projects.', huge))
    if critical:
        issues.append(_group_issue('project-size-critical-group', 'project_footprint', 'critical',
                                   len(critical), 'have critical relative size',
                                   'These projects are significantly larger than peers on this instance.',
                                   'Review managed data/folders and archive or purge stale assets.', critical))
    if high:
        issues.append(_group_issue('project-size-high-group', 'project_footprint', 'warning',
                                   len(high), 'have high project size',
                                   'These projects are above instance norm and add storage pressure.',
                                   'Review large managed datasets/folders for cleanup.', high))
    avg_risk = sum(risks) / len(risks) if risks else 0
    return max(0, min(100, 100 * (1 - avg_risk))), issues


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
    NAMES matter for scoring (the score counts keys)."""
    d = raw or {}
    disabled = {}

    def add(name):
        disabled[name] = {'status': 'Disabled'}

    ai = d.get('aiDrivenAnalyticsSettings')
    if ai:
        if 'enabled' in ai:
            if ai.get('enabled') is False:
                add('AI Assistants')
        else:
            if not ai.get('prepareAICompletionEnabled'):
                add('AI: Prepare Completion')
            if not ai.get('aiGenerateSQLEnabled'):
                add('AI: Generate SQL')
            if not ai.get('aiExplanationsEnabled'):
                add('AI: Explanations')
            if not ai.get('storiesAIEnabled'):
                add('AI: Stories')
    if (d.get('codeAssistantSettings') or {}).get('codeAssistantEnabled') is False:
        add('Code Assistant')
    if (d.get('askDataikuSettings') or {}).get('enabled') is False:
        add('Ask Dataiku')
    if (d.get('sparkSettings') or {}).get('sparkEnabled') is False:
        add('Spark')
    if (d.get('containerSettings') or {}).get('cdeEnabled') is False:
        add('Container Execution (CDE)')
    if (d.get('containerSettings') or {}).get('k8sEnabled') is False:
        add('Kubernetes')
    if (d.get('cgroupSettings') or {}).get('enabled') is False:
        add('CGroups')
    if (d.get('governIntegrationSettings') or {}).get('enabled') is False:
        add('Govern Integration')
    if (d.get('popularDatasetsSettings') or {}).get('enablePopularDatasets') is False:
        add('Popular Datasets')
    if (d.get('impersonation') or {}).get('enabled') is False:
        add('Impersonation')
    mode = (d.get('deployerClientSettings') or {}).get('mode')
    if mode and mode != 'LOCAL':
        disabled['Deployer Client'] = {'status': 'Mode: %s' % mode}
    return disabled


def build_parsed_data(overview, raw_settings, java_memory_settings, code_envs_payload,
                      footprint_payload):
    """Assemble the ParsedData subset calculateHealthScore reads, exactly as the
    live UI loader does."""
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
    return parsed


# ── the score itself ─────────────────────────────────────────────────────────


def calculate_health_score(parsed, thresholds=None):
    """Port of calculateHealthScore with all factor toggles enabled (the
    default) and weights from threshold settings when provided."""
    t = thresholds or {}
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
    py_score, py_issue = _score_python_version(parsed.get('codeEnvs'))
    spark_score, spark_issue = _score_spark_version(parsed.get('sparkSettings'))
    vc_score = _combine_enabled_scores([
        {'enabled': True, 'score': py_score, 'weight': 0.7},
        {'enabled': True, 'score': spark_score, 'weight': 0.3},
    ])
    vc_issues = [i for i in (py_issue, spark_issue) if i]
    categories.append({'category': 'version_currency', 'label': 'Version Currency',
                       'score': vc_score, 'weight': weights['version_currency'], 'issues': vc_issues})
    all_issues.extend(vc_issues)

    # SYSTEM CAPACITY
    mem_score, mem_issue = _score_memory_availability(parsed.get('memoryInfo'))
    fs_score, fs_issues = _score_filesystem(parsed.get('filesystemInfo'))
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
    categories.append({'category': 'system_capacity', 'label': 'System Capacity',
                       'score': sc_score, 'weight': weights['system_capacity'], 'issues': sc_issues})
    all_issues.extend(sc_issues)

    # SECURITY ISOLATION
    iso_issue = next((i for i in sec_issues if i['id'] == 'impersonation-disabled'), None)
    cg_disabled = next((i for i in sec_issues if i['id'] == 'cgroups-disabled'), None)
    cg_empty = next((i for i in sec_issues if i['id'] == 'cgroups-empty-targets'), None)
    si_issues = []
    si_score = 100
    if iso_issue:
        si_score -= 25
        si_issues.append(iso_issue)
    if cg_disabled:
        si_score -= 15
        si_issues.append(cg_disabled)
    if cg_empty:
        si_score -= 20
        si_issues.append(cg_empty)
    si_score = max(0, si_score)
    categories.append({'category': 'security_isolation', 'label': 'Security Isolation',
                       'score': si_score, 'weight': weights['security_isolation'], 'issues': si_issues})
    all_issues.extend(si_issues)

    # CODE ENVIRONMENTS
    ce_score, ce_issues = _score_code_env_complexity(parsed.get('projectFootprint'))
    categories.append({'category': 'code_envs', 'label': 'Code Envs',
                       'score': ce_score, 'weight': weights['code_envs'], 'issues': ce_issues})
    all_issues.extend(ce_issues)

    # PROJECT FOOTPRINT
    pf_score, pf_issues = _score_project_size_pressure(parsed.get('projectFootprint'),
                                                       parsed.get('projectFootprintSummary'))
    categories.append({'category': 'project_footprint', 'label': 'Project Footprint',
                       'score': pf_score, 'weight': weights['project_footprint'], 'issues': pf_issues})
    all_issues.extend(pf_issues)

    # RUNTIME CONFIG
    df_score, df_issue = _score_disabled_features(parsed.get('disabledFeatures'))
    jm_score, jm_issues = _score_java_memory(parsed.get('javaMemorySettings'))
    rd_score, rd_issue = _score_runtime_database(parsed.get('generalSettings'))
    rc_score = _combine_enabled_scores([
        {'enabled': True, 'score': df_score, 'weight': 0.34},
        {'enabled': True, 'score': jm_score, 'weight': 0.33},
        {'enabled': True, 'score': rd_score, 'weight': 0.33},
    ])
    rc_issues = list(jm_issues)
    if df_issue:
        rc_issues.append(df_issue)
    if rd_issue:
        rc_issues.append(rd_issue)
    categories.append({'category': 'runtime_config', 'label': 'Runtime Config',
                       'score': rc_score, 'weight': weights['runtime_config'], 'issues': rc_issues})
    all_issues.extend(rc_issues)

    overall = sum(c['score'] * c['weight'] for c in categories)

    unique, seen = [], set()
    for issue in all_issues:
        if issue['id'] not in seen:
            seen.add(issue['id'])
            unique.append(issue)
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
    }


# ── one-call convenience for tools/triage ────────────────────────────────────


def score_host(client, host='local'):
    """Fetch every score input from the backend for `host` and compute the
    score. Heavy inputs (code-envs, footprint) may raise ScanTimeout — callers
    surface that as scan_running."""
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
    parsed = build_parsed_data(overview, raw_settings, java, code_envs, footprint)
    return calculate_health_score(parsed, thresholds)
