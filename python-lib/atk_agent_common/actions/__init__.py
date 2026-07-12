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

from . import (clusters, connections, db, messaging, plugins_domain,
               projects_domain, runtime, storage, toolkit_scenarios, users)

_DOMAIN_MODULES = (connections, clusters, plugins_domain, projects_domain,
                   runtime, users, storage, db, messaging, toolkit_scenarios)

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
# the generated prose covers the WHOLE catalog. A key is marked `?` exactly
# when its planner defaults it — REQUIRED_TARGET_KEYS is parsed from these
# strings, so an overstated key would downgrade plannable proposals.
_LEGACY_SHAPES = (
    'project-delete {projectKey}',
    'code-env-delete {name, lang?}',
    'db-vacuum/db-analyze {connection, table}',
    'image-delete {images, cutoff, provider?}',
    'plugin-deploy {pluginId, targetHostId}',
    'k8s-exec-config-tune {configName, changes:{memRequestMB|memLimitMB|cpuRequest|cpuLimit}}',
    'log-cleanup {roots?, minAgeDays?, maxDeleteGB?}',
    'docker-prune {mode?: builder|image, keepStorageGB?, filterUntilHours?}',
    'k8s-apply-fix {clusterId, commands[]?, manifestYaml?, execConfigPatch?, verifyRule?} '
    '(at least one of commands/execConfigPatch)',
    'code-env-consolidate {sourceEnvName, targetEnvName, language?, projectKeys?, '
    'usageTypes?, retireSource?}',
    'settings-set {path, newValue}',
)

# Legacy actions that accept targets[] batching (their planners already build
# one canonical per target; the batch layer in actuator.py does the rest).
# k8s-exec-config-tune right-sizes containerized exec configs — an instance
# routinely has several oversized configs, so the agent batches them; its
# planner/executor are per-target-independent (each carries its own
# configName+changes, no cross-target drift-token binding), so batching is
# safe (settings-set precedent).
LEGACY_BATCHABLE = frozenset({'code-env-delete', 'settings-set', 'db-vacuum',
                              'db-analyze', 'plugin-deploy', 'project-delete',
                              'k8s-exec-config-tune'})

BATCHABLE = LEGACY_BATCHABLE | frozenset(
    spec['action'] for spec in SPECS if spec.get('batchable'))

TARGET_SHAPES = '; '.join(_LEGACY_SHAPES + tuple(spec['shape'] for spec in SPECS))


def _required_keys(shape):
    """Required target keys parsed from one shape string: the first balanced
    {...} block, top-level comma-split; a key is optional iff its token (the
    part before any ':'/'['/'{') ends with '?'."""
    start = shape.find('{')
    if start < 0:
        return frozenset()
    depth, end = 0, -1
    for i in range(start, len(shape)):
        if shape[i] == '{':
            depth += 1
        elif shape[i] == '}':
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return frozenset()
    tokens, token, depth = [], '', 0
    for ch in shape[start + 1:end]:
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
        if ch == ',' and depth == 0:
            tokens.append(token)
            token = ''
        else:
            token += ch
    tokens.append(token)
    required = set()
    for tok in tokens:
        head = tok.strip().split(':')[0].strip()  # 'mode?', 'commands[]?', 'changes'
        optional = head.endswith('?')
        key = head.rstrip('?').split('[')[0].strip()
        if key and not optional:
            required.add(key)
    return frozenset(required)


# {action: frozenset(required target keys)} for the WHOLE catalog — parsed
# from the same shape strings the tool descriptions quote, so the
# propose-time shape check (action_items) can never drift from the prose.
# SHAPES carries the per-action prose fragment for catalog consumers
# (Agent Settings page).
REQUIRED_TARGET_KEYS = {}
SHAPES = {}
for _shape in _LEGACY_SHAPES:
    for _action in _shape.split(' {', 1)[0].split('/'):
        REQUIRED_TARGET_KEYS[_action.strip()] = _required_keys(_shape)
        SHAPES[_action.strip()] = _shape
for _spec_row in SPECS:
    REQUIRED_TARGET_KEYS[_spec_row['action']] = _required_keys(_spec_row['shape'])
    SHAPES[_spec_row['action']] = _spec_row['shape']
del _shape, _action, _spec_row

# Risk class for the legacy dozen (planners/executors in actuator.py); the
# registry rows carry their own. ALL_RISKS covers the whole catalog.
_LEGACY_RISKS = {
    'project-delete': 'red', 'code-env-delete': 'red', 'image-delete': 'red',
    'db-vacuum': 'amber', 'db-analyze': 'amber', 'plugin-deploy': 'amber',
    'k8s-exec-config-tune': 'amber', 'log-cleanup': 'amber', 'docker-prune': 'amber',
    'k8s-apply-fix': 'red', 'code-env-consolidate': 'red', 'settings-set': 'amber',
}
ALL_RISKS = dict(_LEGACY_RISKS, **RISKS)

BATCH_NOTE = ('Batchable actions (%s) accept targets: [dict, ...] — several '
              'objects, same action, ONE plan and ONE confirm token.'
              % ', '.join(sorted(BATCHABLE)))


# Coarse capability class per action, surfaced by the Agent Settings page:
#   read/write — mutates persisted configuration (restorable/reversible-ish)
#   execute    — runs/stops/cleans/destroys workloads or data, or sends
# (Read-only tools are the sensors in tools_impl.SENSOR_DESCRIPTIONS — a
# separate surface; every action below is a mutation of some kind.)
# Central on purpose: the assert keeps it complete, so adding an action
# without classifying it is a hard import error.
MODES = {
    # read/write — configuration mutations
    'connection-update': 'read/write',
    'k8s-exec-config-tune': 'read/write',
    'settings-set': 'read/write',
    'variables-set': 'read/write',
    'project-variables-set': 'read/write',
    'project-set-cluster': 'read/write',
    'project-change-owner': 'read/write',
    'scenario-disable': 'read/write',
    'scenario-enable': 'read/write',
    'user-enable': 'read/write',
    'user-disable': 'read/write',
    'code-env-consolidate': 'read/write',
    'toolkit-scenario-write': 'read/write',
    'user-update': 'read/write',
    # execute — run/stop/clean/destroy/send
    'project-delete': 'execute',
    'code-env-delete': 'execute',
    'image-delete': 'execute',
    'db-vacuum': 'execute',
    'db-analyze': 'execute',
    'db-reindex': 'execute',
    'plugin-deploy': 'execute',
    'plugin-update': 'execute',
    'plugin-uninstall': 'execute',
    'plugin-code-env-rebuild': 'execute',
    'code-env-update': 'execute',
    'log-cleanup': 'execute',
    'docker-prune': 'execute',
    'k8s-apply-fix': 'execute',
    'cluster-start': 'execute',
    'cluster-stop': 'execute',
    'cluster-detach': 'execute',
    'cluster-pods-cleanup': 'execute',
    'connection-test': 'execute',
    'connection-index': 'execute',
    'connection-delete': 'execute',
    'project-export': 'execute',
    'project-clear-webapp-runs': 'execute',
    'tmp-cleanup': 'execute',
    'exports-cleanup': 'execute',
    'job-logs-cleanup': 'execute',
    'job-kill': 'execute',
    'scenario-kill': 'execute',
    'scenario-run': 'execute',
    'continuous-activity-stop': 'execute',
    'webapp-backend-stop': 'execute',
    'webapp-backend-restart': 'execute',
    'notebook-kernels-shutdown': 'execute',
    'notebook-clear-outputs': 'execute',
    'dataset-clear': 'execute',
    'dataset-delete': 'execute',
    'api-key-delete': 'execute',
    'notification-send': 'execute',
}
assert set(MODES) == set(REQUIRED_TARGET_KEYS), (
    'MODES out of sync with the catalog: missing %s / stale %s'
    % (sorted(set(REQUIRED_TARGET_KEYS) - set(MODES)),
       sorted(set(MODES) - set(REQUIRED_TARGET_KEYS))))
assert set(ALL_RISKS) == set(REQUIRED_TARGET_KEYS), (
    'ALL_RISKS out of sync with the catalog: missing %s / stale %s'
    % (sorted(set(REQUIRED_TARGET_KEYS) - set(ALL_RISKS)),
       sorted(set(ALL_RISKS) - set(REQUIRED_TARGET_KEYS))))
