from unittest import mock

import conftest  # noqa: F401

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'webapps', 'admin-toolkit'))

import backend  # noqa: E402
from adk_backend import clients as adk_clients  # noqa: E402


class FakeFolderProject:
    def __init__(self, key=backend.MACRO_PROJECT_KEY, folders=None, webapps=None):
        self.key = key
        self.folders = folders if folders is not None else [{'id': 'backups', 'name': 'Backups'}]
        self.webapps = webapps if webapps is not None else []
        self.summary_read = False

    def get_summary(self):
        self.summary_read = True
        return {'projectKey': self.key}

    def list_managed_folders(self):
        return self.folders

    def list_webapps(self):
        return self.webapps


class FakeRemoteClient:
    def __init__(self):
        self.project_keys = []
        self.projects = {
            backend.MACRO_PROJECT_KEY: FakeFolderProject(backend.MACRO_PROJECT_KEY, folders=[]),
            'DIAG_PARSER_BRANCH1': FakeFolderProject(
                'DIAG_PARSER_BRANCH1',
                folders=[{'id': 'backups', 'name': 'Backups'}],
                webapps=[{
                    'type': 'webapp_admin-toolkit_admin-toolkit',
                    'name': 'admintoolkit',
                    'backendRunning': True,
                }],
            ),
        }

    def get_project(self, key):
        self.project_keys.append(key)
        return self.projects[key]

    def list_projects(self):
        return [{'projectKey': key} for key in self.projects]


class NamedClient:
    def __init__(self, name):
        self.name = name


def test_managed_folders_use_remote_support_project():
    fake = FakeRemoteClient()

    with mock.patch.object(backend, '_resolve_client', return_value=fake), \
            mock.patch.object(adk_clients, '_remote_host_config', return_value={}):
        resp = backend.app.test_client().get(
            '/api/managed-folders',
            headers={'X-DSS-Host-Id': 'tam-global'},
        )

    assert resp.status_code == 200
    assert resp.get_json()['folders'] == [{'id': 'backups', 'name': 'Backups'}]
    assert fake.project_keys[-1] == 'DIAG_PARSER_BRANCH1'


def test_install_ini_parser_accepts_flat_and_section_keys():
    parsed = backend._instance_info_from_install_map(backend._parse_install_ini_map("""
general.nodeid = flat-node
general.installid = flat-install
[server]
ssl = true
port = 11200
"""))
    assert parsed == {
        'nodeId': 'flat-node',
        'installId': 'flat-install',
        'https': True,
        'port': '11200',
    }


def test_cache_get_is_scoped_by_active_host():
    backend._CACHE.clear()
    with backend.app.test_request_context('/api/overview', headers={'X-DSS-Host-Id': 'local'}):
        backend.g.host_id = 'local'
        assert backend._cache_get('overview', 60, lambda: {'host': 'local'}) == {'host': 'local'}
    with backend.app.test_request_context('/api/overview', headers={'X-DSS-Host-Id': 'tam-global'}):
        backend.g.host_id = 'tam-global'
        assert backend._cache_get('overview', 60, lambda: {'host': 'remote'}) == {'host': 'remote'}
    with backend.app.test_request_context('/api/overview', headers={'X-DSS-Host-Id': 'local'}):
        backend.g.host_id = 'local'
        assert backend._cache_get('overview', 60, lambda: {'host': 'wrong'}) == {'host': 'local'}


def test_thread_pool_propagates_remote_host_to_thread_client():
    remote = NamedClient('remote')

    with mock.patch.object(adk_clients, '_remote_host_config', return_value={
        'id': 'tam-global',
        'url': 'https://tam-global.example',
        'apiKey': 'secret',
        'verifyTls': True,
    }), mock.patch.object(adk_clients, '_build_remote_client', return_value=remote):
        with backend.app.test_request_context('/api/project-footprint', headers={'X-DSS-Host-Id': 'tam-global'}):
            backend.g.host_id = 'tam-global'
            with backend.ThreadPoolExecutor(max_workers=1) as pool:
                assert pool.submit(lambda: adk_clients._thread_client().name).result(timeout=5) == 'remote'


def test_local_toolkit_client_ignores_remote_thread_context():
    local = NamedClient('local')
    backend._THREAD_LOCAL.__dict__.clear()
    backend._THREAD_LOCAL.host_id = 'tam-global'

    with mock.patch.object(backend.dataiku, 'api_client', return_value=local):
        assert backend._local_toolkit_client().name == 'local'
