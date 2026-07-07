"""Project-domain actuator actions.

project-clear-webapp-runs is the catalog's first B-macro action: the executor
POSTs the backend fs-cleanup route, which invokes the fs-cleanup macro via
ADMINTOOLKIT on the target host — so it is fleet-routable AND the deletion
policy (roots, age, keep-newest-N, running-webapp exclusion) is enforced
inside the macro, below both the model and the backend.
"""

from ..errors import ToolkitError
from . import _base

_DEFAULT_KEEP_DAYS = 7
_DEFAULT_KEEP_LAST_RUNS = 2


def _plan_project_clear_webapp_runs(client, host, target, params):
    project_key = _base.require_str(target, 'projectKey', 'project-clear-webapp-runs')
    try:
        keep_days = int((target or {}).get('keepDays') or _DEFAULT_KEEP_DAYS)
        keep_last = int((target or {}).get('keepLastRuns') or _DEFAULT_KEEP_LAST_RUNS)
    except (TypeError, ValueError):
        raise ToolkitError('project-clear-webapp-runs keepDays/keepLastRuns must be integers.')
    scan = client.get('/api/tools/fs-cleanup/scan', host=host,
                      params={'policy': 'webappruns', 'projectKey': project_key,
                              'minAgeDays': keep_days, 'keepLastRuns': keep_last},
                      heavy=True)
    if not scan.get('ok'):
        raise ToolkitError('fs-cleanup scan failed: %s'
                           % (scan.get('message') or scan.get('error') or scan))
    if project_key not in (scan.get('projectKeys') or []):
        raise ToolkitError(
            'Project %r has no webapp run directories on host %r.' % (project_key, host),
            remediation='Check the project key against storage_footprint sizeBreakdown '
                        "(bucketKey 'webApps').")
    total_gb = scan.get('totalGB') or round((scan.get('totalBytes') or 0) / (1024 ** 3), 3)
    per_webapp = {key: {'runDirs': e.get('runDirs'), 'deletable': e.get('deletableRuns'),
                        'gb': round((e.get('bytes') or 0) / (1024 ** 3), 3)}
                  for key, e in (scan.get('webapps') or {}).items()}
    warnings = list(scan.get('warnings') or [])
    return {'projectKey': project_key, 'keepDays': keep_days, 'keepLastRuns': keep_last}, {
        'summary': 'Delete dead webapp run directories of project %s older than %dd '
                   '(keeping the newest %d per webapp) — reclaims ~%.2f GB across %s '
                   'run dir(s).' % (project_key, keep_days, keep_last, total_gb,
                                    scan.get('totalDirs')),
        'perWebapp': per_webapp,
        'totalReclaimableGB': total_gb,
        'runningExcluded': scan.get('runningExcluded') or None,
        'warnings': warnings or None,
        'note': 'Only run_* directories match the policy — the live backend run, the '
                'initial/ dir and instance-info.json are never touched; the policy is '
                'enforced inside the macro at delete time.',
    }


def _exec_project_clear_webapp_runs(client, host, target):
    result = client.post('/api/tools/fs-cleanup/delete', host=host, red=True,
                         json={'policy': 'webappruns',
                               'projectKey': target['projectKey'],
                               'minAgeDays': target.get('keepDays'),
                               'keepLastRuns': target.get('keepLastRuns'),
                               'dryRun': False})
    if not result.get('ok'):
        raise ToolkitError('Webapp-run cleanup refused/failed: %s'
                           % (result.get('message') or result.get('error') or result))
    return result


def _project_row(client, host, project_key):
    rows = (client.get('/api/projects', host=host) or {}).get('projects') or []
    row = next((p for p in rows if p.get('projectKey') == project_key), None)
    if row is None:
        raise ToolkitError('Project %r not found on host %r.' % (project_key, host),
                           remediation='Check the key with storage_footprint or '
                                       "config_inspect.")
    return row


def _plan_project_export(client, host, target, params):
    project_key = _base.require_str(target, 'projectKey', 'project-export')
    _project_row(client, host, project_key)
    folder = _base.backup_folder(client, host)
    return {'projectKey': project_key}, {
        'summary': 'Export project %s as a zip bundle to %r — a read-only snapshot '
                   '(datasets metadata + flow + code; managed data per DSS export '
                   'defaults).' % (project_key, folder['name']),
        'backupFolder': folder,
        'note': 'The export is the standard DSS project archive — importable on any '
                'instance of the same or newer version.',
    }


def _exec_project_export(client, host, target):
    folder = _base.backup_folder(client, host)
    return _base.post_backend_action(client, host, 'project-export',
                                     {'projectKey': target['projectKey'],
                                      'folderId': folder['id']})


def _plan_project_set_cluster(client, host, target, params):
    project_key = _base.require_str(target, 'projectKey', 'project-set-cluster')
    cluster_id = _base.require_str(target, 'clusterId', 'project-set-cluster')
    _project_row(client, host, project_key)
    current = client.get('/api/tools/admin-actions/project-setting', host=host,
                         params={'projectKey': project_key, 'path': 'k8sCluster'}).get('value')
    canonical = {'projectKey': project_key, 'clusterId': cluster_id,
                 'expectedCurrent': current}
    return canonical, {
        'summary': 'Point project %s at K8s cluster %s (settings.k8sCluster → '
                   'EXPLICIT_CLUSTER).' % (project_key, cluster_id),
        'currentValue': current,
        'proposedValue': {'clusterMode': 'EXPLICIT_CLUSTER', 'clusterId': cluster_id},
        'note': _base.drift_note() + ' Fixes the WARN_CLUSTERS_NONE_SELECTED_PROJECT '
                'sanity warning.',
    }


def _exec_project_set_cluster(client, host, target):
    return _base.post_backend_action(client, host, 'project-set-cluster', {
        'projectKey': target['projectKey'], 'clusterId': target['clusterId'],
        'expectedCurrent': target.get('expectedCurrent')})


def _changes_project_set_cluster(target, result):
    return [{'itemKey': 'project:%s:k8sCluster' % result.get('projectKey'),
             'before': result.get('before'), 'after': result.get('after')}]


def _plan_project_change_owner(client, host, target, params):
    project_key = _base.require_str(target, 'projectKey', 'project-change-owner')
    new_owner = _base.require_str(target, 'newOwner', 'project-change-owner')
    _project_row(client, host, project_key)
    current = client.get('/api/tools/admin-actions/project-setting', host=host,
                         params={'projectKey': project_key, 'path': 'owner'}).get('value')
    canonical = {'projectKey': project_key, 'newOwner': new_owner,
                 'expectedCurrent': current}
    return canonical, {
        'summary': 'Change the owner of project %s: %s → %s.'
                   % (project_key, current, new_owner),
        'currentValue': current,
        'proposedValue': new_owner,
        'note': _base.drift_note() + ' The new owner must be an existing enabled user.',
    }


def _exec_project_change_owner(client, host, target):
    return _base.post_backend_action(client, host, 'project-change-owner', {
        'projectKey': target['projectKey'], 'newOwner': target['newOwner'],
        'expectedCurrent': target.get('expectedCurrent')})


def _changes_project_change_owner(target, result):
    return [{'itemKey': 'project:%s:owner' % result.get('projectKey'),
             'before': result.get('before'), 'after': result.get('after')}]


def _plan_project_variables_set(client, host, target, params):
    project_key = _base.require_str(target, 'projectKey', 'project-variables-set')
    path = _base.require_str(target, 'path', 'project-variables-set')
    if 'newValue' not in (target or {}):
        raise ToolkitError('project-variables-set target needs {"projectKey": ..., '
                           '"path": ..., "newValue": ...} — path is scoped like '
                           '"standard.myVar" or "local.myVar".')
    _base.check_secret_path(path)
    _project_row(client, host, project_key)
    current = client.get('/api/tools/admin-actions/project-setting', host=host,
                         params={'projectKey': project_key,
                                 'path': 'variables.%s' % path}).get('value')
    new_value = target.get('newValue')
    canonical = {'projectKey': project_key, 'path': path, 'newValue': new_value,
                 'expectedCurrent': current}
    import json as _json
    return canonical, {
        'summary': 'Set project %s variable %s: %s → %s.' % (
            project_key, path, _json.dumps(current, default=str)[:120],
            _json.dumps(new_value, default=str)[:120]),
        'currentValue': current,
        'proposedValue': new_value,
        'note': _base.drift_note(),
    }


def _exec_project_variables_set(client, host, target):
    return _base.post_backend_action(client, host, 'project-variables-set', {
        'projectKey': target['projectKey'], 'path': target['path'],
        'newValue': target.get('newValue'),
        'expectedCurrent': target.get('expectedCurrent')})


def _changes_project_variables_set(target, result):
    return [{'itemKey': 'projectVariable:%s:%s' % (result.get('projectKey'),
                                                   result.get('path')),
             'before': result.get('before'), 'after': result.get('after')}]


SPECS = [
    _base.spec('project-clear-webapp-runs',
               'project-clear-webapp-runs {projectKey, keepDays?, keepLastRuns?}', 'amber',
               _plan_project_clear_webapp_runs, _exec_project_clear_webapp_runs,
               batchable=True),
    _base.spec('project-export',
               'project-export {projectKey}', 'green',
               _plan_project_export, _exec_project_export, batchable=True),
    _base.spec('project-set-cluster',
               'project-set-cluster {projectKey, clusterId}', 'amber',
               _plan_project_set_cluster, _exec_project_set_cluster,
               settings_hook=_changes_project_set_cluster),
    _base.spec('project-change-owner',
               'project-change-owner {projectKey, newOwner}', 'amber',
               _plan_project_change_owner, _exec_project_change_owner,
               settings_hook=_changes_project_change_owner),
    _base.spec('project-variables-set',
               'project-variables-set {projectKey, path, newValue} (scoped path, e.g. '
               'standard.myVar; secret paths blocked)', 'amber',
               _plan_project_variables_set, _exec_project_variables_set,
               settings_hook=_changes_project_variables_set),
]
