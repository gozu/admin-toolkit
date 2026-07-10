"""Plugin macro: audit msgType event mix for the Adoption page.

Read-only. Runs as the `dataiku` service account (impersonate=false) so it can
read <DIP_HOME>/run/audit/audit.log* regardless of webapp impersonation.

Guarded-regex mining ported from the diag-parser plugin's audit hot path
(auditWorkloadCore.ts / auditEventBuckets.ts there): every regex is exec'd only
after a cheap substring guard, and the hot path never json-parses a line
(audit files run ~100MB per rotation). Per human actor (authUser, falling back
to mdc.user; api:*/no:auth identities are automation and never bucketed):

- msgType classified into build / run / explore / consume / other. Rules were
  derived from a census of a real ~3M-line audit fixture — do not re-derive.
- authSource counts (USER_FROM_UI vs API-key variants) for the UI-vs-API split.
- Global top-100 human msgType counts for the "what's hot" list.

Streams line-by-line; never loads a whole file. Handles a .gz suffix via gzip.
"""
import calendar
import glob
import gzip
import io
import json
import os
import re
import time

from dataiku.runnables import Runnable

_TOP_MSG_TYPES = 100

_MSG_TYPE_RE = re.compile(r'"msgType":"([^"]+)"')
_AUTH_SOURCE_RE = re.compile(r'"authSource":"([^"]+)"')
_AUTH_USER_RE = re.compile(r'"authUser":"([^"]+)"')
# mdc is a flat object (no nested braces observed in practice), so a
# block-then-inner-search is enough to pull its "user" fallback.
_MDC_BLOCK_RE = re.compile(r'"mdc":\{([^}]*)\}')
_MDC_USER_RE = re.compile(r'"user":"([^"]+)"')
_TIMESTAMP_RE = re.compile(
    r'"timestamp":"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{3}))?'
)

# msgType -> workload bucket. First matching rule wins; keep in sync with
# resource/frontend/src/utils/auditEventBuckets.ts (the UI re-classifies the
# top-N list with the same rules to color it).
_BUCKET_RULES = [
    # Machine telemetry and admin/API plumbing first — never a workload signal.
    (re.compile(r'^compute-resource-usage-'), 'other'),
    (re.compile(r'^security-|^admin-|^publicapi-|api-key|^login|^pnotifications'
                r'|^dss-internal-|^internal-|^unified-monitoring-'), 'other'),
    # Consumption: dashboards/insights, business apps, exports & downloads.
    (re.compile(r'^dashboard|^insight|^application-open$|export|download'), 'consume'),
    # Runs: job/scenario execution and its lifecycle events. Deliberately
    # narrow — job-get-status / jobs-list are UI polling and fall to explore.
    (re.compile(r'^flow-job-|^flow-object-|^job-start$|^job-abort$|^job-retry$'
                r'|^scenario-run|^scenario-fire-trigger$|execute|^runnable-run$'
                r'|^future-abort$|^dataset-clear-samples$'), 'run'),
    # Builds: anything that writes config — saves, creates, deletes, commits,
    # renames, uploads, variable/settings writes.
    (re.compile(r'save|create|delete|commit|rename|upload|import|write-session'
                r'|schedule|-edit$|^set-|-set$|set-settings'), 'build'),
    # Explores: reads, gets, lists, searches, samples, status polling.
    (re.compile(r'read|-get$|-get-|^get-|list|search|browse|^samples$|^interests-'
                r'|^discussion|^tags-|counts$|^catalog-|status$'), 'explore'),
]

_EPOCH_DAY_MS = 24 * 60 * 60 * 1000


def _open_lines(path):
    if path.endswith('.gz'):
        return io.TextIOWrapper(gzip.open(path, 'rb'), encoding='utf-8', errors='replace')
    return open(path, 'r', encoding='utf-8', errors='replace')


def _classify(msg_type, cache):
    bucket = cache.get(msg_type)
    if bucket is not None:
        return bucket
    bucket = 'other'
    for regex, b in _BUCKET_RULES:
        if regex.search(msg_type):
            bucket = b
            break
    cache[msg_type] = bucket
    return bucket


def _extract_actor(line):
    """actor = message.authUser ?? mdc.user, trimmed; None if blank/absent."""
    m = _AUTH_USER_RE.search(line)
    if m:
        actor = m.group(1).strip()
        if actor:
            return actor
    m = _MDC_BLOCK_RE.search(line)
    if m:
        inner = _MDC_USER_RE.search(m.group(1))
        if inner:
            actor = inner.group(1).strip()
            if actor:
                return actor
    return None


def _is_automation(actor):
    return actor.startswith('api:') or actor == 'no:auth'


def _ts_ms(line):
    m = _TIMESTAMP_RE.search(line)
    if not m:
        return None
    # Audit timestamps are +0000 (verified live) — treat as UTC epoch.
    return calendar.timegm((
        int(m.group(1)), int(m.group(2)), int(m.group(3)),
        int(m.group(4)), int(m.group(5)), int(m.group(6)), 0, 0, 0,
    )) * 1000 + int(m.group(7) or 0)


def _parse_audit(audit_dir, max_files=0):
    files = sorted(glob.glob(os.path.join(audit_dir, 'audit.log*')))
    if max_files and max_files > 0:
        files = files[:max_files]

    humans = {}          # login -> {events, buckets{}, authSources{}}
    msg_type_counts = {}  # human msgTypes only
    bucket_cache = {}
    automation_ids = set()
    automation_events = 0
    first_ms = None
    last_ms = None
    lines_scanned = 0
    files_read = 0

    for path in files:
        try:
            fh = _open_lines(path)
        except OSError:
            continue
        files_read += 1
        with fh:
            for line in fh:
                lines_scanned += 1
                # Only msgType-bearing lines are audit *events*; the rest is
                # request plumbing (apicall timings etc.).
                if '"msgType"' not in line:
                    continue
                actor = _extract_actor(line)
                if not actor:
                    continue
                if _is_automation(actor):
                    automation_ids.add(actor)
                    automation_events += 1
                    continue

                ms = _ts_ms(line)
                if ms is not None:
                    first_ms = ms if first_ms is None else min(first_ms, ms)
                    last_ms = ms if last_ms is None else max(last_ms, ms)

                human = humans.get(actor)
                if human is None:
                    human = {'events': 0, 'buckets': {}, 'authSources': {}}
                    humans[actor] = human
                human['events'] += 1

                m = _MSG_TYPE_RE.search(line)
                if m:
                    msg_type = m.group(1)
                    msg_type_counts[msg_type] = msg_type_counts.get(msg_type, 0) + 1
                    bucket = _classify(msg_type, bucket_cache)
                    human['buckets'][bucket] = human['buckets'].get(bucket, 0) + 1

                if '"authSource"' in line:
                    m = _AUTH_SOURCE_RE.search(line)
                    if m:
                        src = m.group(1)
                        human['authSources'][src] = human['authSources'].get(src, 0) + 1

    top_msg_types = dict(sorted(msg_type_counts.items(), key=lambda kv: -kv[1])[:_TOP_MSG_TYPES])
    coverage_days = None
    if first_ms is not None and last_ms is not None and last_ms > first_ms:
        coverage_days = round((last_ms - first_ms) / _EPOCH_DAY_MS, 1)

    return {
        'ok': True,
        'generatedAtMs': int(time.time() * 1000),
        'firstEventMs': first_ms,
        'lastEventMs': last_ms,
        'coverageDays': coverage_days,
        'humans': humans,
        'msgTypeCounts': top_msg_types,
        'automationIdentities': len(automation_ids),
        'automationEvents': automation_events,
        'linesScanned': lines_scanned,
        'filesRead': files_read,
    }


def _reverse_lines(path, block_size=1 << 20):
    """Yield a plain-text file's lines newest-first without reading the whole
    file — the recent mode only ever needs the tail. gz files can't seek, so
    callers fall back to a forward read for those (rare inside a short window)."""
    with open(path, 'rb') as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        carry = b''
        while pos > 0:
            size = min(block_size, pos)
            pos -= size
            fh.seek(pos)
            data = fh.read(size) + carry
            lines = data.split(b'\n')
            carry = lines[0]
            for raw in reversed(lines[1:]):
                yield raw.decode('utf-8', errors='replace')
        if carry:
            yield carry.decode('utf-8', errors='replace')


# Audit lines are appended chronologically with only sub-second jitter; a long
# run of older-than-cutoff lines means the window is done, not an outlier.
_RECENT_STOP_AFTER_OLD = 50
_RECENT_MAX_LINES = 3_000_000  # runaway backstop, ~3 rotations worth


def _parse_recent(audit_dir, window_hours=72):
    """Recent-activity pulse: human events from the last `window_hours`, read
    backwards from the newest audit files. Reports the MEASURED window
    (first/last event actually seen) — rotated files often cover far less than
    asked, and the UI must say what it really got."""
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - int(window_hours * 3600 * 1000)

    # Newest files first; a file whose mtime predates the cutoff (and every
    # older one after it) can't contain in-window events.
    files = sorted(glob.glob(os.path.join(audit_dir, 'audit.log*')),
                   key=lambda p: os.path.getmtime(p), reverse=True)

    hours = {}            # epoch-hour ms -> {'events': n, 'humans': set}
    bucket_counts = {}
    run_types = {}        # msgType -> count, run bucket only
    human_totals = {}     # login -> events
    bucket_cache = {}
    first_ms = None
    last_ms = None
    lines_scanned = 0
    files_read = 0
    exhausted_files = True  # flipped off when we stop at the cutoff instead

    def _ingest(line):
        nonlocal first_ms, last_ms
        actor = _extract_actor(line)
        if not actor or _is_automation(actor):
            return
        ms = _ts_ms(line)
        if ms is None or ms < cutoff_ms:
            return
        first_ms = ms if first_ms is None else min(first_ms, ms)
        last_ms = ms if last_ms is None else max(last_ms, ms)
        hour_ms = (ms // 3600000) * 3600000
        hour = hours.get(hour_ms)
        if hour is None:
            hour = {'events': 0, 'humans': set()}
            hours[hour_ms] = hour
        hour['events'] += 1
        hour['humans'].add(actor)
        human_totals[actor] = human_totals.get(actor, 0) + 1
        m = _MSG_TYPE_RE.search(line)
        if m:
            msg_type = m.group(1)
            bucket = _classify(msg_type, bucket_cache)
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
            if bucket == 'run':
                run_types[msg_type] = run_types.get(msg_type, 0) + 1

    for path in files:
        try:
            if os.path.getmtime(path) * 1000 < cutoff_ms:
                exhausted_files = False
                break
        except OSError:
            continue
        files_read += 1
        old_streak = 0
        stopped_at_cutoff = False
        try:
            if path.endswith('.gz'):
                # No reverse seek in gzip — forward-read the whole file; the
                # cutoff filter in _ingest still applies.
                with _open_lines(path) as fh:
                    for line in fh:
                        lines_scanned += 1
                        if '"msgType"' in line:
                            _ingest(line)
            else:
                for line in _reverse_lines(path):
                    lines_scanned += 1
                    if lines_scanned > _RECENT_MAX_LINES:
                        stopped_at_cutoff = True
                        break
                    if '"msgType"' not in line:
                        continue
                    ms = _ts_ms(line)
                    if ms is not None and ms < cutoff_ms:
                        old_streak += 1
                        if old_streak >= _RECENT_STOP_AFTER_OLD:
                            stopped_at_cutoff = True
                            break
                        continue
                    old_streak = 0
                    _ingest(line)
        except OSError:
            continue
        if stopped_at_cutoff:
            exhausted_files = False
            break

    hour_rows = [
        {'hourMs': h, 'events': v['events'], 'humans': len(v['humans'])}
        for h, v in sorted(hours.items())
    ]
    top_humans = [
        {'login': login, 'events': n}
        for login, n in sorted(human_totals.items(), key=lambda kv: -kv[1])[:8]
    ]
    top_run_types = dict(sorted(run_types.items(), key=lambda kv: -kv[1])[:20])
    coverage_hours = None
    if first_ms is not None and last_ms is not None and last_ms > first_ms:
        coverage_hours = round((last_ms - first_ms) / 3600000.0, 1)

    return {
        'ok': True,
        'mode': 'recent',
        'generatedAtMs': now_ms,
        'windowHours': window_hours,
        'firstEventMs': first_ms,
        'lastEventMs': last_ms,
        'coverageHours': coverage_hours,
        'hours': hour_rows,
        'buckets': bucket_counts,
        'runTypes': top_run_types,
        'topHumans': top_humans,
        'humansActive': len(human_totals),
        # True when the rotated files ran out before reaching the requested
        # window — the measured span is all the audit trail still holds.
        'exhaustedFiles': exhausted_files,
        'filesRead': files_read,
        'linesScanned': lines_scanned,
    }


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        dip_home = os.environ.get('DIP_HOME') or os.environ.get('DKU_DIP_HOME')
        if not dip_home:
            return json.dumps({'ok': False, 'error': 'DIP_HOME not set on host'})
        audit_dir = os.path.join(dip_home, 'run', 'audit')
        mode = (self.config.get('mode') or 'full').strip().lower()
        try:
            max_files = int(self.config.get('max_files') or 0)
        except (TypeError, ValueError):
            max_files = 0
        try:
            window_hours = int(self.config.get('window_hours') or 72)
        except (TypeError, ValueError):
            window_hours = 72
        try:
            if mode == 'recent':
                result = _parse_recent(audit_dir, window_hours=window_hours)
            else:
                result = _parse_audit(audit_dir, max_files=max_files)
        except Exception as exc:
            return json.dumps({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:240]}'})
        result['auditDir'] = audit_dir
        return json.dumps(result)
