"""Back up installed plugin source on its host before an admin mutation.

DSS's public download API only exports development plugins. This macro copies
the installed source, not code environments or instance plugin configuration.
"""
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import zipfile

from dataiku.runnables import Runnable


def archive_plugin(dip_home, plugin_id, destination):
    if not isinstance(plugin_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', plugin_id):
        raise ValueError('Invalid plugin identifier')
    root = Path(dip_home) / 'plugins' / 'installed' / plugin_id
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Installed plugin directory is missing or is a symlink')
    manifest = root / 'plugin.json'
    if manifest.is_symlink() or json.loads(manifest.read_text())['id'] != plugin_id:
        raise ValueError('Plugin manifest does not match the requested plugin')
    files = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in dirs + names:
            path = Path(directory) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError('Plugin backup refuses symlinks and special files')
        files.extend(Path(directory) / name for name in names)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(root).as_posix())
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError('Plugin archive failed its integrity check')
    return len(files)


class PluginBackup(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.config = config

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        import dataiku
        plugin_id = self.config.get('plugin_id')
        filename = self.config.get('filename')
        if filename not in ('plugin-%s.zip' % plugin_id, 'plugin-%s-preupdate.zip' % plugin_id):
            raise ValueError('Invalid backup filename')
        client = dataiku.api_client()
        folder = client.get_project(self.config['project_key']).get_managed_folder(self.config['folder_id'])
        folder.get_definition()
        dip_home = os.environ.get('DIP_HOME') or os.environ.get('DKU_DIP_HOME')
        if not dip_home:
            raise ValueError('DSS home is unavailable')
        with tempfile.NamedTemporaryFile(suffix='.zip') as temp:
            count = archive_plugin(dip_home, plugin_id, temp.name)
            with open(temp.name, 'rb') as stream:
                folder.put_file(filename, stream)
        return json.dumps({'ok': True, 'backupFile': filename, 'files': count})
