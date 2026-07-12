"""Actuator: plan-admin-action / execute-admin-action implementations.

Safety model (layered — every gate independent):
  1. plugin `enable_red_actions` master kill-switch (checked at execute)
  2. per-action Agent Settings gates (action_gates.py — every non-read action
     is OFF until an admin enables it; checked at plan AND execute)
  3. per-agent `allow_red_actions` + `allowed_actions` (checked by the agent)
  4. plan → HMAC confirm_token (confirm.py) → execute recomputes; drift/expiry kills it
  5. execute requires the literal `confirm: true` from the model
  6. the backend's own @advanced red gate still applies server-side
The plan IS the dry run: it gathers the exact targets + blast radius from
read-only scans and shows the human what will happen. There is no single-call
path to a mutation.

Deliberately excluded (structural, not policy — the agent cannot do these):
DSS/backend restart (kills the toolkit itself), license operations, external
credential creation or rotation (the agent holds no cloud/DB secrets), user
creation and password resets, SSO/LDAP paths (settings blacklist), arbitrary
shell, install.ini / systemd / ulimits (root/host-level files), and deleting
the ADMINTOOLKIT project / the admin-toolkit plugin / the toolkit's own API
key (planner-refused). Also still excluded pending explicit opt-in:
container-exec, cs-template migrate. Messaging sends ARE in the catalog now
(notification-send) — policy-whitelisted and default-disabled like everything
else.

Remediation-suite actions (log-cleanup, docker-prune, k8s-apply-fix,
settings-set, project-clear-webapp-runs) are POLICY-GATED below the model:
the pattern/verb/path whitelists in atk_agent_common.policies are re-enforced
inside the macro scripts / executors, so a compromised or confused LLM cannot
widen the blast radius by rephrasing a target.

The non-legacy catalog lives in atk_agent_common.actions (per-domain SPECS
merged into a registry); this module keeps the 12 legacy planners/executors
and the plan/execute protocol (confirm tokens, batching, audit).
"""

import json

from . import actions as actions_registry
from . import action_gates, confirm, shaping
from .errors import RedLocked, ToolkitError
from .policies import kubectl_policy, settings_paths

_LEGACY_ACTIONS = ('project-delete', 'code-env-delete', 'image-delete',
                   'db-vacuum', 'db-analyze', 'plugin-deploy', 'k8s-exec-config-tune',
                   'log-cleanup', 'docker-prune', 'k8s-apply-fix',
                   'code-env-consolidate', 'settings-set')

ACTIONS = _LEGACY_ACTIONS + actions_registry.NEW_ACTIONS

# Generated prose quoted by every tool-description site (single source).
TARGET_SHAPES = actions_registry.TARGET_SHAPES

# Actions accepting targets[] batching: one plan, one token, N targets.
BATCHABLE_ACTIONS = actions_registry.BATCHABLE

_LOCAL_ONLY_ACTIONS = ('k8s-exec-config-tune', 'log-cleanup', 'docker-prune',
                       'k8s-apply-fix', 'settings-set') + actions_registry.LOCAL_ONLY_EXTRA

# k8s-exec-config-tune: the only keys an agent may change, all inside
# kubernetesRuntimeConfig.kubernetesResources of one named execution config.
_K8S_TUNABLE_KEYS = ('memRequestMB', 'memLimitMB', 'cpuRequest', 'cpuLimit')


def _local_only_warning(action, host):
    """Planner-side heads-up when a LOCAL-ONLY action targets a remote host."""
    if host in (None, '', 'local'):
        return None
    return ('Execution is currently LOCAL-ONLY for %s — this plan targets remote host %r '
            'and execute will refuse.' % (action, host))


def _require_local(action, host):
    if host not in (None, '', 'local'):
        raise ToolkitError(
            '%s can currently only execute on the local DSS. Host %r is remote.' % (action, host),
            remediation="Run the change on that host's own agents plugin, or apply it manually.")


def _blocked_extra(client):
    """Admin-extendable settings-set blacklist (plugin CSV param)."""
    raw = client.settings.get('settings_set_blocked_extra') or ''
    return [p.strip() for p in str(raw).split(',') if p.strip()]


def _backup_folder(client, host):
    folders = (client.get('/api/managed-folders', host=host) or {}).get('folders') or []
    if not folders:
        raise ToolkitError(
            'No managed folder exists in the toolkit support project on host %r — deletes '
            'always back up first, so a backup folder is required.' % host,
            remediation='Create a managed folder in the ADMINTOOLKIT project (any filesystem '
                        'connection) and re-plan.')
    return folders[0]


# ── planners: gather targets + blast radius from read-only scans ─────────────


def _plan_project_delete(client, host, target, params):
    key = (target or {}).get('projectKey') or target
    footprint = client.get('/api/project-footprint', host=host, heavy=True,
                           progress_path='/api/project-footprint/progress')
    row = next((p for p in footprint.get('projects') or [] if p.get('projectKey') == key), None)
    if row is None:
        raise ToolkitError('Project %r not found on host %r.' % (key, host),
                           remediation='Check the key with storage-footprint.')
    inactive = client.get('/api/tools/inactive-projects', host=host)
    inactive_row = next((p for p in inactive.get('projects') or [] if p.get('projectKey') == key), None)
    folder = _backup_folder(client, host)
    return {'projectKey': key}, {
        'summary': 'Back up project %s to managed folder %r, then DELETE it.' % (key, folder['name']),
        'projectSizeGB': round(row.get('totalGB') or 0, 2),
        'owner': row.get('owner'),
        'daysInactive': (inactive_row or {}).get('daysInactive'),
        'warning': None if inactive_row else
                   'Project is NOT in the inactive list — it may be in active use.',
        'backupFolder': folder,
        'irreversible': 'Delete is irreversible apart from the zip backup.',
    }


def _plan_code_env_delete(client, host, target, params):
    name = (target or {}).get('name') or target
    lang = (target or {}).get('lang') or 'python'
    envs = client.get('/api/code-envs', host=host, heavy=True,
                      progress_path='/api/code-envs/progress')
    row = next((e for e in envs.get('codeEnvs') or [] if e.get('name') == name), None)
    if row is None:
        raise ToolkitError('Code env %r not found on host %r.' % (name, host),
                           remediation="Check the name with config-inspect domain='code-envs'.")
    folder = _backup_folder(client, host)
    using = row.get('projectKeys') or []
    return {'name': name, 'lang': lang}, {
        'summary': 'Back up code env %s/%s to %r, then DELETE it.' % (lang, name, folder['name']),
        'usageCount': row.get('usageCount'),
        'projectsUsing': using[:20],
        'warning': ('Env is USED by %d project(s) — deleting will break them.' % len(using)) if using else None,
        'backupFolder': folder,
    }


def _plan_db(client, host, target, params, verb):
    connection = (target or {}).get('connection')
    table = (target or {}).get('table')
    if not connection or not table:
        raise ToolkitError('db-%s target needs {"connection": ..., "table": ...}.' % verb)
    data = client.get('/api/tools/db-health/tables', host=host,
                      params={'connection': connection, 'limit': 500}, heavy=True)
    row = next((tbl for tbl in data.get('tables') or [] if tbl.get('name') == table), None)
    if row is None:
        raise ToolkitError('Table %r not found on connection %r.' % (table, connection),
                           remediation="List tables with db-health view='tables'.")
    return {'connection': connection, 'table': table}, {
        'summary': '%s table %s on connection %s.' % (verb.upper(), table, connection),
        'deadTuples': row.get('deadTuples'),
        'rowCount': row.get('rowCount'),
        'totalSize': row.get('totalSize'),
        'note': '%s takes locks briefly; safe for maintenance windows.' % verb.upper(),
    }


def _plan_image_delete(client, host, target, params):
    images = (target or {}).get('images') or []
    cutoff = (target or {}).get('cutoff')
    provider = (target or {}).get('provider') or 'ecr'
    if not images or not cutoff:
        raise ToolkitError('image-delete target needs {"images": [...], "cutoff": ..., "provider": ...}.')
    result = client.post('/api/tools/image-cleaner/delete', host=host, red=True,
                         json={'provider': provider, 'cutoff': cutoff,
                               'images': images, 'dryRun': True})
    return {'provider': provider, 'cutoff': cutoff, 'images': images}, {
        'summary': 'Delete %d container image(s) from %s older than %s.' % (len(images), provider, cutoff),
        'dryRun': result,
    }


def _plan_plugin_deploy(client, host, target, params):
    plugin_id = (target or {}).get('pluginId')
    target_host = (target or {}).get('targetHostId')
    if not plugin_id or not target_host:
        raise ToolkitError('plugin-deploy target needs {"pluginId": ..., "targetHostId": ...}.')
    plugins = client.get('/api/plugins')
    row = next((p for p in plugins.get('pluginDetails') or [] if p.get('id') == plugin_id), None)
    if row is None:
        raise ToolkitError('Plugin %r not installed on the hub.' % plugin_id)
    client.validate_host(target_host)
    return {'pluginId': plugin_id, 'targetHostId': target_host}, {
        'summary': 'Deploy plugin %s (v%s) from the hub to host %s.'
                   % (plugin_id, row.get('installedVersion'), target_host),
        'isDev': row.get('isDev'),
    }


def _find_exec_config(raw_settings, name):
    configs = ((raw_settings.get('containerSettings') or {}).get('executionConfigs')) or []
    return next((c for c in configs if c.get('name') == name), None), [c.get('name') for c in configs]


def _plan_k8s_exec_config_tune(client, host, target, params):
    """Right-size one containerized execution config (k8s cost optimizer).

    changes = subset of {memRequestMB, memLimitMB, cpuRequest, cpuLimit}
    (-1 = unset, matching DSS semantics). Evidence for 'will this hurt anyone'
    comes from CRU context-type usage; the plan carries current vs proposed so
    the human sees the exact diff they are approving.
    """
    name = (target or {}).get('configName')
    changes = (target or {}).get('changes') or {}
    if not name or not changes:
        raise ToolkitError('k8s-exec-config-tune target needs {"configName": ..., "changes": {...}}.')
    bad = [k for k in changes if k not in _K8S_TUNABLE_KEYS]
    if bad:
        raise ToolkitError('Untunable keys %s — only %s may be changed.' % (bad, list(_K8S_TUNABLE_KEYS)))
    changes = {k: (float(v) if 'cpu' in k else int(v)) for k, v in changes.items()}
    raw = client.get('/api/settings/raw', host=host)
    config_row, names = _find_exec_config(raw, name)
    if config_row is None:
        raise ToolkitError('Execution config %r not found on host %r. Configs: %s'
                           % (name, host, ', '.join(names) or '(none)'))
    current = (((config_row.get('kubernetesRuntimeConfig') or {}).get('kubernetesResources')) or {})
    current_view = {k: current.get(k) for k in _K8S_TUNABLE_KEYS}
    warnings = []
    mem_req = changes.get('memRequestMB', current_view.get('memRequestMB'))
    mem_lim = changes.get('memLimitMB', current_view.get('memLimitMB'))
    if isinstance(mem_req, (int, float)) and isinstance(mem_lim, (int, float)) \
            and mem_lim not in (-1,) and mem_req > mem_lim:
        raise ToolkitError('memRequestMB (%s) would exceed memLimitMB (%s).' % (mem_req, mem_lim))
    for key, new in changes.items():
        cur = current_view.get(key)
        if isinstance(cur, (int, float)) and cur not in (-1,) and new not in (-1,) and new < cur / 4:
            warnings.append('%s drops %s → %s (>75%% cut) — workloads peaking above the new '
                            'value will be throttled or OOM-killed.' % (key, cur, new))
    usage = None
    try:
        cru = client.get('/api/cru', host=host, heavy=True)
        usage = next((c for c in cru.get('contextTypes') or []
                      if 'K8S' in str(c.get('type') or '').upper()), None)
    except ToolkitError:
        pass
    if host not in (None, '', 'local'):
        warnings.append('Execution is currently LOCAL-ONLY (general-settings write on the DSS '
                        'running this plugin) — this plan targets a remote host and execute will refuse.')
    return {'configName': name, 'changes': changes}, {
        'summary': 'Tune k8s resources of execution config %r: %s.' % (
            name, ', '.join('%s %s → %s' % (k, current_view.get(k), v) for k, v in changes.items())),
        'current': current_view,
        'proposed': dict(current_view, **changes),
        'configType': config_row.get('type'),
        'usableBy': config_row.get('usableBy'),
        'observedK8sUsage': usage,
        'warnings': warnings or None,
        'note': ('Affects every new containerized workload using this config; running workloads '
                 'keep their old resources until restarted.'),
    }


def _plan_log_cleanup(client, host, target, params):
    """Rotated-log cleanup. The scan is the evidence; the whitelist that
    refuses non-rotated files / non-whitelisted roots lives inside the macro."""
    target = target or {}
    roots = target.get('roots') or []
    if isinstance(roots, str):
        roots = [r.strip() for r in roots.split(',') if r.strip()]
    min_age = int(target.get('minAgeDays') or client.settings.get('log_cleanup_min_age_days') or 3)
    max_gb = int(target.get('maxDeleteGB') or 20)
    scan = client.get('/api/tools/log-cleaner/scan', host=host,
                      params={'roots': ','.join(roots), 'minAgeDays': min_age})
    if not scan.get('ok'):
        raise ToolkitError('Log-cleaner scan failed: %s' % (scan.get('error') or scan))
    warnings = [w for w in (
        _local_only_warning('log-cleanup', host),
    ) if w]
    for refusal in scan.get('refusedRoots') or []:
        warnings.append('Root %r refused: %s (whitelist: run, jobs, scenarios, code-envs/logs, '
                        'analysis-data/logs, exports/logs, tmp/webappruns).'
                        % (refusal.get('root'), refusal.get('reason')))
    disk_used_pct = None
    try:
        overview = client.get('/api/overview', host=host)
        disk_used_pct = ((overview.get('dipHomeStorage') or {}).get('usedPct'))
    except ToolkitError:
        pass
    per_root = {rel: {'files': e.get('files'), 'gb': round((e.get('bytes') or 0) / (1024 ** 3), 3),
                      'sample': e.get('sample')}
                for rel, e in (scan.get('roots') or {}).items()}
    total_gb = scan.get('totalGB') or round((scan.get('totalBytes') or 0) / (1024 ** 3), 3)
    return {'roots': sorted(roots), 'minAgeDays': min_age, 'maxDeleteGB': max_gb}, {
        'summary': 'Delete rotated log files older than %dd under whitelisted DIP_HOME roots '
                   '— reclaims ~%.2f GB across %s files.' % (min_age, total_gb, scan.get('totalFiles')),
        'perRoot': per_root,
        'totalReclaimableGB': total_gb,
        'totalFiles': scan.get('totalFiles'),
        'diskUsedPct': disk_used_pct,
        'capGB': max_gb,
        'warnings': warnings or None,
        'note': 'Only rotated/compressed logs (*.log.<n>, *.log.*.gz, dated rotations) are '
                'eligible — live *.log files can never match the policy, which is enforced '
                'inside the macro at delete time.',
    }


def _plan_docker_prune(client, host, target, params):
    """Docker cache governance: fixed-argv builder/image prune. daemon.json
    limits are NEVER executed — the plan carries a manual sudo script."""
    target = target or {}
    mode = str(target.get('mode') or 'builder').strip().lower()
    if mode not in ('builder', 'image'):
        raise ToolkitError("docker-prune target.mode must be 'builder' or 'image'.")
    keep_gb = int(target.get('keepStorageGB') or 20)
    until_h = int(target.get('filterUntilHours') or 0)
    usage = client.get('/api/tools/docker/usage', host=host)
    if not usage.get('ok'):
        if usage.get('error') == 'docker-permission':
            raise ToolkitError(usage.get('message') or 'The dataiku user cannot reach the docker daemon.',
                               remediation='Add the dataiku user to the docker group '
                                           '(`sudo usermod -aG docker dataiku`) and restart DSS.')
        raise ToolkitError('Docker usage probe failed: %s'
                           % (usage.get('message') or usage.get('error') or usage))
    warnings = [w for w in (_local_only_warning('docker-prune', host),) if w]
    if usage.get('sameFilesystemAsDssData'):
        warnings.append('DockerRootDir (%s) shares a filesystem with DIP_HOME — docker cache '
                        'growth eats the DSS data mount directly; consider the daemon.json '
                        'cache-limit script below (manual, admin-run).' % usage.get('dockerRootDir'))
    script = None
    try:
        script_res = client.get('/api/tools/docker/daemon-script', host=host,
                                params={'keepStorageGB': keep_gb})
        script = script_res.get('script') if script_res.get('ok') else None
    except ToolkitError:
        pass
    canonical = {'mode': mode, 'keepStorageGB': keep_gb, 'filterUntilHours': until_h}
    return canonical, {
        'summary': ('Prune the docker %s cache (keep-storage %d GB)' % (mode, keep_gb))
                   if mode == 'builder' else
                   ('Prune dangling docker images%s' % (' older than %dh' % until_h if until_h else '')),
        'df': usage.get('df'),
        'estimatedReclaimableGB': usage.get('totalReclaimableGB'),
        'dockerRootDir': usage.get('dockerRootDir'),
        'filesystem': usage.get('filesystem'),
        'sameFilesystemAsDssData': usage.get('sameFilesystemAsDssData'),
        'manualDaemonScript': script,
        'warnings': warnings or None,
        'note': 'The prune runs with a FIXED argv (no shell, no --all, docker group only, no '
                'sudo). The daemon.json script above is display-only for a human admin — the '
                'toolkit never executes it.',
    }


def _plan_k8s_apply_fix(client, host, target, params):
    """Policy-gated kubectl fix. Commands are pre-validated here for fast
    refusal; the macro re-validates authoritatively. Optional execConfigPatch
    reuses the k8s-exec-config-tune diff; optional verifyRule re-runs
    k8s-insights after execution."""
    target = target or {}
    cluster_id = (target.get('clusterId') or '').strip()
    commands = [str(c) for c in (target.get('commands') or [])]
    manifest_yaml = target.get('manifestYaml') or ''
    patch = target.get('execConfigPatch') or None
    verify_rule = (target.get('verifyRule') or '').strip() or None
    if not cluster_id:
        raise ToolkitError('k8s-apply-fix target needs {"clusterId": ..., "commands": [...]}.')
    if not commands and not patch:
        raise ToolkitError('k8s-apply-fix needs at least one of commands[] or execConfigPatch.')

    refused = []
    needs_manifest = False
    for cmd in commands:
        ok, reason, parsed = kubectl_policy.validate(cmd)
        if not ok:
            refused.append({'command': cmd[:300], 'reason': reason})
        elif parsed['verb'] == 'apply':
            needs_manifest = True
    if needs_manifest:
        ok, reason, _docs = kubectl_policy.validate_manifest(manifest_yaml)
        if not ok:
            refused.append({'command': 'apply -f {manifest}', 'reason': reason})
    if refused:
        raise ToolkitError(
            'kubectl policy refused %d command(s): %s' % (
                len(refused), '; '.join('%(command)s → %(reason)s' % r for r in refused[:5])),
            remediation='Only apply/patch/delete/label/annotate/scale/rollout-restart on '
                        'namespaced workload kinds are allowed (no secrets, no cluster-scoped '
                        'kinds, no --all/--force, kube-system limited to patch/label/annotate/'
                        'rollout-restart on ds/deploy). Relay this refusal to the user verbatim.')

    plan = {'summary': 'Run %d policy-validated kubectl command(s) on cluster %r%s%s.' % (
        len(commands), cluster_id,
        ' + tune exec config %r' % (patch or {}).get('configName') if patch else '',
        ' and verify rule %r afterwards' % verify_rule if verify_rule else '')}
    warnings = [w for w in (_local_only_warning('k8s-apply-fix', host),) if w]

    if commands:
        preview = client.post('/api/tools/k8s-apply/preview', host=host,
                              json={'clusterId': cluster_id, 'commands': commands,
                                    'manifestYaml': manifest_yaml})
        if not preview.get('ok'):
            if preview.get('refused'):
                raise ToolkitError('kubectl policy refused (macro-side): %s'
                                   % json.dumps(preview['refused'])[:600])
            raise ToolkitError('k8s-apply preview failed: %s' % (preview.get('error') or preview))
        plan['preview'] = preview.get('results')
        plan['manifestDocs'] = preview.get('manifestDocs') or None

    if patch:
        name = (patch or {}).get('configName')
        changes = (patch or {}).get('changes') or {}
        if not name or not changes:
            raise ToolkitError('execConfigPatch needs {"configName": ..., "changes": {...}}.')
        bad = [k for k in changes if k not in _K8S_TUNABLE_KEYS]
        if bad:
            raise ToolkitError('Untunable exec-config keys %s — only %s may be changed.'
                               % (bad, list(_K8S_TUNABLE_KEYS)))
        raw = client.get('/api/settings/raw', host=host)
        config_row, names = _find_exec_config(raw, name)
        if config_row is None:
            raise ToolkitError('Execution config %r not found on host %r. Configs: %s'
                               % (name, host, ', '.join(names) or '(none)'))
        current = (((config_row.get('kubernetesRuntimeConfig') or {}).get('kubernetesResources')) or {})
        current_view = {k: current.get(k) for k in _K8S_TUNABLE_KEYS}
        plan['execConfigPatch'] = {'configName': name, 'current': current_view,
                                   'proposed': dict(current_view, **changes)}
        patch = {'configName': name,
                 'changes': {k: (float(v) if 'cpu' in k else int(v)) for k, v in changes.items()}}

    if verify_rule:
        plan['verification'] = ('After execution, k8s-insights re-runs with rules_filter=%r '
                                'and the result reports whether the rule still fires.' % verify_rule)
    plan['warnings'] = warnings or None
    canonical = {'clusterId': cluster_id, 'commands': commands,
                 'manifestYaml': manifest_yaml or None, 'execConfigPatch': patch,
                 'verifyRule': verify_rule}
    return canonical, plan


def _plan_code_env_consolidate(client, host, target, params):
    """Consolidate code-env usage onto a target env. The backend replace
    endpoint's dry run enumerates the exact usage rows — that table IS the
    evidence the human approves."""
    target = target or {}
    src = (target.get('sourceEnvName') or '').strip()
    tgt = (target.get('targetEnvName') or '').strip()
    lang = (target.get('language') or 'python').strip().lower()
    project_keys = sorted(str(k) for k in (target.get('projectKeys') or [])) or None
    usage_types = sorted(str(t) for t in (target.get('usageTypes') or [])) or None
    retire = bool(target.get('retireSource'))
    if not src or not tgt:
        raise ToolkitError('code-env-consolidate target needs {"sourceEnvName": ..., '
                           '"targetEnvName": ...} (optional language, projectKeys, usageTypes, '
                           'retireSource).')
    dry = client.post('/api/code-envs/replace', host=host, red=True,
                      json={'sourceEnvName': src, 'targetEnvName': tgt,
                            'sourceLanguage': lang, 'dryRun': True,
                            'projectKeys': project_keys, 'usageTypes': usage_types})
    rows = dry.get('results') or []
    warnings = []
    if retire and (project_keys or usage_types):
        warnings.append('retireSource with projectKeys/usageTypes filters is dangerous: usages '
                        'OUTSIDE the filter keep pointing at the source env and will break when '
                        'it is deleted.')
    if retire:
        folder = _backup_folder(client, host)  # deletes always back up first
        warnings.append('Source env %s/%s will be backed up to %r and DELETED after a fully '
                        'successful replacement.' % (lang, src, folder['name']))
    canonical = {'sourceEnvName': src, 'targetEnvName': tgt, 'language': lang,
                 'projectKeys': project_keys, 'usageTypes': usage_types, 'retireSource': retire}
    return canonical, {
        'summary': 'Repoint %d usage(s) of code env %s/%s to %s%s.' % (
            dry.get('matchedRows') or 0, lang, src, tgt,
            ', then retire the source env' if retire else ''),
        'matchedRows': dry.get('matchedRows'),
        'usageRows': [{'projectKey': r.get('projectKey'), 'objectType': r.get('objectType'),
                       'objectId': r.get('objectId'), 'objectName': r.get('objectName')}
                      for r in rows[:40]],
        'usageRowsTruncated': max(0, len(rows) - 40) or None,
        'warnings': warnings or None,
        'note': 'Applying the replacement clears the backend code-env/footprint caches — the '
                'next heavy scans run cold.',
    }


def _plan_settings_set(client, host, target, params):
    """Generic gated settings mutator. Blacklist (security/auth/licensing +
    secret-material segments) is checked here AND re-checked at execute; the
    observed current value is bound into the HMAC-signed target, so any drift
    between plan and execute invalidates the token for free."""
    target = target or {}
    path = (target.get('path') or '').strip()
    if not path or 'newValue' not in target:
        raise ToolkitError('settings-set target needs {"path": ..., "newValue": ...} '
                           '(dot/index path into DSS general settings, e.g. '
                           '"containerSettings.executionConfigs[2].kubernetesNamespace").')
    ok, reason = settings_paths.check_path(path, extra_blocked=_blocked_extra(client))
    if not ok:
        raise ToolkitError('settings-set refused: %s' % reason,
                           remediation='Security/auth/licensing settings and anything touching '
                                       'secret material are never agent-mutable. Relay this '
                                       'refusal to the user verbatim.')
    raw = client.get('/api/settings/raw', host=host)
    current = settings_paths.get_at(raw, path)
    new_value = target.get('newValue')
    warnings = [w for w in (_local_only_warning('settings-set', host),) if w]
    if current is None:
        warnings.append('The path currently resolves to nothing — execute will only succeed if '
                        'every intermediate container exists (settings-set never creates '
                        'subtrees).')
    canonical = {'path': path, 'newValue': new_value, 'expectedCurrent': current}
    return canonical, {
        'summary': 'Set DSS general setting %s: %s → %s.' % (
            path, json.dumps(current, default=str)[:120], json.dumps(new_value, default=str)[:120]),
        'path': path,
        'currentValue': current,
        'proposedValue': new_value,
        'warnings': warnings or None,
        'note': 'The current value is bound into the confirm token — if anyone changes this '
                'setting between plan and execute, execution refuses. The change lands in the '
                'restorable settings history (agents.settings_changes).',
    }


_PLANNERS = {
    'k8s-exec-config-tune': _plan_k8s_exec_config_tune,
    'log-cleanup': _plan_log_cleanup,
    'docker-prune': _plan_docker_prune,
    'k8s-apply-fix': _plan_k8s_apply_fix,
    'code-env-consolidate': _plan_code_env_consolidate,
    'settings-set': _plan_settings_set,
    'project-delete': _plan_project_delete,
    'code-env-delete': _plan_code_env_delete,
    'db-vacuum': lambda c, h, t, p: _plan_db(c, h, t, p, 'vacuum'),
    'db-analyze': lambda c, h, t, p: _plan_db(c, h, t, p, 'analyze'),
    'image-delete': _plan_image_delete,
    'plugin-deploy': _plan_plugin_deploy,
}
_PLANNERS.update(actions_registry.PLANNERS)


# ── executors: drive the backend red endpoints ───────────────────────────────


def _exec_project_delete(client, host, target):
    key = target['projectKey']
    folder = _backup_folder(client, host)
    return client.delete('/api/tools/project-cleaner/%s' % key, host=host,
                         params={'folderId': folder['id']},
                         headers={'X-Confirm-Name': key})


def _exec_code_env_delete(client, host, target):
    folder = _backup_folder(client, host)
    return client.delete('/api/tools/code-env-cleaner/%s/%s' % (target['lang'], target['name']),
                         host=host, params={'folderId': folder['id']},
                         headers={'X-Confirm-Name': target['name']})


def _apply_exec_config_changes(config_name, changes):
    """Shared LOCAL general-settings write for the exec-config family
    (k8s-exec-config-tune, k8s-apply-fix's execConfigPatch half)."""
    import dataiku
    dss = dataiku.api_client()
    general = dss.get_general_settings()
    raw = general.get_raw()
    config_row, names = _find_exec_config(raw, config_name)
    if config_row is None:
        raise ToolkitError('Execution config %r vanished between plan and execute (configs: %s).'
                           % (config_name, ', '.join(names)))
    runtime = config_row.setdefault('kubernetesRuntimeConfig', {})
    resources = runtime.setdefault('kubernetesResources', {})
    before = {k: resources.get(k) for k in _K8S_TUNABLE_KEYS}
    resources.update(changes)
    general.save()
    return {'ok': True, 'configName': config_name,
            'before': before, 'after': {k: resources.get(k) for k in _K8S_TUNABLE_KEYS}}


def _exec_k8s_exec_config_tune(client, host, target):
    """LOCAL-ONLY first increment: exec configs are DSS general settings — a
    pure DSS API write on the instance running this plugin. Fleet-wide needs a
    red endpoint in the admin-toolkit backend (later consolidation)."""
    _require_local('k8s-exec-config-tune', host)
    return _apply_exec_config_changes(target['configName'], target['changes'])


def _exec_log_cleanup(client, host, target):
    """The macro re-walks the filesystem and re-applies the rotated-log policy
    per file — it never trusts this call's parameters beyond scoping."""
    _require_local('log-cleanup', host)
    result = client.post('/api/tools/log-cleaner/delete', host=host, red=True,
                         json={'roots': target.get('roots') or [],
                               'minAgeDays': target.get('minAgeDays'),
                               'maxDeleteGB': target.get('maxDeleteGB'),
                               'dryRun': False})
    if not result.get('ok'):
        raise ToolkitError('Log cleanup refused/failed: %s'
                           % (result.get('message') or result.get('error') or result))
    return result


def _exec_docker_prune(client, host, target):
    _require_local('docker-prune', host)
    result = client.post('/api/tools/docker/prune', host=host, red=True,
                         json={'mode': target['mode'],
                               'keepStorageGB': target.get('keepStorageGB'),
                               'filterUntilHours': target.get('filterUntilHours'),
                               'dryRun': False})
    if not result.get('ok'):
        raise ToolkitError('Docker prune failed: %s'
                           % (result.get('message') or result.get('error') or result))
    return result


def _exec_k8s_apply_fix(client, host, target):
    """kubectl commands (macro re-validates + stops at first failure), then
    the optional exec-config patch, then the optional verification re-audit."""
    _require_local('k8s-apply-fix', host)
    out = {'ok': True}
    commands = target.get('commands') or []
    if commands:
        result = client.post('/api/tools/k8s-apply/execute', host=host, red=True,
                             json={'clusterId': target['clusterId'], 'commands': commands,
                                   'manifestYaml': target.get('manifestYaml') or ''})
        out['kubectl'] = result
        if not result.get('ok'):
            detail = result.get('refused') or result.get('error') or result
            raise ToolkitError('k8s-apply execution failed: %s' % json.dumps(detail, default=str)[:600])
    patch = target.get('execConfigPatch')
    if patch:
        out['execConfigPatch'] = _apply_exec_config_changes(patch['configName'], patch['changes'])
    verify_rule = target.get('verifyRule')
    if verify_rule:
        try:
            audit = client.stream_final('/api/k8s-insights/stream', host=host,
                                        params={'clusterId': target['clusterId'],
                                                'rulesFilter': verify_rule})
            findings = [f for f in (audit.get('findings') or [])
                        if f.get('rule') == verify_rule or f.get('id', '').startswith(verify_rule)]
            out['verification'] = {'ruleId': verify_rule, 'stillFiring': bool(findings),
                                   'findings': findings[:3]}
        except ToolkitError as exc:
            out['verification'] = {'ruleId': verify_rule, 'stillFiring': None,
                                   'error': 'verification re-audit failed: %s' % exc.message}
    return out


def _exec_code_env_consolidate(client, host, target):
    result = client.post('/api/code-envs/replace', host=host, red=True,
                         json={'sourceEnvName': target['sourceEnvName'],
                               'targetEnvName': target['targetEnvName'],
                               'sourceLanguage': target.get('language') or 'python',
                               'dryRun': False,
                               'projectKeys': target.get('projectKeys'),
                               'usageTypes': target.get('usageTypes')})
    failed = result.get('failedRows') or 0
    out = {'ok': failed == 0,
           'replace': {k: result.get(k) for k in
                       ('matchedRows', 'updatedRows', 'skippedRows', 'failedRows')},
           'failedDetail': [r for r in (result.get('results') or [])
                            if r.get('status') == 'failed'][:10] or None}
    if target.get('retireSource'):
        if failed:
            out['retireSkipped'] = ('%d row(s) failed to update — the source env was NOT '
                                    'retired; fix the failures and retire manually.' % failed)
        else:
            out['retire'] = _exec_code_env_delete(
                client, host, {'lang': target.get('language') or 'python',
                               'name': target['sourceEnvName']})
    return out


def _exec_settings_set(client, host, target):
    """Re-read, re-check the blacklist (never trust the plan), refuse on
    drift, then write. expectedCurrent was bound into the HMAC token, so a
    tampered target already died in confirm.verify."""
    _require_local('settings-set', host)
    path = target['path']
    ok, reason = settings_paths.check_path(path, extra_blocked=_blocked_extra(client))
    if not ok:
        raise ToolkitError('settings-set refused at execute: %s' % reason)
    import dataiku
    general = dataiku.api_client().get_general_settings()
    raw = general.get_raw()
    current = settings_paths.get_at(raw, path)
    expected = target.get('expectedCurrent')
    if json.dumps(current, sort_keys=True, default=str) != json.dumps(expected, sort_keys=True, default=str):
        raise ToolkitError(
            'Setting %s drifted between plan and execute (expected %s, found %s) — refusing.'
            % (path, json.dumps(expected, default=str)[:200], json.dumps(current, default=str)[:200]),
            remediation='Re-run plan-admin-action to capture the new current value.')
    try:
        settings_paths.set_at(raw, path, target.get('newValue'))
    except settings_paths.SettingsPathError as exc:
        raise ToolkitError('settings-set write failed: %s' % exc)
    general.save()
    return {'ok': True, 'path': path, 'before': current, 'after': target.get('newValue')}


# Settings-mutating actions record per-key history rows (agents.settings_changes,
# K97 doctrine: prior value + last-50-per-item restore). Dispatch table — add an
# entry when a future executor mutates settings and returns before/after.
def _exec_config_change_items(config_result, changed_keys):
    before = config_result.get('before') or {}
    after = config_result.get('after') or {}
    name = config_result.get('configName')
    return [{'itemKey': 'execConfig:%s:%s' % (name, key),
             'before': before.get(key), 'after': after.get(key)}
            for key in changed_keys]


def _changes_k8s_exec_config_tune(target, result):
    changed = (target or {}).get('changes') or {}
    return _exec_config_change_items(result, changed)


def _changes_settings_set(target, result):
    return [{'itemKey': 'settings:%s' % result.get('path'),
             'before': result.get('before'), 'after': result.get('after')}]


def _changes_k8s_apply_fix(target, result):
    patch_result = result.get('execConfigPatch')
    if not isinstance(patch_result, dict) or not patch_result.get('ok'):
        return []
    changed = ((target or {}).get('execConfigPatch') or {}).get('changes') or {}
    return _exec_config_change_items(patch_result, changed)


_SETTINGS_CHANGE_HOOKS = {
    'k8s-exec-config-tune': _changes_k8s_exec_config_tune,
    'settings-set': _changes_settings_set,
    'k8s-apply-fix': _changes_k8s_apply_fix,
}
_SETTINGS_CHANGE_HOOKS.update(actions_registry.SETTINGS_CHANGE_HOOKS)


def _settings_changes_from_result(action, target, result):
    hook = _SETTINGS_CHANGE_HOOKS.get(action)
    if hook is None or not isinstance(result, dict) or not result.get('ok'):
        return []
    return hook(target, result)


_EXECUTORS = {
    'k8s-exec-config-tune': _exec_k8s_exec_config_tune,
    'log-cleanup': _exec_log_cleanup,
    'docker-prune': _exec_docker_prune,
    'k8s-apply-fix': _exec_k8s_apply_fix,
    'code-env-consolidate': _exec_code_env_consolidate,
    'settings-set': _exec_settings_set,
    'project-delete': _exec_project_delete,
    'code-env-delete': _exec_code_env_delete,
    'db-vacuum': lambda c, h, t: c.post('/api/tools/db-health/vacuum', host=h, red=True,
                                        json={'connection': t['connection'], 'table': t['table']}),
    'db-analyze': lambda c, h, t: c.post('/api/tools/db-health/analyze', host=h, red=True,
                                         json={'connection': t['connection'], 'table': t['table']}),
    'image-delete': lambda c, h, t: c.post('/api/tools/image-cleaner/delete', host=h, red=True,
                                           json={'provider': t['provider'], 'cutoff': t['cutoff'],
                                                 'images': t['images'], 'dryRun': False}),
    'plugin-deploy': lambda c, h, t: c.post('/api/tools/plugins/deploy-one', red=True,
                                            json={'pluginId': t['pluginId'],
                                                  'targetHostId': t['targetHostId']}),
}
_EXECUTORS.update(actions_registry.EXECUTORS)


# ── the two tool impls ───────────────────────────────────────────────────────


def _canonical_sort_key(canonical):
    return json.dumps(canonical, sort_keys=True, separators=(',', ':'), default=str)


def _normalize_targets(target, targets):
    """One list of target dicts from the target/targets pair (raises on both
    empty). A single-element targets[] collapses to the plain single path."""
    if isinstance(targets, list) and targets:
        clean = [t for t in targets if isinstance(t, dict)]
        if len(clean) != len(targets):
            raise ToolkitError('targets must be a list of target dicts.')
        if target is not None and target != {}:
            raise ToolkitError('Pass either target or targets, not both.')
        return clean
    if target is None:
        raise ToolkitError('A target (or targets[]) is required.')
    return [target]


def _plan_batch(client, host, action, target_list, params):
    """Per-target planner runs → ONE combined canonical + plan. Canonicals
    sort deterministically so the same batch always signs identically."""
    pairs = [_PLANNERS[action](client, host, t, params) for t in target_list]
    pairs.sort(key=lambda pair: _canonical_sort_key(pair[0]))
    canonical = {'batchTargets': [c for c, _ in pairs]}
    per_target = []
    warnings = []
    for i, (target_canonical, target_plan) in enumerate(pairs):
        per_target.append({'target': target_canonical,
                           'summary': target_plan.get('summary')})
        raw_warning = target_plan.get('warning') or target_plan.get('warnings')
        for w in ([raw_warning] if isinstance(raw_warning, str) else (raw_warning or [])):
            warnings.append('[target %d] %s' % (i + 1, w))
    first_plan = pairs[0][1]
    plan = {
        'summary': 'BATCH %s × %d targets — ONE approval and ONE confirm token cover '
                   'every target below; execution is per-target with per-target results.'
                   % (action, len(pairs)),
        'targetCount': len(pairs),
        'targets': per_target,
        'warnings': warnings or None,
    }
    for key in ('backupFolder', 'irreversible', 'note'):
        if key in first_plan:
            plan[key] = first_plan[key]
    return canonical, plan


def plan_admin_action(client, host='local', action=None, target=None, params=None,
                      targets=None):
    if action not in ACTIONS:
        return {'error': {'code': 'bad-input',
                          'message': 'action must be one of: %s' % ', '.join(ACTIONS)}}
    if not action_gates.action_enabled(client, action):
        return action_gates.disabled_error(action)
    host = host or 'local'
    target_list = _normalize_targets(target, targets)
    if len(target_list) > 1 and action not in BATCHABLE_ACTIONS:
        raise ToolkitError(
            '%s does not accept batched targets — plan each target separately. '
            'Batchable actions: %s.' % (action, ', '.join(sorted(BATCHABLE_ACTIONS))))
    if len(target_list) == 1:
        canonical, plan = _PLANNERS[action](client, host, target_list[0], params or {})
    else:
        canonical, plan = _plan_batch(client, host, action, target_list, params or {})
    password = client.settings.get('master_password') or ''
    if not password:
        return {'plan': plan, 'canonicalTarget': canonical,
                'error': {'code': 'red-locked',
                          'message': 'Plan built, but no master password is configured — '
                                     'no confirm token can be minted and execution is impossible.'}}
    token, exp = confirm.mint(password, action, host, canonical)
    # Budget-trim the DISPLAY envelope only, then attach canonicalTarget WHOLE:
    # the confirm token is minted over `canonical`, so a trimmed canonicalTarget
    # (e.g. a multi-step custom-code scenario whose steps list gets halved) would
    # no longer match the token and execute would refuse. The full step code
    # therefore always survives here for the human to review, even if the plan's
    # display copy was truncated (which enforce_budget notes).
    out = shaping.enforce_budget({
        'action': action,
        'host': host,
        'plan': plan,
        'confirm_token': token,
        'expiresInSeconds': confirm.TOKEN_TTL_SECONDS,
        'nextStep': ('Show this plan to the user VERBATIM and wait for their explicit confirmation '
                     'in the conversation. Only then call execute-admin-action with this exact '
                     'action/host/target, confirm=true, and the confirm_token.'),
    })
    out['canonicalTarget'] = canonical
    return out


def _execute_batch(client, host, action, batch_targets):
    """Per-entry execution with continue-on-error. Returns (status, result,
    settings_changes): status 'ok' / 'partial' / 'error', one result row per
    entry, settings-change history collected per SUCCESSFUL entry."""
    per_target = []
    changes = []
    ok_count = 0
    for entry in batch_targets:
        try:
            entry_result = _EXECUTORS[action](client, host, entry)
            per_target.append({'target': entry, 'status': 'ok', 'result': entry_result})
            ok_count += 1
            changes.extend(_settings_changes_from_result(action, entry, entry_result))
        except (RedLocked, ToolkitError) as exc:
            per_target.append({'target': entry, 'status': 'error',
                               'error': exc.to_output()['error']})
    error_count = len(batch_targets) - ok_count
    status = 'ok' if error_count == 0 else ('partial' if ok_count else 'error')
    result = {'ok': error_count == 0, 'batch': True,
              'okCount': ok_count, 'errorCount': error_count,
              'perTarget': per_target}
    return status, result, changes


def execute_admin_action(client, host='local', action=None, target=None,
                         confirm_flag=False, confirm_token=None,
                         agent_name='unknown', llm_id=None, provenance=None):
    host = host or 'local'
    settings = client.settings
    if action not in ACTIONS:
        return {'error': {'code': 'bad-input',
                          'message': 'action must be one of: %s' % ', '.join(ACTIONS)}}
    if not settings.get('enable_red_actions'):
        return {'error': {'code': 'red-actions-disabled',
                          'message': 'The agentic-actions master switch is OFF in the plugin settings.',
                          'remediation': 'An administrator must turn it on; agents cannot.'}}
    if not action_gates.action_enabled(client, action):
        return action_gates.disabled_error(action)
    if not confirm_flag:
        return {'error': {'code': 'not-confirmed',
                          'message': 'execute requires confirm=true, sent only after the user '
                                     'explicitly approved the plan in the conversation.'}}
    password = settings.get('master_password') or ''
    try:
        confirm.verify(password, confirm_token, action, host, target)
    except confirm.ConfirmTokenError as exc:
        return {'error': {'code': 'confirm-token-rejected', 'message': str(exc)}}

    from . import audit
    batch_targets = target.get('batchTargets') if isinstance(target, dict) else None
    status, snippet, result = 'error', '', None
    batch_changes = []
    try:
        if isinstance(batch_targets, list):
            status, result, batch_changes = _execute_batch(client, host, action, batch_targets)
        else:
            result = _EXECUTORS[action](client, host, target)
            status = 'ok'
        snippet = json.dumps(result, default=str)[:500]
    except RedLocked as exc:
        snippet = exc.message
        result = exc.to_output()
    except ToolkitError as exc:
        snippet = exc.message
        result = exc.to_output()
    finally:
        # provenance (e.g. action-item batch/item refs) lands in the params
        # column so audit rows can be traced back to the proposing checklist.
        # A batch is ONE audit row: the signed target carries every entry.
        audit_id = audit.record(audit.resolve_connection(settings), agent_name, llm_id, host,
                                action, target, provenance, confirm.token_hash(confirm_token),
                                status, snippet)
    out = {'action': action, 'host': host, 'target': target, 'status': status,
           'result': result, 'auditId': audit_id}
    if provenance:
        out['itemRef'] = provenance
    if audit_id is None:
        out['auditWarning'] = ('Audit row could not be written (no triage connection or DB error) '
                               '— the action still ran; check backend logs.')
    if isinstance(batch_targets, list):
        changes = batch_changes
    else:
        changes = _settings_changes_from_result(action, target, result) if status == 'ok' else []
    if changes:
        written = audit.record_settings_changes(audit.resolve_connection(settings), host, changes,
                                                agent=agent_name, audit_id=audit_id)
        if written is None:
            out['historyWarning'] = ('Settings-change history NOT recorded (audit DB not '
                                     'configured or unreachable) — this change will not be '
                                     'restorable from history. Tell the admin.')
        else:
            out['settingsHistory'] = {
                'recorded': written,
                'note': 'Prior values recorded; restorable from the last 50 changes per item.',
            }
    return shaping.enforce_budget(out)
