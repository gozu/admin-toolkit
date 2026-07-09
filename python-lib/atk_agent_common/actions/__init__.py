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
REQUIRED_TARGET_KEYS = {}
for _shape in _LEGACY_SHAPES:
    for _action in _shape.split(' {', 1)[0].split('/'):
        REQUIRED_TARGET_KEYS[_action.strip()] = _required_keys(_shape)
for _spec_row in SPECS:
    REQUIRED_TARGET_KEYS[_spec_row['action']] = _required_keys(_spec_row['shape'])
del _shape, _action, _spec_row

BATCH_NOTE = ('Batchable actions (%s) accept targets: [dict, ...] — several '
              'objects, same action, ONE plan and ONE confirm token.'
              % ', '.join(sorted(BATCHABLE)))
