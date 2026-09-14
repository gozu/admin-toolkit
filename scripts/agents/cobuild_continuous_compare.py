"""Stop a disposable continuous Python recipe that only sleeps."""
import secrets
import time


def fixtures(run):
    c, tk = run.dss, run.tk
    key = 'ATKAB' + secrets.token_hex(4).upper()
    project = activity = None
    started = False

    def wait_for(check):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if check():
                return
            time.sleep(1)
        raise RuntimeError('Owned continuous activity did not reach expected state')

    try:
        project = c.create_project(key, 'ADTK continuous comparison', 'admin')
        recipe = project.new_recipe('cpython', 'comparison_sleep').create()
        settings = recipe.get_settings()
        settings.set_payload('import time\ntime.sleep(180)\n')
        settings.save()
        activity = recipe.get_continuous_activity()

        def alive():
            return activity.get_status().get('mainLoopState', {}).get('futureInfo', {}).get('alive', False)

        def reset():
            nonlocal started
            if started:
                activity.stop()
                wait_for(lambda: not alive())
            activity.start({'abortAfterCrashes': 1, 'initialRestartDelayMS': 1000})
            started = True
            wait_for(alive)
            time.sleep(3)
            assert alive(), 'Continuous activity failed before the stop test'
            tk.post('/api/cache/clear')

        def verify(_):
            wait_for(lambda: not alive())
            assert activity.get_status()['desiredState'] == 'STOPPED'
            return {'desired_state': 'STOPPED', 'main_loop_alive': False}

        with run.gates(['continuous-activity-stop']):
            run.action('continuous-activity-stop', {'projectKey': key, 'recipeId': recipe.name},
                       reset, verify, 'Disposable continuous Python sleep loop; no streaming data or external source')
    finally:
        errors = []
        if activity and started:
            try:
                activity.stop()
                wait_for(lambda: not alive())
            except Exception as exc:
                errors.append('activity: ' + type(exc).__name__)
        if project:
            try:
                project.delete()
            except Exception as exc:
                errors.append('project: ' + type(exc).__name__)
        run.save({'capability': '_fixtures', 'status': 'cleanup_failed' if errors else 'deleted',
                  'project': key, 'errors': errors})
