"""FS Cleanup macro — policy-scoped filesystem deletion (webapp run dirs +
the aged-entry storage tail: joblogs / tmp / exports).

The macro NEVER trusts a passed list: `delete` re-walks the filesystem and
re-applies atk_agent_common.policies.fs_paths per run directory immediately
before removing it. It also fetches the running-webapp exclusion list ITSELF
(dataiku.api_client() on this host) — a caller can not talk it into deleting
a live backend's run dir. Policy refusals return {'ok': False, ...}; they
never raise.
"""
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
            else:
                return json.dumps({'ok': False, 'error': 'unknown-policy',
                                   'message': 'Unknown policy %r (known: webappruns, %s).'
                                              % (policy, ', '.join(sorted(fs_paths.AGED_POLICIES)))})
            return json.dumps({'ok': False, 'error': 'Unknown operation: %s' % operation})
        except fs_paths.FsPolicyError as exc:
            return json.dumps({'ok': False, 'error': 'policy-refused', 'message': str(exc)})
        except Exception as exc:
            return json.dumps({'ok': False, 'error': '%s: %s' % (type(exc).__name__, str(exc))})
