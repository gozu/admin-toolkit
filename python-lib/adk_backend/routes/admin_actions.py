"""Agent actuator backend — small pure-dataikuapi action implementations.

One POST dispatch (`/api/tools/admin-actions/<action>`, @advanced) plus cheap
inventory GETs. Every impl runs on `g.client` (the per-host DSSClient), so a
new action here is automatically fleet-routable — the multi-instance rule's
B-api pattern. Host-bound filesystem work stays out: that goes through the
fs-cleanup macro (also in this module, `/api/tools/fs-cleanup/*`).

Backups go to a managed folder in the toolkit support project (same doctrine
as the project/code-env cleaners: deletes always back up first). Connection
definition backups may carry credential material — the folder is admin-scoped.
"""
import json
import logging
import re
import tempfile

from flask import Blueprint, g, jsonify, request

from adk_backend.clients import _active_support_project
from adk_backend.macros import _fs_cleanup_macro
from adk_backend.utils import advanced
from atk_agent_common.policies import settings_paths

bp = Blueprint('admin_actions', __name__)
_LOGGER = logging.getLogger(__name__)

# The toolkit must never uninstall itself (or lose its own support tooling).
_PROTECTED_PLUGIN_IDS = ('admin-toolkit',)


def _backup_folder_handle(client, folder_id):
    """Validated managed-folder handle in the toolkit support project
    (factored from the project-cleaner's backup half)."""
    plugin_project = _active_support_project(client)
    dest = plugin_project.get_managed_folder(folder_id)
    dest.get_definition()  # verify it exists
    return dest


def _safe_name(name):
    return re.sub(r'[^a-zA-Z0-9._-]', '_', str(name or ''))


def _backup_json(client, folder_id, filename, payload):
    dest = _backup_folder_handle(client, folder_id)
    data = json.dumps(payload, indent=2, default=str).encode('utf-8')
    import io
    dest.put_file(filename, io.BytesIO(data))
    return filename


def _redact_secrets(node):
    """Recursively replace values whose key matches the secret-material
    blacklist — definition reads must never leak credentials to an agent."""
    if isinstance(node, dict):
        return {k: ('<redacted>' if isinstance(k, str)
                    and settings_paths.BLOCKED_SEGMENT_RE.search(k)
                    else _redact_secrets(v))
                for k, v in node.items()}
    if isinstance(node, list):
        return [_redact_secrets(v) for v in node]
    return node


# ── inventory GETs (read-only grounding for planners) ────────────────────────


@bp.route('/api/tools/admin-actions/connection-definition')
def api_admin_actions_connection_definition():
    """Secret-redacted definition of one connection — the read side of
    connection-update's drift guard."""
    name = (request.args.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name query parameter is required'}), 400
    try:
        definition = g.client.get_connection(name).get_definition()
    except Exception as exc:
        return jsonify({'error': 'Connection %r not readable: %s' % (name, str(exc)[:200])}), 404
    return jsonify({'ok': True, 'name': name, 'definition': _redact_secrets(definition)})


@bp.route('/api/tools/admin-actions/plugin-usages')
def api_admin_actions_plugin_usages():
    """Usage preflight for plugin-uninstall: every component usage across
    projects, plus unresolvable (missing-type) usages."""
    plugin_id = (request.args.get('pluginId') or '').strip()
    if not plugin_id:
        return jsonify({'error': 'pluginId query parameter is required'}), 400
    try:
        raw = g.client.get_plugin(plugin_id).list_usages().get_raw()
    except Exception as exc:
        return jsonify({'error': 'Plugin %r usages not readable: %s'
                                 % (plugin_id, str(exc)[:200])}), 404
    usages = raw.get('usages') or []
    return jsonify({'ok': True, 'pluginId': plugin_id,
                    'usageCount': len(usages), 'usages': usages[:50],
                    'missingTypes': raw.get('missingTypes') or []})


_INVENTORY_PICKS = {
    'scenarios': ('id', 'name', 'type', 'active', 'running', 'nextRun'),
    'webapps': ('id', 'name', 'type'),
    'jobs': ('id', 'state', 'jobType', 'initiator', 'startTime'),
}


def _pick(row, keys):
    return {k: row.get(k) for k in keys if k in row}


@bp.route('/api/tools/admin-actions/inventory')
def api_admin_actions_inventory():
    """Read-only target grounding for the runtime/user planners and the
    config_inspect long-tail domains. Shapes are deliberately tiny — ids and
    the state fields plans warn about, nothing else."""
    domain = (request.args.get('domain') or '').strip()
    project_key = (request.args.get('projectKey') or '').strip()
    active_only = request.args.get('active') in ('1', 'true')
    client = g.client
    try:
        if domain == 'scenarios':
            if not project_key:
                return jsonify({'error': 'projectKey is required for scenarios'}), 400
            rows = client.get_project(project_key).list_scenarios(as_type='listitems')
            rows = [dict(r) if not isinstance(r, dict) else r for r in rows]
            return jsonify({'ok': True, 'scenarios':
                            [_pick(r, _INVENTORY_PICKS['scenarios']) for r in rows[:200]]})
        if domain == 'webapps':
            if not project_key:
                return jsonify({'error': 'projectKey is required for webapps'}), 400
            out = []
            for w in client.get_project(project_key).list_webapps():
                raw = w if isinstance(w, dict) else getattr(w, 'raw', {})
                row = _pick(raw, _INVENTORY_PICKS['webapps'])
                if len(out) < 20:  # backend state is one call per webapp — cap it
                    try:
                        handle = client.get_project(project_key).get_webapp(raw.get('id'))
                        row['backendRunning'] = bool(handle.get_state().running)
                    except Exception:
                        row['backendRunning'] = None
                out.append(row)
            return jsonify({'ok': True, 'webapps': out[:100]})
        if domain == 'jobs':
            if not project_key:
                return jsonify({'error': 'projectKey is required for jobs'}), 400
            rows = []
            for j in client.get_project(project_key).list_jobs()[:100]:
                d = j.get('def') or {}
                rows.append({'id': d.get('id'), 'state': j.get('state'),
                             'jobType': d.get('type'), 'initiator': d.get('initiator'),
                             'startTime': j.get('startTime') or d.get('initiationTimestamp')})
            return jsonify({'ok': True, 'jobs': rows})
        if domain == 'continuous-activities':
            if not project_key:
                return jsonify({'error': 'projectKey is required for '
                                         'continuous-activities'}), 400
            rows = []
            for a in client.get_project(project_key).list_continuous_activities():
                raw = getattr(a, 'raw', None) or {}
                status = {}
                try:
                    status = a.get_status() or {}
                except Exception:
                    pass
                rows.append({'recipeId': raw.get('recipeId') or getattr(a, 'recipe_id', None),
                             'desiredState': status.get('desiredState'),
                             'mainLoopState': (status.get('mainLoopState') or {}).get('futureInfo',
                                              {}).get('alive') if status else None})
            return jsonify({'ok': True, 'activities': rows[:100]})
        if domain == 'notebooks':
            projects = ([project_key] if project_key else
                        [p['projectKey'] for p in client.list_projects()][:100])
            rows, note = [], None
            if not project_key and len(projects) == 100:
                note = 'Capped at the first 100 projects.'
            for pk in projects:
                try:
                    nbs = client.get_project(pk).list_jupyter_notebooks(
                        active=active_only, as_type='listitems')
                except Exception:
                    continue
                for nb in nbs:
                    raw = getattr(nb, '_data', None) or (nb if isinstance(nb, dict) else {})
                    rows.append({'projectKey': pk, 'name': raw.get('name'),
                                 'language': raw.get('language'),
                                 'kernelSpec': (raw.get('kernelSpec') or {}).get('name'),
                                 'lastModifiedOn': raw.get('lastModifiedOn')})
                if len(rows) >= 200:
                    note = (note or '') + ' Notebook list truncated at 200.'
                    break
            return jsonify({'ok': True, 'notebooks': rows[:200], 'note': note})
        if domain == 'datasets':
            if not project_key:
                return jsonify({'error': 'projectKey is required for datasets'}), 400
            project = client.get_project(project_key)
            exposed = set()
            try:
                raw = project.get_settings().get_raw()
                for obj in (raw.get('exposedObjects') or {}).get('objects') or []:
                    if (obj.get('type') or '').upper() == 'DATASET':
                        exposed.add(obj.get('localName'))
            except Exception:
                pass
            rows = [{'name': d.get('name'), 'type': d.get('type'),
                     'exposed': d.get('name') in exposed}
                    for d in project.list_datasets()]
            return jsonify({'ok': True, 'datasets': rows[:300]})
        if domain == 'users':
            rows = [{'login': u.get('login'), 'displayName': u.get('displayName'),
                     'enabled': u.get('enabled', True), 'groups': u.get('groups')}
                    for u in client.list_users()]
            caller = ''
            try:
                caller = (client.get_auth_info() or {}).get('authIdentifier') or ''
            except Exception:
                pass
            return jsonify({'ok': True, 'users': rows[:500], 'callerIdentity': caller})
        if domain == 'api-keys':
            # list_personal_api_keys returns only the CALLER's keys; admins
            # enumerate everyone's through the *_all_* variant.
            try:
                personal_rows = client.list_all_personal_api_keys()
            except Exception:
                personal_rows = client.list_personal_api_keys()
            personal = [_pick(k, ('id', 'user', 'label', 'createdOn', 'createdBy'))
                        for k in personal_rows]
            global_keys = [_pick(k, ('id', 'label', 'createdOn', 'groups'))
                           for k in client.list_global_api_keys()]
            caller = ''
            try:
                caller = (client.get_auth_info() or {}).get('authIdentifier') or ''
            except Exception:
                pass
            return jsonify({'ok': True, 'personal': personal[:200],
                            'global': global_keys[:200], 'callerIdentity': caller})
        return jsonify({'error': 'Unknown inventory domain %r' % domain}), 400
    except Exception as exc:
        return jsonify({'error': 'inventory %s failed: %s'
                                 % (domain, str(exc)[:200])}), 502


@bp.route('/api/tools/admin-actions/project-setting')
def api_admin_actions_project_setting():
    """Read one project-level value for the drift guards: 'owner',
    'k8sCluster', or 'variables.<scope>.<name>' (secret paths refused)."""
    project_key = (request.args.get('projectKey') or '').strip()
    path = (request.args.get('path') or '').strip()
    if not project_key or not path:
        return jsonify({'error': 'projectKey and path are required'}), 400
    try:
        project = g.client.get_project(project_key)
        if path == 'owner':
            value = (project.get_permissions() or {}).get('owner')
        elif path == 'k8sCluster':
            raw = project.get_settings().get_raw()
            value = (raw.get('settings') or {}).get('k8sCluster')
        elif path.startswith('variables.'):
            sub = path[len('variables.'):]
            for seg in settings_paths.parse_path(sub):
                if isinstance(seg, str) and settings_paths.BLOCKED_SEGMENT_RE.search(seg):
                    return jsonify({'error': 'path %r is blocked (secret material)'
                                             % path}), 400
            value = settings_paths.get_at(project.get_variables() or {}, sub)
        else:
            return jsonify({'error': 'Unsupported project setting path %r' % path}), 400
    except settings_paths.SettingsPathError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:
        return jsonify({'error': 'project-setting read failed: %s' % str(exc)[:200]}), 404
    return jsonify({'ok': True, 'projectKey': project_key, 'path': path,
                    'value': _redact_secrets(value)})


@bp.route('/api/tools/admin-actions/global-variable')
def api_admin_actions_global_variable():
    """Read one GLOBAL instance variable for the variables-set drift guard.
    Secret paths and the toolkit's own finding whitelist are refused."""
    path = (request.args.get('path') or '').strip()
    if not path:
        return jsonify({'error': 'path is required'}), 400
    try:
        segments = settings_paths.parse_path(path)
    except settings_paths.SettingsPathError as exc:
        return jsonify({'error': str(exc)}), 400
    for seg in segments:
        if isinstance(seg, str) and settings_paths.BLOCKED_SEGMENT_RE.search(seg):
            return jsonify({'error': 'path %r is blocked (secret material)' % path}), 400
    if segments and str(segments[0]) == 'admin_toolkit_finding_whitelist':
        return jsonify({'error': 'admin_toolkit_finding_whitelist is protected — agents '
                                 'never read or edit the finding whitelist'}), 403
    try:
        value = settings_paths.get_at(g.client.get_variables() or {}, path)
    except Exception as exc:
        return jsonify({'error': 'global-variable read failed: %s' % str(exc)[:200]}), 404
    return jsonify({'ok': True, 'path': path, 'value': _redact_secrets(value)})


# ── action impls (dispatched by the POST route below) ───────────────────────


def _impl_connection_test(client, body):
    name = body.get('name') or ''
    result = client.get_connection(name).test()
    # A failing test is a successful probe with a negative result — only an
    # exception (unknown connection, API failure) is an action failure.
    return {'ok': True, 'name': name,
            'connectionOK': bool((result or {}).get('connectionOK')),
            'result': _redact_secrets(result or {})}


def _impl_connection_delete(client, body):
    name = body.get('name') or ''
    folder_id = body.get('folderId') or ''
    conn = client.get_connection(name)
    definition = conn.get_definition()  # backup keeps credentials — admin-scoped folder
    filename = 'connection-%s.json' % _safe_name(name)
    _backup_json(client, folder_id, filename, definition)
    conn.delete()
    return {'ok': True, 'deleted': name, 'backupFile': filename}


def _impl_connection_update(client, body):
    name = body.get('name') or ''
    path = (body.get('path') or '').strip()
    segments = settings_paths.parse_path(path)  # raises on garbage
    for seg in segments:
        if isinstance(seg, str) and settings_paths.BLOCKED_SEGMENT_RE.search(seg):
            return {'ok': False, 'error': 'path %r is blocked: segment %r matches the '
                                          'secret-material blacklist' % (path, seg)}
    conn = client.get_connection(name)
    definition = conn.get_definition()
    current = settings_paths.get_at(definition, path)
    expected = body.get('expectedCurrent')
    if json.dumps(current, sort_keys=True, default=str) != json.dumps(expected, sort_keys=True, default=str):
        return {'ok': False,
                'error': 'Connection %s %s drifted between plan and execute '
                         '(expected %s, found %s) — refusing.'
                         % (name, path, json.dumps(expected, default=str)[:200],
                            json.dumps(current, default=str)[:200])}
    settings_paths.set_at(definition, path, body.get('newValue'))
    conn.set_definition(definition)
    return {'ok': True, 'name': name, 'path': path,
            'before': current, 'after': body.get('newValue')}


def _impl_cluster_detach(client, body):
    cluster_id = body.get('clusterId') or ''
    folder_id = body.get('folderId') or ''
    cluster = client.get_cluster(cluster_id)
    definition = cluster.get_definition()
    filename = 'cluster-%s.json' % _safe_name(cluster_id)
    _backup_json(client, folder_id, filename, definition)
    cluster.delete()  # removes the DSS attachment only — cloud resources untouched
    return {'ok': True, 'detached': cluster_id, 'backupFile': filename}


def _impl_plugin_uninstall(client, body):
    plugin_id = body.get('pluginId') or ''
    folder_id = body.get('folderId') or ''
    if plugin_id in _PROTECTED_PLUGIN_IDS:
        return {'ok': False, 'error': 'Refusing to uninstall %r — the toolkit never '
                                      'removes itself.' % plugin_id}
    plugin = client.get_plugin(plugin_id)
    usages = plugin.list_usages().get_raw().get('usages') or []
    if usages:  # never trust the plan — re-check at execute time
        return {'ok': False,
                'error': 'Plugin %r is used by %d object(s) — uninstall refused. '
                         'First usages: %s'
                         % (plugin_id, len(usages), json.dumps(usages[:5], default=str)[:400])}
    filename = 'plugin-%s.zip' % _safe_name(plugin_id)
    dest = _backup_folder_handle(client, folder_id)
    with tempfile.NamedTemporaryFile(suffix='.zip', delete=True) as tmp:
        client.download_plugin_to_file(plugin_id, tmp.name)
        with open(tmp.name, 'rb') as fh:
            dest.put_file(filename, fh)
    future = plugin.delete(force=False)
    result = future.wait_for_result() if future is not None else None
    return {'ok': True, 'uninstalled': plugin_id, 'backupFile': filename,
            'result': result}


def _impl_cluster_stop(client, body):
    cluster_id = body.get('clusterId') or ''
    terminate = bool(body.get('terminate'))
    client.get_cluster(cluster_id).stop(terminate=terminate)
    return {'ok': True, 'stopped': cluster_id, 'terminated': terminate}


def _impl_cluster_start(client, body):
    cluster_id = body.get('clusterId') or ''
    client.get_cluster(cluster_id).start()
    return {'ok': True, 'started': cluster_id}


def _impl_cluster_pods_cleanup(client, body):
    cluster_id = body.get('clusterId') or ''
    cluster = client.get_cluster(cluster_id)
    pods = cluster.delete_finished_pods()
    jobs = cluster.delete_finished_jobs()
    return {'ok': True, 'clusterId': cluster_id, 'pods': pods, 'jobs': jobs}


def _impl_code_env_update(client, body):
    name = body.get('name') or ''
    lang = (body.get('lang') or 'PYTHON').upper()
    env = client.get_code_env(lang, name)
    result = env.update_packages(force_rebuild_env=bool(body.get('forceRebuild')))
    images = None
    try:
        definition = env.get_definition()
        if definition.get('allContainerConfs') or definition.get('containerConfs'):
            images = env.update_images()
    except Exception as exc:
        images = {'warning': 'image refresh failed: %s' % str(exc)[:200]}
    return {'ok': True, 'name': name, 'lang': lang, 'update': result, 'images': images}


def _impl_plugin_update(client, body):
    plugin_id = body.get('pluginId') or ''
    folder_id = body.get('folderId') or ''
    if plugin_id in _PROTECTED_PLUGIN_IDS:
        return {'ok': False, 'error': 'Refusing to touch %r — the toolkit never updates '
                                      'itself through an agent.' % plugin_id}
    plugin = client.get_plugin(plugin_id)
    filename = 'plugin-%s-preupdate.zip' % _safe_name(plugin_id)
    dest = _backup_folder_handle(client, folder_id)
    with tempfile.NamedTemporaryFile(suffix='.zip', delete=True) as tmp:
        client.download_plugin_to_file(plugin_id, tmp.name)
        with open(tmp.name, 'rb') as fh:
            dest.put_file(filename, fh)
    future = plugin.update_from_store()
    result = future.wait_for_result() if future is not None else None
    return {'ok': True, 'updated': plugin_id, 'backupFile': filename, 'result': result}


def _impl_plugin_code_env_rebuild(client, body):
    plugin_id = body.get('pluginId') or ''
    future = client.get_plugin(plugin_id).update_code_env()
    result = future.wait_for_result() if future is not None else None
    return {'ok': True, 'pluginId': plugin_id, 'result': result}


def _impl_project_export(client, body):
    project_key = body.get('projectKey') or ''
    folder_id = body.get('folderId') or ''
    dest = _backup_folder_handle(client, folder_id)
    filename = 'project-%s.zip' % _safe_name(project_key)
    with tempfile.NamedTemporaryFile(suffix='.zip', delete=True) as tmp:
        client.get_project(project_key).export_to_file(tmp.name)
        with open(tmp.name, 'rb') as fh:
            dest.put_file(filename, fh)
    return {'ok': True, 'projectKey': project_key, 'backupFile': filename}


def _drifted(current, expected):
    return json.dumps(current, sort_keys=True, default=str) != \
        json.dumps(expected, sort_keys=True, default=str)


def _drift_refusal(what, expected, current):
    return {'ok': False,
            'error': '%s drifted between plan and execute (expected %s, found %s) — '
                     'refusing.' % (what, json.dumps(expected, default=str)[:200],
                                    json.dumps(current, default=str)[:200])}


def _impl_project_set_cluster(client, body):
    project_key = body.get('projectKey') or ''
    cluster_id = body.get('clusterId') or ''
    settings = client.get_project(project_key).get_settings()
    raw = settings.get_raw()
    current = (raw.get('settings') or {}).get('k8sCluster')
    if _drifted(current, body.get('expectedCurrent')):
        return _drift_refusal('Project %s k8sCluster' % project_key,
                              body.get('expectedCurrent'), current)
    new_value = {'clusterMode': 'EXPLICIT_CLUSTER', 'clusterId': cluster_id}
    raw.setdefault('settings', {})['k8sCluster'] = new_value
    settings.save()
    return {'ok': True, 'projectKey': project_key, 'before': current, 'after': new_value}


def _impl_project_change_owner(client, body):
    project_key = body.get('projectKey') or ''
    new_owner = body.get('newOwner') or ''
    project = client.get_project(project_key)
    perms = project.get_permissions()
    current = perms.get('owner')
    if _drifted(current, body.get('expectedCurrent')):
        return _drift_refusal('Project %s owner' % project_key,
                              body.get('expectedCurrent'), current)
    perms['owner'] = new_owner
    project.set_permissions(perms)
    return {'ok': True, 'projectKey': project_key, 'before': current, 'after': new_owner}


def _impl_project_variables_set(client, body):
    project_key = body.get('projectKey') or ''
    path = (body.get('path') or '').strip()
    for seg in settings_paths.parse_path(path):
        if isinstance(seg, str) and settings_paths.BLOCKED_SEGMENT_RE.search(seg):
            return {'ok': False, 'error': 'path %r is blocked (secret material)' % path}
    project = client.get_project(project_key)
    variables = project.get_variables() or {}
    current = settings_paths.get_at(variables, path)
    if _drifted(current, body.get('expectedCurrent')):
        return _drift_refusal('Project %s variable %s' % (project_key, path),
                              body.get('expectedCurrent'), current)
    settings_paths.set_at(variables, path, body.get('newValue'))
    project.set_variables(variables)
    return {'ok': True, 'projectKey': project_key, 'path': path,
            'before': current, 'after': body.get('newValue')}


def _impl_job_kill(client, body):
    project_key = body.get('projectKey') or ''
    job_id = body.get('jobId') or ''
    client.get_project(project_key).get_job(job_id).abort()
    return {'ok': True, 'projectKey': project_key, 'jobId': job_id, 'aborted': True}


def _impl_scenario_set_active(client, body):
    project_key = body.get('projectKey') or ''
    scenario_id = body.get('scenarioId') or ''
    new_active = bool(body.get('active'))
    settings = client.get_project(project_key).get_scenario(scenario_id).get_settings()
    current = bool(settings.active)
    if _drifted(current, body.get('expectedCurrent')):
        return _drift_refusal('Scenario %s/%s active' % (project_key, scenario_id),
                              body.get('expectedCurrent'), current)
    settings.active = new_active
    settings.save()
    return {'ok': True, 'projectKey': project_key, 'scenarioId': scenario_id,
            'before': current, 'after': new_active}


def _impl_scenario_kill(client, body):
    project_key = body.get('projectKey') or ''
    scenario_id = body.get('scenarioId') or ''
    client.get_project(project_key).get_scenario(scenario_id).abort()
    return {'ok': True, 'projectKey': project_key, 'scenarioId': scenario_id,
            'aborted': True}


def _impl_scenario_run(client, body):
    project_key = body.get('projectKey') or ''
    scenario_id = body.get('scenarioId') or ''
    trigger = client.get_project(project_key).get_scenario(scenario_id).run()
    run_id = getattr(trigger, 'run_id', None) or getattr(trigger, 'trigger_fire_id', None)
    return {'ok': True, 'projectKey': project_key, 'scenarioId': scenario_id,
            'triggered': True, 'runId': run_id}


def _impl_continuous_activity_stop(client, body):
    project_key = body.get('projectKey') or ''
    recipe_id = body.get('recipeId') or ''
    client.get_project(project_key).get_continuous_activity(recipe_id).stop()
    return {'ok': True, 'projectKey': project_key, 'recipeId': recipe_id, 'stopped': True}


def _impl_webapp_backend_stop(client, body):
    project_key = body.get('projectKey') or ''
    webapp_id = body.get('webappId') or ''
    client.get_project(project_key).get_webapp(webapp_id).stop_backend()
    return {'ok': True, 'projectKey': project_key, 'webappId': webapp_id,
            'backend': 'stopped'}


def _impl_webapp_backend_restart(client, body):
    project_key = body.get('projectKey') or ''
    webapp_id = body.get('webappId') or ''
    client.get_project(project_key).get_webapp(webapp_id).start_or_restart_backend()
    return {'ok': True, 'projectKey': project_key, 'webappId': webapp_id,
            'backend': 'restarted'}


def _impl_notebook_kernels_shutdown(client, body):
    project_key = (body.get('projectKey') or '').strip()
    projects = ([project_key] if project_key else
                [p['projectKey'] for p in client.list_projects()][:100])
    shut, errors = [], []
    for pk in projects:
        try:
            notebooks = client.get_project(pk).list_jupyter_notebooks(active=True)
        except Exception as exc:
            errors.append({'projectKey': pk, 'error': str(exc)[:120]})
            continue
        for nb in notebooks:
            name = getattr(nb, 'notebook_name', None) or str(nb)
            try:
                nb.unload()
                shut.append({'projectKey': pk, 'name': name})
            except Exception as exc:
                errors.append({'projectKey': pk, 'name': name, 'error': str(exc)[:120]})
    return {'ok': True, 'shutdownCount': len(shut), 'kernels': shut[:50],
            'errors': errors[:20] or None}


def _impl_notebook_clear_outputs(client, body):
    project_key = body.get('projectKey') or ''
    name = body.get('notebookName') or ''
    client.get_project(project_key).get_jupyter_notebook(name).clear_outputs()
    return {'ok': True, 'projectKey': project_key, 'notebookName': name, 'cleared': True}


def _impl_user_set_enabled(client, body):
    login = body.get('login') or ''
    new_enabled = bool(body.get('enabled'))
    caller = ''
    try:
        caller = (client.get_auth_info() or {}).get('authIdentifier') or ''
    except Exception:
        pass
    if not new_enabled and caller and login == caller:
        return {'ok': False, 'error': 'Refusing to disable %r — the identity the toolkit '
                                      'runs as (self-lockout).' % login}
    settings = client.get_user(login).get_settings()
    current = bool(settings.enabled)
    if _drifted(current, body.get('expectedCurrent')):
        return _drift_refusal('User %s enabled' % login,
                              body.get('expectedCurrent'), current)
    settings.enabled = new_enabled
    settings.save()
    return {'ok': True, 'login': login, 'before': current, 'after': new_enabled}


def _impl_api_key_delete(client, body):
    key_type = (body.get('keyType') or '').lower()
    key_id = body.get('keyId') or ''
    caller = ''
    try:
        caller = (client.get_auth_info() or {}).get('authIdentifier') or ''
    except Exception:
        pass
    if key_type == 'personal':
        try:
            rows = client.list_all_personal_api_keys()
        except Exception:
            rows = client.list_personal_api_keys()
        row = next((k for k in rows if k.get('id') == key_id), None)
        if row is None:
            return {'ok': False, 'error': 'Personal API key %r not found.' % key_id}
        if caller and row.get('user') == caller:
            return {'ok': False, 'error': 'Refusing to delete key %r of %r — the identity '
                                          'the toolkit runs as (self-lockout).'
                                          % (key_id, caller)}
        client.get_personal_api_key(key_id).delete()
    elif key_type == 'global':
        client.get_global_api_key_by_id(key_id).delete()
    else:
        return {'ok': False, 'error': "keyType must be 'personal' or 'global'."}
    return {'ok': True, 'keyType': key_type, 'deleted': key_id}


def _impl_dataset_clear(client, body):
    project_key = body.get('projectKey') or ''
    name = body.get('datasetName') or ''
    ack = bool(body.get('ackExposed'))
    project = client.get_project(project_key)
    try:  # re-check exposure at execute time — never trust the plan
        raw = project.get_settings().get_raw()
        exposed = any((obj.get('type') or '').upper() == 'DATASET'
                      and obj.get('localName') == name
                      for obj in (raw.get('exposedObjects') or {}).get('objects') or [])
    except Exception:
        exposed = True  # unknown ⇒ safe side: require the ack
    if exposed and not ack:
        return {'ok': False, 'error': 'Dataset %s/%s is exposed to other projects — '
                                      'clear refused without ackExposed.'
                                      % (project_key, name)}
    result = project.get_dataset(name).clear()
    return {'ok': True, 'projectKey': project_key, 'datasetName': name,
            'cleared': True, 'result': result}


def _impl_connection_index(client, body):
    names = [str(n) for n in (body.get('connectionNames') or []) if str(n).strip()]
    if names:
        future = client.catalog_index_connections(connection_names=names)
    else:
        future = client.catalog_index_connections(all_connections=True)
    result = future.wait_for_result() if hasattr(future, 'wait_for_result') else future
    return {'ok': True, 'indexed': names or 'all', 'result': result}


def _impl_variables_set(client, body):
    path = (body.get('path') or '').strip()
    segments = settings_paths.parse_path(path)
    for seg in segments:
        if isinstance(seg, str) and settings_paths.BLOCKED_SEGMENT_RE.search(seg):
            return {'ok': False, 'error': 'path %r is blocked (secret material)' % path}
    if segments and str(segments[0]) == 'admin_toolkit_finding_whitelist':
        return {'ok': False, 'error': 'admin_toolkit_finding_whitelist is protected — '
                                      'agents never edit the finding whitelist.'}
    variables = client.get_variables() or {}
    current = settings_paths.get_at(variables, path)
    if _drifted(current, body.get('expectedCurrent')):
        return _drift_refusal('Global variable %s' % path,
                              body.get('expectedCurrent'), current)
    settings_paths.set_at(variables, path, body.get('newValue'))
    client.set_variables(variables)
    return {'ok': True, 'path': path, 'before': current, 'after': body.get('newValue')}


_ACTION_IMPLS = {
    'connection-test': _impl_connection_test,
    'connection-delete': _impl_connection_delete,
    'connection-update': _impl_connection_update,
    'connection-index': _impl_connection_index,
    'dataset-clear': _impl_dataset_clear,
    'cluster-detach': _impl_cluster_detach,
    'cluster-stop': _impl_cluster_stop,
    'cluster-start': _impl_cluster_start,
    'cluster-pods-cleanup': _impl_cluster_pods_cleanup,
    'plugin-uninstall': _impl_plugin_uninstall,
    'plugin-update': _impl_plugin_update,
    'plugin-code-env-rebuild': _impl_plugin_code_env_rebuild,
    'code-env-update': _impl_code_env_update,
    'project-export': _impl_project_export,
    'project-set-cluster': _impl_project_set_cluster,
    'project-change-owner': _impl_project_change_owner,
    'project-variables-set': _impl_project_variables_set,
    'job-kill': _impl_job_kill,
    'scenario-set-active': _impl_scenario_set_active,
    'scenario-kill': _impl_scenario_kill,
    'scenario-run': _impl_scenario_run,
    'continuous-activity-stop': _impl_continuous_activity_stop,
    'webapp-backend-stop': _impl_webapp_backend_stop,
    'webapp-backend-restart': _impl_webapp_backend_restart,
    'notebook-kernels-shutdown': _impl_notebook_kernels_shutdown,
    'notebook-clear-outputs': _impl_notebook_clear_outputs,
    'user-set-enabled': _impl_user_set_enabled,
    'api-key-delete': _impl_api_key_delete,
    'variables-set': _impl_variables_set,
}


@bp.route('/api/tools/admin-actions/<action>', methods=['POST'])
@advanced
def api_admin_actions_execute(action):
    impl = _ACTION_IMPLS.get(action)
    if impl is None:
        return jsonify({'error': 'Unknown admin action %r' % action}), 404
    body = request.get_json(force=True, silent=True) or {}
    try:
        result = impl(g.client, body)
    except settings_paths.SettingsPathError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    except Exception as exc:
        _LOGGER.error('[admin-actions] %s failed: %s', action, exc)
        return jsonify({'ok': False, 'error': '%s: %s' % (type(exc).__name__, str(exc)[:300])}), 500
    status = 200 if result.get('ok') else 409
    if not result.get('ok'):
        _LOGGER.info('[admin-actions] %s refused: %s', action, result.get('error'))
    else:
        _LOGGER.info('[admin-actions] %s ok: %s', action, json.dumps(result, default=str)[:300])
    return jsonify(result), status


# ── fs-cleanup (B-macro pattern: host filesystem work stays in the macro) ────


@bp.route('/api/tools/fs-cleanup/scan')
def api_fs_cleanup_scan():
    try:
        result = _fs_cleanup_macro(
            g.client, 'scan',
            policy=(request.args.get('policy') or 'webappruns').strip(),
            project_key=(request.args.get('projectKey') or '').strip() or None,
            min_age_days=request.args.get('minAgeDays', type=int),
            keep_last_runs=request.args.get('keepLastRuns', type=int))
    except Exception as exc:
        _LOGGER.error('[fs-cleanup] scan macro failed: %s', exc)
        return jsonify({'ok': False, 'error': str(exc)}), 502
    return jsonify(result), 200 if result.get('ok') else 400


@bp.route('/api/tools/fs-cleanup/delete', methods=['POST'])
@advanced
def api_fs_cleanup_delete():
    body = request.get_json(force=True, silent=True) or {}
    dry_run = bool(body.get('dryRun', True))
    try:
        result = _fs_cleanup_macro(
            g.client, 'delete',
            policy=(body.get('policy') or 'webappruns').strip(),
            project_key=(body.get('projectKey') or '').strip() or None,
            min_age_days=body.get('minAgeDays'),
            keep_last_runs=body.get('keepLastRuns'),
            max_delete_gb=body.get('maxDeleteGB'),
            dry_run=dry_run)
    except Exception as exc:
        _LOGGER.error('[fs-cleanup] delete macro failed: %s', exc)
        return jsonify({'ok': False, 'error': str(exc)}), 502
    _LOGGER.info('[fs-cleanup] delete dryRun=%s deletedRuns=%s reclaimedGB=%s',
                 dry_run, result.get('totalDeletedRuns'), result.get('totalReclaimedGB'))
    return jsonify(result), 200 if result.get('ok') else 400
