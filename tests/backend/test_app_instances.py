"""Unit tests for the App Instances tracking core (Projects -> App Instances).

The subtle piece is `appInstanceCreatorFullId`: DSS overloads it, so it holds a
'<projectKey>.<recipeName>' pair only for instances an App recipe run created,
and the plain app id for instances made from the app's homepage. Verified live
against DSS 14.7 — both shapes appear on the same instance.
"""

import json

import pytest

from adk_backend.routes import app_instances as ai


# ---- recipe type -> app id ----

def test_app_id_from_recipe_type_strips_the_prefix():
    assert ai._app_id_from_recipe_type('App_PROJECT_SALES') == 'PROJECT_SALES'
    assert ai._app_id_from_recipe_type(
        'App_PLUGIN_recommendation-system_recommendation-workflow'
    ) == 'PLUGIN_recommendation-system_recommendation-workflow'


def test_app_id_from_recipe_type_ignores_non_app_recipes():
    """A 'sync'/'python' recipe must never be mistaken for an app call."""
    assert ai._app_id_from_recipe_type('sync') == ''
    assert ai._app_id_from_recipe_type('python') == ''


# ---- project listing split ----

class _FakeClient:
    def __init__(self, projects):
        self._projects = projects

    def list_projects(self):
        return self._projects


def test_collect_projects_splits_instances_from_scannable():
    client = _FakeClient([
        {'projectKey': 'SALES', 'name': 'Sales', 'ownerLogin': 'ana',
         'projectAppType': 'REGULAR'},
        {'projectKey': 'SALES_APP', 'projectAppType': 'APP_TEMPLATE'},
        {'projectKey': 'RUN_load_a1', 'name': 'Execute recipe SALES.load',
         'ownerLogin': 'ana', 'projectAppType': 'APP_INSTANCE',
         'generatingAppId': 'PROJECT_SALES_APP',
         'versionTag': {'lastModifiedOn': 1700000000000}},
    ])
    instances, scannable = ai._collect_projects(client)

    assert [i['projectKey'] for i in instances] == ['RUN_load_a1']
    assert instances[0]['generatingAppId'] == 'PROJECT_SALES_APP'
    assert instances[0]['lastModified'] == 1700000000000
    # Attribution fields stay empty until the macro fills them.
    assert instances[0]['creatorFullId'] is None
    assert instances[0]['orphan'] is None
    # The template is swept for App_ recipes; the instance is not (it is a copy
    # of the template's flow, so its recipes would double-count).
    assert scannable == ['SALES', 'SALES_APP']


def test_collect_projects_survives_a_listing_failure():
    class _Broken:
        def list_projects(self):
            raise RuntimeError('boom')

    assert ai._collect_projects(_Broken()) == ([], [])


# ---- the overloaded creator field ----

def _instance(key, app_id='PROJECT_SALES_APP'):
    return {'projectKey': key, 'generatingAppId': app_id, 'creatorFullId': None,
            'creatorProjectKey': None, 'creatorRecipeName': None,
            'isTemporary': None, 'orphan': None}


def _stub_macro(monkeypatch, rows, ok=True, error=None):
    payload = {'ok': ok, 'rows': rows, 'unreadable': []}
    if error:
        payload['error'] = error
    monkeypatch.setattr(ai, '_app_instances_macro', lambda client: payload)


def test_recipe_created_instance_is_attributed(monkeypatch):
    _stub_macro(monkeypatch, [{
        'projectKey': 'RUN_load_a1',
        'generatingAppId': 'PROJECT_SALES_APP',
        'appInstanceCreatorFullId': 'SALES.load',
        'isTemporaryAppInstance': True,
    }])
    instances = [_instance('RUN_load_a1')]
    status = ai._attribute_instances(None, instances)

    assert status['available'] is True
    assert status['attributed'] == 1
    assert instances[0]['creatorFullId'] == 'SALES.load'
    assert instances[0]['creatorProjectKey'] == 'SALES'
    assert instances[0]['creatorRecipeName'] == 'load'
    assert instances[0]['isTemporary'] is True


def test_homepage_instance_is_not_attributed_to_a_phantom_recipe(monkeypatch):
    """DSS stores the APP ID in appInstanceCreatorFullId for homepage-created
    instances. Splitting on '.' alone would invent a recipe named after part of
    the app id and then report the instance as an orphan of it."""
    _stub_macro(monkeypatch, [{
        'projectKey': 'RUN_tpl_probe1',
        'generatingAppId': 'PROJECT_SALES_APP',
        'appInstanceCreatorFullId': 'PROJECT_SALES_APP',
        'isTemporaryAppInstance': True,
    }])
    instances = [_instance('RUN_tpl_probe1')]
    status = ai._attribute_instances(None, instances)

    assert status['attributed'] == 0
    assert instances[0]['creatorFullId'] is None
    # isTemporary is still recorded — it just is not evidence of recipe origin.
    assert instances[0]['isTemporary'] is True


def test_dotted_app_id_is_not_mistaken_for_a_recipe(monkeypatch):
    """Guards the '.' heuristic on its own: an app id containing a dot must
    still compare equal to generatingAppId and so stay unattributed."""
    _stub_macro(monkeypatch, [{
        'projectKey': 'RUN_x',
        'generatingAppId': 'PLUGIN_com.acme.tools_flow',
        'appInstanceCreatorFullId': 'PLUGIN_com.acme.tools_flow',
        'isTemporaryAppInstance': False,
    }])
    instances = [_instance('RUN_x', 'PLUGIN_com.acme.tools_flow')]
    ai._attribute_instances(None, instances)
    assert instances[0]['creatorFullId'] is None


def test_macro_failure_disables_attribution_rather_than_guessing(monkeypatch):
    def _raise(client):
        raise RuntimeError('macro project missing')

    monkeypatch.setattr(ai, '_app_instances_macro', _raise)
    instances = [_instance('RUN_load_a1')]
    status = ai._attribute_instances(None, instances)

    assert status['available'] is False
    assert 'macro project missing' in status['error']
    assert instances[0]['creatorFullId'] is None


def test_macro_not_ok_is_reported(monkeypatch):
    _stub_macro(monkeypatch, [], ok=False, error='DIP_HOME not set on host')
    status = ai._attribute_instances(None, [])
    assert status['available'] is False
    assert status['error'] == 'DIP_HOME not set on host'


# ---- per-project recipe sweep ----

class _FakeRecipe:
    def __init__(self, raw):
        self._raw = raw

    def get_settings(self):
        return self

    def get_recipe_raw_definition(self):
        return self._raw


class _FakeProject:
    def __init__(self, listing, raws):
        self._listing = listing
        self._raws = raws

    def list_recipes(self):
        return self._listing

    def get_recipe(self, name):
        raw = self._raws[name]
        if isinstance(raw, Exception):
            raise raw
        return _FakeRecipe(raw)


class _ProjectClient:
    def __init__(self, project):
        self._project = project

    def get_project(self, key):
        return self._project


def test_scan_project_recipes_only_pays_for_app_recipes():
    project = _FakeProject(
        [{'name': 'load', 'type': 'App_PROJECT_SALES_APP'},
         {'name': 'clean', 'type': 'shaker'}],
        {'load': {'type': 'App_PROJECT_SALES_APP',
                  'params': {'variables': {}, 'keepInstance': True}}},
    )
    row = ai._scan_project_recipes(_ProjectClient(project), 'SALES')

    assert row['error'] is None
    assert len(row['recipes']) == 1
    recipe = row['recipes'][0]
    assert recipe['fullId'] == 'SALES.load'
    assert recipe['appId'] == 'PROJECT_SALES_APP'
    assert recipe['keepInstance'] is True


def test_scan_project_recipes_marks_an_unreadable_recipe_unknown():
    """keepInstance must stay None, never default to False — a False would read
    as 'this recipe is fine' on a recipe nobody could check."""
    project = _FakeProject(
        [{'name': 'load', 'type': 'App_PROJECT_SALES_APP'}],
        {'load': RuntimeError('permission denied')},
    )
    row = ai._scan_project_recipes(_ProjectClient(project), 'SALES')

    assert row['recipes'][0]['keepInstance'] is None
    assert 'permission denied' in row['recipes'][0]['error']


def test_scan_project_recipes_degrades_an_unreadable_project():
    class _Broken:
        def get_project(self, key):
            raise RuntimeError('no such project')

    row = ai._scan_project_recipes(_Broken(), 'GONE')
    assert row['recipes'] == []
    assert 'no such project' in row['error']


# ---- the macro's own config-tree parsing ----

def _build_inventory(dip_home):
    """Import the runnable with dataiku.runnables stubbed, as conftest does for
    the backend modules."""
    import sys
    import types

    if 'dataiku.runnables' not in sys.modules:
        sub = types.ModuleType('dataiku.runnables')

        class Runnable:
            pass

        sub.Runnable = Runnable
        sys.modules['dataiku.runnables'] = sub
        sys.modules['dataiku'].runnables = sub

    import importlib.util
    import os
    path = os.path.join(os.path.dirname(__file__), '..', '..',
                        'python-runnables', 'app-instances', 'runnable.py')
    spec = importlib.util.spec_from_file_location('atk_app_instances_runnable', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._build_inventory(dip_home)


def _write_project(root, key, params):
    import os
    directory = os.path.join(root, 'config', 'projects', key)
    os.makedirs(directory)
    with open(os.path.join(directory, 'params.json'), 'w') as handle:
        json.dump(params, handle)


def test_macro_returns_only_app_projects_and_counts_the_rest(tmp_path):
    root = str(tmp_path)
    _write_project(root, 'SALES', {'projectAppType': 'REGULAR'})
    _write_project(root, 'PLAIN', {})  # no projectAppType at all
    _write_project(root, 'SALES_APP', {'projectAppType': 'APP_TEMPLATE'})
    _write_project(root, 'RUN_load_a1', {
        'projectAppType': 'APP_INSTANCE',
        'generatingAppId': 'PROJECT_SALES_APP',
        'appInstanceCreatorFullId': 'SALES.load',
        'isTemporaryAppInstance': True,
    })

    result = _build_inventory(root)

    assert result['ok'] is True
    assert result['regularProjects'] == 2
    assert result['scannedProjects'] == 4
    by_key = {r['projectKey']: r for r in result['rows']}
    assert set(by_key) == {'SALES_APP', 'RUN_load_a1'}
    assert by_key['RUN_load_a1']['appInstanceCreatorFullId'] == 'SALES.load'
    assert by_key['RUN_load_a1']['isTemporaryAppInstance'] is True
    assert by_key['SALES_APP']['appInstanceCreatorFullId'] is None


def test_macro_reports_unreadable_params_rather_than_swallowing_them(tmp_path):
    import os
    root = str(tmp_path)
    _write_project(root, 'GOOD', {'projectAppType': 'APP_TEMPLATE'})
    bad = os.path.join(root, 'config', 'projects', 'BAD')
    os.makedirs(bad)
    with open(os.path.join(bad, 'params.json'), 'w') as handle:
        handle.write('{not json')

    result = _build_inventory(root)

    assert result['ok'] is True
    assert [r['projectKey'] for r in result['rows']] == ['GOOD']
    assert [u['projectKey'] for u in result['unreadable']] == ['BAD']


def test_macro_refuses_a_dip_home_without_a_config_tree(tmp_path):
    result = _build_inventory(str(tmp_path))
    assert result['ok'] is False
    assert 'config/projects' in result['error']
