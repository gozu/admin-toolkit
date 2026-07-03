"""Plugin macro: collect host-level metadata.

Read-only. Used by the Overview module to show the same card regardless of
whether the active host is local or a remote preset.
"""
import json
import os
import platform
import re
import subprocess

from dataiku.runnables import Runnable


def _read(path, max_bytes=64 * 1024):
    try:
        with open(path, 'rb') as fh:
            return fh.read(max_bytes).decode('utf-8', errors='replace')
    except OSError as exc:
        return f'__error__: {type(exc).__name__}: {str(exc)[:120]}'


def _run(cmd, timeout=8):
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if completed.returncode == 0:
            return completed.stdout or ''
        return (completed.stdout or '') + (completed.stderr or '')
    except Exception as exc:
        return f'__error__: {type(exc).__name__}: {str(exc)[:160]}'


def _read_cpuinfo():
    text = _read('/proc/cpuinfo')
    if text.startswith('__error__'):
        return {'error': text}
    model = None
    physical_ids = set()
    logical_cores = 0
    core_pairs = set()
    current_physical = None
    current_core = None
    for line in text.splitlines():
        if not line.strip():
            if current_physical is not None and current_core is not None:
                core_pairs.add((current_physical, current_core))
            current_physical = None
            current_core = None
            continue
        if ':' not in line:
            continue
        key, _, val = line.partition(':')
        key = key.strip().lower()
        val = val.strip()
        if key == 'model name' and not model:
            model = val
        elif key == 'physical id':
            physical_ids.add(val)
            current_physical = val
        elif key == 'core id':
            current_core = val
        elif key == 'processor':
            logical_cores += 1
    if current_physical is not None and current_core is not None:
        core_pairs.add((current_physical, current_core))
    physical_cores = len(core_pairs) or None
    return {
        'modelName': model,
        'logicalCores': logical_cores,
        'physicalCores': physical_cores,
        'physicalSockets': len(physical_ids) or None,
    }


def _read_os_release():
    text = _read('/etc/os-release')
    if text.startswith('__error__'):
        return {'error': text}
    out = {}
    for line in text.splitlines():
        if '=' not in line:
            continue
        k, _, v = line.partition('=')
        out[k.strip()] = v.strip().strip('"')
    return out


def _read_install_ini(dip_home):
    if not dip_home:
        return None
    path = os.path.join(dip_home, 'install.ini')
    text = _read(path)
    if text.startswith('__error__'):
        return {'error': text}
    out = {}
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or line.startswith(';'):
            continue
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1]
            continue
        if '=' in line:
            k, _, v = line.partition('=')
            out[f'{section}.{k.strip()}' if section else k.strip()] = v.strip()
    return out


def _read_dss_version(dip_home):
    if not dip_home:
        return None
    for candidate in ('dss-version.json', 'config/dss-version.json'):
        text = _read(os.path.join(dip_home, candidate))
        if text.startswith('__error__'):
            continue
        try:
            return json.loads(text)
        except Exception:
            return {'raw': text[:1024]}
    return None


def _read_java_mem_raw(dip_home):
    if not dip_home:
        return None
    for candidate in ('bin/env-default.sh', 'install.ini'):
        text = _read(os.path.join(dip_home, candidate))
        if text.startswith('__error__'):
            continue
        hits = re.findall(r'-Xm[xs]\d+[mgMG]', text)
        if hits:
            return hits
    return None


def _dip_home_storage(dip_home):
    """Mount holding DIP_HOME via `df -PT -B1` (mirrors sysinfo._dip_home_storage);
    feeds the health score's NFS / data-mount-full cap rules."""
    if not dip_home:
        return None
    output = _run(['df', '-PT', '-B1', dip_home])
    if not output or output.startswith('__error__'):
        return None
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    parts = re.split(r'\s+', lines[1], maxsplit=6)
    if len(parts) < 7:
        return None
    filesystem, fs_type, blocks, used, _available, capacity, mount_path = parts
    try:
        used_pct = int(str(capacity).rstrip('%'))
    except ValueError:
        try:
            used_pct = round(int(used) * 100 / max(int(blocks), 1))
        except ValueError:
            used_pct = None
    return {
        'path': dip_home,
        'mount': mount_path,
        'filesystem': filesystem,
        'fsType': fs_type.lower(),
        'usedPct': used_pct,
    }


def _tail_supervisord_log(dip_home):
    if not dip_home:
        return ''
    path = os.path.join(dip_home, 'run', 'supervisord.log')
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as fh:
            if size > 256 * 1024:
                fh.seek(size - (256 * 1024))
            return fh.read().decode('utf-8', errors='replace')
    except OSError as exc:
        return f'__error__: {type(exc).__name__}: {str(exc)[:120]}'


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        dip_home = os.environ.get('DIP_HOME') or os.environ.get('DKU_DIP_HOME')
        result = {
            'cpu': _read_cpuinfo(),
            'os': _read_os_release(),
            'install': _read_install_ini(dip_home),
            'version': _read_dss_version(dip_home),
            'javaMemRaw': _read_java_mem_raw(dip_home),
            'freeOutput': _run(['free', '-m']),
            'ulimitOutput': _run(['bash', '-lc', 'ulimit -a']),
            'dfOutput': _run(['df', '-h']),
            'pythonVersion': platform.python_version(),
            'supervisordLog': _tail_supervisord_log(dip_home),
            'dipHome': dip_home,
            'dipHomeStorage': _dip_home_storage(dip_home),
        }
        return json.dumps(result)
