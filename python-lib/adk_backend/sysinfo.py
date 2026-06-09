"""Host/system introspection: filesystem, memory, OS, Spark, license parsing."""

import json
import os
import platform
import re
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional

from adk_backend.logparse import _coerce_log_text

def _dip_home() -> str:
    dip_home = os.environ.get('DIP_HOME') or os.environ.get('DSS_HOME') or '/data/dataiku/dss_data'
    if not dip_home.endswith('/'):
        dip_home += '/'
    return dip_home


def _safe_read_text(path: str) -> Optional[str]:
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            return handle.read()
    except Exception:
        return None


def _safe_read_json(path: str) -> Optional[Dict[str, Any]]:
    text = _safe_read_text(path)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None



def _run_command(cmd: List[str]) -> Optional[str]:
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        return output.decode('utf-8', errors='replace')
    except Exception:
        return None


def _format_size_kb(value: int) -> str:
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.2f} GB"
    if value >= 1024:
        return f"{value / 1024:.2f} MB"
    return f"{value} KB"


def _format_size_bytes(value: int) -> str:
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.2f} MB"
    if value >= 1024:
        return f"{value / 1024:.2f} KB"
    return f"{value} bytes"


def _format_size_human(value: int) -> str:
    if value <= 0:
        return "0 B"
    units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
    size = float(value)
    unit_idx = 0
    while size >= 1024 and unit_idx < len(units) - 1:
        size /= 1024
        unit_idx += 1
    return f"{size:.2f} {units[unit_idx]}"


_PSEUDO_FS_TYPES = {
    'autofs',
    'bpf',
    'cgroup',
    'cgroup2',
    'configfs',
    'debugfs',
    'devpts',
    'devtmpfs',
    'efivarfs',
    'fusectl',
    'hugetlbfs',
    'mqueue',
    'nsfs',
    'proc',
    'pstore',
    'ramfs',
    'rpc_pipefs',
    'securityfs',
    'sysfs',
    'tmpfs',
    'tracefs',
}


def _read_df_mount_usage() -> List[Dict[str, Any]]:
    output = _run_command(['df', '-B1', '-PT'])
    if not output:
        return []
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        return []

    mounts: List[Dict[str, Any]] = []
    for line in lines[1:]:
        parts = re.split(r"\s+", line, maxsplit=6)
        if len(parts) < 7:
            continue
        filesystem, fs_type, blocks, used, available, _capacity, mount_path = parts
        try:
            mounts.append({
                'filesystem': filesystem,
                'fsType': fs_type.lower(),
                'blocks': int(blocks),
                'used': int(used),
                'available': int(available),
                'path': os.path.abspath(mount_path),
            })
        except Exception:
            continue
    return mounts


def _is_virtual_mount(mount: Dict[str, Any]) -> bool:
    fs_type = str(mount.get('fsType') or '').lower()
    mount_path = str(mount.get('path') or '')
    if fs_type in _PSEUDO_FS_TYPES:
        return True
    for prefix in ('/proc', '/sys', '/dev', '/run'):
        if mount_path == prefix or mount_path.startswith(prefix + '/'):
            return True
    return False


def _summarize_df_mounts() -> Dict[str, Any]:
    mounts = _read_df_mount_usage()
    included: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    for mount in mounts:
        if _is_virtual_mount(mount):
            excluded.append(mount)
        else:
            included.append(mount)

    by_path: Dict[str, int] = {}
    root_used = 0
    mounted_used = 0
    top_buckets: Dict[str, Dict[str, Any]] = {}
    for mount in included:
        mount_path = str(mount.get('path') or '/')
        used = int(mount.get('used') or 0)
        by_path[mount_path] = by_path.get(mount_path, 0) + used
        if mount_path == '/':
            root_used += used
            continue
        mounted_used += used
        top = '/' + mount_path.strip('/').split('/')[0]
        bucket = top_buckets.setdefault(top, {'size': 0, 'mounts': []})
        bucket['size'] = int(bucket.get('size') or 0) + used
        bucket['mounts'].append(mount_path)

    return {
        'included': included,
        'excluded': excluded,
        'byPath': by_path,
        'rootUsed': int(root_used),
        'mountedUsed': int(mounted_used),
        'totalUsed': int(root_used + mounted_used),
        'topBuckets': top_buckets,
    }



def _parse_memory_info(free_output: Optional[str]) -> Dict[str, str]:
    if not free_output:
        return {}
    lines = [line.strip() for line in free_output.strip().split('\n') if line.strip()]
    if len(lines) < 2:
        return {}

    headers = re.split(r"\s+", lines[0])
    mem_values = re.split(r"\s+", lines[1])
    start_index = 1 if mem_values and mem_values[0].lower().startswith('mem') else 0

    memory_info: Dict[str, str] = {}
    for idx, header in enumerate(headers):
        value_index = idx + start_index
        if value_index >= len(mem_values):
            continue
        try:
            mb_value = int(mem_values[value_index])
        except Exception:
            continue
        if mb_value >= 1024:
            memory_info[header] = f"{round(mb_value / 1024)} GB"
        else:
            memory_info[header] = f"{mb_value:,} MB"

    if len(lines) >= 3:
        swap_values = re.split(r"\s+", lines[2])
        if len(swap_values) > 3:
            try:
                swap_total = int(swap_values[1])
                swap_used = int(swap_values[2])
                swap_free = int(swap_values[3])
            except Exception:
                swap_total = 0
                swap_used = 0
                swap_free = 0
            if swap_total > 0:
                def fmt(v: int) -> str:
                    return f"{v / 1024:.2f} GB" if v >= 1024 else f"{v:,} MB"
                memory_info['Swap total'] = fmt(swap_total)
                memory_info['Swap used'] = fmt(swap_used)
                memory_info['Swap free'] = fmt(swap_free)
            else:
                memory_info['Swap'] = 'Not configured'

    order = [
        'total', 'used', 'free', 'available', 'shared', 'buff/cache',
        'Swap', 'Swap total', 'Swap used', 'Swap free'
    ]
    ordered: Dict[str, str] = {}
    for key in order:
        if key in memory_info:
            ordered[key] = memory_info[key]
    for key, value in memory_info.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def _parse_system_limits(ulimit_output: Optional[str]) -> Dict[str, str]:
    if not ulimit_output:
        return {}
    lines = [line.strip() for line in ulimit_output.strip().split('\n') if line.strip()]
    temp_limits: Dict[str, str] = {}

    for line in lines:
        match = re.match(r"^([^()]+)\s+\(([^)]+)\)\s+(.+)$", line)
        if not match:
            continue
        name = match.group(1).strip()
        details = match.group(2).strip()
        value = match.group(3).strip()

        if value == 'unlimited':
            temp_limits[name] = 'Unlimited'
            continue
        try:
            num_value = int(value)
            if 'kbytes' in details:
                temp_limits[name] = _format_size_kb(num_value)
            elif 'bytes' in details:
                temp_limits[name] = _format_size_bytes(num_value)
            else:
                temp_limits[name] = f"{num_value:,}"
        except Exception:
            temp_limits[name] = value

    priority = [
        'open files',
        'max user processes',
        'max memory size',
        'stack size',
        'max locked memory',
        'pending signals',
    ]
    ordered: Dict[str, str] = {}
    for key in priority:
        if key in temp_limits:
            ordered[key] = temp_limits.pop(key)
    ordered.update(temp_limits)
    return ordered


def _parse_filesystem_info(df_output: Optional[str]) -> List[Dict[str, str]]:
    if not df_output:
        return []
    lines = [line.rstrip() for line in df_output.strip().split('\n') if line.strip()]
    if len(lines) < 2:
        return []

    entries: List[Dict[str, str]] = []
    i = 1
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        parts = re.split(r"\s+", line)
        has_percentage = any(re.match(r"^\d{1,3}%$", p) for p in parts)

        if not has_percentage and i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            if next_line:
                line = parts[0] + ' ' + next_line
                i += 1
        final_parts = re.split(r"\s+", line)
        percent_idx = next((idx for idx, p in enumerate(final_parts) if re.match(r"^\d{1,3}%$", p)), -1)
        if percent_idx >= 4:
            entries.append({
                'Filesystem': ' '.join(final_parts[:percent_idx - 3]),
                'Size': final_parts[percent_idx - 3],
                'Used': final_parts[percent_idx - 2],
                'Available': final_parts[percent_idx - 1],
                'Use%': final_parts[percent_idx],
                'Mounted on': ' '.join(final_parts[percent_idx + 1:]),
            })
        elif len(final_parts) >= 6 and re.match(r"^\d{1,3}%$", final_parts[4]):
            entries.append({
                'Filesystem': final_parts[0],
                'Size': final_parts[1],
                'Used': final_parts[2],
                'Available': final_parts[3],
                'Use%': final_parts[4],
                'Mounted on': ' '.join(final_parts[5:]),
            })
        i += 1

    return entries


def _get_cpu_cores() -> str:
    """Read /proc/cpuinfo to compute cores and threads, matching the parent webapp's format."""
    try:
        cpuinfo = _safe_read_text('/proc/cpuinfo')
        if not cpuinfo:
            return str(os.cpu_count() or '??')
        threads = len(re.findall(r'^processor\s*:', cpuinfo, re.MULTILINE))
        cores_match = re.search(r'^cpu cores\s*:\s*(\d+)', cpuinfo, re.MULTILINE)
        if not threads or not cores_match:
            return str(os.cpu_count() or '??')
        cores_per_socket = int(cores_match.group(1))
        physical_ids = re.findall(r'^physical id\s*:\s*(\d+)', cpuinfo, re.MULTILINE)
        sockets = len(set(physical_ids)) if physical_ids else 1
        total_cores = sockets * cores_per_socket
        if threads > total_cores:
            return f"{total_cores} Cores / {threads} Threads"
        return str(total_cores)
    except Exception:
        return str(os.cpu_count() or '??')


def _get_os_info() -> str:
    os_release = _safe_read_text('/etc/os-release')
    if os_release:
        for line in os_release.split('\n'):
            if line.startswith('PRETTY_NAME='):
                value = line.split('=', 1)[1].strip().strip('"')
                if value:
                    return value
    return platform.platform()


def _parse_supervisord_restart(log_content: Any) -> Optional[str]:
    text = _coerce_log_text(log_content)
    if not text:
        return None
    lines = text.split('\n')
    target_line = None
    for line in reversed(lines):
        if 'success: backend entered RUNNING state' in line:
            target_line = line
            break
    if not target_line:
        return None
    match = re.match(r"^(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2},\d{3})", target_line)
    if not match:
        return None
    timestamp_str = match.group(1).replace(',', '.')
    try:
        dt = datetime.fromisoformat(timestamp_str)
        return dt.strftime('%b %d, %Y, %I:%M %p')
    except Exception:
        return None


def _find_spark_version(settings: Any) -> Optional[str]:
    if isinstance(settings, dict):
        for key, value in settings.items():
            if isinstance(key, str) and key.lower() in ('spark.version', 'sparkversion'):
                return str(value)
            found = _find_spark_version(value)
            if found:
                return found
    elif isinstance(settings, list):
        for item in settings:
            found = _find_spark_version(item)
            if found:
                return found
    return None


def _format_camel_case(value: str) -> str:
    value = value.replace('.', ' ')
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value).split()
    return ' '.join(part.capitalize() for part in parts)


def _format_date_string(value: str) -> str:
    if not value:
        return value
    if len(value) == 8 and value.isdigit():
        try:
            dt = datetime.strptime(value, '%Y%m%d')
            return dt.strftime('%b %d, %Y')
        except Exception:
            return value
    return value


def _parse_license(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        'license': data or {},
        'licenseInfo': data or {},
        'company': None,
        'licenseProperties': {},
        'hasLicenseUsage': False,
    }
    if not data:
        return result

    content = data.get('content') if isinstance(data.get('content'), dict) else data
    licensee = content.get('licensee') or {}
    if isinstance(licensee, dict):
        result['company'] = licensee.get('company')

    properties = content.get('properties') or {}
    for key, value in properties.items():
        formatted_key = _format_camel_case(str(key))
        if key == 'emittedOn' and isinstance(value, str):
            formatted_value = _format_date_string(value)
        else:
            formatted_value = str(value)
        result['licenseProperties'][formatted_key] = formatted_value

    if content.get('expiresOn'):
        result['licenseProperties']['Expires On'] = _format_date_string(content['expiresOn'])

    usage = content.get('usage') or {}

    def usage_value(current: Any, limit: Any) -> Optional[str]:
        try:
            current_f = float(current)
            limit_f = float(limit)
        except Exception:
            return None
        if limit_f <= 0:
            return None
        return f"{current} / {limit} ({round((current_f / limit_f) * 100)}%)"

    if usage:
        result['hasLicenseUsage'] = True
        if usage.get('namedUsers'):
            current = usage['namedUsers'].get('current')
            limit = usage['namedUsers'].get('limit')
            value = usage_value(current, limit)
            if value:
                result['licenseProperties']['Named Users'] = value
        if usage.get('concurrentUsers'):
            current = usage['concurrentUsers'].get('current')
            limit = usage['concurrentUsers'].get('limit')
            value = usage_value(current, limit)
            if value:
                result['licenseProperties']['Concurrent Users'] = value
        if usage.get('connections'):
            current = usage['connections'].get('current')
            limit = usage['connections'].get('limit')
            value = usage_value(current, limit)
            if value:
                result['licenseProperties']['Connections'] = value
        if usage.get('projects'):
            current = usage['projects'].get('current')
            limit = usage['projects'].get('limit')
            value = usage_value(current, limit)
            if value:
                result['licenseProperties']['Projects'] = value
        if usage.get('features'):
            for feature in usage['features']:
                name = feature.get('name')
                current = feature.get('current')
                limit = feature.get('limit')
                if name:
                    value = usage_value(current, limit)
                    if value:
                        result['licenseProperties'][_format_camel_case(name)] = value

    return result

