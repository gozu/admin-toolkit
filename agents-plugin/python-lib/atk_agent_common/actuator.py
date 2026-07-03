"""Actuator: plan-admin-action / execute-admin-action implementations.

Safety model (layered — every gate independent):
  1. plugin `enable_red_actions` master kill-switch (checked at execute)
  2. per-agent `allow_red_actions` + `allowed_actions` (checked by the agent)
  3. plan → HMAC confirm_token (confirm.py) → execute recomputes; drift/expiry kills it
  4. execute requires the literal `confirm: true` from the model
  5. the backend's own @advanced red gate still applies server-side
The plan IS the dry run: it gathers the exact targets + blast radius from
read-only scans and shows the human what will happen. There is no single-call
path to a mutation.

Deliberately excluded (highest blast radius, documented later opt-in):
container-exec / code-env replace, email send, cs-template migrate.
"""

import json

from . import confirm, shaping
from .errors import RedLocked, ToolkitError

ACTIONS = ('project-delete', 'code-env-delete', 'image-delete',
           'db-vacuum', 'db-analyze', 'plugin-deploy', 'k8s-exec-config-tune')

# k8s-exec-config-tune: the only keys an agent may change, all inside
# kubernetesRuntimeConfig.kubernetesResources of one named execution config.
_K8S_TUNABLE_KEYS = ('memRequestMB', 'memLimitMB', 'cpuRequest', 'cpuLimit')


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
                           remediation='Check the key with storage-footprint or adoption-metrics.')
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


_PLANNERS = {
    'k8s-exec-config-tune': _plan_k8s_exec_config_tune,
    'project-delete': _plan_project_delete,
    'code-env-delete': _plan_code_env_delete,
    'db-vacuum': lambda c, h, t, p: _plan_db(c, h, t, p, 'vacuum'),
    'db-analyze': lambda c, h, t, p: _plan_db(c, h, t, p, 'analyze'),
    'image-delete': _plan_image_delete,
    'plugin-deploy': _plan_plugin_deploy,
}


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


def _exec_k8s_exec_config_tune(client, host, target):
    """LOCAL-ONLY first increment: exec configs are DSS general settings — a
    pure DSS API write on the instance running this plugin. Fleet-wide needs a
    red endpoint in the admin-toolkit backend (later consolidation)."""
    if host not in (None, '', 'local'):
        raise ToolkitError(
            'k8s-exec-config-tune can currently only execute on the local DSS (general-settings '
            'write). Host %r is remote.' % host,
            remediation='Run the change on that host\'s own agents plugin, or apply it manually '
                        'in Administration → Settings → Containerized execution.')
    import dataiku
    dss = dataiku.api_client()
    general = dss.get_general_settings()
    raw = general.get_raw()
    config_row, names = _find_exec_config(raw, target['configName'])
    if config_row is None:
        raise ToolkitError('Execution config %r vanished between plan and execute (configs: %s).'
                           % (target['configName'], ', '.join(names)))
    runtime = config_row.setdefault('kubernetesRuntimeConfig', {})
    resources = runtime.setdefault('kubernetesResources', {})
    before = {k: resources.get(k) for k in _K8S_TUNABLE_KEYS}
    resources.update(target['changes'])
    general.save()
    return {'ok': True, 'configName': target['configName'],
            'before': before, 'after': {k: resources.get(k) for k in _K8S_TUNABLE_KEYS}}


_EXECUTORS = {
    'k8s-exec-config-tune': _exec_k8s_exec_config_tune,
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


# ── the two tool impls ───────────────────────────────────────────────────────


def plan_admin_action(client, host='local', action=None, target=None, params=None):
    if action not in ACTIONS:
        return {'error': {'code': 'bad-input',
                          'message': 'action must be one of: %s' % ', '.join(ACTIONS)}}
    host = host or 'local'
    canonical, plan = _PLANNERS[action](client, host, target, params or {})
    password = client.settings.get('red_actions_password') or ''
    if not password:
        return {'plan': plan, 'canonicalTarget': canonical,
                'error': {'code': 'red-locked',
                          'message': 'Plan built, but no Advanced Actions password is configured — '
                                     'no confirm token can be minted and execution is impossible.'}}
    token, exp = confirm.mint(password, action, host, canonical)
    return shaping.enforce_budget({
        'action': action,
        'host': host,
        'canonicalTarget': canonical,
        'plan': plan,
        'confirm_token': token,
        'expiresInSeconds': confirm.TOKEN_TTL_SECONDS,
        'nextStep': ('Show this plan to the user VERBATIM and wait for their explicit confirmation '
                     'in the conversation. Only then call execute-admin-action with this exact '
                     'action/host/target, confirm=true, and the confirm_token.'),
    })


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
                          'message': 'The enable_red_actions master switch is OFF in the plugin settings.',
                          'remediation': 'An administrator must turn it on; agents cannot.'}}
    if not confirm_flag:
        return {'error': {'code': 'not-confirmed',
                          'message': 'execute requires confirm=true, sent only after the user '
                                     'explicitly approved the plan in the conversation.'}}
    password = settings.get('red_actions_password') or ''
    try:
        confirm.verify(password, confirm_token, action, host, target)
    except confirm.ConfirmTokenError as exc:
        return {'error': {'code': 'confirm-token-rejected', 'message': str(exc)}}

    from . import audit
    status, snippet, result = 'error', '', None
    try:
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
        audit_id = audit.record(settings.get('triage_connection'), agent_name, llm_id, host,
                                action, target, provenance, confirm.token_hash(confirm_token),
                                status, snippet)
    out = {'action': action, 'host': host, 'target': target, 'status': status,
           'result': result, 'auditId': audit_id}
    if provenance:
        out['itemRef'] = provenance
    if audit_id is None:
        out['auditWarning'] = ('Audit row could not be written (no triage connection or DB error) '
                               '— the action still ran; check backend logs.')
    return shaping.enforce_budget(out)
