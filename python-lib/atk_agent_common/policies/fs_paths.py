"""Filesystem-cleanup deletion policy (fs-cleanup macro).

Doctrine (same layered model as log_files.py): the macro re-walks the
filesystem and re-applies this policy per directory immediately before
deleting — it never trusts a passed list. Every policy names its allowed
roots relative to DIP_HOME; realpath containment, symlink refusal, age gates
and keep-newest-N are enforced here, below the model AND below the backend.

Policies:
  webappruns — dead webapp run directories. Observed layout (akaos, DSS 14):
      <DIP_HOME>/webappruns/<PROJECT_KEY>/<webappId>/run_<Y-m-d-H-M-S-ms>/
      plus an `initial/` dir and `instance-info.json` per webapp — those are
      never deletable (only run_* directories match). `tmp/webappruns` is
      kept as a legacy fallback root for older layouts.
  tmp / exports / joblogs — reserved for the storage-tail phase; scanning an
      unimplemented policy raises so nothing silently no-ops.
"""

import os
import re
import stat as stat_mod
import time

SAMPLE_LIMIT = 5

# Policy name → roots relative to DIP_HOME. Deletion never leaves these.
POLICY_ROOTS = {
    'webappruns': ('webappruns', 'tmp/webappruns'),
}

# A deletable webapp-run directory basename: run_2026-04-09-15-19-29-765.
# `initial` and `instance-info.json` can never match by construction.
RUN_DIR_RE = re.compile(r'^run_\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}-\d+$')


class FsPolicyError(ValueError):
    pass


def resolve_roots(dip_home, policy):
    """[(rel, realpath)] of the existing roots for `policy`.

    Raises FsPolicyError on an unknown policy — an unimplemented policy must
    refuse loudly, never scan an empty set and report "nothing to delete".
    """
    roots = POLICY_ROOTS.get(policy)
    if not roots:
        raise FsPolicyError('unknown fs-cleanup policy %r (known: %s)'
                            % (policy, ', '.join(sorted(POLICY_ROOTS))))
    out = []
    for rel in roots:
        abspath = os.path.realpath(os.path.join(dip_home, rel))
        if os.path.isdir(abspath):
            out.append((rel, abspath))
    return out


def _contained_under(real_path, root_real):
    return real_path == root_real or real_path.startswith(root_real + os.sep)


def is_deletable_run_dir(path, dip_home, min_age_days=7, now=None, newest_mtime=None):
    """Authoritative per-directory check for the webappruns policy.

    Enforces, in order: run_* basename pattern, symlink refusal, directory,
    realpath containment under a webappruns root, and minimum age (the run
    dir's own mtime). Keep-newest-N and running-webapp exclusion are ordering
    decisions made by the caller — this function is the floor below them.
    """
    basename = os.path.basename(path.rstrip(os.sep))
    if not RUN_DIR_RE.match(basename):
        return False, 'not-a-run-dir'
    try:
        st = os.lstat(path)
    except OSError as exc:
        return False, 'stat-failed: %s' % exc
    if stat_mod.S_ISLNK(st.st_mode):
        return False, 'symlink'
    if not stat_mod.S_ISDIR(st.st_mode):
        return False, 'not-a-directory'
    real = os.path.realpath(path)
    roots = resolve_roots(dip_home, 'webappruns')
    if not any(_contained_under(real, root_real) for _, root_real in roots):
        return False, 'outside-allowed-roots'
    now = now if now is not None else time.time()
    mtime = newest_mtime if newest_mtime is not None else st.st_mtime
    age_days = (now - mtime) / 86400.0
    if age_days < float(min_age_days):
        return False, 'too-young (%.1fd < %sd)' % (age_days, min_age_days)
    return True, 'ok'


def _dir_size(path):
    """Recursive byte size of a directory (symlinked subtrees not followed)."""
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames
                       if not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                continue
    return total


def _newest_mtime(path):
    """The newest mtime anywhere inside a run dir — a run whose files are
    still being written counts as young even if the dir inode is old."""
    newest = 0.0
    try:
        newest = os.lstat(path).st_mtime
    except OSError:
        return newest
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames
                       if not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            try:
                newest = max(newest, os.lstat(os.path.join(dirpath, name)).st_mtime)
            except OSError:
                continue
    return newest


def scan_webappruns(dip_home, project_key=None, min_age_days=7, keep_last_runs=2,
                    running_exclusions=None, now=None):
    """Enumerate deletable webapp run dirs.

    Layout walked: <root>/<PROJECT>/<webappId>/run_*. Per webapp the newest
    `keep_last_runs` run dirs are kept unconditionally (run_* names sort
    chronologically); the rest are candidates iff they pass
    is_deletable_run_dir. `running_exclusions` is a set of '<PROJECT>/<webappId>'
    keys whose newest run dir is skipped regardless (the live backend's dir).

    Returns {'webapps': {'<PROJECT>/<webappId>': {runDirs, deletableRuns,
    bytes, sample[<=5], skipped}}, 'totalDirs', 'totalBytes', 'projectKeys'}.
    """
    now = now if now is not None else time.time()
    running_exclusions = set(running_exclusions or ())
    keep_last_runs = max(0, int(keep_last_runs))
    webapps = {}
    total_dirs = 0
    total_bytes = 0
    projects_seen = set()
    for _rel, root in resolve_roots(dip_home, 'webappruns'):
        for project in sorted(os.listdir(root)):
            if project_key and project != project_key:
                continue
            project_dir = os.path.join(root, project)
            if os.path.islink(project_dir) or not os.path.isdir(project_dir):
                continue
            projects_seen.add(project)
            for webapp_id in sorted(os.listdir(project_dir)):
                webapp_dir = os.path.join(project_dir, webapp_id)
                if os.path.islink(webapp_dir) or not os.path.isdir(webapp_dir):
                    continue
                run_dirs = sorted(
                    (d for d in os.listdir(webapp_dir) if RUN_DIR_RE.match(d)),
                    reverse=True)  # newest first — the timestamp format sorts
                if not run_dirs:
                    continue
                key = '%s/%s' % (project, webapp_id)
                entry = {'runDirs': len(run_dirs), 'deletableRuns': 0, 'bytes': 0,
                         'sample': [], 'skipped': 0}
                kept = keep_last_runs
                if key in running_exclusions:
                    kept = max(kept, 1)  # the live backend's dir is always kept
                for run_name in run_dirs[kept:]:
                    full = os.path.join(webapp_dir, run_name)
                    ok, _reason = is_deletable_run_dir(
                        full, dip_home, min_age_days, now=now,
                        newest_mtime=_newest_mtime(full))
                    if not ok:
                        entry['skipped'] += 1
                        continue
                    size = _dir_size(full)
                    entry['deletableRuns'] += 1
                    entry['bytes'] += size
                    total_dirs += 1
                    total_bytes += size
                    if len(entry['sample']) < SAMPLE_LIMIT:
                        entry['sample'].append(full)
                if entry['deletableRuns'] or entry['skipped']:
                    webapps[key] = entry
    return {'webapps': webapps, 'totalDirs': total_dirs, 'totalBytes': total_bytes,
            'projectKeys': sorted(projects_seen)}
