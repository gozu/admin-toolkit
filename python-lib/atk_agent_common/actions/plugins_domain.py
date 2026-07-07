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


SPECS = [
    _base.spec('plugin-uninstall',
               'plugin-uninstall {pluginId}', 'red',
               _plan_plugin_uninstall, _exec_plugin_uninstall, batchable=True),
]
