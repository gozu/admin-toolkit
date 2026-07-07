"""Plugin-domain actuator actions (B-api: backend red routes on g.client).

plugin-uninstall is usage-gated twice: the planner refuses when list_usages
reports any usage (an agent should propose code-env or recipe migration
first), and the backend impl re-checks at execute time. The plugin zip is
backed up to the managed folder before deletion, and the toolkit refuses to
uninstall itself.
"""

from ..errors import ToolkitError
from . import _base

_PROTECTED_PLUGIN_IDS = ('admin-toolkit',)


def _plugin_row(client, host, plugin_id):
    data = client.get('/api/plugins', host=host)
    details = data.get('pluginDetails') or []
    row = next((p for p in details if p.get('id') == plugin_id), None)
    if row is None:
        ids = sorted(str(p.get('id')) for p in details)
        raise ToolkitError(
            'Plugin %r not installed on host %r. Installed: %s'
            % (plugin_id, host, ', '.join(ids[:25]) or '(none)'),
            remediation="Check the id with config_inspect domain='plugins'.")
    return row


def _plan_plugin_uninstall(client, host, target, params):
    plugin_id = _base.require_str(target, 'pluginId', 'plugin-uninstall')
    if plugin_id in _PROTECTED_PLUGIN_IDS:
        raise ToolkitError('Refusing to plan the uninstall of %r — the toolkit never '
                           'removes itself.' % plugin_id)
    row = _plugin_row(client, host, plugin_id)
    usages = client.get('/api/tools/admin-actions/plugin-usages', host=host,
                        params={'pluginId': plugin_id})
    usage_count = usages.get('usageCount') or 0
    if usage_count:
        sample = ', '.join('%s/%s' % (u.get('projectKey'), u.get('objectId'))
                           for u in (usages.get('usages') or [])[:5])
        raise ToolkitError(
            'Plugin %r is used by %d object(s) (%s%s) — uninstall refused.'
            % (plugin_id, usage_count, sample, '…' if usage_count > 5 else ''),
            remediation='Migrate or delete the using objects first, then re-plan. '
                        'Relay the usage list to the user.')
    folder = _base.backup_folder(client, host)
    missing = usages.get('missingTypes') or []
    return {'pluginId': plugin_id}, {
        'summary': 'Back up plugin %s (v%s) as a zip to %r, then UNINSTALL it.'
                   % (plugin_id, row.get('installedVersion'), folder['name']),
        'installedVersion': row.get('installedVersion'),
        'isDev': row.get('isDev'),
        'usageCount': usage_count,
        'backupFolder': folder,
        'warnings': (['Usage analysis could not resolve %d type(s) — usages of those '
                      'may be hidden.' % len(missing)] if missing else None),
        'note': 'Restore = re-upload the backed-up zip from the managed folder.',
    }


def _exec_plugin_uninstall(client, host, target):
    folder = _base.backup_folder(client, host)
    return _base.post_backend_action(client, host, 'plugin-uninstall',
                                     {'pluginId': target['pluginId'],
                                      'folderId': folder['id']})


def _plan_plugin_update(client, host, target, params):
    plugin_id = _base.require_str(target, 'pluginId', 'plugin-update')
    row = _plugin_row(client, host, plugin_id)
    folder = _base.backup_folder(client, host)
    warnings = []
    if row.get('isDev'):
        warnings.append('%s is a DEV plugin — store update will fail; update it from its '
                        'git repo or a zip instead.' % plugin_id)
    return {'pluginId': plugin_id}, {
        'summary': 'Back up plugin %s (currently v%s) as a zip to %r, then UPDATE it from '
                   'the Dataiku store.' % (plugin_id, row.get('installedVersion'),
                                           folder['name']),
        'installedVersion': row.get('installedVersion'),
        'backupFolder': folder,
        'warnings': warnings or None,
        'note': 'Rollback = re-upload the backed-up zip. Code-env-based components keep '
                'their env until plugin-code-env-rebuild runs.',
    }


def _exec_plugin_update(client, host, target):
    folder = _base.backup_folder(client, host)
    return _base.post_backend_action(client, host, 'plugin-update',
                                     {'pluginId': target['pluginId'],
                                      'folderId': folder['id']})


def _plan_plugin_code_env_rebuild(client, host, target, params):
    plugin_id = _base.require_str(target, 'pluginId', 'plugin-code-env-rebuild')
    row = _plugin_row(client, host, plugin_id)
    return {'pluginId': plugin_id}, {
        'summary': 'Rebuild the managed code env of plugin %s (v%s).'
                   % (plugin_id, row.get('installedVersion')),
        'installedVersion': row.get('installedVersion'),
        'note': 'Kernels already running keep the old env until they recycle; new ones '
                'pick up the rebuilt env.',
    }


def _exec_plugin_code_env_rebuild(client, host, target):
    return _base.post_backend_action(client, host, 'plugin-code-env-rebuild',
                                     {'pluginId': target['pluginId']})


def _code_env_row(client, host, name):
    # Same heavy scan + shape the legacy code-env-delete planner uses.
    data = client.get('/api/code-envs', host=host, heavy=True,
                      progress_path='/api/code-envs/progress')
    envs = data.get('codeEnvs') or []
    row = next((e for e in envs if e.get('name') == name), None)
    if row is None:
        names = sorted(str(e.get('name')) for e in envs)
        raise ToolkitError(
            'Code env %r not found on host %r. Envs: %s'
            % (name, host, ', '.join(names[:25]) or '(none)'),
            remediation="Check the name with config_inspect domain='code-envs'.")
    return row


def _plan_code_env_update(client, host, target, params):
    name = _base.require_str(target, 'name', 'code-env-update')
    row = _code_env_row(client, host, name)
    force_rebuild = bool((target or {}).get('forceRebuild'))
    lang = str((target or {}).get('lang') or row.get('lang')
               or row.get('envLang') or 'PYTHON').upper()
    canonical = {'name': name, 'lang': lang, 'forceRebuild': force_rebuild}
    return canonical, {
        'summary': 'Update packages of code env %s (%s)%s, then refresh its container '
                   'images if any are configured.'
                   % (name, lang, ' with a full rebuild' if force_rebuild else ''),
        'usageCount': row.get('usageCount'),
        'note': 'Running kernels keep the old env until restarted. A failed package '
                'resolution leaves the env unchanged.',
    }


def _exec_code_env_update(client, host, target):
    return _base.post_backend_action(client, host, 'code-env-update', {
        'name': target['name'], 'lang': target.get('lang') or 'PYTHON',
        'forceRebuild': bool(target.get('forceRebuild'))})


SPECS = [
    _base.spec('plugin-uninstall',
               'plugin-uninstall {pluginId}', 'red',
               _plan_plugin_uninstall, _exec_plugin_uninstall, batchable=True),
    _base.spec('plugin-update',
               'plugin-update {pluginId} (store update; zip backup first)', 'amber',
               _plan_plugin_update, _exec_plugin_update),
    _base.spec('plugin-code-env-rebuild',
               'plugin-code-env-rebuild {pluginId}', 'amber',
               _plan_plugin_code_env_rebuild, _exec_plugin_code_env_rebuild),
    _base.spec('code-env-update',
               'code-env-update {name, lang?, forceRebuild?}', 'amber',
               _plan_code_env_update, _exec_code_env_update),
]
