"""Local /proc per-process sampling for the resource stream — no subprocess.

htop/top-style: a long-lived sampler keeps the previous sweep's cumulative
utime+stime per pid and derives instantaneous CPU%% from the delta against the
/proc/stat total-jiffies delta (`100 * cpuCount * d(ticks) / d(total)` — % of
one core, matching `ps pcpu` semantics). Only the LOCAL host uses this; remote
hosts keep the process-metrics macro. The payload shape is kept in lockstep
with python-runnables/process-metrics/runnable.py so the frontend store can't
tell the two sources apart.
"""
import os
import pwd
from typing import Any, Dict, List, Optional, Tuple

from adk_backend.sysinfo import _dip_home

# Same row cap as the process-metrics macro (union of top-N by RSS and by CPU).
_MAX_PROCESSES = 200

_PAGE_KB = max(1, os.sysconf('SC_PAGESIZE') // 1024) if hasattr(os, 'sysconf') else 4

_UID_CACHE: Dict[int, str] = {}


def _uid_name(uid: int) -> str:
    name = _UID_CACHE.get(uid)
    if name is None:
        try:
            name = pwd.getpwuid(uid).pw_name
        except Exception:
            name = str(uid)
        _UID_CACHE[uid] = name
    return name


def _parse_pid_stat(text: str) -> Optional[Tuple[str, int, int, int, int]]:
    """/proc/<pid>/stat → (comm, utime+stime, starttime, vszKb, rssPages).

    comm (field 2) may contain spaces and parentheses, so split around the
    outermost parens; fields after ')' are then whitespace-separated with
    field N of stat(5) at index N-3.
    """
    lb = text.find('(')
    rb = text.rfind(')')
    if lb < 0 or rb <= lb:
        return None
    comm = text[lb + 1:rb]
    rest = text[rb + 2:].split()
    if len(rest) < 22:
        return None
    try:
        utime = int(rest[11])       # field 14
        stime = int(rest[12])       # field 15
        starttime = int(rest[19])   # field 22 — disambiguates reused pids
        vsz_kb = int(rest[20]) // 1024  # field 23 (bytes)
        rss_pages = int(rest[21])   # field 24
    except ValueError:
        return None
    return comm, utime + stime, starttime, vsz_kb, rss_pages


def _pid_command(entry: str, comm: str) -> str:
    try:
        with open('/proc/%s/cmdline' % entry, 'rb') as fh:
            raw = fh.read()
        command = ' '.join(
            part.decode('utf-8', 'replace') for part in raw.split(b'\0') if part
        )
    except Exception:
        command = ''
    # Kernel threads have an empty cmdline — show [comm] like ps does.
    return command or '[%s]' % comm


def _read_proc_processes(
    prev: Optional[Dict[str, Any]],
    total_jiffies: int,
    cpu_count: int,
    mem_total_kb: int,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """One /proc sweep. `prev` is the previous sweep's returned state (or None
    on the first call). Returns (payload, state): payload is None on the first
    sweep — there is no tick delta to derive CPU%% from yet."""
    d_total = 0
    prev_ticks: Dict[int, Tuple[int, int]] = {}
    if prev is not None:
        d_total = total_jiffies - int(prev.get('total') or 0)
        prev_ticks = prev.get('ticks') or {}

    ticks: Dict[int, Tuple[int, int]] = {}
    procs: List[Dict[str, Any]] = []
    for entry in os.listdir('/proc'):
        if not entry.isdigit():
            continue
        try:
            with open('/proc/%s/stat' % entry, 'r') as fh:
                parsed = _parse_pid_stat(fh.read())
            if parsed is None:
                continue
            comm, cpu_ticks, starttime, vsz_kb, rss_pages = parsed
            pid = int(entry)
            ticks[pid] = (starttime, cpu_ticks)
            cpu_pct = 0.0
            if d_total > 0:
                prev_entry = prev_ticks.get(pid)
                # starttime match rules out a pid reused since the last sweep.
                if prev_entry and prev_entry[0] == starttime:
                    cpu_pct = max(
                        0.0, 100.0 * cpu_count * (cpu_ticks - prev_entry[1]) / d_total
                    )
            rss_kb = rss_pages * _PAGE_KB
            uid = os.stat('/proc/%s' % entry).st_uid
            procs.append({
                'pid': pid,
                'user': _uid_name(uid),
                'cpuPercent': round(cpu_pct, 1),
                'memPercent': round(100.0 * rss_kb / mem_total_kb, 1) if mem_total_kb else 0.0,
                'rssKb': rss_kb,
                'vszKb': vsz_kb,
                'command': _pid_command(entry, comm),
            })
        except Exception:
            continue  # pid vanished mid-read

    state = {'total': total_jiffies, 'ticks': ticks}
    if prev is None:
        return None, state

    total = len(procs)
    truncated = total > _MAX_PROCESSES
    if truncated:
        # Keep both the top N by memory and by CPU so neither doughnut/table
        # sort hides a process that ranks high on its own metric.
        by_rss = sorted(procs, key=lambda p: p['rssKb'], reverse=True)[:_MAX_PROCESSES]
        by_cpu = sorted(procs, key=lambda p: p['cpuPercent'], reverse=True)[:_MAX_PROCESSES]
        kept = {p['pid']: p for p in by_rss}
        for p in by_cpu:
            kept.setdefault(p['pid'], p)
        procs = sorted(kept.values(), key=lambda p: p['rssKb'], reverse=True)
    else:
        procs.sort(key=lambda p: p['rssKb'], reverse=True)

    payload = {
        'ok': True,
        'processes': procs,
        'totalProcesses': total,
        'truncated': truncated,
        'dipHome': _dip_home(),
    }
    return payload, state
