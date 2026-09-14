"""Owned project archives and plugin fixtures; always remove test objects."""
import io
import json
import secrets
import zipfile
from urllib.parse import urlsplit


def fixtures(run):
    c, tk = run.dss, run.tk
    suffix = secrets.token_hex(4)
    key, plugin_id = 'ATKAB' + suffix.upper(), 'atk-ab-' + suffix
    project = None
    parts = urlsplit(tk.base_url).path.strip('/').split('/')
    support = c.get_project(parts[parts.index('web-apps-backends') + 1])
    folder = support.get_managed_folder(tk.get('/api/managed-folders')['folders'][0]['id'])
    backups = set()

    def archive_exists(filename, member):
        backups.add(filename)
        with folder.get_file(filename) as stream:
            archive = zipfile.ZipFile(io.BytesIO(stream.content))
            names = archive.namelist()
            if not any(name.endswith(member) for name in names):
                run.save({'capability': '_fixture_archive', 'status': 'unexpected_layout',
                          'members': names[:20]})
                raise AssertionError('Expected archive member absent')

    def reset_project():
        nonlocal project
        if key not in c.list_project_keys():
            project = c.create_project(key, 'ADTK archive comparison ' + suffix, 'admin')
        project.set_variables({'standard': {'comparison': suffix}, 'local': {}})
        tk.post('/api/cache/clear')

    def verify_export(result):
        archive_exists(result['result']['backupFile'], 'export-manifest.json')
        return {'readable_archive': True, 'project_still_exists': key in c.list_project_keys()}

    def verify_delete(result):
        assert key not in c.list_project_keys()
        # The legacy cleaner returns its path under a different key.
        files = folder.list_contents()['items']
        matching = [row['path'] for row in files if key in row['path'] and row['path'].endswith('.zip')]
        assert matching
        for name in matching:
            archive_exists(name, 'export-manifest.json')
        return {'project_deleted': True, 'readable_backup': True}

    def installed():
        return any(row['id'] == plugin_id for row in c.list_plugins())

    def reset_plugin():
        if not installed():
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('plugin.json', json.dumps({'id': plugin_id, 'version': '0.0.1',
                    'meta': {'label': 'Disposable ADTK comparison', 'description': 'No data or workloads',
                             'author': 'ADTK test', 'icon': 'icon-beaker', 'licenseInfo': 'Internal test'}}))
                z.writestr('code-env/python/desc.json', json.dumps({
                    'acceptedPythonInterpreters': ['PYTHON311'], 'forceConda': False,
                    'installCorePackages': False, 'installJupyterSupport': False}))
                z.writestr('code-env/python/spec/requirements.txt', '')
            archive.seek(0)
            c.install_plugin_from_archive(archive)
        tk.post('/api/cache/clear')

    def reset_plugin_env():
        reset_plugin()
        plugin = c.get_plugin(plugin_id)
        try:
            future = plugin.create_code_env(python_interpreter='PYTHON311')
            if future:
                creation = future.wait_for_result()
                settings = plugin.get_settings()
                settings.set_code_env(creation['envName'])
                settings.save()
        except Exception as exc:
            if 'already' not in str(exc).lower():
                run.save({'capability': '_fixture_env', 'status': 'fixture_error',
                          'detail': str(exc)[:350]})
                raise

    def verify_rebuild(result):
        assert installed() and result['result']['ok']
        return {'unused_plugin_environment_rebuilt': True}

    def verify_uninstall(result):
        if installed():
            run.save({'capability': '_fixture_plugin', 'status': 'not_deleted',
                      'result_keys': sorted(result.get('result', {})),
                      'error': str(result.get('result', {}).get('error'))[:350],
                      'status_value': result.get('status')})
            raise AssertionError('Plugin still installed')
        archive_exists(result['result']['backupFile'], 'plugin.json')
        return {'unused_plugin_deleted': True, 'readable_backup': True}

    try:
        with run.gates(['project-export', 'project-delete', 'plugin-code-env-rebuild', 'plugin-uninstall']):
            if not getattr(run, 'only', None) or 'project-export' in run.only:
                run.action('project-export', {'projectKey': key}, reset_project, verify_export,
                           'Disposable project: read exported ZIP and confirm project survives')
            if not getattr(run, 'only', None) or 'project-delete' in run.only:
                run.action('project-delete', {'projectKey': key}, reset_project, verify_delete,
                           'Disposable project: independently verify absence and readable ZIP backup')
            if not getattr(run, 'only', None) or 'plugin-code-env-rebuild' in run.only:
                run.action('plugin-code-env-rebuild', {'pluginId': plugin_id}, reset_plugin_env, verify_rebuild,
                       'Unused test plugin; no packages, core libraries or Jupyter requested')
            if not getattr(run, 'only', None) or 'plugin-uninstall' in run.only:
                run.action('plugin-uninstall', {'pluginId': plugin_id}, reset_plugin, verify_uninstall,
                       'Unused test plugin: verify absence and readable ZIP backup')
    finally:
        errors = []
        for label, callback in [
            ('project', lambda: c.get_project(key).delete() if key in c.list_project_keys() else None),
            ('plugin', lambda: c.get_plugin(plugin_id).delete() if installed() else None),
        ]:
            try:
                callback()
            except Exception as exc:
                errors.append(label + ': ' + type(exc).__name__)
        for path in backups:
            try:
                folder.delete_file(path)
            except Exception as exc:
                errors.append('backup: ' + type(exc).__name__)
        for row in c.list_code_envs():
            if row['envName'] == 'plugin_' + plugin_id + '_managed':
                try:
                    c.get_code_env('PYTHON', row['envName']).delete()
                except Exception as exc:
                    errors.append('plugin environment: ' + type(exc).__name__)
        run.save({'capability': '_fixtures', 'status': 'cleanup_failed' if errors else 'deleted',
                  'project': key, 'plugin': plugin_id, 'errors': errors})
