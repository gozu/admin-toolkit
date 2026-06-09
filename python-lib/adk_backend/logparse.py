"""Log text coercion plus error extraction and HTML formatting."""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

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
            ts_match = re.search(r"\[(\d{4}/\d{2}/\d{2}-\d{2}:\d{2}:\d{2}\.\d{3})\]", formatted_line)
            if ts_match:
                formatted_line = formatted_line.replace(ts_match.group(0), f'<span class="log-timestamp">{ts_match.group(0)}</span>')
            else:
                start_ts_match = re.search(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d{3})?)", formatted_line)
                if start_ts_match:
                    formatted_line = formatted_line.replace(start_ts_match.group(1), f'<span class="log-timestamp">{start_ts_match.group(1)}</span>')

            level_match = re.search(r"\[(INFO|WARN|ERROR|FATAL|SEVERE|DEBUG|TRACE)\]", formatted_line)
            if level_match:
                formatted_line = formatted_line.replace(level_match.group(0), f'<span class="log-level">{level_match.group(0)}</span>')

            formatted_line = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", '<span class="hljs-number">\\g<0></span>', formatted_line)
            formatted_line = re.sub(r"\[ct: \d+\]", '<span class="hljs-number">\\g<0></span>', formatted_line)
            formatted_line = re.sub(
                r"\d+\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com\/[a-z0-9.\/-]+:[a-z0-9.\/-]+",
                '<span class="hljs-string">\\g<0></span>',
                formatted_line,
            )
            formatted_line = re.sub(
                r"\b(pod|deployment|service|node|configmap|secret|namespace|replicaset|daemonset)s?\b",
                '<span class="hljs-title">\\g<0></span>',
                formatted_line,
                flags=re.IGNORECASE,
            )
            formatted_line = re.sub(
                r"Process [a-z]+ done \(return code \d+\)|Running [a-z]+ \([^)]+\)",
                '<span class="hljs-comment">\\g<0></span>',
                formatted_line,
            )

            output += f'<div class="{class_name}">{formatted_line}</div>'
        output += '</div>'
    return output

