"""Plugin macro: app-template / app-instance inventory from the config tree.

Read-only. Runs as the `dataiku` service account (impersonate=false) so it can
read <DIP_HOME>/config/projects/ regardless of webapp impersonation.

Why a macro at all — `client.list_projects()` already carries `projectAppType`
and `generatingAppId`, but DSS strips the two fields that matter most for
tracking App-as-recipe debris (verified live against DSS 14.7, both
`list_projects()` and `get_settings().get_raw()`):

  - `appInstanceCreatorFullId` — "<PROJECT_KEY>.<recipeName>" of the App recipe
    whose run created this instance. Without it the only API-visible link is
    the project label ("Execute recipe X.y"), a display string a user can
    rename, and the instance project key (`RUN_<recipeName>_<random>`) which
    carries the recipe name but not its project.
  - `isTemporaryAppInstance` — true for instances created by a recipe run
    (git + catalog indexing disabled), false for instances a human made from
    the app's homepage. Only the former accumulate as run debris.

Both live in <DIP_HOME>/config/projects/<KEY>/params.json alongside
`projectAppType` and `generatingAppId` (SerializedProject in the DSS backend).

Emits one row per project that is an app template or an app instance; regular
projects are counted but not returned. Unreadable params.json files are
reported by key and errno rather than swallowed — a partial inventory must
never read as a complete one.
"""
import json
import os

from dataiku.runnables import Runnable

_APP_TYPES = ('APP_TEMPLATE', 'APP_INSTANCE')


def _build_inventory(dip_home):
    projects_dir = os.path.join(dip_home, 'config', 'projects')
    if not os.path.isdir(projects_dir):
        return {'ok': False, 'error': 'no config/projects under %s' % dip_home}

    rows = []
    unreadable = []
    regular = 0
    scanned = 0

    for key in sorted(os.listdir(projects_dir)):
        params_path = os.path.join(projects_dir, key, 'params.json')
        if not os.path.isfile(params_path):
            continue
        scanned += 1
        try:
            with open(params_path, 'r') as handle:
                params = json.load(handle)
        except Exception as exc:
            unreadable.append({
                'projectKey': key,
                'error': '%s: %s' % (type(exc).__name__, str(exc)[:160]),
            })
            continue

        app_type = params.get('projectAppType') or 'REGULAR'
        if app_type not in _APP_TYPES:
            regular += 1
            continue

        rows.append({
            'projectKey': key,
            'projectAppType': app_type,
            'generatingAppId': params.get('generatingAppId') or None,
            'generatingAppVersion': params.get('generatingAppVersion') or None,
            # Present only on instances; None on templates and on instances
            # created outside a recipe run.
            'appInstanceCreatorFullId': params.get('appInstanceCreatorFullId') or None,
            'isTemporaryAppInstance': bool(params.get('isTemporaryAppInstance')),
        })

    return {
        'ok': True,
        'rows': rows,
        'unreadable': unreadable,
        'scannedProjects': scanned,
        'regularProjects': regular,
    }


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        dip_home = os.environ.get('DIP_HOME') or os.environ.get('DKU_DIP_HOME')
        if not dip_home:
            return json.dumps({'ok': False, 'error': 'DIP_HOME not set on host'})
        try:
            result = _build_inventory(dip_home)
        except Exception as exc:
            return json.dumps({'ok': False, 'error': '%s: %s' % (type(exc).__name__, str(exc)[:240])})
        return json.dumps(result)
