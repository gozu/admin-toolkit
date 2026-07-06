"""Rotated-log deletion policy.

Doctrine: a live ``*.log`` can NEVER be deleted — only rotated/compressed
artifacts (``*.log.<int>``, ``*.log[.<int>].gz/.zip/...``, dated rotations,
``*.out.<int>``) that are older than a minimum age and physically contained
in a whitelisted directory under DIP_HOME. The macro re-walks the filesystem
and re-applies :func:`is_deletable` per file — it never trusts a passed list.
"""

import os
import re
import stat as stat_mod
import time

# Roots (relative to DIP_HOME) the log cleaner may touch. Anything else —
# including config/, databases/, uploads/ — is refused by containment.
ALLOWED_ROOTS = (
    'run',
    'jobs',
    'scenarios',
    'code-envs/logs',
    'analysis-data/logs',
    'exports/logs',
    'tmp/webappruns',
)

# Basename patterns for rotated/compressed log artifacts. A bare `.log` (live
# file) matches none of these by construction.
ROTATED_PATTERNS = (
    re.compile(r'\.log\.\d+$'),                                    # backend.log.3
    re.compile(r'\.log(\.\d+)?\.(gz|zip|bz2|xz)$'),                # backend.log.gz / backend.log.3.gz
    re.compile(r'\.log[.-]\d{4}-\d{2}-\d{2}(\.(gz|zip|bz2|xz))?$'),  # access.log-2026-05-01(.gz)
    re.compile(r'\.out\.\d+$'),                                    # kernel.out.1
    re.compile(r'\.out(\.\d+)?\.(gz|zip|bz2|xz)$'),                # kernel.out.1.gz
)

SAMPLE_LIMIT = 5


def is_rotated_name(basename):
    """True when `basename` looks like a rotated/compressed log artifact."""
    return any(p.search(basename or '') for p in ROTATED_PATTERNS)


def resolve_roots(dip_home, roots=None):
    """Resolve requested roots against the whitelist.

    `roots` is a list of relative root names (or None/empty = all allowed).
    Returns (allowed_abs: [(rel, abspath)], refused: [{root, reason}]).
    """
    requested = [r.strip().strip('/') for r in (roots or []) if r and r.strip()]
    if not requested:
        requested = list(ALLOWED_ROOTS)
    allowed, refused = [], []
    for rel in requested:
        if rel not in ALLOWED_ROOTS:
            refused.append({'root': rel, 'reason': 'not-in-whitelist'})
            continue
        abspath = os.path.realpath(os.path.join(dip_home, rel))
        allowed.append((rel, abspath))
    return allowed, refused


def _contained_under(real_path, root_real):
    return real_path == root_real or real_path.startswith(root_real + os.sep)


def is_deletable(path, dip_home, min_age_days=3, now=None, st=None):
    """Authoritative per-file check. Returns (ok: bool, reason: str).

    Enforces, in order: rotated-pattern basename, symlink refusal, regular
    file, realpath containment under a whitelisted root, and minimum age.
    `st` (an os.lstat result) and `now` are injectable for tests.
    """
    basename = os.path.basename(path)
    if not is_rotated_name(basename):
        return False, 'not-a-rotated-log'
    try:
        st = st or os.lstat(path)
    except OSError as exc:
        return False, 'stat-failed: %s' % exc
    if stat_mod.S_ISLNK(st.st_mode):
        return False, 'symlink'
    if not stat_mod.S_ISREG(st.st_mode):
        return False, 'not-a-regular-file'
    real = os.path.realpath(path)
    allowed, _ = resolve_roots(dip_home)
    if not any(_contained_under(real, root_real) for _, root_real in allowed):
        return False, 'outside-allowed-roots'
    now = now if now is not None else time.time()
    age_days = (now - st.st_mtime) / 86400.0
    if age_days < float(min_age_days):
        return False, 'too-young (%.1fd < %sd)' % (age_days, min_age_days)
    return True, 'ok'


def scan(dip_home, roots=None, min_age_days=3, now=None):
    """Walk whitelisted roots and aggregate deletable rotated logs.

    Returns {'roots': {rel: {files, bytes, sample[<=5]}}, 'refusedRoots': [...],
    'totalFiles', 'totalBytes'} — aggregates + capped samples only, so the
    result stays inside macro JSON size limits.
    """
    allowed, refused = resolve_roots(dip_home, roots)
    now = now if now is not None else time.time()
    per_root = {}
    total_files = 0
    total_bytes = 0
    for rel, abspath in allowed:
        entry = {'files': 0, 'bytes': 0, 'sample': []}
        if os.path.isdir(abspath):
            for dirpath, dirnames, filenames in os.walk(abspath):
                # never follow directory symlinks out of the root
                dirnames[:] = [d for d in dirnames
                               if not os.path.islink(os.path.join(dirpath, d))]
                for name in filenames:
                    full = os.path.join(dirpath, name)
                    try:
                        st = os.lstat(full)
                    except OSError:
                        continue
                    ok, _ = is_deletable(full, dip_home, min_age_days, now=now, st=st)
                    if not ok:
                        continue
                    entry['files'] += 1
                    entry['bytes'] += st.st_size
                    if len(entry['sample']) < SAMPLE_LIMIT:
                        entry['sample'].append(full)
        per_root[rel] = entry
        total_files += entry['files']
        total_bytes += entry['bytes']
    return {'roots': per_root, 'refusedRoots': refused,
            'totalFiles': total_files, 'totalBytes': total_bytes}
