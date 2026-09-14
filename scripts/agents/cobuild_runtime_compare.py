"""Owned workload, API-key and environment fixtures for the DSS runtime."""
import secrets
import time


def wait_for(check, timeout=120):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = check()
        if result:
            return result
        time.sleep(2)
    raise RuntimeError('Owned fixture did not reach expected state')


def fixtures(run):
    c, tk = run.dss, run.tk
    suffix = secrets.token_hex(4)
    key = 'ATKAB' + suffix.upper()
    login = 'atk_ab_' + suffix
    env_name = 'atk_ab_' + suffix
    project = user = env = scenario = job = api_key = None
    cases = {}
    try:
        project = c.create_project(key, 'ADTK runtime comparison ' + suffix, 'admin')
        run.save({'capability': '_fixtures', 'status': 'created', 'project': key})
        user = c.create_user(login, secrets.token_urlsafe(40), display_name='ADTK runtime test',
                             groups=[], profile='EXPLORER')

        def reset_key():
            nonlocal api_key
            if api_key:
                ids = {row.id for row in c.list_all_personal_api_keys()}
                if api_key.id_ in ids:
                    api_key.delete()
            api_key = c.create_personal_api_key_for_user(login, label='Disposable ADTK comparison')

        def verify_key(_):
            assert api_key.id_ not in {row.id for row in c.list_all_personal_api_keys()}
            return {'owned_api_key_deleted': True}

        cases['api-key-delete'] = (lambda: {'keyType': 'personal', 'keyId': api_key.id_}, reset_key,
                                   verify_key, 'New key belonging only to the disposable test user')

        scenario = project.create_scenario('Disposable sleeper', 'custom_python',
                                           {'active': False, 'triggers': [], 'params': {}})
        settings = scenario.get_settings()
        settings.code = 'import time\ntime.sleep(180)\n'
        settings.save()

        def reset_scenario():
            if scenario.get_status().running:
                scenario.abort()
                wait_for(lambda: not scenario.get_status().running)
            scenario.run()
            wait_for(lambda: scenario.get_status().running)
            time.sleep(3)  # Let the custom-Python body actually start before aborting.

        def verify_killed(_):
            wait_for(lambda: not scenario.get_status().running)
            last = scenario.get_last_runs(limit=1)[0]
            assert last.outcome == 'ABORTED', 'Observed outcome: ' + str(last.outcome)
            return {'running': False, 'outcome': 'ABORTED'}

        cases['scenario-kill'] = ({'projectKey': key, 'scenarioId': scenario.id}, reset_scenario,
                                  verify_killed, 'Abort a disposable 180-second sleep; no business processing')

        dataset = project.new_managed_dataset('comparison_output').with_store_into('filesystem_managed').create()
        recipe = project.new_recipe('python', 'comparison_sleep').with_output('comparison_output').create()
        settings = recipe.get_settings()
        settings.set_code('import time\ntime.sleep(180)\n')
        settings.save()

        def reset_job():
            nonlocal job
            if job and job.get_status().get('baseStatus', {}).get('state') == 'RUNNING':
                job.abort()
            job = project.start_job({'type': 'NON_RECURSIVE_FORCED_BUILD',
                                      'outputs': [{'id': 'comparison_output', 'type': 'DATASET'}]})
            wait_for(lambda: job.get_status().get('baseStatus', {}).get('state') == 'RUNNING')

        def verify_job(_):
            wait_for(lambda: job.get_status().get('baseStatus', {}).get('state') == 'ABORTED')
            return {'job_state': 'ABORTED'}

        cases['job-kill'] = (lambda: {'projectKey': key, 'jobId': job.id}, reset_job,
                             verify_job, 'Abort a disposable Python recipe that only sleeps')

        def reset_env():
            nonlocal env
            if env_name not in {r['envName'] for r in c.list_code_envs()}:
                env = c.create_code_env('PYTHON', env_name, 'DESIGN_MANAGED',
                                         params={'pythonInterpreter': 'PYTHON311', 'installCorePackages': False,
                                                 'installJupyterSupport': False,
                                                 'desc': {'installCorePackages': False, 'installJupyterSupport': False}})
            else:
                env = c.get_code_env('PYTHON', env_name)
            tk.post('/api/cache/clear')

        def verify_env_update(result):
            assert env_name in {r['envName'] for r in c.list_code_envs()}
            assert result.get('status') == 'ok'
            return {'owned_environment_exists': True, 'update_completed': True}

        def verify_env_delete(_):
            assert env_name not in {r['envName'] for r in c.list_code_envs()}
            return {'owned_environment_deleted': True}

        cases['code-env-update'] = ({'name': env_name, 'lang': 'PYTHON'}, reset_env,
                                    verify_env_update, 'Unused Python 3.11 environment with no requested packages')
        cases['code-env-delete'] = ({'name': env_name, 'lang': 'python'}, reset_env,
                                    verify_env_delete, 'Back up and delete unused disposable environment')
        tk.post('/api/cache/clear')
        with run.gates(list(cases)):
            for action, case in cases.items():
                if getattr(run, 'only', None) and action not in run.only:
                    continue
                run.action(action, *case)
    finally:
        errors = []
        for label, callback in [
            ('scenario', lambda: scenario.abort() if scenario and scenario.get_status().running else None),
            ('job', lambda: job.abort() if job and job.get_status().get('baseStatus', {}).get('state') == 'RUNNING' else None),
            ('project', lambda: project.delete(clear_managed_datasets=True, clear_output_managed_folders=True) if project else None),
            ('user', lambda: user.delete() if user else None),
            ('environment', lambda: c.get_code_env('PYTHON', env_name).delete() if env_name in {r['envName'] for r in c.list_code_envs()} else None),
        ]:
            try:
                callback()
            except Exception as exc:
                errors.append(label + ': ' + type(exc).__name__)
        run.save({'capability': '_fixtures', 'status': 'cleanup_failed' if errors else 'deleted',
                  'project': key, 'errors': errors})
