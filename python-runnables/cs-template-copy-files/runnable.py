"""Plugin macro: copy files between two Code Studio libName directories.

Runs as the `dataiku` service account (impersonate=false) so it can write into
`<DIP_HOME>/lib/code_studio/<project>/<libName>/`, which is owned by
`dataiku:dataiku` and not writable by impersonated webapp users (e.g.
`dssuser_*`).

Mirrors the no-overwrite behavior of the original in-webapp copy helper:
files that already exist at the destination (placed there by the new
template's starter content) are recorded as `skipped` and not touched.
"""
import grp
import json
import os
import pwd
import shutil

from dataiku.runnables import Runnable


def _runtime_id():
    info = {}
    try:
        info['euid'] = os.geteuid()
        info['egid'] = os.getegid()
        try:
            info['euser'] = pwd.getpwuid(info['euid']).pw_name
        except KeyError:
            info['euser'] = None
        try:
            info['egroup'] = grp.getgrgid(info['egid']).gr_name
        except KeyError:
            info['egroup'] = None
    except Exception as exc:
        info['error'] = str(exc)[:120]
    return info


def _path_id(p):
    try:
        st = os.stat(p)
        owner = pwd.getpwuid(st.st_uid).pw_name if st.st_uid is not None else None
        group = grp.getgrgid(st.st_gid).gr_name if st.st_gid is not None else None
        return {
            'path': p,
            'mode': oct(st.st_mode & 0o7777),
            'uid': st.st_uid, 'owner': owner,
            'gid': st.st_gid, 'group': group,
        }
    except OSError as exc:
        return {'path': p, 'error': str(exc)[:120]}


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        src_dir = (self.config.get('src_dir') or '').strip()
        dst_dir = (self.config.get('dst_dir') or '').strip()
        walk_only = bool(self.config.get('walk_only'))

        runtime = _runtime_id()
        debug = {
            'runtime': runtime,
            'src_dir_stat': _path_id(src_dir) if src_dir else None,
            'dst_dir_stat': _path_id(dst_dir) if dst_dir else None,
            'dst_parent_stat': _path_id(os.path.dirname(dst_dir.rstrip('/'))) if dst_dir else None,
            'walk_only': walk_only,
        }

        if walk_only:
            if not src_dir:
                return json.dumps({
                    'count': 0, 'totalBytes': 0, 'walked': [],
                    'copied': [], 'skipped': [], 'errors': [],
                    'error': 'src_dir is required',
                    'debug': debug,
                })
            if not os.path.isdir(src_dir):
                return json.dumps({
                    'count': 0, 'totalBytes': 0, 'walked': [],
                    'copied': [], 'skipped': [], 'errors': [],
                    'note': 'source directory does not exist',
                    'debug': debug,
                })
            walked = []
            total_bytes = 0

            def _on_walk_error(_exc):
                return None

            for root, _dirs, files in os.walk(src_dir, onerror=_on_walk_error):
                for name in files:
                    full = os.path.join(root, name)
                    try:
                        size = os.path.getsize(full)
                    except OSError:
                        continue
                    rel = os.path.relpath(full, src_dir)
                    walked.append({'path': rel, 'bytes': size})
                    total_bytes += size
            walked.sort(key=lambda e: e['path'])
            return json.dumps({
                'count': len(walked),
                'totalBytes': total_bytes,
                'walked': walked,
                'copied': [], 'skipped': [], 'errors': [],
                'debug': debug,
            })

        if not src_dir or not dst_dir:
            return json.dumps({
                'count': 0, 'totalBytes': 0,
                'copied': [], 'skipped': [], 'errors': [],
                'error': 'src_dir and dst_dir are required',
                'debug': debug,
            })

        if not os.path.isdir(src_dir):
            return json.dumps({
                'count': 0, 'totalBytes': 0,
                'copied': [], 'skipped': [], 'errors': [],
                'note': 'source directory does not exist',
                'debug': debug,
            })

        copied = []
        skipped = []
        errors = []
        total_bytes_copied = 0

        def on_walk_error(_exc):
            return None

        for root, _dirs, files in os.walk(src_dir, onerror=on_walk_error):
            rel_root = os.path.relpath(root, src_dir)
            dst_root = dst_dir if rel_root == '.' else os.path.join(dst_dir, rel_root)
            try:
                os.makedirs(dst_root, exist_ok=True)
            except OSError as exc:
                errors.append({'path': rel_root, 'error': str(exc)[:300]})
                continue
            for name in files:
                src_path = os.path.join(root, name)
                dst_path = os.path.join(dst_root, name)
                rel = os.path.relpath(src_path, src_dir)
                if os.path.exists(dst_path):
                    skipped.append({'path': rel, 'reason': 'exists-in-template-starter'})
                    continue
                try:
                    shutil.copy2(src_path, dst_path)
                    size = os.path.getsize(dst_path)
                    copied.append({'path': rel, 'bytes': size})
                    total_bytes_copied += size
                except OSError as exc:
                    errors.append({'path': rel, 'error': str(exc)[:300]})

        debug['dst_dir_stat_after'] = _path_id(dst_dir)

        return json.dumps({
            'count': len(copied),
            'totalBytes': total_bytes_copied,
            'copied': copied,
            'skipped': skipped,
            'errors': errors,
            'debug': debug,
        })
