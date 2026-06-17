"""Plugin macro: collect per-process CPU and memory usage.

Read-only. Runs as the `dataiku` service account (impersonate=false) so it can
see every process on the target host regardless of webapp impersonation. Backs
the Overview "Memory usage by PID" breakdown and the "CPU" sub-page.
"""
import json
import os
import subprocess

from dataiku.runnables import Runnable

# Cap rows to bound the JSON payload. A DSS host typically runs well under this;
# when exceeded, we keep the heaviest `limit` by memory AND by CPU (union, so at
# most 2x the cap) so both the Memory and CPU sub-pages — which share this one
# fetch — show their full top-N. `truncated` is set whenever rows were dropped.
_MAX_PROCESSES = 200


def _run(cmd, timeout=8):
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if completed.returncode == 0:
            return completed.stdout or ''
        return '__error__: ' + ((completed.stderr or completed.stdout or 'ps failed')[:200])
    except Exception as exc:
        return f'__error__: {type(exc).__name__}: {str(exc)[:160]}'


def _collect_processes(limit=_MAX_PROCESSES):
    # pid user %cpu %mem rss(KB) vsz(KB) args(full command, may contain spaces)
    # `user:64` overrides ps's default 8-char USER width — otherwise long
    # accounts (e.g. dssuser_<login>) get clipped to "dssuser+". The padding
    # spaces it adds are collapsed by split(None, 6) below, so parsing is
    # unaffected. 64 is 2x the Linux useradd username limit.
    out = _run(['ps', '-eo', 'pid=,user:64=,pcpu=,pmem=,rss=,vsz=,args='])
    if not isinstance(out, str) or out.startswith('__error__'):
        return {'ok': False, 'error': out or 'ps produced no output'}
    procs = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        pid, user, pcpu, pmem, rss, vsz, args = parts
        try:
            procs.append({
                'pid': int(pid),
                'user': user,
                'cpuPercent': float(pcpu),
                'memPercent': float(pmem),
                'rssKb': int(rss),
                'vszKb': int(vsz),
                'command': args,
            })
        except ValueError:
            continue
    total = len(procs)
    truncated = total > limit
    if truncated:
        # Keep both the top `limit` by memory and the top `limit` by CPU so
        # neither sub-page hides a process that ranks high on its own metric.
        by_rss = sorted(procs, key=lambda p: p['rssKb'], reverse=True)[:limit]
        by_cpu = sorted(procs, key=lambda p: p['cpuPercent'], reverse=True)[:limit]
        kept = {p['pid']: p for p in by_rss}
        for p in by_cpu:
            kept.setdefault(p['pid'], p)
        procs = sorted(kept.values(), key=lambda p: p['rssKb'], reverse=True)
    else:
        procs.sort(key=lambda p: p['rssKb'], reverse=True)
    # DIP_HOME varies per host; report the *target* host's value (this macro runs
    # on the host) so the frontend can strip it from process command lines.
    # Mirrors the fallback chain in python-lib/adk_backend/sysinfo.py:_dip_home.
    dip_home = os.environ.get('DIP_HOME') or os.environ.get('DSS_HOME') or ''
    return {
        'ok': True,
        'processes': procs,
        'totalProcesses': total,
        'truncated': truncated,
        'dipHome': dip_home,
    }


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        return json.dumps(_collect_processes())
