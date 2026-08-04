"""Agent data-access domain registry — the single source of truth for what
the agents can query and what can fix what they find.

One row per queryable domain of the registry-backed config_inspect tool:
name, LLM-facing summary, the tools_impl handler that fetches+shapes it, the
canonical ParsedData fields it covers, and the catalogued actuator actions
that can remediate findings in it. The tool's domain enumeration, its
description prose, and its 'list' manifest are all generated from these rows,
so the model can never learn a domain that doesn't exist (or miss one that
does).

PARSED_FIELD_COVERAGE accounts for every OTHER ParsedData data field — the
ones served by a different sensor, deferred to a future domain, or
structurally unfixable — and MODULE_COVERAGE does the same per webapp module.
scripts/check_agent_domain_coverage.mjs (wired into check:contracts and the
deploy gate) fails the build unless every ParsedData data field and every
module id is accounted for here: adding parsed data without exposing it to
the agent breaks the build.

Rows are pure metadata (handlers are function NAMES in tools_impl, resolved
by getattr) so this module imports with a bare python3 — the contract check
execs it with PYTHONPATH=python-lib and no DSS dependencies.
"""


def _domain(name, summary, handler, parsed_fields=(), fix_actions=(),
            fix_waiver=None, filters=(), detail_modes=(), heavy=False,
            project_scoped=False, fields=()):
    """One registry row. `fix_actions` names catalogued actuator actions that
    can remediate findings in this domain; a domain with none MUST carry a
    `fix_waiver` explaining why (asserted at import)."""
    return {'name': name, 'summary': summary, 'handler': handler,
            'parsed_fields': tuple(parsed_fields), 'fix_actions': tuple(fix_actions),
            'fix_waiver': fix_waiver, 'filters': tuple(filters),
            'detail_modes': tuple(detail_modes), 'heavy': heavy,
            'project_scoped': project_scoped, 'fields': tuple(fields)}


DOMAINS = (
    _domain(
        'projects',
        'Project inventory: projectKey, name, owner. Resolves a project label '
        'to its KEY (name_filter matches key or label substring) — the key every '
        'per-project domain and action needs.',
        '_domain_projects',
        parsed_fields=('projects',),
        fix_actions=('project-delete', 'project-export', 'project-change-owner',
                     'project-set-cluster', 'project-variables-set'),
        filters=('name_filter = key/label substring',),
        fields=('projectCount', 'projects')),
    _domain(
        'connections',
        'Connection inventory (counts by type, per-connection type/params); '
        "detail='health' adds a live probe naming the failing connections.",
        '_domain_connections',
        parsed_fields=('connections', 'connectionCounts', 'connectionDetails',
                       'connectionHealth', 'connectionHealthTotal'),
        fix_actions=('connection-update', 'connection-test', 'connection-delete',
                     'connection-index'),
        filters=('name_filter = connection name/type substring',),
        detail_modes=('health',),
        fields=('countsByType', 'connections', 'healthProbe')),
    _domain(
        'connections-usage',
        'Per-connection usage: which projects/datasets/LLM recipes use each '
        'connection, with project owner + ownerEmail on every row, plus the '
        'projects that have an active scenario trigger (the ones broken '
        'connections actually hurt). The grounding for owner outreach and '
        'connection repairs. name_filter = one connection for full detail; '
        "detail='fs' adds local-filesystem object rows.",
        '_domain_connections_usage',
        parsed_fields=('connectionDatasetUsages', 'connectionLlmUsages',
                       'connectionActiveTriggerProjects',
                       'connectionLocalFilesystemUsages', 'connectionUsageTotal',
                       'connectionUsageScanned', 'connectionUsageScanErrors',
                       'connectionUsageFailedProjectCount',
                       'connectionUsageScannedProjectCount'),
        fix_actions=('connection-update', 'connection-delete', 'notification-send',
                     'user-update', 'scenario-disable'),
        filters=('name_filter = connection name', 'page'),
        detail_modes=('fs',),
        heavy=True,
        fields=('datasetUsages', 'llmUsages', 'activeTriggerProjects',
                'projectUrlBase', 'scan')),
    _domain(
        'code-envs',
        'Code environments: totals, deprecated-Python and unused envs, largest '
        'by size (whitelist-aware).',
        '_domain_code_envs',
        parsed_fields=('codeEnvs', 'codeEnvSizes', 'codeEnvsExpectedCount',
                       'provisionalCodeEnvs', 'codeEnvsCompare',
                       'pythonVersionCounts', 'rVersionCounts', 'totalEnvCount',
                       'skippedEnvCount'),
        fix_actions=('code-env-delete', 'code-env-update', 'code-env-consolidate',
                     'plugin-code-env-rebuild'),
        filters=('name_filter = env name substring',),
        heavy=True,
        fields=('totals', 'deprecatedPython', 'unused', 'largest')),
    _domain(
        'plugins',
        "Installed plugins (id, version, dev flag); detail='usage' adds "
        'projects-using counts and the unused-plugin list.',
        '_domain_plugins',
        parsed_fields=('plugins', 'pluginDetails', 'pluginsCount',
                       'pluginUsagesPending'),
        fix_actions=('plugin-update', 'plugin-uninstall', 'plugin-deploy',
                     'plugin-code-env-rebuild'),
        filters=('name_filter = plugin id/label substring',),
        detail_modes=('usage',),
        fields=('pluginsCount', 'plugins', 'devPlugins', 'unusedPlugins', 'mostUsed')),
    _domain(
        'llms',
        'LLM connections and the models they expose, grouped by connection.',
        '_domain_llms',
        fix_actions=('connection-update', 'notification-send'),
        filters=('name_filter = id/connection/model substring',),
        fields=('llmCount', 'byConnection')),
    _domain(
        'clusters',
        "K8s cluster attachments (state, architecture); detail='health' adds a "
        'reachability sweep naming failing/stale attachments.',
        '_domain_clusters',
        parsed_fields=('clusters',),
        fix_actions=('cluster-start', 'cluster-stop', 'cluster-detach',
                     'cluster-pods-cleanup', 'k8s-apply-fix', 'project-set-cluster'),
        filters=('name_filter = cluster id/name substring',),
        detail_modes=('health',),
        fields=('clusters', 'unavailable', 'reachability')),
    _domain(
        'users',
        'User accounts WITH email, profile, groups and enabled state (the rich '
        'listing — resolves owner logins to email addresses), plus the '
        'no-email and disabled rollups.',
        '_domain_users',
        parsed_fields=('users', 'userStats', 'usersByProjects'),
        fix_actions=('user-update', 'user-enable', 'user-disable'),
        filters=('name_filter = login/name/email substring', 'page'),
        fields=('userStats', 'userCount', 'disabled', 'noEmail', 'users')),
    _domain(
        'api-keys',
        'Personal and global API keys (ids and labels only — secrets never shown).',
        '_domain_api_keys',
        fix_actions=('api-key-delete',),
        fields=('personal', 'global')),
    _domain(
        'scenarios',
        'Scenarios of ONE project (name_filter = projectKey): id, type, '
        'active/running state, next run.',
        '_domain_project_scoped',
        fix_actions=('scenario-run', 'scenario-kill', 'scenario-disable',
                     'scenario-enable', 'toolkit-scenario-write'),
        filters=('name_filter = PROJECT KEY (required)',),
        project_scoped=True,
        fields=('scenarios',)),
    _domain(
        'webapps',
        'Webapps of ONE project (name_filter = projectKey), with backend '
        'running state.',
        '_domain_project_scoped',
        fix_actions=('webapp-backend-stop', 'webapp-backend-restart',
                     'project-clear-webapp-runs'),
        filters=('name_filter = PROJECT KEY (required)',),
        project_scoped=True,
        fields=('webapps',)),
    _domain(
        'notebooks',
        'Jupyter notebooks of ONE project (name_filter = projectKey), with '
        'kernel and last-modified info.',
        '_domain_project_scoped',
        fix_actions=('notebook-kernels-shutdown', 'notebook-clear-outputs'),
        filters=('name_filter = PROJECT KEY (required)',),
        project_scoped=True,
        fields=('notebooks',)),
    _domain(
        'jobs',
        'Recent jobs of ONE project (name_filter = projectKey): state, type, '
        'initiator.',
        '_domain_project_scoped',
        fix_actions=('job-kill', 'job-logs-cleanup'),
        filters=('name_filter = PROJECT KEY (required)',),
        project_scoped=True,
        fields=('jobs',)),
    _domain(
        'datasets',
        "Datasets of ONE project (name_filter = projectKey); detail='usage' "
        'adds flow lineage (producing/consuming recipes, webapp/scenario '
        "name-refs) plus the 'unreferenced' and 'deleteCandidates' rollups — "
        'the grounding for dataset-delete cleanup. Rows carry exposed=true '
        'when shared.',
        '_domain_project_scoped',
        fix_actions=('dataset-clear', 'dataset-delete'),
        filters=('name_filter = PROJECT KEY (required)',),
        detail_modes=('usage',),
        project_scoped=True,
        fields=('datasets', 'summary')),
    _domain(
        'continuous-activities',
        'Continuous activities of ONE project (name_filter = projectKey): '
        'recipe id, desired state, loop liveness.',
        '_domain_project_scoped',
        fix_actions=('continuous-activity-stop',),
        filters=('name_filter = PROJECT KEY (required)',),
        project_scoped=True,
        fields=('activities',)),
    _domain(
        'adoption',
        'Adoption/engagement analytics: totals, licensing seats vs per-profile '
        "usage, monthly build trend, top builders, repeat-builder split. "
        "detail='inventory' = full-history object inventory (macro); "
        "detail='events' = recent audit-event pulse (macro).",
        '_domain_adoption',
        parsed_fields=('adoptionData',),
        fix_actions=('notification-send', 'user-update', 'user-disable',
                     'project-delete'),
        detail_modes=('inventory', 'events'),
        heavy=True,
        fields=('totals', 'licensing', 'profileCounts', 'monthlyTrend',
                'builderStats', 'repeatBuilders')),
    _domain(
        'settings',
        'DSS instance general settings, redacted (secret values masked, '
        'auth/SSO/security/licensing families stripped — the same policy as '
        'settings-set): cgroups, limits, container exec configs, spark, '
        'internal DB, disabled features. name_filter = top-level key '
        'substring for the full redacted subtree.',
        '_domain_settings',
        parsed_fields=('enabledSettings', 'sparkSettings', 'containerSettings',
                       'containerExecDefaults', 'execResourceConfigs',
                       'integrationSettings', 'resourceLimits', 'cgroupSettings',
                       'proxySettings', 'maxRunningActivities', 'jekSettings',
                       'disabledFeatures', 'generalSettings'),
        fix_actions=('settings-set', 'k8s-exec-config-tune'),
        filters=('name_filter = top-level settings key substring',),
        fields=('keys', 'cgroups', 'limits', 'containerExec', 'spark',
                'internalDatabase', 'impersonation')),
    _domain(
        'cost-detail',
        'Full CRU cost detail the summary compute_cost tool drops: '
        'per-connection SQL cost, idle resources, daily timeline, LLM models, '
        'K8s reservations; name_filter = one projectKey for its nested '
        'byUser/byConnection/byModel breakdowns.',
        '_domain_cost_detail',
        parsed_fields=('projectCostData',),
        fix_actions=('job-kill', 'scenario-kill', 'scenario-disable',
                     'continuous-activity-stop', 'cluster-stop',
                     'notebook-kernels-shutdown', 'webapp-backend-stop'),
        filters=('name_filter = projectKey',),
        heavy=True,
        fields=('totals', 'span', 'topProjects', 'byConnection', 'idleResources',
                'daily', 'llmModels', 'project')),
)

DOMAINS_BY_NAME = {row['name']: row for row in DOMAINS}


def _covered(via, fix_actions=(), fix_waiver=None):
    return {'via': via, 'fix_actions': tuple(fix_actions), 'fix_waiver': fix_waiver}


# Every ParsedData data field NOT covered by a domain row above: which agent
# surface serves it (sensor:<tool> / action:<planner read> / deferred:<future
# domain>) and what fixes findings in it. 'deferred:' entries are the
# contract-driven backlog — promote them to DOMAINS rows over time.
PARSED_FIELD_COVERAGE = {
    # basic/system info — instance_health 'system' section
    'company': _covered('sensor:instance_health', fix_waiver='informational'),
    'dssVersion': _covered('sensor:instance_health',
                           fix_waiver='DSS upgrades are structurally excluded'),
    'pythonVersion': _covered('sensor:instance_health',
                              fix_waiver='host Python is not agent-mutable'),
    'diagType': _covered('waiver:static-diag artifact of the upload path',
                         fix_waiver='not an issue surface'),
    'lastRestartTime': _covered('sensor:instance_health',
                                fix_waiver='DSS restart is structurally excluded'),
    'instanceInfo': _covered('sensor:instance_health', fix_waiver='informational'),
    'cpuCores': _covered('sensor:instance_health',
                         fix_waiver='hardware sizing is not agent-mutable'),
    'osInfo': _covered('sensor:instance_health', fix_waiver='informational'),
    'memoryInfo': _covered('sensor:instance_health',
                           fix_actions=('notebook-kernels-shutdown',
                                        'webapp-backend-stop', 'job-kill',
                                        'k8s-exec-config-tune', 'settings-set')),
    'systemLimits': _covered('sensor:instance_health',
                             fix_waiver='ulimits/systemd are root/host-level files '
                                        '— deliberately excluded'),
    'filesystemInfo': _covered('sensor:instance_health',
                               fix_actions=('log-cleanup', 'tmp-cleanup',
                                            'exports-cleanup', 'job-logs-cleanup',
                                            'docker-prune',
                                            'project-clear-webapp-runs',
                                            'project-delete', 'dataset-clear')),
    'dipHomeStorage': _covered('sensor:instance_health',
                               fix_actions=('log-cleanup', 'tmp-cleanup',
                                            'exports-cleanup', 'job-logs-cleanup',
                                            'project-clear-webapp-runs')),
    # settings family — the 'settings' domain row above; only the
    # auth/security families stay read-waivered.
    'authSettings': _covered('waiver:auth/SSO family — blacklisted for agent reads',
                             fix_waiver='SSO/LDAP paths are blacklisted for agents'),
    'javaMemorySettings': _covered('sensor:instance_health (java section)',
                                   fix_waiver='env-default.sh is a host-level file '
                                              '— deliberately excluded'),
    'javaMemoryLimits': _covered('sensor:instance_health (java section)',
                                 fix_waiver='env-default.sh is a host-level file '
                                            '— deliberately excluded'),
    'securityDefaults': _covered('waiver:security family — blacklisted for agent reads',
                                 fix_waiver='security paths are write-blacklisted too'),
    'ldapAuthorizedGroups': _covered('waiver:SSO/LDAP family — blacklisted for agent reads',
                                     fix_waiver='SSO/LDAP paths are blacklisted for agents'),
    'connectionAudit': _covered('toolkit_get:connections-audit',
                                fix_actions=('connection-update',)),
    # sanity + logs — instance_health / log_errors sensors
    'sanityCheck': _covered('sensor:instance_health (sanity section)',
                            fix_actions=('settings-set', 'connection-update',
                                         'log-cleanup', 'db-vacuum')),
    'sanityCheckMaxSeverity': _covered('sensor:instance_health (sanity section)',
                                       fix_actions=('settings-set',)),
    'formattedLogErrors': _covered('sensor:log_errors',
                                   fix_actions=('log-cleanup', 'job-logs-cleanup')),
    'rawLogErrors': _covered('sensor:log_errors',
                             fix_actions=('log-cleanup', 'job-logs-cleanup')),
    'logStats': _covered('sensor:log_errors', fix_actions=('log-cleanup',)),
    # footprint — storage_footprint sensor
    'projectFootprint': _covered('sensor:storage_footprint',
                                 fix_actions=('project-delete', 'project-export',
                                              'project-clear-webapp-runs',
                                              'dataset-clear', 'dataset-delete')),
    'projectFootprintSummary': _covered('sensor:storage_footprint',
                                        fix_actions=('project-delete',)),
    # AI compute
    'llmAudit': _covered('toolkit_get:llm-audit',
                         fix_actions=('connection-update', 'notification-send')),
    # messaging
    'mailChannels': _covered('action:notification-send planner (channels list)',
                             fix_actions=('notification-send', 'settings-set')),
    'configuredMailChannel': _covered('action:notification-send planner',
                                      fix_actions=('settings-set',)),
    # license — full license detail is a diag-upload artifact; live seat
    # pressure is served by the adoption domain's licensing section and fixed
    # by right-sizing profiles
    'license': _covered('domain:adoption (licensing section; full detail is diag-only)',
                        fix_actions=('user-update', 'user-disable')),
    'licenseInfo': _covered('domain:adoption (licensing section; full detail is diag-only)',
                            fix_actions=('user-update', 'user-disable')),
    'licenseProperties': _covered('domain:adoption (licensing section; full detail is diag-only)',
                                  fix_actions=('user-update', 'user-disable')),
    'hasLicenseUsage': _covered('domain:adoption (licensing section)',
                                fix_waiver='presence flag'),
    # directory tree
    'dirTree': _covered('toolkit_get:dir-tree',
                        fix_actions=('log-cleanup', 'tmp-cleanup', 'exports-cleanup',
                                     'job-logs-cleanup', 'project-clear-webapp-runs',
                                     'docker-prune')),
}

# Module-id → agent surface, same contract as PARSED_FIELD_COVERAGE but per
# webapp module (moduleRegistry.MODULES). Checked by the same script.
MODULE_COVERAGE = {
    'mission-control': 'sensor:triage_sweep + instance_health',
    'summary': 'sensor:instance_health include_score',
    'filesystem': 'sensor:instance_health (system section) + toolkit_get:dir-tree',
    'resources': 'toolkit_get:resources-snapshot + resources-processes (point samples of the live stream)',
    'connections-inventory': 'domain:connections',
    'connections-insights': 'domain:connections-usage',
    'connections-health': 'domain:connections detail=health',
    'connections-fs-migration': "domain:connections-usage detail='fs'",
    'project-cleaner': 'sensor:storage_footprint (cleanup candidates)',
    'projects': 'domain:projects + sensor:storage_footprint',
    'project-compute': 'sensor:compute_cost (context types)',
    'project-cost': 'domain:cost-detail',
    'users': 'domain:users',
    'adoption': 'domain:adoption',
    'user-churn': 'domain:users (churn/reassignment roll-ups are UI-side derivations of the same user+activity snapshot)',
    'plugins-installed': 'domain:plugins',
    'plugins': 'domain:plugins + action:plugin-deploy',
    'code-envs': 'domain:code-envs',
    'code-envs-cleaner': 'domain:code-envs',
    'code-envs-comparison': 'domain:code-envs',
    'code-envs-broken': 'waiver:build-log parsing surface — no agent read path to the per-env logs',
    'container-execs': 'toolkit_get:container-execs',
    'image-cleaner': 'action:image-delete planner grounding + toolkit_get:docker-usage',
    'cs-template-replacement': 'toolkit_get:cs-templates + cs-template-projects (read-only; migrate stays excluded)',
    'llm-audit': 'toolkit_get:llm-audit',
    'k8s-insights': 'sensor:k8s_health',
    'agents': 'waiver:the agent surface itself',
    'agent-tuning': 'waiver:the agent surface itself',
    'agent-settings': 'waiver:the agent surface itself',
    'agent-explainer': 'waiver:static explainer page — no data access',
    'settings': 'domain:settings + action:settings-set + notification-send channels',
    'logs': 'sensor:log_errors',
    'sanity-check': 'sensor:instance_health (sanity section)',
    'db-health': 'sensor:db_health',
    'report': 'waiver:report generator — writes, not parsed data',
    'feedback': 'waiver:static action page',
}


def manifest():
    """The cheap 'list' payload for the agent: everything it needs to pick a
    domain in one small call."""
    return [{'name': row['name'], 'summary': row['summary'],
             'filters': list(row['filters']), 'detail': list(row['detail_modes']),
             'heavy': row['heavy'], 'projectScoped': row['project_scoped'],
             'fields': list(row['fields']), 'fixActions': list(row['fix_actions'])}
            for row in DOMAINS]


def contract_manifest():
    """What scripts/check_agent_domain_coverage.mjs consumes."""
    return {
        'domains': [{'name': row['name'],
                     'parsedFields': list(row['parsed_fields']),
                     'fixActions': list(row['fix_actions']),
                     'fixWaiver': row['fix_waiver']}
                    for row in DOMAINS],
        'parsedFieldCoverage': {field: entry['via']
                                for field, entry in PARSED_FIELD_COVERAGE.items()},
        'moduleCoverage': dict(MODULE_COVERAGE),
    }


def _assert_registry():
    from . import actions as actions_registry
    catalog = set(actions_registry.REQUIRED_TARGET_KEYS)
    names = [row['name'] for row in DOMAINS]
    assert len(set(names)) == len(names), 'duplicate domain names'
    seen_fields = {}
    for row in DOMAINS:
        assert row['summary'] and row['handler'], row['name']
        assert row['fix_actions'] or row['fix_waiver'], (
            'domain %r has no fix_actions and no fix_waiver — every queryable '
            'domain needs an actuator action that can fix its findings (or an '
            'explicit waiver)' % row['name'])
        unknown = set(row['fix_actions']) - catalog
        assert not unknown, 'domain %r names uncatalogued fix actions: %s' % (
            row['name'], sorted(unknown))
        for field in row['parsed_fields']:
            assert field not in seen_fields, (
                'ParsedData field %r claimed by both %r and %r'
                % (field, seen_fields[field], row['name']))
            assert field not in PARSED_FIELD_COVERAGE, (
                'ParsedData field %r is both a domain field (%r) and a coverage '
                'entry' % (field, row['name']))
            seen_fields[field] = row['name']
    for field, entry in PARSED_FIELD_COVERAGE.items():
        assert entry['fix_actions'] or entry['fix_waiver'], (
            'parsed field %r has no fix_actions and no fix_waiver' % field)
        unknown = set(entry['fix_actions']) - catalog
        assert not unknown, 'parsed field %r names uncatalogued fix actions: %s' % (
            field, sorted(unknown))


_assert_registry()
