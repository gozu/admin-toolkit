"""Runtime-ops actuator actions (B-api: backend red routes on g.client).

Everything here stops/starts/kicks DSS-managed workloads through DSS APIs —
never Linux-level kills (rubric K97: DSS-managed processes are stopped at the
DSS level, they respawn otherwise). Scenario enable/disable is drift-guarded
like a settings change and lands in the restorable history; variables-set
covers GLOBAL instance variables and refuses the toolkit's own finding
whitelist (agents never edit their own suppression list).
"""

import json

from ..errors import ToolkitError
from . import _base

# The per-item finding whitelist must never be agent-writable (or readable):
# an agent editing its own suppression list would defeat the whole doctrine.
_PROTECTED_VARIABLES = ('admin_toolkit_finding_whitelist',)


def _inventory(client, host, domain, project_key=None, active=False):
    params = {'domain': domain}
    if project_key:
        params['projectKey'] = project_key
    if active:
        params['active'] = '1'
    return client.get('/api/tools/admin-actions/inventory', host=host, params=params)


def _scenario_row(client, host, project_key, scenario_id):
    rows = _inventory(client, host, 'scenarios', project_key).get('scenarios') or []
    row = next((s for s in rows if s.get('id') == scenario_id), None)
    if row is None:
        ids = sorted(str(s.get('id')) for s in rows)
        raise ToolkitError(
            'Scenario %r not found in project %r. Scenarios: %s'
            % (scenario_id, project_key, ', '.join(ids[:20]) or '(none)'),
            remediation="Check with config_inspect domain='scenarios' filter=<projectKey>.")
    return row


def _plan_job_kill(client, host, target, params):
    project_key = _base.require_str(target, 'projectKey', 'job-kill')
    job_id = _base.require_str(target, 'jobId', 'job-kill')
    rows = _inventory(client, host, 'jobs', project_key).get('jobs') or []
    row = next((j for j in rows if j.get('id') == job_id), None)
    warnings = []
    if row is None:
        warnings.append('Job %s is not in the recent-jobs list of %s — it may have '
                        'already finished; aborting a finished job is a no-op.'
                        % (job_id, project_key))
    elif str(row.get('state') or '').upper() not in ('RUNNING', 'COMPUTING_DEPS', 'WAITING'):
        warnings.append('Job %s is in state %s — aborting a non-running job is a no-op.'
                        % (job_id, row.get('state')))
    return {'projectKey': project_key, 'jobId': job_id}, {
        'summary': 'Abort job %s in project %s via the DSS job API.' % (job_id, project_key),
        'state': (row or {}).get('state'),
        'warnings': warnings or None,
        'note': 'DSS-level abort — partial outputs follow normal job-failure semantics.',
    }


def _exec_job_kill(client, host, target):
    return _base.post_backend_action(client, host, 'job-kill', {
        'projectKey': target['projectKey'], 'jobId': target['jobId']})


def _plan_scenario_toggle(client, host, target, action, new_active):
    project_key = _base.require_str(target, 'projectKey', action)
    scenario_id = _base.require_str(target, 'scenarioId', action)
    row = _scenario_row(client, host, project_key, scenario_id)
    current = bool(row.get('active'))
    warnings = []
    if current == new_active:
        warnings.append('Scenario %s auto-triggers are already %s — executing is a no-op.'
                        % (scenario_id, 'enabled' if new_active else 'disabled'))
    canonical = {'projectKey': project_key, 'scenarioId': scenario_id,
                 'active': new_active, 'expectedCurrent': current}
    return canonical, {
        'summary': '%s auto-triggers of scenario %s (%s) in project %s.'
                   % ('Enable' if new_active else 'Disable', scenario_id,
                      row.get('name') or '?', project_key),
        'scenarioName': row.get('name'),
        'currentValue': current,
        'proposedValue': new_active,
        'running': row.get('running'),
        'warnings': warnings or None,
        'note': _base.drift_note() + (' Revert = the inverse scenario-%s.'
                                      % ('disable' if new_active else 'enable')),
    }


def _plan_scenario_disable(client, host, target, params):
    return _plan_scenario_toggle(client, host, target, 'scenario-disable', False)


def _plan_scenario_enable(client, host, target, params):
    return _plan_scenario_toggle(client, host, target, 'scenario-enable', True)


def _exec_scenario_toggle(client, host, target):
    return _base.post_backend_action(client, host, 'scenario-set-active', {
        'projectKey': target['projectKey'], 'scenarioId': target['scenarioId'],
        'active': bool(target.get('active')),
        'expectedCurrent': target.get('expectedCurrent')})


def _changes_scenario_toggle(target, result):
    return [{'itemKey': 'scenario:%s:%s:active' % (result.get('projectKey'),
                                                   result.get('scenarioId')),
             'before': result.get('before'), 'after': result.get('after')}]


def _plan_scenario_kill(client, host, target, params):
    project_key = _base.require_str(target, 'projectKey', 'scenario-kill')
    scenario_id = _base.require_str(target, 'scenarioId', 'scenario-kill')
    row = _scenario_row(client, host, project_key, scenario_id)
    warnings = []
    if not row.get('running'):
        warnings.append('Scenario %s is not currently running — abort is a no-op.'
                        % scenario_id)
    return {'projectKey': project_key, 'scenarioId': scenario_id}, {
        'summary': 'Abort the running scenario %s (%s) in project %s.'
                   % (scenario_id, row.get('name') or '?', project_key),
        'scenarioName': row.get('name'),
        'running': row.get('running'),
        'warnings': warnings or None,
        'note': 'DSS-level abort of the current run; the scenario stays enabled.',
    }


def _exec_scenario_kill(client, host, target):
    return _base.post_backend_action(client, host, 'scenario-kill', {
        'projectKey': target['projectKey'], 'scenarioId': target['scenarioId']})


def _plan_scenario_run(client, host, target, params):
    project_key = _base.require_str(target, 'projectKey', 'scenario-run')
    scenario_id = _base.require_str(target, 'scenarioId', 'scenario-run')
    row = _scenario_row(client, host, project_key, scenario_id)
    warnings = []
    if row.get('running'):
        warnings.append('Scenario %s is ALREADY running — this queues/overlaps a manual run.'
                        % scenario_id)
    return {'projectKey': project_key, 'scenarioId': scenario_id}, {
        'summary': 'Trigger a manual run of scenario %s (%s) in project %s.'
                   % (scenario_id, row.get('name') or '?', project_key),
        'scenarioName': row.get('name'),
        'running': row.get('running'),
        'warnings': warnings or None,
        'note': 'Runs with the scenario\'s own settings; works even when auto-triggers '
                'are disabled.',
    }


def _exec_scenario_run(client, host, target):
    return _base.post_backend_action(client, host, 'scenario-run', {
        'projectKey': target['projectKey'], 'scenarioId': target['scenarioId']})


def _plan_continuous_activity_stop(client, host, target, params):
    project_key = _base.require_str(target, 'projectKey', 'continuous-activity-stop')
    recipe_id = _base.require_str(target, 'recipeId', 'continuous-activity-stop')
    rows = _inventory(client, host, 'continuous-activities',
                      project_key).get('activities') or []
    row = next((a for a in rows if a.get('recipeId') == recipe_id), None)
    warnings = []
    if row is None:
        warnings.append('Recipe %s has no continuous activity registered in %s — stop '
                        'may be a no-op.' % (recipe_id, project_key))
    return {'projectKey': project_key, 'recipeId': recipe_id}, {
        'summary': 'Stop the continuous activity of recipe %s in project %s.'
                   % (recipe_id, project_key),
        'activity': row,
        'warnings': warnings or None,
        'note': 'The activity stays stopped until someone starts it again (desired state '
                'is persisted by DSS).',
    }


def _exec_continuous_activity_stop(client, host, target):
    return _base.post_backend_action(client, host, 'continuous-activity-stop', {
        'projectKey': target['projectKey'], 'recipeId': target['recipeId']})


def _webapp_row(client, host, project_key, webapp_id):
    rows = _inventory(client, host, 'webapps', project_key).get('webapps') or []
    row = next((w for w in rows if w.get('id') == webapp_id), None)
    if row is None:
        ids = sorted('%s (%s)' % (w.get('id'), w.get('name')) for w in rows)
        raise ToolkitError(
            'Webapp %r not found in project %r. Webapps: %s'
            % (webapp_id, project_key, ', '.join(ids[:15]) or '(none)'),
            remediation="Check with config_inspect domain='webapps' filter=<projectKey>.")
    return row


def _plan_webapp_backend(client, host, target, action, verb):
    project_key = _base.require_str(target, 'projectKey', action)
    webapp_id = _base.require_str(target, 'webappId', action)
    row = _webapp_row(client, host, project_key, webapp_id)
    warnings = []
    if action == 'webapp-backend-stop' and row.get('backendRunning') is False:
        warnings.append('Backend of %s is already stopped — executing is a no-op.'
                        % webapp_id)
    return {'projectKey': project_key, 'webappId': webapp_id}, {
        'summary': '%s the backend of webapp %s (%s) in project %s.'
                   % (verb, webapp_id, row.get('name') or '?', project_key),
        'webappName': row.get('name'),
        'webappType': row.get('type'),
        'backendRunning': row.get('backendRunning'),
        'warnings': warnings or None,
        'note': 'Users of the webapp lose their session while the backend is down.',
    }


def _plan_webapp_backend_stop(client, host, target, params):
    return _plan_webapp_backend(client, host, target, 'webapp-backend-stop', 'STOP')


def _plan_webapp_backend_restart(client, host, target, params):
    return _plan_webapp_backend(client, host, target, 'webapp-backend-restart', 'RESTART')


def _exec_webapp_backend_stop(client, host, target):
    return _base.post_backend_action(client, host, 'webapp-backend-stop', {
        'projectKey': target['projectKey'], 'webappId': target['webappId']})


def _exec_webapp_backend_restart(client, host, target):
    return _base.post_backend_action(client, host, 'webapp-backend-restart', {
        'projectKey': target['projectKey'], 'webappId': target['webappId']})


def _plan_notebook_kernels_shutdown(client, host, target, params):
    project_key = str((target or {}).get('projectKey') or '').strip() or None
    inv = _inventory(client, host, 'notebooks', project_key, active=True)
    sessions = inv.get('notebooks') or []
    if not sessions:
        raise ToolkitError('No active notebook kernels found%s on host %r.'
                           % (' in project %r' % project_key if project_key else '', host),
                           remediation='Nothing to shut down — do not propose this item.')
    canonical = {'projectKey': project_key or ''}
    return canonical, {
        'summary': 'Shut down %d active notebook kernel(s)%s — unsaved kernel state is '
                   'lost, notebook files are untouched.'
                   % (len(sessions), ' in project %s' % project_key if project_key else
                      ' across all projects'),
        'kernels': sessions[:20],
        'kernelCount': len(sessions),
        'truncatedNote': inv.get('note'),
        'note': 'Notebook files and outputs stay on disk; only the running kernels '
                '(and their memory) go away. Users can restart kernels at any time.',
    }


def _exec_notebook_kernels_shutdown(client, host, target):
    return _base.post_backend_action(client, host, 'notebook-kernels-shutdown', {
        'projectKey': target.get('projectKey') or ''})


def _plan_notebook_clear_outputs(client, host, target, params):
    project_key = _base.require_str(target, 'projectKey', 'notebook-clear-outputs')
    name = _base.require_str(target, 'notebookName', 'notebook-clear-outputs')
    rows = _inventory(client, host, 'notebooks', project_key).get('notebooks') or []
    row = next((n for n in rows if n.get('name') == name), None)
    if row is None:
        names = sorted(str(n.get('name')) for n in rows)
        raise ToolkitError(
            'Notebook %r not found in project %r. Notebooks: %s'
            % (name, project_key, ', '.join(names[:15]) or '(none)'),
            remediation="Check with config_inspect domain='notebooks' filter=<projectKey>.")
    return {'projectKey': project_key, 'notebookName': name}, {
        'summary': 'Clear the saved cell outputs of notebook %s in project %s (shrinks '
                   'the .ipynb; code cells untouched).' % (name, project_key),
        'language': row.get('language'),
        'note': 'Outputs are NOT restorable — re-running the notebook regenerates them.',
    }


def _exec_notebook_clear_outputs(client, host, target):
    return _base.post_backend_action(client, host, 'notebook-clear-outputs', {
        'projectKey': target['projectKey'], 'notebookName': target['notebookName']})


def _plan_variables_set(client, host, target, params):
    path = _base.require_str(target, 'path', 'variables-set')
    if 'newValue' not in (target or {}):
        raise ToolkitError('variables-set target needs {"path": ..., "newValue": ...} '
                           '(dot/index path into the GLOBAL instance variables).')
    segments = _base.check_secret_path(path)
    root = str(segments[0]) if segments else ''
    if root in _PROTECTED_VARIABLES:
        raise ToolkitError(
            'Variable %r is protected: agents never read or edit the toolkit\'s own '
            'finding whitelist.' % root,
            remediation='Whitelist changes are made by a human in Settings → Findings '
                        'whitelist. Relay this refusal verbatim.')
    current = client.get('/api/tools/admin-actions/global-variable', host=host,
                         params={'path': path}).get('value')
    new_value = target.get('newValue')
    canonical = {'path': path, 'newValue': new_value, 'expectedCurrent': current}
    return canonical, {
        'summary': 'Set global instance variable %s: %s → %s.' % (
            path, json.dumps(current, default=str)[:120],
            json.dumps(new_value, default=str)[:120]),
        'currentValue': current,
        'proposedValue': new_value,
        'note': _base.drift_note() + ' Global variables apply instance-wide (project '
                'variables can still override them).',
    }


def _exec_variables_set(client, host, target):
    return _base.post_backend_action(client, host, 'variables-set', {
        'path': target['path'], 'newValue': target.get('newValue'),
        'expectedCurrent': target.get('expectedCurrent')})


def _changes_variables_set(target, result):
    return [{'itemKey': 'globalVariable:%s' % result.get('path'),
             'before': result.get('before'), 'after': result.get('after')}]


SPECS = [
    _base.spec('job-kill',
               'job-kill {projectKey, jobId}', 'amber',
               _plan_job_kill, _exec_job_kill, batchable=True),
    _base.spec('scenario-disable',
               'scenario-disable {projectKey, scenarioId}', 'amber',
               _plan_scenario_disable, _exec_scenario_toggle, batchable=True,
               settings_hook=_changes_scenario_toggle),
    _base.spec('scenario-enable',
               'scenario-enable {projectKey, scenarioId}', 'amber',
               _plan_scenario_enable, _exec_scenario_toggle, batchable=True,
               settings_hook=_changes_scenario_toggle),
    _base.spec('scenario-kill',
               'scenario-kill {projectKey, scenarioId}', 'amber',
               _plan_scenario_kill, _exec_scenario_kill),
    _base.spec('scenario-run',
               'scenario-run {projectKey, scenarioId}', 'amber',
               _plan_scenario_run, _exec_scenario_run),
    _base.spec('continuous-activity-stop',
               'continuous-activity-stop {projectKey, recipeId}', 'amber',
               _plan_continuous_activity_stop, _exec_continuous_activity_stop),
    _base.spec('webapp-backend-stop',
               'webapp-backend-stop {projectKey, webappId}', 'amber',
               _plan_webapp_backend_stop, _exec_webapp_backend_stop, batchable=True),
    _base.spec('webapp-backend-restart',
               'webapp-backend-restart {projectKey, webappId}', 'amber',
               _plan_webapp_backend_restart, _exec_webapp_backend_restart, batchable=True),
    _base.spec('notebook-kernels-shutdown',
               'notebook-kernels-shutdown {projectKey?} (all active kernels, or one '
               'project\'s)', 'amber',
               _plan_notebook_kernels_shutdown, _exec_notebook_kernels_shutdown),
    _base.spec('notebook-clear-outputs',
               'notebook-clear-outputs {projectKey, notebookName}', 'amber',
               _plan_notebook_clear_outputs, _exec_notebook_clear_outputs, batchable=True),
    _base.spec('variables-set',
               'variables-set {path, newValue} (GLOBAL instance variables; secret paths '
               'and the finding whitelist blocked)', 'amber',
               _plan_variables_set, _exec_variables_set,
               settings_hook=_changes_variables_set),
]
