"""Curated read-endpoint registry — the toolkit_get bridge's single source
of truth ("read all": full read parity with the webapp).

One row per whitelisted backend GET endpoint the generic toolkit_get sensor
may fetch: name, path, allowed query params, heaviness. The tool's endpoint
enumeration, its description prose and its 'list' manifest are generated from
these rows (the config_inspect pattern), so the model can never learn an
endpoint that doesn't exist. scripts/check_agent_read_coverage.mjs (wired
into the contracts gate) fails the build unless every registered path exists
as a backend route and TOOLKIT_PAGES stays in lockstep with the frontend
module registry.

TOOLKIT_PAGES maps every webapp module id to a one-line "what that page
shows" — the list_capabilities meta-tool serves it so agents can point users
at the right page instead of confabulating policy about missing tools.

Rows are pure metadata: this module imports with a bare python3 (the
contract check execs it with PYTHONPATH=python-lib, no DSS dependencies).
"""


def _endpoint(name, path, summary, params=(), heavy=False, progress_path=None,
              local_only=False):
    return {'name': name, 'path': path, 'summary': summary,
            'params': tuple(params), 'heavy': heavy,
            'progress_path': progress_path, 'local_only': local_only}


ENDPOINTS = (
    _endpoint(
        'errors', '/api/logs/errors',
        'Parsed backend.log error/warning groups with stats (the Errors page). '
        'For raw lines or a grep, prefer the log_tail sensor.'),
    _endpoint(
        'users-churn', '/api/users/churn',
        'Churn & Seats data: per-user activity/login/created proxy chain, '
        'dormant + disabled seat groups, yearly account flow (the Users → '
        'Churn & Seats page).'),
    _endpoint(
        'cost-cru-detail', '/api/cru',
        'Full Cost/CRU detail: totals, per-project/user/connection/model '
        'breakdowns, daily series, idle resources (the Projects → Cost page). '
        'Span = rolling audit retention.',
        heavy=True),
    _endpoint(
        'llm-audit', '/api/llm-audit',
        'LLM Mesh model audit: per-model/connection usage, tokens and cost '
        'estimates with pricing-catalog matches (the Model Audit page).',
        heavy=True, progress_path='/api/llm-audit/progress'),
    _endpoint(
        'k8s-insights', '/api/k8s-insights/clusters',
        'K8s cluster inventory with states and stale attachments (the K8s '
        'Insights page). Reachability probes + per-cluster audits live in the '
        'k8s_health sensor.'),
    _endpoint(
        'db-health-connections', '/api/tools/db-health/connections',
        'RuntimeDB candidates: PostgreSQL connections on the host + the '
        'configured DB Health connection (pass it to the db-health-* views).'),
    _endpoint(
        'db-health-overview', '/api/tools/db-health/overview',
        'RuntimeDB overview: size, version, table count, dead/live tuples '
        '(the DB Health page).',
        params=('connection', 'limit')),
    _endpoint(
        'db-health-tables', '/api/tools/db-health/tables',
        'RuntimeDB per-table stats: sizes, dead tuples, vacuum/analyze ages.',
        params=('connection', 'limit')),
    _endpoint(
        'db-health-per-project', '/api/tools/db-health/per-project',
        'RuntimeDB usage grouped by project.',
        params=('connection', 'limit')),
    _endpoint(
        'audit-timeline', '/api/agents/actions',
        'Agent-action audit timeline: the most recent agent-executed actions '
        'with status and result snippets (the Agents page timeline).',
        params=('limit',), local_only=True),
    _endpoint(
        'settings-snapshot', '/api/settings',
        'Toolkit backend runtime settings: current values + defaults '
        '(cache TTLs, worker counts, thresholds).'),
    _endpoint(
        'version', '/api/mode',
        'Toolkit plugin version + backend mode — cite it when asked what '
        'version is running.'),
    _endpoint(
        'resources-snapshot', '/api/host/resource-sample',
        'Instantaneous CPU/memory counter snapshot of the host (one sample '
        'of the Resources live graph; call twice to derive CPU%).'),
    _endpoint(
        'resources-processes', '/api/host/process-metrics',
        'Per-process CPU + memory snapshot of the host (the Resources page '
        'process table). fresh=1 bypasses the short cache.',
        params=('fresh',)),
    _endpoint(
        'connections-audit', '/api/connections/audit',
        'Connection configuration audit: fast-write, details readability, '
        'HDFS interface, filesystem_root findings per connection (the '
        'Connections → Insights audit column).'),
    _endpoint(
        'container-execs', '/api/container-execs',
        'Containerized execution configs with per-project usage, requests/'
        'limits and registry links (the Container Execs page). '
        'projectKeys=A,B narrows the scan.',
        params=('projectKeys',), heavy=True),
    _endpoint(
        'cs-templates', '/api/cs-template/templates',
        'Code Studio template inventory (the CS Templates page). Read-only — '
        'template migration stays a human-driven page action.'),
    _endpoint(
        'cs-template-projects', '/api/cs-template/projects',
        'Per-project Code Studios with the template each one uses (the CS '
        'Templates page). Read-only; scans every project.',
        params=('includeState',), heavy=True),
    _endpoint(
        'dir-tree', '/api/dir-tree',
        'DIP_HOME directory tree with per-directory sizes (the Filesystem '
        "page treemap). scope=dss|project (+projectKey), path drills into a "
        'subtree, maxDepth caps depth.',
        params=('scope', 'projectKey', 'path', 'maxDepth'), heavy=True),
    _endpoint(
        'docker-usage', '/api/tools/docker/usage',
        'Docker disk usage on the host (images/containers/build-cache — the '
        'grounding for docker-prune).'),
)

# Webapp module id → what that page shows. MUST cover every id in
# resource/frontend/src/utils/moduleRegistry.ts (checked by
# scripts/check_agent_read_coverage.mjs, both directions).
TOOLKIT_PAGES = {
    'mission-control': 'Fleet wall: every host with live health scores',
    'summary': 'One-host health summary with the 0-100 score',
    'filesystem': 'Filesystem mounts, usage and directory tree',
    'resources': 'Live CPU/memory stream + process table',
    'connections-inventory': 'Connection inventory by type with params',
    'connections-insights': 'Connection→project usage matrix with owners',
    'connections-health': 'Live connection test results',
    'connections-fs-migration': 'Filesystem-connection migration candidates',
    'project-cleaner': 'Per-project cleanup: exports, webapp runs, tmp',
    'projects': 'Project inventory: owners, sizes, activity',
    'project-compute': 'Compute usage by context type',
    'project-cost': 'Cost/CRU: treemap, daily series, leaderboard',
    'users': 'User inventory: profiles, groups, seats',
    'adoption': 'Activity analytics: builders, trends, cohorts',
    'user-churn': 'Churn & Seats: dormant seats, reassignment estimates',
    'plugins-installed': 'Installed plugins with versions',
    'plugins': 'Plugin sync/deploy across hosts',
    'code-envs': 'Code env cleaner: sizes, usage, delete candidates',
    'code-envs-cleaner': 'Code env insights: deprecated Python, largest envs',
    'code-envs-comparison': 'Package diff between two code envs',
    'container-execs': 'Container exec configs with requests/limits',
    'image-cleaner': 'Docker registry images: sprawl + delete candidates',
    'cs-template-replacement': 'CS template replacement across projects',
    'llm-audit': 'LLM model audit: usage, tokens, cost estimates',
    'k8s-insights': 'K8s clusters: reachability, pressure, rule findings',
    'agents': 'Agent chat + action plans + audit timeline',
    'agent-tuning': 'Agent prompt/model overrides (versioned)',
    'agent-settings': 'Agent Permissions: sensor + action gates',
    'agent-explainer': 'How Agents Work: animated tour of the agent pipeline + guardrails',
    'settings': 'Toolkit settings: hosts, thresholds, whitelists, DB',
    'logs': 'Backend.log error groups',
    'sanity-check': 'DSS sanity-check messages',
    'db-health': 'RuntimeDB PostgreSQL health views',
    'report': 'Quarterly health report deck generator',
    'feedback': 'Feedback / feature-request form',
}


def manifest():
    """The cheap 'list' payload for the agent."""
    return [{'name': row['name'], 'summary': row['summary'],
             'params': list(row['params']), 'heavy': row['heavy'],
             'localOnly': row['local_only']}
            for row in ENDPOINTS]


def tool_description():
    """Generated from the registry — the model can only ever learn endpoints
    that actually exist (drift-proof by construction)."""
    names = '|'.join(row['name'] for row in ENDPOINTS)
    heavy = ', '.join(row['name'] for row in ENDPOINTS if row['heavy'])
    return (
        "Fetch one whitelisted toolkit read endpoint — the generic bridge to "
        "every webapp dataset without a dedicated sensor (host, endpoint=%s, "
        "params, fields[], top_n, page). endpoint='list' returns the CHEAP "
        'manifest (summaries, allowed params, heaviness); call it first when '
        'unsure. Heavy endpoints (%s) run scans — minutes, possibly '
        'scan_running. fields=[...] keeps only those top-level output keys; '
        'top_n/page window every list in the payload.'
        % (names, heavy))


def contract_manifest():
    """What scripts/check_agent_read_coverage.mjs consumes."""
    return {
        'endpoints': [{'name': row['name'], 'path': row['path'],
                       'params': list(row['params']),
                       'progressPath': row['progress_path']}
                      for row in ENDPOINTS],
        'pages': dict(TOOLKIT_PAGES),
    }


def _assert_registry():
    names = [row['name'] for row in ENDPOINTS]
    assert len(set(names)) == len(names), 'duplicate endpoint names'
    for row in ENDPOINTS:
        assert row['summary'], row['name']
        assert row['path'].startswith('/api/'), row['name']
        assert 'password' not in row['params'], (
            'endpoint %r must never whitelist a credential param' % row['name'])
    for page, blurb in TOOLKIT_PAGES.items():
        assert blurb, page


_assert_registry()
