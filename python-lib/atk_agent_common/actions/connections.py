"""Connection-domain actuator actions (B-api: backend red routes on g.client).

connection-update deliberately reuses the settings-set idiom: dot/index path
into the connection definition, secret-segment blacklist (credentials are
never readable or mutable through this action), observed current value bound
into the signed target. The blank-host repair the acceptance run needs is
exactly `path='params.host'`.
"""

import json

from ..errors import ToolkitError
from . import _base


def _connection_row(client, host, name):
    data = client.get('/api/connections', host=host)
    details = data.get('connectionDetails') or []
    row = next((c for c in details if c.get('name') == name), None)
    if row is None:
        names = sorted(str(c.get('name')) for c in details)
        raise ToolkitError(
            'Connection %r not found on host %r. Connections: %s'
            % (name, host, ', '.join(names[:25]) or '(none)'),
            remediation="Check the name with config_inspect domain='connections'.")
    return row


def _usage_summary(client, host, name):
    """Best-effort usage counts for one connection via the epoch-memoized
    usages scan (None means unknown, not zero)."""
    from .. import health
    usages = health.fetch_connection_usages(client, host)
    if usages is None:
        return None
    out = {'projectCount': 0, 'datasetCount': 0, 'recipeCount': 0}
    for row in (usages.get('datasetUsages') or []) + (usages.get('llmUsages') or []):
        if row.get('name') != name:
            continue
        for key in out:
            out[key] += row.get(key) or 0
    return out


def _plan_connection_test(client, host, target, params):
    name = _base.require_str(target, 'name', 'connection-test')
    row = _connection_row(client, host, name)
    return {'name': name}, {
        'summary': 'Run the DSS connection test for %s (type %s) — a read-only probe.'
                   % (name, row.get('type')),
        'connectionType': row.get('type'),
        'note': 'No configuration is changed; the result reports connectionOK true/false.',
    }


def _exec_connection_test(client, host, target):
    return _base.post_backend_action(client, host, 'connection-test',
                                     {'name': target['name']})


def _plan_connection_delete(client, host, target, params):
    name = _base.require_str(target, 'name', 'connection-delete')
    row = _connection_row(client, host, name)
    folder = _base.backup_folder(client, host)
    usage = _usage_summary(client, host, name)
    warnings = []
    if usage is None:
        warnings.append('Connection usage could not be verified (usage scan unavailable) — '
                        'confirm manually that nothing depends on %s before approving.' % name)
    elif (usage.get('projectCount') or 0) > 0 or (usage.get('datasetCount') or 0) > 0 \
            or (usage.get('recipeCount') or 0) > 0:
        warnings.append('Connection %s is USED (%s project(s), %s dataset(s), %s recipe(s)) '
                        '— deleting will break them.'
                        % (name, usage.get('projectCount'), usage.get('datasetCount'),
                           usage.get('recipeCount')))
    return {'name': name}, {
        'summary': 'Back up the definition of connection %s (type %s) to %r, then DELETE it.'
                   % (name, row.get('type'), folder['name']),
        'connectionType': row.get('type'),
        'usage': usage,
        'backupFolder': folder,
        'warnings': warnings or None,
        'note': 'The definition backup may carry credential material — the backup folder '
                'is admin-scoped. Restore = recreate the connection from the JSON.',
    }


def _exec_connection_delete(client, host, target):
    folder = _base.backup_folder(client, host)
    return _base.post_backend_action(client, host, 'connection-delete',
                                     {'name': target['name'], 'folderId': folder['id']})


def _plan_connection_update(client, host, target, params):
    name = _base.require_str(target, 'name', 'connection-update')
    path = _base.require_str(target, 'path', 'connection-update')
    if 'newValue' not in (target or {}):
        raise ToolkitError('connection-update target needs {"name": ..., "path": ..., '
                           '"newValue": ...} (dot/index path into the connection '
                           'definition, e.g. "params.host").')
    _base.check_secret_path(path)
    _connection_row(client, host, name)
    definition = client.get('/api/tools/admin-actions/connection-definition', host=host,
                            params={'name': name}).get('definition') or {}
    from ..policies import settings_paths
    current = settings_paths.get_at(definition, path)
    new_value = target.get('newValue')
    warnings = []
    if current is None:
        warnings.append('The path currently resolves to nothing — execute will only succeed '
                        'if every intermediate container exists (the update never creates '
                        'subtrees).')
    canonical = {'name': name, 'path': path, 'newValue': new_value,
                 'expectedCurrent': current}
    return canonical, {
        'summary': 'Set connection %s definition %s: %s → %s.' % (
            name, path, json.dumps(current, default=str)[:120],
            json.dumps(new_value, default=str)[:120]),
        'currentValue': current,
        'proposedValue': new_value,
        'warnings': warnings or None,
        'note': _base.drift_note(),
    }


def _exec_connection_update(client, host, target):
    return _base.post_backend_action(client, host, 'connection-update', {
        'name': target['name'], 'path': target['path'],
        'newValue': target.get('newValue'),
        'expectedCurrent': target.get('expectedCurrent')})


def _changes_connection_update(target, result):
    return [{'itemKey': 'connection:%s:%s' % (result.get('name'), result.get('path')),
             'before': result.get('before'), 'after': result.get('after')}]


def _plan_connection_index(client, host, target, params):
    names = [str(n).strip() for n in ((target or {}).get('connectionNames') or [])
             if str(n).strip()]
    data = client.get('/api/connections', host=host)
    known = {c.get('name') for c in data.get('connectionDetails') or []}
    unknown = [n for n in names if n not in known]
    if unknown:
        raise ToolkitError('Unknown connection(s): %s.' % ', '.join(unknown),
                           remediation="Check names with config_inspect "
                                       "domain='connections'.")
    canonical = {'connectionNames': sorted(names)}
    return canonical, {
        'summary': 'Re-index %s in the DSS catalog (read-only crawl of table/dataset '
                   'metadata).' % (('connections ' + ', '.join(sorted(names)))
                                   if names else 'ALL connections'),
        'connectionCount': len(names) or len(known),
        'note': 'Indexing only refreshes catalog metadata — no data or configuration '
                'changes. Large connections can take a while.',
    }


def _exec_connection_index(client, host, target):
    return _base.post_backend_action(client, host, 'connection-index',
                                     {'connectionNames': target.get('connectionNames') or []})


SPECS = [
    _base.spec('connection-test',
               'connection-test {name}', 'green',
               _plan_connection_test, _exec_connection_test, batchable=True),
    _base.spec('connection-index',
               'connection-index {connectionNames?} (empty = all)', 'green',
               _plan_connection_index, _exec_connection_index),
    _base.spec('connection-update',
               'connection-update {name, path, newValue} (path into the connection '
               'definition, e.g. params.host; secret paths blocked)', 'amber',
               _plan_connection_update, _exec_connection_update,
               settings_hook=_changes_connection_update),
    _base.spec('connection-delete',
               'connection-delete {name}', 'red',
               _plan_connection_delete, _exec_connection_delete, batchable=True),
]
