"""Storage-tail actuator actions.

The three fs cleanups are B-macro: the executor POSTs the backend fs-cleanup
route, which runs the fs-cleanup macro on the target host — the aged-entry
policy (roots, depth, exclusions, age by newest inner mtime, keep-newest-N)
is enforced inside the macro, below both the model and the backend.
dataset-clear is B-api, red and IRREVERSIBLE: it truncates managed dataset
data; datasets exposed to other projects are refused unless the target
carries an explicit ackExposed the human approved.
dataset-delete is B-api, red and IRREVERSIBLE: the definition JSON is backed
up first (data is not), and the planner grounds on the lineage inventory
(detail=usage) — exposed datasets need ackExposed, datasets with consuming
recipes / webapp refs / active-scenario refs need ackReferenced.
"""

from ..errors import ToolkitError
from . import _base

# Per-policy plan defaults (passed explicitly all the way to the macro so the
# runnable.json INT default can never override them).
_FS_DEFAULTS = {
    'tmp': {'minAgeDays': 15, 'keepLast': 0},
    'exports': {'minAgeDays': 7, 'keepLast': 0},
    'joblogs': {'minAgeDays': 15, 'keepLast': 5},
}

_FS_NOTES = {
    'tmp': ('Deletes aged entries INSIDE the DIP_HOME tmp buckets (tmp/<bucket>/<entry>); '
            'the bucket directories themselves and the webappruns bucket are never touched.'),
    'exports': ('Deletes aged export artifacts (exports/<kind>/<entry>) — one-shot '
                'downloads users can regenerate.'),
    'joblogs': ('Deletes whole aged job directories (jobs/<PROJECT>/<jobDir>) — activity '
                'logs and job metadata go with them; the newest N per project survive.'),
}


def _plan_fs_cleanup(client, host, target, action, policy):
    defaults = _FS_DEFAULTS[policy]
    try:
        min_age = int((target or {}).get('minAgeDays') or defaults['minAgeDays'])
        keep_last = int((target or {}).get('keepLast')
                        if (target or {}).get('keepLast') not in (None, '')
                        else defaults['keepLast'])
        max_gb = int((target or {}).get('maxDeleteGB') or 50)
    except (TypeError, ValueError):
        raise ToolkitError('%s minAgeDays/keepLast/maxDeleteGB must be integers.' % action)
    project_key = str((target or {}).get('projectKey') or '').strip()
    if project_key and policy != 'joblogs':
        raise ToolkitError('%s does not take a projectKey (only job-logs-cleanup is '
                           'project-scoped).' % action)
    scan = client.get('/api/tools/fs-cleanup/scan', host=host,
                      params={'policy': policy, 'projectKey': project_key or None,
                              'minAgeDays': min_age, 'keepLastRuns': keep_last},
                      heavy=True)
    if not scan.get('ok'):
        raise ToolkitError('fs-cleanup scan failed: %s'
                           % (scan.get('message') or scan.get('error') or scan))
    total_gb = scan.get('totalGB') or round((scan.get('totalBytes') or 0) / (1024 ** 3), 3)
    if not scan.get('totalDirs'):
        raise ToolkitError('Nothing matches the %s policy%s on host %r (age >= %sd).'
                           % (policy, ' for project %r' % project_key if project_key else '',
                              host, min_age),
                           remediation='Nothing to delete — do not propose this item.')
    groups = {key: {'entries': e.get('entries'), 'deletable': e.get('deletable'),
                    'gb': round((e.get('bytes') or 0) / (1024 ** 3), 3)}
              for key, e in (scan.get('groups') or {}).items()}
    canonical = {'policy': policy, 'minAgeDays': min_age, 'keepLast': keep_last,
                 'maxDeleteGB': max_gb}
    if project_key:
        canonical['projectKey'] = project_key
    return canonical, {
        'summary': 'Delete %s aged %s entr(ies) older than %dd — reclaims ~%.2f GB.'
                   % (scan.get('totalDirs'), policy, min_age, total_gb),
        'perGroup': groups,
        'totalReclaimableGB': total_gb,
        'capGB': max_gb,
        'note': _FS_NOTES[policy] + ' The policy is re-applied per entry inside the '
                'macro at delete time.',
    }


def _exec_fs_cleanup(client, host, target):
    result = client.post('/api/tools/fs-cleanup/delete', host=host, red=True,
                         json={'policy': target['policy'],
                               'projectKey': target.get('projectKey') or None,
                               'minAgeDays': target.get('minAgeDays'),
                               'keepLastRuns': target.get('keepLast'),
                               'maxDeleteGB': target.get('maxDeleteGB'),
                               'dryRun': False})
    if not result.get('ok'):
        raise ToolkitError('%s cleanup refused/failed: %s'
                           % (target.get('policy'),
                              result.get('message') or result.get('error') or result))
    return result


def _plan_tmp_cleanup(client, host, target, params):
    return _plan_fs_cleanup(client, host, target, 'tmp-cleanup', 'tmp')


def _plan_exports_cleanup(client, host, target, params):
    return _plan_fs_cleanup(client, host, target, 'exports-cleanup', 'exports')


def _plan_job_logs_cleanup(client, host, target, params):
    return _plan_fs_cleanup(client, host, target, 'job-logs-cleanup', 'joblogs')


def _plan_dataset_clear(client, host, target, params):
    project_key = _base.require_str(target, 'projectKey', 'dataset-clear')
    name = _base.require_str(target, 'datasetName', 'dataset-clear')
    ack = bool((target or {}).get('ackExposed'))
    inv = client.get('/api/tools/admin-actions/inventory', host=host,
                     params={'domain': 'datasets', 'projectKey': project_key})
    rows = inv.get('datasets') or []
    row = next((d for d in rows if d.get('name') == name), None)
    if row is None:
        names = sorted(str(d.get('name')) for d in rows)
        raise ToolkitError(
            'Dataset %r not found in project %r. Datasets: %s'
            % (name, project_key, ', '.join(names[:15]) or '(none)'),
            remediation="Check with config_inspect domain='datasets' filter=<projectKey>.")
    exposed = bool(row.get('exposed'))
    if exposed and not ack:
        raise ToolkitError(
            'Dataset %s/%s is EXPOSED to other projects — clearing it breaks them. '
            'Refused without explicit acknowledgement.' % (project_key, name),
            remediation='If the admin confirms in the conversation, re-plan with '
                        '"ackExposed": true in the target.')
    warnings = ['This clear is IRREVERSIBLE — the dataset DATA is deleted (schema and '
                'settings survive); only rebuilding the dataset regenerates it.']
    if exposed:
        warnings.append('Dataset is exposed to other projects and the admin acknowledged '
                        'the blast radius (ackExposed).')
    canonical = {'projectKey': project_key, 'datasetName': name}
    if exposed:
        canonical['ackExposed'] = True
    return canonical, {
        'summary': 'CLEAR the data of dataset %s in project %s (type %s) — irreversible.'
                   % (name, project_key, row.get('type')),
        'datasetType': row.get('type'),
        'exposed': exposed,
        'irreversible': True,
        'warnings': warnings,
        'note': 'Restore is NOT possible for cleared data — say so when presenting '
                'this plan. Rebuilding the dataset (job/scenario) regenerates it.',
    }


def _exec_dataset_clear(client, host, target):
    return _base.post_backend_action(client, host, 'dataset-clear', {
        'projectKey': target['projectKey'], 'datasetName': target['datasetName'],
        'ackExposed': bool(target.get('ackExposed'))})


def _plan_dataset_delete(client, host, target, params):
    project_key = _base.require_str(target, 'projectKey', 'dataset-delete')
    name = _base.require_str(target, 'datasetName', 'dataset-delete')
    ack_exposed = bool((target or {}).get('ackExposed'))
    ack_referenced = bool((target or {}).get('ackReferenced'))
    drop_data = bool((target or {}).get('dropData'))
    folder = _base.backup_folder(client, host)
    inv = client.get('/api/tools/admin-actions/inventory', host=host, heavy=True,
                     params={'domain': 'datasets', 'projectKey': project_key,
                             'detail': 'usage'})
    rows = inv.get('datasets') or []
    row = next((d for d in rows if d.get('name') == name), None)
    if row is None:
        names = sorted(str(d.get('name')) for d in rows)
        raise ToolkitError(
            'Dataset %r not found in project %r. Datasets: %s'
            % (name, project_key, ', '.join(names[:15]) or '(none)'),
            remediation="Check with config_inspect domain='datasets' filter=<projectKey>.")
    exposed = bool(row.get('exposed'))
    if exposed and not ack_exposed:
        raise ToolkitError(
            'Dataset %s/%s is EXPOSED to other projects — deleting it breaks them. '
            'Refused without explicit acknowledgement.' % (project_key, name),
            remediation='If the admin confirms in the conversation, re-plan with '
                        '"ackExposed": true in the target.')
    consumers = row.get('consumers') or []
    webapp_refs = row.get('webappRefs') or []
    active_scenario_refs = [s for s in (row.get('scenarioRefs') or [])
                            if not s.endswith('(inactive)')]
    referenced = consumers or webapp_refs or active_scenario_refs
    if referenced and not ack_referenced:
        raise ToolkitError(
            'Dataset %s/%s is still referenced — consumers: %s; webapps: %s; active '
            'scenarios: %s. Deleting it breaks them. Refused without explicit '
            'acknowledgement.' % (project_key, name,
                                  ', '.join(consumers) or '(none)',
                                  ', '.join(webapp_refs) or '(none)',
                                  ', '.join(active_scenario_refs) or '(none)'),
            remediation='If the admin confirms the blast radius in the conversation, '
                        're-plan with "ackReferenced": true in the target.')
    warnings = ['This delete is IRREVERSIBLE — the definition JSON is backed up, the '
                'DATA is not. dropData=%s: %s' % (
                    drop_data,
                    'the underlying files/tables are dropped with it.' if drop_data
                    else 'the underlying files/tables stay on the connection (set '
                         '"dropData": true to reclaim managed storage).')]
    producers = row.get('producers') or []
    if producers:
        warnings.append('Producing recipe(s) %s are left ORPHANED (missing output) — '
                        'delete them separately in the Flow.' % ', '.join(producers))
    inactive_refs = [s for s in (row.get('scenarioRefs') or []) if s.endswith('(inactive)')]
    if inactive_refs:
        warnings.append('Referenced by INACTIVE scenario(s) %s — they fail if ever '
                        're-enabled.' % ', '.join(inactive_refs))
    if exposed:
        warnings.append('Dataset is exposed to other projects and the admin acknowledged '
                        'the blast radius (ackExposed).')
    if referenced:
        warnings.append('Dataset is still referenced and the admin acknowledged the '
                        'blast radius (ackReferenced).')
    canonical = {'projectKey': project_key, 'datasetName': name, 'dropData': drop_data}
    if exposed:
        canonical['ackExposed'] = True
    if referenced:
        canonical['ackReferenced'] = True
    return canonical, {
        'summary': 'Back up the definition of dataset %s in project %s (type %s) to %r, '
                   'then DELETE it%s — irreversible.'
                   % (name, project_key, row.get('type'), folder['name'],
                      ' dropping its data' if drop_data else ''),
        'datasetType': row.get('type'),
        'exposed': exposed,
        'lineage': {'producers': producers, 'consumers': consumers,
                    'webappRefs': webapp_refs, 'scenarioRefs': row.get('scenarioRefs') or []},
        'backupFolder': folder,
        'irreversible': True,
        'warnings': warnings,
        'note': 'Restore = recreate the dataset from the definition JSON (schema and '
                'settings only — deleted DATA does not come back). Say so when '
                'presenting this plan.',
    }


def _exec_dataset_delete(client, host, target):
    folder = _base.backup_folder(client, host)
    return _base.post_backend_action(client, host, 'dataset-delete', {
        'projectKey': target['projectKey'], 'datasetName': target['datasetName'],
        'dropData': bool(target.get('dropData')),
        'ackExposed': bool(target.get('ackExposed')),
        'ackReferenced': bool(target.get('ackReferenced')),
        'folderId': folder['id']})


SPECS = [
    _base.spec('tmp-cleanup',
               'tmp-cleanup {minAgeDays?, maxDeleteGB?}', 'amber',
               _plan_tmp_cleanup, _exec_fs_cleanup),
    _base.spec('exports-cleanup',
               'exports-cleanup {minAgeDays?, maxDeleteGB?}', 'amber',
               _plan_exports_cleanup, _exec_fs_cleanup),
    _base.spec('job-logs-cleanup',
               'job-logs-cleanup {projectKey?, minAgeDays?, maxDeleteGB?}', 'amber',
               _plan_job_logs_cleanup, _exec_fs_cleanup),
    _base.spec('dataset-clear',
               'dataset-clear {projectKey, datasetName, ackExposed?} (IRREVERSIBLE; '
               'exposed datasets refused without ack)', 'red',
               _plan_dataset_clear, _exec_dataset_clear, batchable=True),
    _base.spec('dataset-delete',
               'dataset-delete {projectKey, datasetName, dropData?, ackExposed?, '
               'ackReferenced?} (IRREVERSIBLE; definition backed up, DATA is not; '
               'exposed or still-referenced datasets refused without acks)', 'red',
               _plan_dataset_delete, _exec_dataset_delete, batchable=True),
]
