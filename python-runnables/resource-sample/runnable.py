"""Plugin macro: instantaneous CPU/memory counter snapshot.

Read-only and very cheap (two /proc file reads, no subprocess). Returns the RAW
cumulative jiffy counters from /proc/stat plus /proc/meminfo sizes; the caller
diffs two consecutive samples to derive CPU%. Backs the Resources page live
graph on remote hosts. Standalone on purpose — mirrors the /proc parsers in
python-lib/adk_backend/sysinfo.py, which serve the local-host path in-process.
"""
import json
import time

from dataiku.runnables import Runnable


def _parse_proc_stat(text):
    """First `cpu ` aggregate line → cumulative jiffies + cpu core count."""
    cpu = None
    cpu_count = 0
    for line in (text or '').splitlines():
        if line.startswith('cpu '):
            fields = line.split()[1:]
            values = []
            for f in fields[:8]:
                try:
                    values.append(int(f))
                except ValueError:
                    values.append(0)
            while len(values) < 8:
                values.append(0)
            cpu = {
                'user': values[0], 'nice': values[1], 'system': values[2],
                'idle': values[3], 'iowait': values[4], 'irq': values[5],
                'softirq': values[6], 'steal': values[7],
            }
        elif line.startswith('cpu'):
            cpu_count += 1
    if cpu is None:
        return None
    cpu['cpuCount'] = cpu_count
    return cpu


_MEMINFO_KEYS = {
    'MemTotal': 'totalKb',
    'MemFree': 'freeKb',
    'MemAvailable': 'availableKb',
    'Buffers': 'buffersKb',
    'Cached': 'cachedKb',
    'SwapTotal': 'swapTotalKb',
    'SwapFree': 'swapFreeKb',
}


def _parse_proc_meminfo(text):
    """`Key:   12345 kB` lines → the seven sizes the live graph consumes."""
    out = {}
    for line in (text or '').splitlines():
        key, _, rest = line.partition(':')
        mapped = _MEMINFO_KEYS.get(key.strip())
        if not mapped:
            continue
        parts = rest.split()
        if not parts:
            continue
        try:
            out[mapped] = int(parts[0])
        except ValueError:
            continue
    return out if 'totalKb' in out else None


def _read_resource_sample():
    try:
        with open('/proc/stat', 'r') as f:
            stat_text = f.read()
        with open('/proc/meminfo', 'r') as f:
            meminfo_text = f.read()
    except Exception as exc:
        return {'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:160]}'}
    cpu = _parse_proc_stat(stat_text)
    mem = _parse_proc_meminfo(meminfo_text)
    if cpu is None or mem is None:
        return {'ok': False, 'error': 'unparseable /proc/stat or /proc/meminfo'}
    return {'ok': True, 'ts': time.time(), 'cpu': cpu, 'mem': mem}


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        return json.dumps(_read_resource_sample())
