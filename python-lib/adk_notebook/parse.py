"""Pure parsing/formatting helpers — copied verbatim from
webapps/admin-toolkit/backend.py.

These functions take already-fetched command output (the strings the
host-metrics / process-metrics macros return) and turn them into the same
dict/list shapes the webapp serializes to JSON. They are PURE — no Flask, no
filesystem, no shell — so the notebook card files produce byte-identical data
to the webapp cards.

The function bodies below are kept identical (underscore names and all) to the
backend originals so a diff stays clean; public, underscore-free aliases are
re-exported at the bottom for ergonomic use from the card files.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional


# --- backend.py:469 ---------------------------------------------------------
def _coerce_log_text(payload: Any) -> Optional[str]:
    def collect(value: Any, depth: int = 0) -> List[str]:
        if depth > 6 or value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, bytes):
            return [value.decode('utf-8', errors='replace')]
        if isinstance(value, list):
            out: List[str] = []
            for item in value:
                out.extend(collect(item, depth + 1))
            return out
        if isinstance(value, dict):
            ordered_keys = ['line', 'message', 'text', 'content', 'log', 'data', 'result', 'value', 'records', 'entries', 'lines']
            out: List[str] = []
            for key in ordered_keys:
                if key in value:
                    out.extend(collect(value.get(key), depth + 1))
            if out:
                return out
            for child in value.values():
                out.extend(collect(child, depth + 1))
            return out
        return [str(value)]

    lines = [line for line in collect(payload) if isinstance(line, str) and line.strip()]
    if not lines:
        return None
    return '\n'.join(lines)


# --- backend.py:509 ---------------------------------------------------------
def _format_size_kb(value: int) -> str:
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.2f} GB"
    if value >= 1024:
        return f"{value / 1024:.2f} MB"
    return f"{value} KB"


# --- backend.py:517 ---------------------------------------------------------
def _format_size_bytes(value: int) -> str:
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.2f} MB"
    if value >= 1024:
        return f"{value / 1024:.2f} KB"
    return f"{value} bytes"


# --- backend.py:525 ---------------------------------------------------------
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


# --- backend.py:783 ---------------------------------------------------------
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


# --- backend.py:842 ---------------------------------------------------------
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


# --- backend.py:886 ---------------------------------------------------------
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


# --- backend.py:966 ---------------------------------------------------------
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


# --- backend.py:1005 --------------------------------------------------------
def _format_camel_case(value: str) -> str:
    value = value.replace('.', ' ')
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value).split()
    return ' '.join(part.capitalize() for part in parts)


# --- backend.py:1011 --------------------------------------------------------
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


# --- backend.py:1023 --------------------------------------------------------
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


# --- backend.py:1102 --------------------------------------------------------
def _parse_log_errors(content: Any) -> Dict[str, Any]:
    text = _coerce_log_text(content)
    if not text:
        return {
            'formattedLogErrors': 'No log errors found',
            'rawLogErrors': [],
            'logStats': {
                'Total Lines': 0,
                'Unique Errors': 0,
                'Displayed Errors': 0,
            }
        }

    lines = text.split('\n')
    lines_before = 10
    lines_after = 100
    time_threshold = 5
    max_errors = 5
    log_levels = [r"\[ERROR\]", r"\[FATAL\]", r"\[SEVERE\]", r"\[WARN\]", r"\bERROR\b", r"\bFATAL\b", r"\bSEVERE\b", r"\bWARN\b"]
    log_level_regex = re.compile(r"(" + '|'.join(log_levels) + r")")
    timestamp_regex = re.compile(r"\[(\d{4}/\d{2}/\d{2}-\d{2}:\d{2}:\d{2}\.\d{3})\]")
    leading_timestamp_regex = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d{3})?)")

    def parse_ts(line: str) -> Optional[float]:
        match = timestamp_regex.search(line)
        if match:
            try:
                dt = datetime.strptime(match.group(1), '%Y/%m/%d-%H:%M:%S.%f')
                return dt.timestamp()
            except Exception:
                pass
        alt = leading_timestamp_regex.search(line)
        if alt:
            value = alt.group(1).replace(',', '.')
            try:
                dt = datetime.fromisoformat(value)
                return dt.timestamp()
            except Exception:
                return None
        return None

    line_count = 0
    error_count = 0
    recent_errors = []
    error_signatures = set()
    before_buffer: List[str] = []
    collecting_after = 0
    after_buffer: List[str] = []
    last_error_timestamp: Optional[float] = None
    last_error_had_real_timestamp = False
    error_line = 0
    error_timestamp_str = ''

    for line in lines:
        line_count += 1

        if collecting_after > 0:
            after_buffer.append(line)
            collecting_after -= 1
            if collecting_after == 0:
                header = "\n" + '=' * 40 + f"\nERROR FOUND AT LINE {error_line} (TIMESTAMP: {error_timestamp_str}):\n" + '=' * 40 + "\n\n\n\n"
                current_error = [header] + before_buffer + after_buffer
                recent_errors.append({'timestamp': error_timestamp_str, 'data': current_error})
                if len(recent_errors) > max_errors:
                    recent_errors.pop(0)
                after_buffer = []
                before_buffer = []
                continue

        before_buffer.append(line)
        if len(before_buffer) > lines_before:
            before_buffer.pop(0)

        if not log_level_regex.search(line):
            continue

        current_ts = parse_ts(line)
        had_real_timestamp = current_ts is not None
        if current_ts is None:
            # Keep parsing stacktraces and non-standard logs that do not carry timestamps.
            current_ts = float(line_count)

        timestamp_str = datetime.fromtimestamp(current_ts).strftime('%Y-%m-%d-%H:%M:%S')
        signature = line[-60:].strip() if len(line) > 60 else line.strip()
        if signature in error_signatures:
            error_signatures.remove(signature)

        if last_error_timestamp is not None and had_real_timestamp and last_error_had_real_timestamp:
            if current_ts - last_error_timestamp < time_threshold:
                if collecting_after > 0:
                    collecting_after = max(collecting_after, lines_after)
                    after_buffer.append(line)
                    collecting_after -= 1
                continue

        error_count += 1
        error_line = line_count
        error_timestamp_str = timestamp_str
        last_error_timestamp = current_ts
        last_error_had_real_timestamp = had_real_timestamp
        error_signatures.add(signature)

        collecting_after = lines_after
        after_buffer = [line]
        collecting_after -= 1

    if collecting_after > 0:
        header = "\n" + '=' * 40 + f"\nERROR FOUND AT LINE {error_line} (TIMESTAMP: {error_timestamp_str}):\n" + '=' * 40 + "\n\n\n\n"
        current_error = [header] + before_buffer + after_buffer
        recent_errors.append({'timestamp': error_timestamp_str, 'data': current_error})
        if len(recent_errors) > max_errors:
            recent_errors.pop(0)

    if recent_errors:
        formatted = _format_log_errors(recent_errors)
    else:
        # No regex-matched errors — show last 1000 lines raw as a fallback
        tail_lines = lines[-1000:] if len(lines) > 1000 else lines
        raw_tail = '\n'.join(tail_lines)
        escaped = (raw_tail
                   .replace('&', '&amp;')
                   .replace('<', '&lt;')
                   .replace('>', '&gt;'))
        formatted = (
            '<div class="log-error-block">'
            '<div class="log-header">No ERROR/FATAL/SEVERE/WARN patterns matched — showing last '
            f'{len(tail_lines):,} lines of backend.log</div>'
            f'<pre style="white-space:pre-wrap;word-break:break-all;font-size:12px;">{escaped}</pre>'
            '</div>'
        )
        recent_errors = [{'timestamp': 'tail', 'data': tail_lines}]

    return {
        'formattedLogErrors': formatted,
        'rawLogErrors': recent_errors,
        'logStats': {
            'Total Lines': line_count,
            'Unique Errors': error_count,
            'Displayed Errors': len(recent_errors),
        }
    }


# --- backend.py:1245 --------------------------------------------------------
def _format_log_errors(errors: List[Dict[str, Any]]) -> str:
    if not errors:
        return 'No log errors found'

    output = ''
    for error in errors:
        output += '<div class="log-error-block">'
        for line in error['data']:
            if 'ERROR FOUND AT LINE' in line:
                header = line.replace('=' * 40, '=' * 20)
                header_parts = header.split('\n')
                formatted_header = ''
                for part in header_parts:
                    formatted_header += '<br>' if part.strip() == '' else part + '<br>'
                formatted_header += '<br>'
                output += f'<div class="log-entry log-header">{formatted_header}</div>'
                continue

            class_name = 'log-entry'
            if '[INFO]' in line or re.search(r"\bINFO\b", line):
                class_name += ' log-info'
            elif '[WARN]' in line or re.search(r"\bWARN\b", line):
                class_name += ' log-warn'
            elif '[ERROR]' in line or re.search(r"\bERROR\b", line):
                class_name += ' log-error'
            elif '[FATAL]' in line or re.search(r"\bFATAL\b", line):
                class_name += ' log-fatal'
            elif '[SEVERE]' in line or re.search(r"\bSEVERE\b", line):
                class_name += ' log-severe'
            elif '[DEBUG]' in line or re.search(r"\bDEBUG\b", line):
                class_name += ' log-debug'
            elif '[TRACE]' in line or re.search(r"\bTRACE\b", line):
                class_name += ' log-trace'

            formatted_line = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            output += f'<div class="{class_name}">{formatted_line}</div>'
        output += '</div>'
    return output


# --- backend.py:1678 --------------------------------------------------------
def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


# --- backend.py:1685 --------------------------------------------------------
def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


# --- backend.py:2536 --------------------------------------------------------
def _parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'y', 'on')


# --- backend.py:2957 --------------------------------------------------------
def _normalize_language(lang_raw: Any) -> str:
    if isinstance(lang_raw, str) and lang_raw.strip().lower().startswith('r'):
        return 'r'
    return 'python'


# --- Public, underscore-free aliases (card files import these) --------------
coerce_log_text = _coerce_log_text
format_size_kb = _format_size_kb
format_size_bytes = _format_size_bytes
format_size_human = _format_size_human
parse_memory_info = _parse_memory_info
parse_system_limits = _parse_system_limits
parse_filesystem_info = _parse_filesystem_info
parse_supervisord_restart = _parse_supervisord_restart
format_camel_case = _format_camel_case
format_date_string = _format_date_string
parse_license = _parse_license
parse_log_errors = _parse_log_errors
format_log_errors = _format_log_errors
coerce_int = _coerce_int
coerce_float = _coerce_float
parse_bool = _parse_bool
normalize_language = _normalize_language
