#!/usr/bin/env python3
"""64-case inventory for whole-task model replacement, with explicit fixtures.

Run this module inside a DSS macro for action groups. It deliberately does not
reuse the old image/Python approvals or provision a cloud cluster implicitly.
Every runnable group receives ModelComparison, never the operation bridge.
"""
import importlib
import os

from cobuild_compare import READS
from cobuild_model_compare import TRANSPORT
from render_cobuild_comparison import PURPOSE
from atk_agent_common import actuator, tools_impl

GROUPS = {
    'reads': tuple(n for n in READS if n != 'db_health'),
    'fixture': ('scenario-enable', 'scenario-disable', 'project-variables-set',
                'variables-set', 'notebook-clear-outputs', 'user-enable',
                'user-disable', 'user-update', 'project-change-owner'),
    'data': ('connection-test', 'connection-update', 'connection-delete',
             'dataset-clear', 'dataset-delete', 'webapp-backend-stop',
             'webapp-backend-restart', 'scenario-run', 'toolkit-scenario-write'),
    'runtime': ('api-key-delete', 'scenario-kill', 'job-kill',
                'code-env-update', 'code-env-delete'),
    'native': ('settings-set', 'k8s-exec-config-tune'),
    'archive': ('project-export', 'project-delete', 'plugin-code-env-rebuild', 'plugin-uninstall'),
    'consolidate': ('code-env-consolidate', 'connection-index'),
    'detach': ('cluster-detach',),
    'continuous': ('continuous-activity-stop',),
    'notebook': ('notebook-kernels-shutdown',),
    'store': ('plugin-update',),
    'cleanup': ('tmp-cleanup', 'exports-cleanup', 'job-logs-cleanup',
                'log-cleanup', 'project-clear-webapp-runs'),
    'docker': ('docker-prune',),
    'database': ('db_health', 'db-vacuum', 'db-analyze', 'db-reindex'),
    'notification': ('notification-send',),
    'project_cluster': ('project-set-cluster',),
    'cloud': ('cluster-start', 'cluster-stop', 'cluster-pods-cleanup',
              'k8s-apply-fix'),
    'images': ('image-delete',),
    'python': ('python-run',),
    'excluded': ('plugin-deploy',),
}
PREREQUISITES = {
    'cloud': 'Needs a new owned cluster lifecycle fixture; previous cloud resources were deleted.',
    'images': 'Needs new disposable image targets; previous two deletion approvals were consumed.',
    'python': 'Needs fresh per-run approval of concrete Python code; previous two approvals were consumed.',
    'excluded': 'plugin-deploy remains excluded at the user\'s request.',
}


def inventory():
    names = [name for group in GROUPS.values() for name in group]
    actual = set(actuator.ACTIONS) | set(tools_impl.SENSOR_DESCRIPTIONS)
    if len(names) != 64 or len(set(names)) != 64 or set(names) != set(PURPOSE) or set(names) != actual:
        raise AssertionError('The model replacement suite must cover every capability exactly once')
    return {name: group for group, names in GROUPS.items() for name in names}


def run_group(run, group, selected=None):
    inventory()
    if selected is not None and not set(selected) <= set(GROUPS[group]):
        raise ValueError('Selected cases must belong to this fixture group')
    names = tuple(n for n in GROUPS[group] if selected is None or n in selected)
    if not names:
        return
    run.only = set(names)
    if group in PREREQUISITES:
        for name in names:
            run.save({'capability': name, 'status': 'skipped' if group == 'excluded' else 'blocked',
                      'transport': TRANSPORT, 'reason': PREREQUISITES[group]})
        return
    if group == 'reads':
        for name in names:
            with run.gates([name]):
                run.read(name, READS[name])
        return
    # Audit, Python subprocesses, SQL, Docker and host files belong to the
    # selected DSS host, not to the workstation running this controller.
    if not os.environ.get('DIP_HOME'):
        raise RuntimeError('Action fixture groups must run inside a macro on the selected DSS host')
    before = len(run.rows)
    module = importlib.import_module('cobuild_' + group + '_compare')
    try:
        if group in ('fixture', 'data'):
            module.fixtures(run, selected=names)
        else:
            module.fixtures(run)
    except Exception as exc:
        run.save({'capability': '_group', 'group': group, 'status': 'fixture_failed',
                  'reason': type(exc).__name__})
    measured = {r['capability'] for r in run.rows[before:]}
    for name in names:
        if name not in measured:
            run.save({'capability': name, 'status': 'blocked', 'transport': TRANSPORT,
                      'reason': 'Fixture did not produce a measurement; inspect private runtime logs.'})
    if any(r['status'] in ('cleanup_failed', 'restore_pending') for r in run.rows[before:]):
        raise RuntimeError('Fixture cleanup needs reconciliation before another group')


inventory()
