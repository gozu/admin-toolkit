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


SPECS = [
    _base.spec('project-clear-webapp-runs',
               'project-clear-webapp-runs {projectKey, keepDays?, keepLastRuns?}', 'amber',
               _plan_project_clear_webapp_runs, _exec_project_clear_webapp_runs,
               batchable=True),
]
