"""Update an owned placeholder to a real store release, then remove it.

The store ID must be absent before this test. No existing installation is used.
This checks source replacement, not migration of an in-use plugin's settings.
"""
import io
import json
import time
import zipfile
from urllib.parse import urlsplit


def fixtures(run):
    c, tk = run.dss, run.tk
    plugin_id = 'kml-format'
    started_ms = time.time() * 1000
    assert not any(p['id'] == plugin_id for p in c.list_plugins()), 'Store fixture already exists; refusing'
    parts = urlsplit(tk.base_url).path.strip('/').split('/')
    support = c.get_project(parts[parts.index('web-apps-backends') + 1])
    folder = support.get_managed_folder(tk.get('/api/managed-folders')['folders'][0]['id'])
    backup = 'plugin-' + plugin_id + '-preupdate.zip'
    assert not any(p['path'].lstrip('/') == backup for p in folder.list_contents()['items'])

    def installed():
        return next((p for p in c.list_plugins() if p['id'] == plugin_id), None)

    def delete_plugin():
        c.get_plugin(plugin_id).delete().wait_for_result()
        if installed():
            raise RuntimeError('Owned plugin deletion was not verified')

    def active_updates():
        return [f for f in c.list_futures(all_users=True)
                if f.get('alive') and f.get('payload', {}).get('action') == 'plugin_install'
                and any(t.get('objectType') == 'PLUGIN' and t.get('projectKey') == plugin_id
                        for t in f.get('payload', {}).get('targets', []))]

    assert not active_updates(), 'A previous store operation is still running; reconcile it first'

    def reconcile_updates():
        deadline = time.monotonic() + 900
        while active_updates() and time.monotonic() < deadline:
            time.sleep(2)
        pending = active_updates()
        if pending:
            for future in pending:
                if future.get('startTime', 0) >= started_ms:
                    c.get_future(future['jobId']).abort()
            raise RuntimeError('Store update is still active; cleanup requires reconciliation')

    def reset():
        reconcile_updates()
        if installed():
            assert not c.get_plugin(plugin_id).list_usages().get_raw().get('usages')
            delete_plugin()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as z:
            z.writestr('plugin.json', json.dumps({'id': plugin_id, 'version': '0.0.0', 'meta': {
                'label': 'Disposable ADTK store comparison', 'author': 'ADTK test',
                'description': 'Owned empty placeholder for a store update test',
                'icon': 'icon-beaker', 'licenseInfo': 'Internal test'}}))
            z.writestr('resource/comparison.txt', 'owned placeholder')
        stream.seek(0)
        c.install_plugin_from_archive(stream)
        tk.post('/api/cache/clear')

    def verify(result):
        assert result.get('status') == 'ok', 'Store update did not return success'
        row = installed()
        assert row and row['version'] == '0.1.0'
        assert result['result']['ok'] and result['result']['backupFile'] == backup
        with folder.get_file(backup) as response:
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                assert json.loads(z.read('plugin.json'))['version'] == '0.0.0'
                assert z.read('resource/comparison.txt') == b'owned placeholder'
        return {'store_version': row['version'], 'previous_source_backed_up': True}

    try:
        with run.gates(['plugin-update']):
            run.action('plugin-update', {'pluginId': plugin_id}, reset, verify,
                       'Owned empty kml-format 0.0.0 placeholder replaced by store 0.1.0; previous source backed up')
    finally:
        errors = []
        reconcile_updates()  # Never delete a fixture while its update is still running.
        try:
            if installed():
                delete_plugin()
        except Exception as exc:
            errors.append('plugin: ' + type(exc).__name__)
        try:
            if any(p['path'].lstrip('/') == backup for p in folder.list_contents()['items']):
                folder.delete_file(backup)
        except Exception as exc:
            errors.append('backup: ' + type(exc).__name__)
        run.save({'capability': '_fixtures', 'status': 'cleanup_failed' if errors else 'deleted',
                  'plugin': plugin_id, 'errors': errors})
