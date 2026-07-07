"""FS Cleanup macro — policy-scoped filesystem deletion (webapp run dirs).

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
        try:
            min_age_days = int(self.config.get('min_age_days') or 7)
            keep_last_runs = int(self.config.get('keep_last_runs') or 2)
            max_delete_gb = int(self.config.get('max_delete_gb') or 50)
        except (TypeError, ValueError):
            return json.dumps({'ok': False, 'error': 'min_age_days, keep_last_runs and '
                                                     'max_delete_gb must be integers'})
        dry_run = _bool(self.config.get('dry_run'), default=True)
        dip_home = _dip_home()
        if not dip_home:
            return json.dumps({'ok': False, 'error': 'DIP_HOME not set'})
        try:
            if policy != 'webappruns':
                return json.dumps({'ok': False, 'error': 'unknown-policy',
                                   'message': 'Only the webappruns policy is implemented.'})
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
            return json.dumps({'ok': False, 'error': 'Unknown operation: %s' % operation})
        except fs_paths.FsPolicyError as exc:
            return json.dumps({'ok': False, 'error': 'policy-refused', 'message': str(exc)})
        except Exception as exc:
            return json.dumps({'ok': False, 'error': '%s: %s' % (type(exc).__name__, str(exc))})
