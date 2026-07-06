"""Docker Cache Governor macro.

Every docker invocation is a FIXED argv list from
atk_agent_common.policies.docker_cmds — no shell, no free-text interpolation.
Requires the linux `dataiku` user to be in the docker group (no sudo);
permission failures return a structured `docker-permission` error the plan
surfaces with the exact fix. daemon.json limits are NEVER executed here —
`daemon-config-script` returns the sudo script text for a human admin.
"""
import json
import os
import subprocess

from dataiku.runnables import Runnable

from atk_agent_common.policies import docker_cmds


def _bool(value, default=False):
    if value is None or value == '':
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('true', '1', 'yes', 'y')


def _dip_home():
    return os.environ.get('DIP_HOME') or os.environ.get('DKU_DIP_HOME') or ''


_PERMISSION_MARKERS = ('permission denied', 'got permission denied', 'dial unix /var/run/docker.sock')


def _run(argv, timeout=300):
    """Run a fixed argv list. Returns {'ok', 'stdout', 'stderr', 'rc'} or a
    structured error dict; never raises."""
    try:
        proc = subprocess.run(argv, shell=False, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {'ok': False, 'error': 'docker-missing',
                'message': 'The docker CLI is not installed on this host (or not on PATH '
                           'for the dataiku user).'}
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': 'timeout',
                'message': 'docker command timed out after %ss: %s' % (timeout, ' '.join(argv))}
    stderr = (proc.stderr or '').strip()
    if proc.returncode != 0:
        low = stderr.lower()
        if any(m in low for m in _PERMISSION_MARKERS):
            return {'ok': False, 'error': 'docker-permission',
                    'message': 'The dataiku user cannot talk to the docker daemon. Fix: add '
                               'dataiku to the docker group (`sudo usermod -aG docker dataiku`) '
                               'and restart DSS. The toolkit never uses sudo for docker.',
                    'stderr': stderr[:500]}
        return {'ok': False, 'error': 'docker-failed', 'rc': proc.returncode,
                'stderr': stderr[:1000], 'stdout': (proc.stdout or '')[:500]}
    return {'ok': True, 'stdout': proc.stdout or '', 'stderr': stderr, 'rc': 0}


def _parse_size_to_bytes(text):
    """Docker human sizes: '1.5GB', '250MB', '0B', '1.2kB'."""
    s = str(text or '').strip()
    units = {'B': 1, 'KB': 1000, 'MB': 1000 ** 2, 'GB': 1000 ** 3, 'TB': 1000 ** 4}
    for suffix in ('TB', 'GB', 'MB', 'KB', 'B'):
        if s.upper().endswith(suffix):
            try:
                return int(float(s[:-len(suffix)]) * units[suffix])
            except ValueError:
                return None
    return None


def _df():
    """`docker system df --format '{{json .}}'` → one JSON object per line
    (Images / Containers / Local Volumes / Build Cache)."""
    res = _run(docker_cmds.build_command('df'))
    if not res['ok']:
        return res
    rows = []
    total_reclaimable = 0
    for line in res['stdout'].splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        reclaimable_raw = str(row.get('Reclaimable') or '')
        reclaimable = _parse_size_to_bytes(reclaimable_raw.split('(')[0])
        rows.append({
            'type': row.get('Type'),
            'total': row.get('TotalCount') or row.get('Total'),
            'active': row.get('Active'),
            'size': row.get('Size'),
            'reclaimable': reclaimable_raw,
            'reclaimableBytes': reclaimable,
        })
        if reclaimable:
            total_reclaimable += reclaimable
    return {'ok': True, 'rows': rows, 'totalReclaimableBytes': total_reclaimable,
            'totalReclaimableGB': round(total_reclaimable / (1000 ** 3), 2)}


def _usage_scan():
    """docker info → DockerRootDir; compare filesystems with DIP_HOME (the
    overlay2-eating-the-data-mount detector) and add df totals."""
    res = _run(docker_cmds.build_command('info'))
    if not res['ok']:
        return res
    try:
        info = json.loads(res['stdout'])
    except json.JSONDecodeError as exc:
        return {'ok': False, 'error': 'docker-info-unparseable', 'message': str(exc)[:200]}
    root_dir = info.get('DockerRootDir') or '/var/lib/docker'
    dip_home = _dip_home()
    out = {
        'ok': True,
        'dockerRootDir': root_dir,
        'driver': info.get('Driver'),
        'imagesCount': info.get('Images'),
        'containersRunning': info.get('ContainersRunning'),
        'serverVersion': info.get('ServerVersion'),
        'dipHome': dip_home,
        'sameFilesystemAsDssData': None,
    }
    try:
        root_stat = os.stat(root_dir)
        dip_stat = os.stat(dip_home) if dip_home else None
        out['sameFilesystemAsDssData'] = bool(dip_stat and root_stat.st_dev == dip_stat.st_dev)
        vfs = os.statvfs(root_dir)
        total = vfs.f_frsize * vfs.f_blocks
        free = vfs.f_frsize * vfs.f_bavail
        out['filesystem'] = {
            'totalBytes': total,
            'freeBytes': free,
            'usedPct': round(100.0 * (total - free) / total, 1) if total else None,
        }
    except OSError as exc:
        out['filesystemError'] = str(exc)[:200]
    df = _df()
    if df.get('ok'):
        out['df'] = df['rows']
        out['totalReclaimableBytes'] = df['totalReclaimableBytes']
        out['totalReclaimableGB'] = df['totalReclaimableGB']
    else:
        out['dfError'] = df
    return out


def _prune(op, keep_storage_gb, filter_until_hours, dry_run):
    try:
        argv = docker_cmds.build_command(
            op,
            keep_storage_gb=keep_storage_gb if op == 'builder-prune' else None,
            filter_until_hours=filter_until_hours if op == 'image-prune' else None)
    except ValueError as exc:
        return {'ok': False, 'refused': [str(exc)]}
    if dry_run:
        df = _df()
        if not df.get('ok'):
            return df
        return {'ok': True, 'dryRun': True, 'command': argv,
                'note': 'docker prune has no native dry-run — this is the current df '
                        'RECLAIMABLE estimate; the real prune reclaims at most this.',
                'df': df['rows'], 'estimatedReclaimableBytes': df['totalReclaimableBytes'],
                'estimatedReclaimableGB': df['totalReclaimableGB']}
    res = _run(argv, timeout=1800)
    if not res['ok']:
        return res
    reclaimed = None
    for line in res['stdout'].splitlines():
        if 'Total reclaimed space:' in line:
            reclaimed = line.split(':', 1)[1].strip()
    return {'ok': True, 'dryRun': False, 'command': argv,
            'totalReclaimed': reclaimed,
            'totalReclaimedBytes': _parse_size_to_bytes(reclaimed),
            'outputTail': res['stdout'][-1500:]}


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        operation = str(self.config.get('operation') or '').strip().lower()
        try:
            keep_storage_gb = int(self.config.get('keep_storage_gb') or 20)
            filter_until_hours = int(self.config.get('filter_until_hours') or 0)
        except (TypeError, ValueError):
            return json.dumps({'ok': False,
                               'error': 'keep_storage_gb and filter_until_hours must be integers'})
        dry_run = _bool(self.config.get('dry_run'), default=True)
        try:
            if operation == 'df':
                return json.dumps(_df())
            if operation == 'usage-scan':
                return json.dumps(_usage_scan())
            if operation in ('builder-prune', 'image-prune'):
                return json.dumps(_prune(operation, keep_storage_gb, filter_until_hours, dry_run))
            if operation == 'daemon-config-script':
                return json.dumps({
                    'ok': True,
                    'script': docker_cmds.daemon_json_script(keep_storage_gb=keep_storage_gb),
                    'note': 'This script is NEVER executed by the toolkit — hand it to a '
                            'human admin with root access. It restarts the docker daemon.',
                })
            return json.dumps({'ok': False, 'error': 'Unknown operation: %s' % operation})
        except Exception as exc:
            return json.dumps({'ok': False, 'error': '%s: %s' % (type(exc).__name__, str(exc))})
