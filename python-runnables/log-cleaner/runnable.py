"""Log Cleaner macro — rotated-log deletion with in-macro policy enforcement.

The macro NEVER trusts a passed file list: `delete` re-walks the filesystem
and re-applies atk_agent_common.policies.log_files.is_deletable to every file
immediately before unlinking it. Policy refusals return {'ok': False,
'refused': [...]} — they never raise.
"""
import json
import os
import time

from dataiku.runnables import Runnable

from atk_agent_common.policies import log_files


def _bool(value, default=False):
    if value is None or value == '':
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('true', '1', 'yes', 'y')


def _dip_home():
    return os.environ.get('DIP_HOME') or os.environ.get('DKU_DIP_HOME') or ''


def _delete(dip_home, roots, min_age_days, max_delete_gb, dry_run):
    now = time.time()
    allowed, refused = log_files.resolve_roots(dip_home, roots)

    # Pass 1: enumerate candidates and enforce the size cap BEFORE touching
    # anything. Candidates are re-checked per file in pass 2 (TOCTOU-safe
    # enough for logs; the policy check runs at unlink time regardless).
    candidates = []
    total_bytes = 0
    for rel, abspath in allowed:
        if not os.path.isdir(abspath):
            continue
        for dirpath, dirnames, filenames in os.walk(abspath):
            dirnames[:] = [d for d in dirnames
                           if not os.path.islink(os.path.join(dirpath, d))]
            for name in filenames:
                full = os.path.join(dirpath, name)
                try:
                    st = os.lstat(full)
                except OSError:
                    continue
                ok, _ = log_files.is_deletable(full, dip_home, min_age_days, now=now, st=st)
                if ok:
                    candidates.append((rel, full, st.st_size))
                    total_bytes += st.st_size

    cap_bytes = float(max_delete_gb) * (1024 ** 3)
    if total_bytes > cap_bytes:
        return {
            'ok': False,
            'error': 'cap-exceeded',
            'message': ('Delete candidates total %.2f GB which exceeds the %s GB cap — '
                        'nothing was deleted. Raise max_delete_gb or narrow the roots.'
                        % (total_bytes / (1024 ** 3), max_delete_gb)),
            'candidateFiles': len(candidates),
            'candidateBytes': total_bytes,
            'refusedRoots': refused,
        }

    per_root = {}
    skipped = []
    reclaimed = 0
    for rel, full, size in candidates:
        entry = per_root.setdefault(rel, {'deletedFiles': 0, 'reclaimedBytes': 0,
                                          'skipped': 0, 'sample': []})
        # Authoritative re-check at unlink time — the file may have changed
        # (or been swapped for a symlink) since enumeration.
        ok, reason = log_files.is_deletable(full, dip_home, min_age_days, now=now)
        if not ok:
            entry['skipped'] += 1
            if len(skipped) < 20:
                skipped.append({'path': full, 'reason': reason})
            continue
        if not dry_run:
            try:
                os.unlink(full)
            except OSError as exc:
                entry['skipped'] += 1
                if len(skipped) < 20:
                    skipped.append({'path': full, 'reason': 'unlink-failed: %s' % exc})
                continue
        entry['deletedFiles'] += 1
        entry['reclaimedBytes'] += size
        reclaimed += size
        if len(entry['sample']) < log_files.SAMPLE_LIMIT:
            entry['sample'].append(full)

    return {
        'ok': True,
        'dryRun': bool(dry_run),
        'roots': per_root,
        'refusedRoots': refused,
        'skippedDetail': skipped,
        'totalDeletedFiles': sum(e['deletedFiles'] for e in per_root.values()),
        'totalReclaimedBytes': reclaimed,
        'totalReclaimedGB': round(reclaimed / (1024 ** 3), 3),
    }


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        operation = str(self.config.get('operation') or '').strip().lower()
        roots = [r.strip() for r in str(self.config.get('roots') or '').split(',') if r.strip()]
        try:
            min_age_days = int(self.config.get('min_age_days') or 3)
            max_delete_gb = int(self.config.get('max_delete_gb') or 20)
        except (TypeError, ValueError):
            return json.dumps({'ok': False, 'error': 'min_age_days and max_delete_gb must be integers'})
        dry_run = _bool(self.config.get('dry_run'), default=True)
        dip_home = _dip_home()
        if not dip_home:
            return json.dumps({'ok': False, 'error': 'DIP_HOME not set'})
        try:
            if operation == 'scan':
                result = log_files.scan(dip_home, roots=roots, min_age_days=min_age_days)
                result['ok'] = True
                result['minAgeDays'] = min_age_days
                result['totalGB'] = round(result['totalBytes'] / (1024 ** 3), 3)
                return json.dumps(result)
            if operation == 'delete':
                return json.dumps(_delete(dip_home, roots, min_age_days, max_delete_gb, dry_run))
            return json.dumps({'ok': False, 'error': 'Unknown operation: %s' % operation})
        except Exception as exc:
            return json.dumps({'ok': False, 'error': '%s: %s' % (type(exc).__name__, str(exc))})
