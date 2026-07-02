"""Audit directory → per-UTC-day activity aggregates.

Streams $DIP_HOME/run/audit/audit.log* line by line (gz-aware, same reader as
python-runnables/cru-audit), keeps only human UI actions per
classification.is_ui_user_event, and buckets them into whole UTC days.

The result is pure data (JSON-safe dict) — no DB access here, so the exact
same code runs inside the local webapp and inside the story-audit-aggregate
macro on a remote host.

Error policy: a malformed LINE is counted in parseErrors (log rotation can
truncate mid-line); an unreadable FILE raises, because silently skipping a
whole rotated file is how history quietly degrades.
"""
import glob
import gzip
import io
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from adk_backend.story.classification import VOCAB_VERSION, classify_msg_type, is_ui_user_event

# Bumped whenever the payload contract below changes shape; the hub collector
# rejects remote payloads whose formatVersion differs.
FORMAT_VERSION = 1


def _open_lines(path):
    if path.endswith('.gz'):
        return io.TextIOWrapper(gzip.open(path, 'rb'), encoding='utf-8', errors='replace')
    return open(path, 'r', encoding='utf-8', errors='replace')


def _utc_day(timestamp: str) -> Optional[str]:
    """'YYYY-MM-DD' UTC bucket for an audit ISO timestamp, None if unparseable."""
    ts = str(timestamp or '')
    # Fast path: DSS writes '...T HH:MM:SS.mmm+0000' — already UTC.
    if len(ts) >= 10 and (ts.endswith('+0000') or ts.endswith('Z') or ts.endswith('+00:00')):
        day = ts[:10]
        if len(day) == 10 and day[4] == '-' and day[7] == '-':
            return day
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f%z', '%Y-%m-%dT%H:%M:%S%z'):
        try:
            return datetime.strptime(ts, fmt).astimezone(timezone.utc).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None


def aggregate_audit_dir(
    audit_dir: str,
    since_day: Optional[str] = None,
    lookback_days: int = 14,
    max_files: int = 0,
    today: Optional[str] = None,
) -> Dict[str, Any]:
    """Aggregate audit.log* under audit_dir into per-day activity.

    - since_day ('YYYY-MM-DD'): days <= since_day are EXCLUDED — they are
      already final in Postgres (cursor rule) and must never be rewritten.
    - lookback_days: days older than today - lookback_days are excluded, so a
      first run on a host with months of rotated logs stays bounded.
    - today: injectable for tests; defaults to the current UTC date.
    """
    files = sorted(glob.glob(os.path.join(audit_dir, 'audit.log*')))
    if max_files and max_files > 0:
        files = files[:max_files]

    today_day = today or datetime.now(timezone.utc).strftime('%Y-%m-%d')
    min_day = (
        datetime.strptime(today_day, '%Y-%m-%d') - timedelta(days=int(lookback_days))
    ).strftime('%Y-%m-%d')

    # day -> (login, project_key) -> [viewing, developing]
    activity: Dict[str, Dict[Any, list]] = {}
    # day -> (project_key, msg_type) -> count
    counts: Dict[str, Dict[Any, int]] = {}

    files_read = 0
    lines_scanned = 0
    parse_errors = 0
    first_ts = None
    last_ts = None

    for path in files:
        fh = _open_lines(path)  # unreadable file → raises (never skip silently)
        files_read += 1
        with fh:
            for line in fh:
                lines_scanned += 1
                if '"generic"' not in line and "'generic'" not in line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    parse_errors += 1
                    continue
                keep, login, project_key, msg_type = is_ui_user_event(obj)
                if not keep:
                    continue
                ts = obj.get('timestamp')
                day = _utc_day(ts)
                if day is None:
                    parse_errors += 1
                    continue
                if day < min_day or day > today_day:
                    continue
                if since_day and day <= since_day:
                    continue
                if first_ts is None or ts < first_ts:
                    first_ts = ts
                if last_ts is None or ts > last_ts:
                    last_ts = ts
                slot = activity.setdefault(day, {}).setdefault((login, project_key), [0, 0])
                slot[0] += 1
                if classify_msg_type(msg_type) == 'developing':
                    slot[1] += 1
                key = (project_key, msg_type)
                day_counts = counts.setdefault(day, {})
                day_counts[key] = day_counts.get(key, 0) + 1

    days: Dict[str, Any] = {}
    for day in sorted(set(activity) | set(counts)):
        days[day] = {
            'userActivity': [
                {
                    'login': login,
                    'projectKey': project_key,
                    'viewingActions': viewing,
                    'developingActions': developing,
                }
                for (login, project_key), (viewing, developing) in sorted(activity.get(day, {}).items())
            ],
            'eventCounts': [
                {'projectKey': project_key, 'msgType': msg_type, 'count': count}
                for (project_key, msg_type), count in sorted(counts.get(day, {}).items())
            ],
        }

    return {
        'ok': True,
        'formatVersion': FORMAT_VERSION,
        'vocabVersion': VOCAB_VERSION,
        'days': days,
        'filesRead': files_read,
        'linesScanned': lines_scanned,
        'parseErrors': parse_errors,
        'firstTs': first_ts,
        'lastTs': last_ts,
    }
