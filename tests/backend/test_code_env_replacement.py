import os
import sys
from unittest import mock

import conftest  # noqa: F401

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'webapps', 'admin-toolkit'))

import backend  # noqa: E402
from adk_backend.routes import code_env_replace  # noqa: E402


class FakeSettings:
    def __init__(self, raw):
        self.raw = raw
        self.saved = False

    def get_raw(self):
        return self.raw

    def save(self):
        self.saved = True


class FakeNotebookContent:
    def __init__(self, raw):
        self.raw = raw
        self.saved = False

    def get_raw(self):
        return self.raw

    def set_raw(self, raw):
        self.raw = raw

    def save(self):
        self.saved = True


class FakeNotebook:
    def __init__(self, content):
        self.content = content

    def get_content(self):
        return self.content


class FakeProject:
    def __init__(self, settings, notebooks):
        self.settings = settings
        self.notebooks = notebooks

    def get_settings(self):
        return self.settings

    def get_jupyter_notebook(self, notebook_id):
        return self.notebooks[notebook_id]


class FakeClient:
    def __init__(self, usages=None):
        self.envs = [
            {'envLang': 'PYTHON', 'envName': 'source_py', 'kernelSpecName': 'kernel_source'},
            {'envLang': 'PYTHON', 'envName': 'target_py', 'kernelSpecName': 'kernel_target'},
            {'envLang': 'R', 'envName': 'target_r'},
        ]
        self.usages = usages if usages is not None else [
            {'envLang': 'PYTHON', 'envName': 'source_py', 'projectKey': 'P1', 'objectType': 'PROJECT', 'objectId': 'P1', 'objectName': 'Project One'},
            {'envLang': 'PYTHON', 'envName': 'source_py', 'projectKey': 'P1', 'objectType': 'RECIPE', 'objectId': 'recipe_a', 'objectName': 'recipe_a'},
            {'envLang': 'PYTHON', 'envName': 'source_py', 'projectKey': 'P1', 'objectType': 'WEBAPP', 'objectId': 'webapp_a', 'objectName': 'webapp_a'},
            {'envLang': 'PYTHON', 'envName': 'source_py', 'projectKey': 'P1', 'objectType': 'SCENARIO', 'objectId': 'scenario_a', 'objectName': 'scenario_a'},
            {'envLang': 'PYTHON', 'envName': 'source_py', 'projectKey': 'P1', 'objectType': 'NOTEBOOK', 'objectId': 'notebook_a', 'objectName': 'notebook_a'},
        ]
        self.project_settings = FakeSettings({
            'settings': {'codeEnvs': {'python': {'envMode': 'EXPLICIT_ENV', 'envName': 'source_py'}}},
        })
        self.recipe = {'recipe': {'params': {'envSelection': {'envMode': 'EXPLICIT_ENV', 'envName': 'source_py'}}}}
        self.webapp = {'params': {'envSelection': {'envMode': 'EXPLICIT_ENV', 'envName': 'source_py'}}}
        self.scenario = {'params': {'envSelection': {'envMode': 'EXPLICIT_ENV', 'envName': 'source_py'}}}
        self.notebook_content = FakeNotebookContent({'metadata': {'kernelspec': {'name': 'kernel_source', 'display_name': 'kernel_source'}}})
        self.project = FakeProject(self.project_settings, {'notebook_a': FakeNotebook(self.notebook_content)})
        self.puts = []

    def list_code_envs(self):
        return self.envs

    def list_code_env_usages(self):
        return self.usages

    def get_project(self, project_key):
        assert project_key == 'P1'
        return self.project

    def _perform_json(self, method, path, body=None):
        if method == 'GET' and path == '/admin/code-envs/PYTHON/target_py':
            return {'kernelSpecName': 'kernel_target'}
        if method == 'GET' and path == '/admin/code-envs/PYTHON/source_py':
            return {'kernelSpecName': 'kernel_source'}
        if method == 'GET' and path == '/projects/P1/recipes/recipe_a':
            return self.recipe
        if method == 'PUT' and path == '/projects/P1/recipes/recipe_a':
            self.recipe = body
            self.puts.append((method, path, body))
            return body
        if method == 'GET' and path == '/projects/P1/webapps/webapp_a':
            return self.webapp
        if method == 'GET' and path == '/projects/P1/scenarios/scenario_a':
            return self.scenario
        raise AssertionError(f'unexpected _perform_json {method} {path}')

    def _perform_empty(self, method, path, body=None):
        if method == 'PUT' and path == '/projects/P1/webapps/webapp_a':
            self.webapp = body
            self.puts.append((method, path, body))
            return None
        if method == 'PUT' and path == '/projects/P1/scenarios/scenario_a':
            self.scenario = body
            self.puts.append((method, path, body))
            return None
        raise AssertionError(f'unexpected _perform_empty {method} {path}')


def post_replace(client, payload):
    return client.post('/api/code-envs/replace', json={
        'sourceEnvName': 'source_py',
        'sourceLanguage': 'python',
        'targetEnvName': 'target_py',
        'dryRun': True,
        **payload,
    })


def test_code_env_replace_dry_run_returns_matches_without_mutation():
    fake = FakeClient()
    flask_client = backend.app.test_client()
    with mock.patch.object(backend.dataiku, 'api_client', return_value=fake), \
            mock.patch.object(code_env_replace, '_build_project_info', return_value={'P1': {'name': 'Project One', 'owner': 'owner'}}):
        resp = post_replace(flask_client, {})

    data = resp.get_json()
    assert resp.status_code == 200
    assert data['dryRun'] is True
    assert data['matchedRows'] == 5
    assert data['updatedRows'] == 0
    assert fake.project_settings.raw['settings']['codeEnvs']['python']['envName'] == 'source_py'
    assert fake.recipe['recipe']['params']['envSelection']['envName'] == 'source_py'


def test_code_env_replace_unknown_target_returns_400():
    fake = FakeClient()
    flask_client = backend.app.test_client()
    with mock.patch.object(backend.dataiku, 'api_client', return_value=fake):
        resp = post_replace(flask_client, {'targetEnvName': 'missing_py'})

    assert resp.status_code == 400
    assert 'Unknown targetEnvName' in resp.get_json()['error']


def test_code_env_replace_cross_language_target_returns_400():
    fake = FakeClient()
    flask_client = backend.app.test_client()
    with mock.patch.object(backend.dataiku, 'api_client', return_value=fake):
        resp = post_replace(flask_client, {'targetEnvName': 'target_r'})

    assert resp.status_code == 400
    assert 'language does not match' in resp.get_json()['error']


def test_code_env_replace_updates_all_supported_surfaces():
    fake = FakeClient()
    flask_client = backend.app.test_client()
    with mock.patch.object(backend.dataiku, 'api_client', return_value=fake), \
            mock.patch.object(code_env_replace, '_build_project_info', return_value={'P1': {'name': 'Project One', 'owner': 'owner'}}):
        resp = post_replace(flask_client, {'dryRun': False})

    data = resp.get_json()
    assert resp.status_code == 200
    assert data['updatedRows'] == 5
    assert data['failedRows'] == 0
    assert fake.project_settings.raw['settings']['codeEnvs']['python']['envName'] == 'target_py'
    assert fake.recipe['recipe']['params']['envSelection']['envName'] == 'target_py'
    assert fake.webapp['params']['envSelection']['envName'] == 'target_py'
    assert fake.scenario['params']['envSelection']['envName'] == 'target_py'
    assert fake.notebook_content.raw['metadata']['kernelspec']['name'] == 'kernel_target'


def test_code_env_replace_skips_stale_rows():
    fake = FakeClient(usages=[
        {'envLang': 'PYTHON', 'envName': 'source_py', 'projectKey': 'P1', 'objectType': 'RECIPE', 'objectId': 'recipe_a', 'objectName': 'recipe_a'},
    ])
    fake.recipe['recipe']['params']['envSelection']['envName'] = 'other_py'
    flask_client = backend.app.test_client()
    with mock.patch.object(backend.dataiku, 'api_client', return_value=fake), \
            mock.patch.object(code_env_replace, '_build_project_info', return_value={'P1': {'name': 'Project One', 'owner': 'owner'}}):
        resp = post_replace(flask_client, {'dryRun': False})

    data = resp.get_json()
    assert resp.status_code == 200
    assert data['matchedRows'] == 1
    assert data['updatedRows'] == 0
    assert data['skippedRows'] == 1
    assert data['results'][0]['status'] == 'skipped'
    assert 'Current env is other_py' in data['results'][0]['error']
