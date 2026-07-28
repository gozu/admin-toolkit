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
  tmp / exports / joblogs — "aged entries" policies. Deletable unit =
      <root>/<group>/<entry> (depth 2): jobs/<PROJECT>/<jobDir>,
      tmp/<bucket>/<entry>, exports/<kind>/<entry>. Group dirs themselves are
      never deleted (DSS expects its tmp buckets to exist); entries age out by
      NEWEST inner mtime, with keep-newest-N per group. tmp excludes the
      webappruns bucket (legacy layout overlap with the webappruns policy).
  orphans — on-disk artifacts in project-keyed directories whose project no
      longer exists (DSS reports them as `orphanProjects` in the
      directories-footprint payload, but offers no API to reclaim them).
      Deletable unit = <area root>/<KEY>, e.g. jupyter-run/dku-workdirs/PLEASE.
      There is NO age gate: an orphan has no age semantics — the project it
      belonged to is already gone, so "how old is it" says nothing about
      whether it is safe to remove.

Orphan identification mirrors DSS's own rule (DataDirectoriesBucketer's
ProjectList: a directory sitting directly under a project-keyed root, whose
name matches ^[A-Za-z0-9_]+$ and is not a live project key). DSS's rule has
false positives — on akaos `managed_datasets/uploads` is reported as an orphan
project named `uploads` while its children are named after LIVE projects — so
this policy adds two gates DSS has no reason to implement: a reserved-name
list and, primarily, a refusal when the directory's own children name live
projects. Those are hard refusals with no override.
"""

import os
import re
import stat as stat_mod
import time

SAMPLE_LIMIT = 5

# DSS footprint area key → DIP_HOME-relative root, for exactly the areas whose
# deletable unit is <root>/<PROJECT_KEY>.
#
# Deliberately absent, and refused as `unsupported-area` rather than silently
# skipped:
#   config  (config/projects/<KEY>)        — a surviving project definition DSS
#                                            failed to load, not debris
#   git     (config/projects/<KEY>/.git)   — same, one level deeper
#   shakerSamples (caches/shaker-samples/<KEY>.*) — not a plain <root>/<KEY>
#                                            directory (DSS keys it on the text
#                                            before the first dot)
ORPHAN_AREA_ROOTS = {
    'agentTools': 'agent-tools',
    'analysis': 'analysis-data',
    'codeStudioResources': 'lib/code_studio',
    'dkuWorkdirs': 'jupyter-run/dku-workdirs',
    'docportal': 'docportal/projects',
    'jobs': 'jobs',
    'libResources': 'lib/projects',
    'managedDatasets': 'managed_datasets',
    'managedFolders': 'managed_folders',
    'notebookResults': 'notebook_results/jupyter',
    'preparedBundles': 'prepared_bundles',
    'projectStandards': 'project-standards',
    'savedModels': 'saved_models',
    'scenarios': 'scenarios',
    'thumbnails': 'thumbnails',
    'uploadedDatasets': 'uploads',
    'webApps': 'webappruns',
    'wikiAttachments': 'wiki-attachments',
    'workloadFolders': 'workload-folders/webapps',
}

# Policy name → roots relative to DIP_HOME. Deletion never leaves these.
POLICY_ROOTS = {
    'webappruns': ('webappruns', 'tmp/webappruns'),
    'joblogs': ('jobs',),
    'tmp': ('tmp',),
    'exports': ('exports',),
    'orphans': tuple(sorted(set(ORPHAN_AREA_ROOTS.values()))),
}

# DSS's own project-key shape (PROJECT_KEY_PATTERN). Lowercase is allowed on
# purpose: a stricter uppercase-only gate would diverge from DSS's
# classification and silently hide real orphans on instances with lowercase
# keys — while the live-children rule below is what actually keeps us safe.
ORPHAN_KEY_RE = re.compile(r'^[A-Za-z0-9_]+$')

# DSS-internal directory names that sit at the project-key position and are
# never a project. This mirrors DSS's own SomeFolder("tmp_upload_box")
# exclusion and is the SECONDARY defense only — a hardcoded list can never be
# complete, which is why is_deletable_orphan_dir's live-children check is the
# primary one.
RESERVED_ORPHAN_NAMES = frozenset({'uploads', 'tmp_upload_box', 'initial', 'tmp'})

# Areas DSS reports on an orphan item that this policy will never delete. They
# are named here (rather than merely left out of ORPHAN_AREA_ROOTS) so a path
# under one refuses with `unsupported-area (<area>)` — an explanation — instead
# of the generic `outside-allowed-roots`.
ORPHAN_UNSUPPORTED_AREA_ROOTS = {
    'config': 'config/projects',
    'shakerSamples': 'caches/shaker-samples',
}

# Aged-entry policies (storage tail): per-policy defaults + excluded groups.
AGED_POLICIES = {
    'joblogs': {'default_min_age_days': 15, 'default_keep_last': 5,
                'exclude_groups': ()},
    'tmp': {'default_min_age_days': 15, 'default_keep_last': 0,
            'exclude_groups': ('webappruns',)},
    'exports': {'default_min_age_days': 7, 'default_keep_last': 0,
                'exclude_groups': ()},
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


def is_deletable_aged_entry(path, dip_home, policy, min_age_days, now=None,
                            newest_mtime=None):
    """Authoritative per-entry check for the aged-entry policies (joblogs /
    tmp / exports). Enforces: known policy, symlink refusal, realpath
    containment, depth (exactly <root>/<group>/<entry>), excluded groups, and
    minimum age by the newest inner mtime. Keep-newest-N is the caller's
    ordering decision — this is the floor below it."""
    spec = AGED_POLICIES.get(policy)
    if spec is None:
        return False, 'not-an-aged-policy'
    try:
        st = os.lstat(path)
    except OSError as exc:
        return False, 'stat-failed: %s' % exc
    if stat_mod.S_ISLNK(st.st_mode):
        return False, 'symlink'
    real = os.path.realpath(path)
    parent = os.path.dirname(real)
    group = os.path.basename(parent)
    grandparent = os.path.dirname(parent)
    roots = resolve_roots(dip_home, policy)
    if not any(grandparent == root_real for _, root_real in roots):
        return False, 'outside-allowed-depth'
    if not any(_contained_under(real, root_real) for _, root_real in roots):
        return False, 'outside-allowed-roots'
    if group in spec['exclude_groups']:
        return False, 'excluded-group (%s)' % group
    now = now if now is not None else time.time()
    if newest_mtime is None:
        newest_mtime = _newest_mtime(real) if stat_mod.S_ISDIR(st.st_mode) else st.st_mtime
    age_days = (now - newest_mtime) / 86400.0
    if age_days < float(min_age_days):
        return False, 'too-young (%.1fd < %sd)' % (age_days, min_age_days)
    return True, 'ok'


def _entry_size(path):
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    if stat_mod.S_ISDIR(st.st_mode):
        return _dir_size(path)
    return st.st_size


def aged_candidates(dip_home, policy, group=None, keep_last=None):
    """[(group, path, mtime)] of keep-newest-filtered entries for one aged
    policy (delete re-enumerates through this too — no trusted lists). The
    per-entry policy floor is NOT applied here; callers run
    is_deletable_aged_entry on every path."""
    spec = AGED_POLICIES.get(policy)
    if spec is None:
        raise FsPolicyError('policy %r is not an aged-entry policy' % policy)
    keep = spec['default_keep_last'] if keep_last is None else max(0, int(keep_last))
    out = []
    for _rel, root in resolve_roots(dip_home, policy):
        for group_name in sorted(os.listdir(root)):
            if group and group_name != group:
                continue
            if group_name in spec['exclude_groups']:
                continue
            group_dir = os.path.join(root, group_name)
            if os.path.islink(group_dir) or not os.path.isdir(group_dir):
                continue
            entries = []
            for name in os.listdir(group_dir):
                full = os.path.join(group_dir, name)
                try:
                    mtime = os.lstat(full).st_mtime
                except OSError:
                    continue
                entries.append((full, mtime))
            entries.sort(key=lambda e: e[1], reverse=True)  # newest first
            out.extend((group_name, full, mtime) for full, mtime in entries[keep:])
    return out


def scan_aged_entries(dip_home, policy, group=None, min_age_days=None,
                      keep_last=None, now=None):
    """Enumerate deletable aged entries. Returns {'groups': {group: {entries,
    deletable, bytes, sample[<=5], skipped}}, 'totalDirs', 'totalBytes',
    'projectKeys' (= group names, so the webappruns consumers keep working)}."""
    spec = AGED_POLICIES.get(policy)
    if spec is None:
        raise FsPolicyError('policy %r is not an aged-entry policy' % policy)
    now = now if now is not None else time.time()
    age = spec['default_min_age_days'] if min_age_days is None else min_age_days
    groups = {}
    total_dirs = 0
    total_bytes = 0
    for group_name, full, _mtime in aged_candidates(dip_home, policy, group=group,
                                                    keep_last=keep_last):
        entry = groups.setdefault(group_name, {'entries': 0, 'deletable': 0,
                                               'bytes': 0, 'sample': [], 'skipped': 0})
        entry['entries'] += 1
        ok, _reason = is_deletable_aged_entry(full, dip_home, policy, age, now=now)
        if not ok:
            entry['skipped'] += 1
            continue
        size = _entry_size(full)
        entry['deletable'] += 1
        entry['bytes'] += size
        total_dirs += 1
        total_bytes += size
        if len(entry['sample']) < SAMPLE_LIMIT:
            entry['sample'].append(full)
    groups = {k: v for k, v in groups.items() if v['deletable'] or v['skipped']}
    return {'groups': groups, 'totalDirs': total_dirs, 'totalBytes': total_bytes,
            'projectKeys': sorted(groups)}


# ── orphan projects ─────────────────────────────────────────────────────────


def _unsupported_orphan_area(parent_real, dip_home):
    """Area key when `parent_real` is a root we deliberately refuse to delete
    from (config/projects, caches/shaker-samples), else None."""
    for area, rel in sorted(ORPHAN_UNSUPPORTED_AREA_ROOTS.items()):
        if os.path.realpath(os.path.join(dip_home, rel)) == parent_real:
            return area
    return None


def _live_child_hits(path, live_project_keys):
    """Immediate children of `path` that name a live project — matched on the
    plain name AND on the text before the first dot (DSS's <KEY>.<dataset>
    layout). Returns (hits, error) so an unreadable directory refuses rather
    than passing the check by accident."""
    try:
        children = os.listdir(path)
    except OSError as exc:
        return None, 'listdir-failed: %s' % exc
    hits = set()
    for name in children:
        if name in live_project_keys:
            hits.add(name)
            continue
        head = name.split('.', 1)[0]
        if head and head in live_project_keys:
            hits.add(head)
    return hits, None


def is_deletable_orphan_dir(path, dip_home, live_project_keys, now=None):
    """Authoritative per-directory check for the orphans policy.

    Enforces, in order: symlink refusal, directory, exact depth (the parent
    must BE one of the orphan area roots, so only <root>/<KEY> qualifies —
    never a root itself and never anything deeper), DSS's project-key shape,
    not-a-live-project, the reserved-name list, and finally the primary
    defense: refuse when the directory's own immediate children name live
    projects (that is the `managed_datasets/uploads` case, and every future
    shared bucket DSS forgets to exclude, regardless of its name).

    An empty `live_project_keys` fails CLOSED — without the live list every
    directory looks orphaned. `now` is accepted for signature parity with the
    other policies and is unused: orphans have no age semantics.

    Never raises; returns (ok, reason).
    """
    del now  # orphans have no age gate — see the module docstring
    try:
        st = os.lstat(path)
    except OSError as exc:
        return False, 'stat-failed: %s' % exc
    if stat_mod.S_ISLNK(st.st_mode):
        return False, 'symlink'
    if not stat_mod.S_ISDIR(st.st_mode):
        return False, 'not-a-directory'
    real = os.path.realpath(path)
    parent = os.path.dirname(real)
    roots = resolve_roots(dip_home, 'orphans')
    if not any(parent == root_real for _, root_real in roots):
        unsupported = _unsupported_orphan_area(parent, dip_home)
        if unsupported:
            return False, 'unsupported-area (%s)' % unsupported
        if any(_contained_under(real, root_real) for _, root_real in roots):
            return False, 'outside-allowed-depth'
        return False, 'outside-allowed-roots'
    basename = os.path.basename(real)
    if not ORPHAN_KEY_RE.match(basename):
        return False, 'not-a-project-key-shape'
    live_project_keys = set(live_project_keys or ())
    if basename in live_project_keys:
        return False, 'live-project'
    if basename in RESERVED_ORPHAN_NAMES:
        return False, 'reserved-name'
    hits, err = _live_child_hits(real, live_project_keys)
    if err:
        return False, err
    if hits:
        return False, 'contains-live-projects (%s)' % ', '.join(sorted(hits)[:5])
    if not live_project_keys:
        return False, 'live-project-list-unavailable'
    return True, 'ok'


def orphan_candidates(dip_home, live_project_keys, project_key=None):
    """[(key, area, path)] of the directories DSS would classify as orphan
    projects — directory, project-key shape, not a live project key. Sizes and
    the safety gates are NOT applied here; every caller (scan AND delete) runs
    is_deletable_orphan_dir on each path itself."""
    live_project_keys = set(live_project_keys or ())
    out = []
    for area, rel in sorted(ORPHAN_AREA_ROOTS.items()):
        root = os.path.realpath(os.path.join(dip_home, rel))
        if not os.path.isdir(root):
            continue
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for name in names:
            if project_key and name != project_key:
                continue
            if not ORPHAN_KEY_RE.match(name) or name in live_project_keys:
                continue
            full = os.path.join(root, name)
            if os.path.islink(full) or not os.path.isdir(full):
                continue
            out.append((name, area, full))
    return out


def scan_orphans(dip_home, live_project_keys, project_key=None):
    """Enumerate orphan-project locations on the FILESYSTEM (not from the
    footprint payload — the payload is a report, this is the thing we delete).

    Returns {'orphans': {KEY: {'areas': [{area, path, bytes, deletable,
    reason}], 'bytes', 'deletableAreas', 'blockedAreas'}}, 'totalDirs',
    'totalBytes', 'projectKeys'}. `totalBytes`/`totalDirs` count only the
    DELETABLE locations — they are what the caller's delete cap measures.
    Blocked locations are still reported, with their refusal reason, so the UI
    can explain the refusal instead of hiding it.
    """
    orphans = {}
    total_dirs = 0
    total_bytes = 0
    for key, area, full in orphan_candidates(dip_home, live_project_keys,
                                             project_key=project_key):
        ok, reason = is_deletable_orphan_dir(full, dip_home, live_project_keys)
        size = _dir_size(full)
        entry = orphans.setdefault(key, {'areas': [], 'bytes': 0,
                                         'deletableAreas': 0, 'blockedAreas': 0})
        entry['areas'].append({'area': area, 'path': full, 'bytes': size,
                               'deletable': ok, 'reason': reason})
        entry['bytes'] += size
        if ok:
            entry['deletableAreas'] += 1
            total_dirs += 1
            total_bytes += size
        else:
            entry['blockedAreas'] += 1
    return {'orphans': orphans, 'totalDirs': total_dirs, 'totalBytes': total_bytes,
            'projectKeys': sorted(orphans)}
