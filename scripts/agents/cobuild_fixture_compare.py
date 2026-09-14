#!/usr/bin/env python3
"""Run bounded action comparisons on a disposable DSS project and test user.

Explicit --execute is required. No existing workloads or user accounts are
modified. The test user has no groups and is deleted in finally. The scratch
project is deleted with its managed data and run logs. All gate changes are
restored. Use only an instance authorized for these live tests.
"""
import argparse
import copy
import json
import secrets
import time

from cobuild_compare import Comparison, ROOT


def wait_for(check, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(2)
    raise RuntimeError('Fixture postcondition timed out')


def fixtures(run, selected=None):
    c, tk = run.dss, run.tk
    suffix = secrets.token_hex(4)
    key = 'ATKAB' + suffix.upper()
    login = 'atk_ab_' + suffix
    variable = 'atk_ab_' + suffix
    project = user = None
    cases = {}
    try:
        project = c.create_project(key, 'ADTK Cobuild comparison ' + suffix, 'admin')
        run.save({'capability': '_fixtures', 'status': 'created', 'project': key})
        scenario = project.create_scenario('Comparison scenario', 'step_based',
                                           {'active': False, 'triggers': [], 'params': {'steps': []}})
        starget = {'projectKey': key, 'scenarioId': scenario.id}

        def set_active(active):
            s = scenario.get_settings()
            s.active = active
            s.save()

        def active_is(value):
            assert scenario.get_settings().active == value
            return {'active': value, 'triggers': 0}

        cases['scenario-enable'] = (starget, lambda: set_active(False), lambda _: active_is(True),
                                    'Enable trigger-free disposable scenario')
        cases['scenario-disable'] = (starget, lambda: set_active(True), lambda _: active_is(False),
                                     'Disable trigger-free disposable scenario')

        def reset_variables():
            project.set_variables({'standard': {'comparison': 'before'}, 'local': {}})

        def verify_variables(_):
            v = project.get_variables()
            assert v['standard']['comparison'] == 'after'
            return {'standard.comparison': 'after'}

        cases['project-variables-set'] = ({'projectKey': key, 'path': 'standard.comparison', 'newValue': 'after'},
                                          reset_variables, verify_variables, 'One disposable project variable')

        def global_reset():
            v = c.get_global_variables()
            v[variable] = 'before'
            c.set_variables(v)

        def global_verify(_):
            assert c.get_global_variables()[variable] == 'after'
            return {'owned_test_variable': 'after'}

        cases['variables-set'] = ({'path': variable, 'newValue': 'after'}, global_reset,
                                  global_verify, 'One uniquely named test variable; removed in cleanup')

        notebook_content = {'nbformat': 4, 'nbformat_minor': 2,
                            'metadata': {'kernelspec': {'name': 'python3', 'display_name': 'Python 3', 'language': 'python'}},
                            'cells': [{'cell_type': 'code', 'metadata': {}, 'execution_count': 1,
                                       'source': ['print(7)'], 'outputs': [{'output_type': 'stream', 'name': 'stdout', 'text': ['7\n']}]}]}
        notebook = project.create_jupyter_notebook('comparison', copy.deepcopy(notebook_content))

        def reset_notebook():
            content = notebook.get_content()
            content.get_raw().update(copy.deepcopy(notebook_content))
            content.save()

        def verify_notebook(_):
            cells = notebook.get_content().get_cells()
            assert cells[0]['source'] == ['print(7)'] and not cells[0]['outputs']
            return {'source_preserved': True, 'outputs': 0}

        cases['notebook-clear-outputs'] = ({'projectKey': key, 'notebookName': 'comparison'},
                                           reset_notebook, verify_notebook, 'Saved synthetic notebook output; no kernel required')

        try:
            user = c.create_user(login, secrets.token_urlsafe(40), display_name='ADTK comparison before',
                                 groups=[], profile='EXPLORER')

            def set_user(enabled=True, display='ADTK comparison before'):
                s = user.get_settings()
                s.enabled = enabled
                s.get_raw()['displayName'] = display
                s.save()

            def verify_user(enabled=None, display=None):
                s = user.get_settings()
                if enabled is not None:
                    assert s.enabled is enabled
                if display is not None:
                    assert s.get_raw()['displayName'] == display
                return {'enabled': s.enabled, 'displayName': s.get_raw()['displayName']}

            cases['user-disable'] = ({'login': login}, lambda: set_user(True),
                                      lambda _: verify_user(False), 'New test account with no groups')
            cases['user-enable'] = ({'login': login}, lambda: set_user(False),
                                     lambda _: verify_user(True), 'New test account with no groups')
            cases['user-update'] = ({'login': login, 'displayName': 'ADTK comparison after'}, lambda: set_user(True),
                                     lambda _: verify_user(display='ADTK comparison after'), 'Display name only on new test account')

            def reset_owner():
                permissions = project.get_permissions()
                permissions['owner'] = 'admin'
                project.set_permissions(permissions)

            def verify_owner(_):
                assert project.get_permissions()['owner'] == login
                return {'owner_changed_to_fixture_user': True}

            cases['project-change-owner'] = ({'projectKey': key, 'newOwner': login}, reset_owner,
                                              verify_owner, 'Disposable project and test user')
        except Exception as exc:
            run.save({'capability': '_user_fixture', 'status': 'blocked', 'reason': type(exc).__name__})

        keys = [name for name in cases if selected is None or name in selected]
        # The backend inventory may predate the just-created project/user.
        tk.post('/api/cache/clear')
        with run.gates(keys):
            for name in keys:
                run.action(name, *cases[name])
    finally:
        errors = []
        try:
            v = c.get_global_variables()
            if variable in v:
                del v[variable]
                c.set_variables(v)
        except Exception as exc:
            errors.append('test variable: ' + type(exc).__name__)
        if project:
            try:
                project.delete(clear_managed_datasets=True, clear_output_managed_folders=True)
            except Exception as exc:
                errors.append('project: ' + type(exc).__name__)
        if user:
            try:
                user.delete()
            except Exception as exc:
                errors.append('test user: ' + type(exc).__name__)
        run.save({'capability': '_fixtures', 'status': 'cleanup_failed' if errors else 'deleted',
                  'project': key, 'errors': errors})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--url-file', default=str(ROOT / '.dss-url'))
    parser.add_argument('--key-file', default=str(ROOT / '.dss-api-key'))
    parser.add_argument('--output', required=True)
    parser.add_argument('--action', action='append')
    args = parser.parse_args()
    if not args.execute:
        parser.error('--execute is required for live fixture mutations')
    run = Comparison(args.url_file, args.key_file, args.output)
    fixtures(run, args.action)


if __name__ == '__main__':
    main()
