"""Unit tests for the /proc/stat + /proc/meminfo resource-sample parsers."""

from adk_backend.sysinfo import _parse_proc_meminfo, _parse_proc_stat

PROC_STAT = """\
cpu  74608 2520 24433 1117073 6176 4054 0 148 0 0
cpu0 17977 551 6103 279059 1596 1109 0 40 0 0
cpu1 18902 649 6206 278794 1481 943 0 33 0 0
cpu2 18560 632 6109 279357 1537 1015 0 37 0 0
cpu3 19169 688 6015 279863 1562 987 0 38 0 0
intr 33124509 122 9 0 0 0 0 3 0 1
ctxt 23456789
btime 1751900000
processes 123456
procs_running 3
procs_blocked 0
"""

PROC_MEMINFO = """\
MemTotal:       16384000 kB
MemFree:         1024000 kB
MemAvailable:    8192000 kB
Buffers:          204800 kB
Cached:          4096000 kB
SwapCached:            0 kB
Active:          9000000 kB
Inactive:        4000000 kB
SwapTotal:       2097152 kB
SwapFree:        2097152 kB
Dirty:               128 kB
"""


def test_parse_proc_stat_aggregate_and_count():
    cpu = _parse_proc_stat(PROC_STAT)
    assert cpu == {
        'user': 74608, 'nice': 2520, 'system': 24433, 'idle': 1117073,
        'iowait': 6176, 'irq': 4054, 'softirq': 0, 'steal': 148,
        'cpuCount': 4,
    }


def test_parse_proc_stat_short_line_pads_zeroes():
    # Ancient kernels may omit iowait/irq/softirq/steal on the cpu line.
    cpu = _parse_proc_stat('cpu  10 20 30 40\ncpu0 10 20 30 40\n')
    assert cpu is not None
    assert cpu['user'] == 10 and cpu['idle'] == 40
    assert cpu['iowait'] == 0 and cpu['steal'] == 0
    assert cpu['cpuCount'] == 1


def test_parse_proc_stat_rejects_garbage():
    assert _parse_proc_stat('') is None
    assert _parse_proc_stat(None) is None
    assert _parse_proc_stat('intr 123\nctxt 456\n') is None


def test_parse_proc_meminfo_extracts_mapped_keys():
    mem = _parse_proc_meminfo(PROC_MEMINFO)
    assert mem == {
        'totalKb': 16384000,
        'freeKb': 1024000,
        'availableKb': 8192000,
        'buffersKb': 204800,
        'cachedKb': 4096000,
        'swapTotalKb': 2097152,
        'swapFreeKb': 2097152,
    }


def test_parse_proc_meminfo_requires_total():
    # MemAvailable alone (no MemTotal) → unusable for the mem% formula.
    assert _parse_proc_meminfo('MemAvailable: 100 kB\n') is None
    assert _parse_proc_meminfo('') is None
    assert _parse_proc_meminfo(None) is None


def test_parse_proc_meminfo_skips_malformed_lines():
    mem = _parse_proc_meminfo('MemTotal: abc kB\nMemTotal: 500 kB\nMemFree:\n')
    assert mem == {'totalKb': 500}
