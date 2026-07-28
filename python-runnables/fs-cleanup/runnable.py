"""FS Cleanup macro — policy-scoped filesystem deletion (webapp run dirs, the
aged-entry storage tail joblogs / tmp / exports, and orphan-project locations).

The macro NEVER trusts a passed list: `delete` re-walks the filesystem and
re-applies atk_agent_common.policies.fs_paths per run directory immediately
before removing it. It also fetches the running-webapp exclusion list ITSELF
(dataiku.api_client() on this host) — a caller can not talk it into deleting
a live backend's run dir. The orphans policy likewise fetches the LIVE project
list itself, and fails closed when it can't. Policy refusals return
{'ok': False, ...}; they never raise.

On UIF instances the orphans delete is expected to be PARTIAL: this macro runs
with impersonate=false (as `dataiku`) while some orphan subtrees are owned by
dssuser_* with no ACL for us. Those paths are reported by path and errno
rather than swallowed — the result never claims a clean delete it did not do.
"""
import getpass
import json
import os
import shutil
import time

from dataiku.runnables import Runnable

from atk_agent_common.policies import fs_paths


def _bool(value, default=False):
    if value is None or value == '':
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('true', '1', 'yes', 'y')


def _dip_home():
    return os.environ.get('DIP_HOME') or os.environ.get('DKU_DIP_HOME') or ''


def _running_webapp_keys(project_key=None):
    """'<PROJECT>/<webappId>' of every webapp whose backend is currently
    running. Best-effort: an API failure yields an empty set plus a warning —
    keep-newest-N still protects the active run dir."""
    import dataiku
    keys = set()
    warnings = []
    try:
        client = dataiku.api_client()
        project_keys = [project_key] if project_key else \
            [p['projectKey'] for p in client.list_projects()]
        for pk in project_keys:
            try:
                for webapp in client.get_project(pk).list_webapps(as_type='listitems'):
                    raw = getattr(webapp, '_data', webapp) or {}
                    webapp_id = raw.get('id') or ''
                    if not webapp_id:
                        continue
                    try:
                        state = client.get_project(pk).get_webapp(webapp_id).get_state()
                        running = bool(state.running)
                    except Exception:
                        running = True  # unknown state ⇒ treat as running (safe side)
                    if running:
                        keys.add('%s/%s' % (pk, webapp_id))
            except Exception as exc:
                warnings.append('webapp listing failed for %s: %s' % (pk, str(exc)[:120]))
    except Exception as exc:
        warnings.append('running-webapp exclusion unavailable: %s' % str(exc)[:200])
    return keys, warnings


def _delete_webappruns(dip_home, project_key, min_age_days, keep_last_runs,
                       max_delete_gb, dry_run):
    now = time.time()
    exclusions, warnings = _running_webapp_keys(project_key or None)
    scan = fs_paths.scan_webappruns(dip_home, project_key=project_key or None,
                                    min_age_days=min_age_days,
                                    keep_last_runs=keep_last_runs,
                                    running_exclusions=exclusions, now=now)
    cap_bytes = float(max_delete_gb) * (1024 ** 3)
    if scan['totalBytes'] > cap_bytes:
        return {
            'ok': False,
            'error': 'cap-exceeded',
            'message': ('Delete candidates total %.2f GB which exceeds the %s GB cap — '
                        'nothing was deleted. Raise max_delete_gb or narrow to one project.'
                        % (scan['totalBytes'] / (1024 ** 3), max_delete_gb)),
            'candidateDirs': scan['totalDirs'],
            'candidateBytes': scan['totalBytes'],
            'warnings': warnings or None,
        }

    per_webapp = {}
    skipped = []
    reclaimed = 0
    deleted_dirs = 0
    for key, entry in scan['webapps'].items():
        out = per_webapp.setdefault(key, {'deletedRuns': 0, 'reclaimedBytes': 0,
                                          'skipped': entry.get('skipped', 0), 'sample': []})
        # Delete re-enumerates the candidates itself (the scan's samples are
        # capped) and re-checks the policy per directory at removal time.
        for full in _candidate_dirs(dip_home, key, min_age_days, keep_last_runs,
                                    exclusions, now):
            # Authoritative re-check at delete time — the dir may have changed
            # (new run started, dir swapped for a symlink) since enumeration.
            ok, reason = fs_paths.is_deletable_run_dir(
                full, dip_home, min_age_days, now=now,
                newest_mtime=fs_paths._newest_mtime(full))
            if not ok:
                out['skipped'] += 1
                if len(skipped) < 20:
                    skipped.append({'path': full, 'reason': reason})
                continue
            size = fs_paths._dir_size(full)
            if not dry_run:
                try:
                    shutil.rmtree(full)
                except OSError as exc:
                    out['skipped'] += 1
                    if len(skipped) < 20:
                        skipped.append({'path': full, 'reason': 'rmtree-failed: %s' % exc})
                    continue
            out['deletedRuns'] += 1
            out['reclaimedBytes'] += size
            reclaimed += size
            deleted_dirs += 1
            if len(out['sample']) < fs_paths.SAMPLE_LIMIT:
                out['sample'].append(full)

    return {
        'ok': True,
        'dryRun': bool(dry_run),
        'policy': 'webappruns',
        'webapps': per_webapp,
        'skippedDetail': skipped,
        'runningExcluded': sorted(exclusions),
        'totalDeletedRuns': deleted_dirs,
        'totalReclaimedBytes': reclaimed,
        'totalReclaimedGB': round(reclaimed / (1024 ** 3), 3),
        'warnings': warnings or None,
    }


def _candidate_dirs(dip_home, webapp_key, min_age_days, keep_last_runs, exclusions, now):
    """Fresh candidate run dirs for one '<PROJECT>/<webappId>' key (delete
    re-enumerates rather than trusting the scan's sample cap)."""
    project, webapp_id = webapp_key.split('/', 1)
    kept = max(0, int(keep_last_runs))
    if webapp_key in exclusions:
        kept = max(kept, 1)
    out = []
    for _rel, root in fs_paths.resolve_roots(dip_home, 'webappruns'):
        webapp_dir = os.path.join(root, project, webapp_id)
        if os.path.islink(webapp_dir) or not os.path.isdir(webapp_dir):
            continue
        run_dirs = sorted((d for d in os.listdir(webapp_dir)
                           if fs_paths.RUN_DIR_RE.match(d)), reverse=True)
        out.extend(os.path.join(webapp_dir, d) for d in run_dirs[kept:])
    return out


def _delete_aged(dip_home, policy, group, min_age_days, keep_last,
                 max_delete_gb, dry_run):
    """Aged-entry delete: re-enumerate through aged_candidates, re-apply the
    per-entry policy floor immediately before each removal."""
    now = time.time()
    scan = fs_paths.scan_aged_entries(dip_home, policy, group=group or None,
                                      min_age_days=min_age_days,
                                      keep_last=keep_last, now=now)
    cap_bytes = float(max_delete_gb) * (1024 ** 3)
    if scan['totalBytes'] > cap_bytes:
        return {
            'ok': False,
            'error': 'cap-exceeded',
            'message': ('Delete candidates total %.2f GB which exceeds the %s GB cap — '
                        'nothing was deleted. Raise max_delete_gb or narrow the scope.'
                        % (scan['totalBytes'] / (1024 ** 3), max_delete_gb)),
            'candidateDirs': scan['totalDirs'],
            'candidateBytes': scan['totalBytes'],
        }
    per_group = {}
    skipped = []
    reclaimed = 0
    deleted = 0
    for group_name, full, _mtime in fs_paths.aged_candidates(
            dip_home, policy, group=group or None, keep_last=keep_last):
        out = per_group.setdefault(group_name, {'deleted': 0, 'reclaimedBytes': 0,
                                                'skipped': 0, 'sample': []})
        ok, reason = fs_paths.is_deletable_aged_entry(
            full, dip_home, policy, min_age_days, now=now)
        if not ok:
            out['skipped'] += 1
            if len(skipped) < 20:
                skipped.append({'path': full, 'reason': reason})
            continue
        size = fs_paths._entry_size(full)
        if not dry_run:
            try:
                if os.path.isdir(full) and not os.path.islink(full):
                    shutil.rmtree(full)
                else:
                    os.remove(full)
            except OSError as exc:
                out['skipped'] += 1
                if len(skipped) < 20:
                    skipped.append({'path': full, 'reason': 'remove-failed: %s' % exc})
                continue
        out['deleted'] += 1
        out['reclaimedBytes'] += size
        reclaimed += size
        deleted += 1
        if len(out['sample']) < fs_paths.SAMPLE_LIMIT:
            out['sample'].append(full)
    return {
        'ok': True,
        'dryRun': bool(dry_run),
        'policy': policy,
        'groups': per_group,
        'skippedDetail': skipped,
        'totalDeletedRuns': deleted,
        'totalReclaimedBytes': reclaimed,
        'totalReclaimedGB': round(reclaimed / (1024 ** 3), 3),
    }


def _live_project_keys():
    """The live project keys on this host. Unlike _running_webapp_keys this
    FAILS CLOSED: without the live list an orphan cannot be told apart from a
    live project, so callers must refuse rather than guess. Returns
    (keys, warning) with keys=None on failure."""
    import dataiku
    try:
        client = dataiku.api_client()
        keys = {p['projectKey'] for p in client.list_projects()
                if isinstance(p, dict) and p.get('projectKey')}
    except Exception as exc:
        return None, 'live project list unavailable: %s' % str(exc)[:200]
    return keys, None


def _rmtree_collecting(path, before_bytes):
    """rmtree that gathers per-path failures instead of raising, then classifies
    the outcome by what is actually left on disk. Returns
    (status ∈ {deleted, partial, failed}, errors, reclaimed_bytes)."""
    errors = []

    def _onerror(_func, failed_path, exc_info):
        exc = exc_info[1]
        errors.append({'path': failed_path,
                       'errno': getattr(exc, 'errno', None),
                       'message': str(exc)[:200]})

    try:
        shutil.rmtree(path, onerror=_onerror)
    except Exception as exc:  # onerror handles OSError; this is belt-and-braces
        errors.append({'path': path, 'errno': getattr(exc, 'errno', None),
                       'message': str(exc)[:200]})
    if not os.path.exists(path):
        return 'deleted', errors, before_bytes
    # Still there: partial iff we actually reclaimed something. An orphan whose
    # every byte survived is a plain failure, not a partial success.
    remaining = fs_paths._dir_size(path)
    status = 'partial' if remaining < before_bytes else 'failed'
    return status, errors, max(0, before_bytes - remaining)


def _delete_orphans(dip_home, target_key, max_delete_gb, dry_run):
    """Orphan-project delete: scan, cap, then re-enumerate and re-apply the
    per-directory policy floor immediately before each rmtree."""
    live_keys, warning = _live_project_keys()
    warnings = [warning] if warning else []
    if live_keys is None:
        return {
            'ok': False,
            'error': 'live-projects-unavailable',
            'message': ('The live project list could not be fetched, so an orphan '
                        'cannot be told apart from a live project — nothing was '
                        'deleted.'),
            'runAsUser': getpass.getuser(),
            'warnings': warnings,
        }
    scan = fs_paths.scan_orphans(dip_home, live_keys, project_key=target_key or None)
    cap_bytes = float(max_delete_gb) * (1024 ** 3)
    if scan['totalBytes'] > cap_bytes:
        return {
            'ok': False,
            'error': 'cap-exceeded',
            'message': ('Delete candidates total %.2f GB which exceeds the %s GB cap — '
                        'nothing was deleted. Raise max_delete_gb or narrow to one '
                        'orphan key.'
                        % (scan['totalBytes'] / (1024 ** 3), max_delete_gb)),
            'candidateDirs': scan['totalDirs'],
            'candidateBytes': scan['totalBytes'],
            'runAsUser': getpass.getuser(),
            'warnings': warnings or None,
        }

    per_key = {}
    skipped = []
    failed_paths = []
    reclaimed = 0
    deleted_dirs = 0
    partial = False
    for key, area, full in fs_paths.orphan_candidates(
            dip_home, live_keys, project_key=target_key or None):
        out = per_key.setdefault(key, {'areas': [], 'deletedAreas': 0, 'partialAreas': 0,
                                       'reclaimedBytes': 0, 'blocked': []})
        # Authoritative re-check at delete time — the directory may have changed
        # (project recreated, dir swapped for a symlink) since enumeration.
        ok, reason = fs_paths.is_deletable_orphan_dir(full, dip_home, live_keys)
        if not ok:
            out['blocked'].append({'path': full, 'reason': reason})
            if len(skipped) < 20:
                skipped.append({'path': full, 'reason': reason})
            continue
        size = fs_paths._dir_size(full)
        if dry_run:
            status, errors = 'deleted', []
        else:
            status, errors, size = _rmtree_collecting(full, size)
        out['areas'].append({'area': area, 'path': full, 'bytes': size,
                             'status': status, 'errors': errors[:20]})
        if status != 'deleted':
            partial = True
            for err in errors:
                if len(failed_paths) < 20:
                    failed_paths.append(err)
        if status == 'failed':
            continue  # nothing was reclaimed here — do not credit it
        if status == 'partial':
            out['partialAreas'] += 1
        else:
            out['deletedAreas'] += 1
        out['reclaimedBytes'] += size
        reclaimed += size
        deleted_dirs += 1

    return {
        'ok': True,
        'dryRun': bool(dry_run),
        'policy': 'orphans',
        'runAsUser': getpass.getuser(),
        'orphans': per_key,
        'skippedDetail': skipped,
        'failedPaths': failed_paths,
        'partial': partial,
        'totalDeletedDirs': deleted_dirs,
        'totalReclaimedBytes': reclaimed,
        'totalReclaimedGB': round(reclaimed / (1024 ** 3), 3),
        'warnings': warnings or None,
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
        policy = str(self.config.get('policy') or 'webappruns').strip().lower()
        target_project = str(self.config.get('project_key') or '').strip()
        aged_spec = fs_paths.AGED_POLICIES.get(policy)
        default_age = aged_spec['default_min_age_days'] if aged_spec else 7
        default_keep = aged_spec['default_keep_last'] if aged_spec else 2
        try:
            min_age_days = int(self.config.get('min_age_days') or default_age)
            keep_last_runs = int(self.config.get('keep_last_runs')
                                 if self.config.get('keep_last_runs') not in (None, '')
                                 else default_keep)
            max_delete_gb = int(self.config.get('max_delete_gb') or 50)
        except (TypeError, ValueError):
            return json.dumps({'ok': False, 'error': 'min_age_days, keep_last_runs and '
                                                     'max_delete_gb must be integers'})
        dry_run = _bool(self.config.get('dry_run'), default=True)
        dip_home = _dip_home()
        if not dip_home:
            return json.dumps({'ok': False, 'error': 'DIP_HOME not set'})
        try:
            if policy == 'webappruns':
                if operation == 'scan':
                    exclusions, warnings = _running_webapp_keys(target_project or None)
                    result = fs_paths.scan_webappruns(
                        dip_home, project_key=target_project or None,
                        min_age_days=min_age_days, keep_last_runs=keep_last_runs,
                        running_exclusions=exclusions)
                    result.update({'ok': True, 'policy': policy, 'minAgeDays': min_age_days,
                                   'keepLastRuns': keep_last_runs,
                                   'runningExcluded': sorted(exclusions),
                                   'totalGB': round(result['totalBytes'] / (1024 ** 3), 3),
                                   'warnings': warnings or None})
                    return json.dumps(result)
                if operation == 'delete':
                    return json.dumps(_delete_webappruns(
                        dip_home, target_project, min_age_days, keep_last_runs,
                        max_delete_gb, dry_run))
            elif policy in fs_paths.AGED_POLICIES:
                if operation == 'scan':
                    result = fs_paths.scan_aged_entries(
                        dip_home, policy, group=target_project or None,
                        min_age_days=min_age_days, keep_last=keep_last_runs)
                    result.update({'ok': True, 'policy': policy,
                                   'minAgeDays': min_age_days,
                                   'keepLast': keep_last_runs,
                                   'totalGB': round(result['totalBytes'] / (1024 ** 3), 3)})
                    return json.dumps(result)
                if operation == 'delete':
                    return json.dumps(_delete_aged(
                        dip_home, policy, target_project, min_age_days,
                        keep_last_runs, max_delete_gb, dry_run))
            elif policy == 'orphans':
                # min_age_days / keep_last_runs are meaningless here: an orphan's
                # project is already gone, so its age says nothing about safety.
                if operation == 'scan':
                    live_keys, warning = _live_project_keys()
                    if live_keys is None:
                        return json.dumps({
                            'ok': False, 'error': 'live-projects-unavailable',
                            'message': ('The live project list could not be fetched, so '
                                        'orphans cannot be identified.'),
                            'runAsUser': getpass.getuser(),
                            'warnings': [warning]})
                    result = fs_paths.scan_orphans(dip_home, live_keys,
                                                   project_key=target_project or None)
                    result.update({'ok': True, 'policy': policy,
                                   'runAsUser': getpass.getuser(),
                                   'liveProjectCount': len(live_keys),
                                   'totalGB': round(result['totalBytes'] / (1024 ** 3), 3),
                                   'warnings': None})
                    return json.dumps(result)
                if operation == 'delete':
                    return json.dumps(_delete_orphans(
                        dip_home, target_project, max_delete_gb, dry_run))
            else:
                return json.dumps({'ok': False, 'error': 'unknown-policy',
                                   'message': 'Unknown policy %r (known: webappruns, orphans, %s).'
                                              % (policy, ', '.join(sorted(fs_paths.AGED_POLICIES)))})
            return json.dumps({'ok': False, 'error': 'Unknown operation: %s' % operation})
        except fs_paths.FsPolicyError as exc:
            return json.dumps({'ok': False, 'error': 'policy-refused', 'message': str(exc)})
        except Exception as exc:
            return json.dumps({'ok': False, 'error': '%s: %s' % (type(exc).__name__, str(exc))})
