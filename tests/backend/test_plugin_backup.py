"""Installed-plugin backups must be restorable and precede every mutation."""
import importlib.util
import json
from pathlib import Path
import sys
import types
from unittest.mock import MagicMock, patch
import zipfile

import conftest  # noqa: F401
import pytest

from adk_backend.routes import admin_actions


@pytest.fixture
def archive_module(monkeypatch):
    stub = types.ModuleType('dataiku.runnables')
    stub.Runnable = object
    monkeypatch.setitem(sys.modules, 'dataiku.runnables', stub)
    path = Path(__file__).resolve().parents[2] / 'python-runnables/plugin-backup/runnable.py'
    spec = importlib.util.spec_from_file_location('plugin_backup_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source(tmp_path):
    root = tmp_path / 'plugins/installed/example'
    root.mkdir(parents=True)
    (root / 'plugin.json').write_text(json.dumps({'id': 'example', 'version': '1'}))
    (root / 'resource').mkdir()
    (root / 'resource/binary.bin').write_bytes(bytes(range(256)))
    return root


def test_dss_constructor_keeps_parameters_separate_from_project(archive_module):
    macro = archive_module.PluginBackup('ADMINTOOLKIT', {'plugin_id': 'example'}, {})
    assert macro.config == {'plugin_id': 'example'}


def test_archive_preserves_relative_paths_and_bytes(archive_module, tmp_path):
    root = source(tmp_path)
    output = tmp_path / 'backup.zip'
    assert archive_module.archive_plugin(tmp_path, 'example', output) == 2
    with zipfile.ZipFile(output) as z:
        assert set(z.namelist()) == {'plugin.json', 'resource/binary.bin'}
        for name in z.namelist():
            assert z.read(name) == (root / name).read_bytes()


@pytest.mark.parametrize('identifier', ['../example', '', '/tmp', 'a/b', '..'])
def test_rejects_path_escape(archive_module, tmp_path, identifier):
    source(tmp_path)
    with pytest.raises(ValueError):
        archive_module.archive_plugin(tmp_path, identifier, tmp_path / 'out.zip')


@pytest.mark.parametrize('kind', ['file', 'directory', 'root', 'manifest'])
def test_refuses_symlinks_without_upload(archive_module, tmp_path, kind):
    root = source(tmp_path)
    if kind == 'root':
        root.rename(root.with_name('real'))
        root.symlink_to(root.with_name('real'), target_is_directory=True)
    elif kind == 'manifest':
        (root / 'plugin.json').rename(tmp_path / 'manifest.json')
        (root / 'plugin.json').symlink_to(tmp_path / 'manifest.json')
    else:
        (root / 'linked').symlink_to(tmp_path if kind == 'directory' else root / 'plugin.json')
    with pytest.raises(ValueError):
        archive_module.archive_plugin(tmp_path, 'example', tmp_path / 'out.zip')


@pytest.mark.parametrize('action', ['uninstall', 'update'])
def test_backup_failure_prevents_mutation(action):
    client = MagicMock()
    client.get_plugin.return_value.list_usages.return_value.get_raw.return_value = {'usages': []}
    client.list_plugins.return_value = [{'id': 'example', 'isDev': False}]
    macro = MagicMock()
    macro.get_result.return_value = {'ok': False}
    with patch.object(admin_actions, '_backup_folder_handle'), \
         patch.object(admin_actions, '_resolve_macro_project') as project, \
         patch.object(admin_actions, '_active_support_project'):
        project.return_value.get_macro.return_value = macro
        with pytest.raises(RuntimeError, match='mutation refused'):
            getattr(admin_actions, '_impl_plugin_' + action)(client, {'pluginId': 'example', 'folderId': 'f'})
    client.get_plugin.return_value.delete.assert_not_called()
    client.get_plugin.return_value.update_from_store.assert_not_called()


def test_installed_archive_uses_selected_host_and_destination():
    client = MagicMock()
    client.list_plugins.return_value = [{'id': 'example', 'isDev': False}]
    macro = MagicMock()
    macro.get_result.return_value = {'ok': True, 'backupFile': 'plugin-example.zip'}
    with patch.object(admin_actions, '_backup_folder_handle'), \
         patch.object(admin_actions, '_resolve_macro_project') as project, \
         patch.object(admin_actions, '_active_support_project') as support:
        project.return_value.get_macro.return_value = macro
        support.return_value.project_key = 'BACKUPS'
        admin_actions._backup_plugin(client, 'example', 'folder', 'plugin-example.zip')
    project.assert_called_once_with(client)
    assert macro.run.call_args.kwargs['params'] == {
        'plugin_id': 'example', 'project_key': 'BACKUPS',
        'folder_id': 'folder', 'filename': 'plugin-example.zip'}
    client.download_plugin_to_file.assert_not_called()
