"""Actuator action registry — the single source of truth for the non-legacy
catalog.

Each domain module exposes SPECS (rows built by actions._base.spec); this
package merges them into the dispatch tables actuator.py consumes and
GENERATES the target-shape prose every tool description quotes, so the
catalog, the planners/executors, and all three description sites
(action_items, ops-actuator in-agent tool, plan-admin-action component tool)
can never drift apart.

The 12 legacy actions keep their planners/executors in actuator.py; only
their shapes and batchability are declared here so the generated prose and
the batch gate cover the whole catalog.
"""

from . import clusters, connections, db, plugins_domain, projects_domain, runtime, storage, users

_DOMAIN_MODULES = (connections, clusters, plugins_domain, projects_domain,
                   runtime, users, storage, db)

SPECS = [spec for module in _DOMAIN_MODULES for spec in module.SPECS]

NEW_ACTIONS = tuple(spec['action'] for spec in SPECS)
assert len(set(NEW_ACTIONS)) == len(NEW_ACTIONS), 'duplicate action ids in actions registry'

PLANNERS = {spec['action']: spec['planner'] for spec in SPECS}
EXECUTORS = {spec['action']: spec['executor'] for spec in SPECS}
SETTINGS_CHANGE_HOOKS = {spec['action']: spec['settings_hook']
                         for spec in SPECS if spec.get('settings_hook')}
LOCAL_ONLY_EXTRA = tuple(spec['action'] for spec in SPECS if spec.get('local_only'))
RISKS = {spec['action']: spec['risk'] for spec in SPECS}

# Target shapes of the legacy dozen (planners in actuator.py). Kept here so
# the generated prose covers the WHOLE catalog.
_LEGACY_SHAPES = (
    'project-delete {projectKey}',
    'code-env-delete {name, lang}',
    'db-vacuum/db-analyze {connection, table}',
    'image-delete {provider, cutoff, images}',
    'plugin-deploy {pluginId, targetHostId}',
    'k8s-exec-config-tune {configName, changes:{memRequestMB|memLimitMB|cpuRequest|cpuLimit}}',
    'log-cleanup {roots?, minAgeDays?, maxDeleteGB?}',
    'docker-prune {mode: builder|image, keepStorageGB?, filterUntilHours?}',
    'k8s-apply-fix {clusterId, commands[], manifestYaml?, execConfigPatch?, verifyRule?}',
    'code-env-consolidate {sourceEnvName, targetEnvName, language?, projectKeys?, '
    'usageTypes?, retireSource?}',
    'settings-set {path, newValue}',
)

# Legacy actions that accept targets[] batching (their planners already build
# one canonical per target; the batch layer in actuator.py does the rest).
LEGACY_BATCHABLE = frozenset({'code-env-delete', 'settings-set', 'db-vacuum',
                              'db-analyze', 'plugin-deploy', 'project-delete'})

BATCHABLE = LEGACY_BATCHABLE | frozenset(
    spec['action'] for spec in SPECS if spec.get('batchable'))

TARGET_SHAPES = '; '.join(_LEGACY_SHAPES + tuple(spec['shape'] for spec in SPECS))

BATCH_NOTE = ('Batchable actions (%s) accept targets: [dict, ...] — several '
              'objects, same action, ONE plan and ONE confirm token.'
              % ', '.join(sorted(BATCHABLE)))
