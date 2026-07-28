"""Unit tests for the fs-cleanup deletion policy (the authoritative layer)."""

import os
import time

import pytest

from atk_agent_common.policies import fs_paths


@pytest.fixture()
def dip_home(tmp_path):
    """A fake DIP_HOME with a webappruns/ root."""
    (tmp_path / 'webappruns').mkdir()
    return str(tmp_path)


def _run_dir(dip_home, project, webapp, stamp, age_days=30.0, size=256):
    path = os.path.join(dip_home, 'webappruns', project, webapp, 'run_%s' % stamp)
    os.makedirs(path)
    payload = os.path.join(path, 'backend.out')
    with open(payload, 'wb') as fh:
        fh.write(b'x' * size)
    old = time.time() - age_days * 86400
    os.utime(payload, (old, old))
    os.utime(path, (old, old))
    return path


# ---- run-dir name matrix ----

@pytest.mark.parametrize('name,expected', [
    ('run_2026-04-09-15-19-29-765', True),
    ('run_2026-12-31-23-59-59-1', True),
    # the per-webapp non-run entries must NEVER match
    ('initial', False),
    ('instance-info.json', False),
    ('run_', False),
    ('run_notadate', False),
    ('run_2026-04-09', False),
    ('backend.log.1', False),
])
def test_run_dir_name_matrix(name, expected):
    assert bool(fs_paths.RUN_DIR_RE.match(name)) is expected


# ---- policy resolution ----

def test_unknown_policy_refuses(dip_home):
    # 'exports' graduated to a real policy in the storage-tail phase — use a
    # name that will never exist.
    with pytest.raises(fs_paths.FsPolicyError):
        fs_paths.resolve_roots(dip_home, 'definitely-not-a-policy')


def test_only_existing_roots_resolve(dip_home):
    roots = fs_paths.resolve_roots(dip_home, 'webappruns')
    assert [rel for rel, _ in roots] == ['webappruns']  # tmp/webappruns absent


# ---- is_deletable_run_dir ----

def test_old_run_dir_deletable(dip_home):
    p = _run_dir(dip_home, 'PROJ', 'aBcDeF1', '2026-01-01-00-00-00-000')
    ok, reason = fs_paths.is_deletable_run_dir(p, dip_home, min_age_days=7)
    assert ok, reason


def test_young_run_dir_refused(dip_home):
    p = _run_dir(dip_home, 'PROJ', 'aBcDeF1', '2026-01-01-00-00-00-000', age_days=1.0)
    ok, reason = fs_paths.is_deletable_run_dir(p, dip_home, min_age_days=7)
    assert not ok and reason.startswith('too-young')


def test_fresh_content_makes_dir_young(dip_home):
    """A run dir with recently-written files counts as young even when the
    dir inode is old (newest_mtime injection)."""
    p = _run_dir(dip_home, 'PROJ', 'aBcDeF1', '2026-01-01-00-00-00-000')
    ok, reason = fs_paths.is_deletable_run_dir(
        p, dip_home, min_age_days=7, newest_mtime=time.time())
    assert not ok and reason.startswith('too-young')


def test_non_run_dir_refused(dip_home):
    path = os.path.join(dip_home, 'webappruns', 'PROJ', 'aBcDeF1', 'initial')
    os.makedirs(path)
    ok, reason = fs_paths.is_deletable_run_dir(path, dip_home, min_age_days=0)
    assert not ok and reason == 'not-a-run-dir'


def test_outside_root_refused(tmp_path, dip_home):
    outside = tmp_path / 'config' / 'run_2026-01-01-00-00-00-000'
    outside.mkdir(parents=True)
    ok, reason = fs_paths.is_deletable_run_dir(str(outside), dip_home, min_age_days=0)
    assert not ok and reason == 'outside-allowed-roots'


def test_symlinked_run_dir_refused(tmp_path, dip_home):
    real = tmp_path / 'elsewhere' / 'run_2026-01-01-00-00-00-000'
    real.mkdir(parents=True)
    link = os.path.join(dip_home, 'webappruns', 'PROJ', 'aBcDeF1',
                        'run_2026-02-02-00-00-00-000')
    os.makedirs(os.path.dirname(link))
    os.symlink(str(real), link)
    ok, reason = fs_paths.is_deletable_run_dir(link, dip_home, min_age_days=0)
    assert not ok and reason in ('symlink', 'outside-allowed-roots')


# ---- scan_webappruns ----

def test_scan_keeps_newest_n(dip_home):
    for i in range(5):
        _run_dir(dip_home, 'PROJ', 'aBcDeF1', '2026-01-0%d-00-00-00-000' % (i + 1))
    scan = fs_paths.scan_webappruns(dip_home, min_age_days=7, keep_last_runs=2)
    entry = scan['webapps']['PROJ/aBcDeF1']
    assert entry['runDirs'] == 5
    assert entry['deletableRuns'] == 3  # newest 2 kept
    assert scan['totalDirs'] == 3
    assert scan['totalBytes'] == 3 * 256
    # the kept dirs are the two NEWEST (samples list the older ones only)
    assert all('run_2026-01-04' not in s and 'run_2026-01-05' not in s
               for s in entry['sample'])


def test_scan_project_filter(dip_home):
    _run_dir(dip_home, 'AAA', 'w1', '2026-01-01-00-00-00-000')
    _run_dir(dip_home, 'BBB', 'w2', '2026-01-01-00-00-00-000')
    scan = fs_paths.scan_webappruns(dip_home, project_key='AAA',
                                    min_age_days=7, keep_last_runs=0)
    assert scan['projectKeys'] == ['AAA']
    assert list(scan['webapps']) == ['AAA/w1']


def test_scan_running_exclusion_keeps_newest(dip_home):
    """keep_last_runs=0 but the webapp is running ⇒ its newest run survives."""
    for i in range(3):
        _run_dir(dip_home, 'PROJ', 'live1', '2026-01-0%d-00-00-00-000' % (i + 1))
    scan = fs_paths.scan_webappruns(dip_home, min_age_days=7, keep_last_runs=0,
                                    running_exclusions={'PROJ/live1'})
    assert scan['webapps']['PROJ/live1']['deletableRuns'] == 2


def test_scan_age_gate_skips_young(dip_home):
    _run_dir(dip_home, 'PROJ', 'w1', '2026-01-01-00-00-00-000', age_days=1.0)
    _run_dir(dip_home, 'PROJ', 'w1', '2026-01-02-00-00-00-000', age_days=30.0)
    scan = fs_paths.scan_webappruns(dip_home, min_age_days=7, keep_last_runs=0)
    entry = scan['webapps']['PROJ/w1']
    assert entry['deletableRuns'] == 1
    assert entry['skipped'] == 1


# ── aged-entry policies (storage tail: joblogs / tmp / exports) ──────────────

def _mk_aged(tmp_path, rel, age_days, is_dir=True, content=b'x' * 1024):
    import os, time
    full = tmp_path / rel
    if is_dir:
        full.mkdir(parents=True)
        (full / 'payload.bin').write_bytes(content)
    else:
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(content)
    stamp = time.time() - age_days * 86400
    for p in ([full / 'payload.bin', full] if is_dir else [full]):
        os.utime(p, (stamp, stamp))
    return str(full)


def test_joblogs_scan_keeps_newest_per_project(tmp_path):
    old1 = _mk_aged(tmp_path, 'jobs/P1/Build_a__NP__2026-01-01T00-00-00.000', 60)
    old2 = _mk_aged(tmp_path, 'jobs/P1/Build_b__NP__2026-02-01T00-00-00.000', 40)
    _mk_aged(tmp_path, 'jobs/P1/Build_c__NP__2026-06-30T00-00-00.000', 1)
    scan = fs_paths.scan_aged_entries(str(tmp_path), 'joblogs', min_age_days=15,
                                      keep_last=1)
    assert scan['totalDirs'] == 2
    assert scan['groups']['P1']['deletable'] == 2
    assert sorted(scan['groups']['P1']['sample']) == sorted([old1, old2])
    # keep_last above the count -> nothing deletable
    scan = fs_paths.scan_aged_entries(str(tmp_path), 'joblogs', min_age_days=15,
                                      keep_last=5)
    assert scan['totalDirs'] == 0


def test_tmp_policy_excludes_webappruns_bucket(tmp_path):
    _mk_aged(tmp_path, 'tmp/webappruns/OLDPROJ', 90)
    keep = _mk_aged(tmp_path, 'tmp/codeenv/stale-build', 90)
    scan = fs_paths.scan_aged_entries(str(tmp_path), 'tmp', min_age_days=15)
    assert scan['projectKeys'] == ['codeenv']
    assert scan['groups']['codeenv']['sample'] == [keep]
    ok, reason = fs_paths.is_deletable_aged_entry(
        str(tmp_path / 'tmp/webappruns/OLDPROJ'), str(tmp_path), 'tmp', 15)
    assert not ok and 'excluded-group' in reason


def test_aged_entry_floor_symlink_and_depth(tmp_path):
    import os
    real = _mk_aged(tmp_path, 'exports/data/old-export', 30)
    link = tmp_path / 'exports/data/evil-link'
    os.symlink(real, str(link))
    ok, reason = fs_paths.is_deletable_aged_entry(str(link), str(tmp_path), 'exports', 7)
    assert not ok and reason == 'symlink'
    # depth: the group dir itself is never a unit
    ok, reason = fs_paths.is_deletable_aged_entry(
        str(tmp_path / 'exports/data'), str(tmp_path), 'exports', 7)
    assert not ok and 'depth' in reason
    ok, _ = fs_paths.is_deletable_aged_entry(real, str(tmp_path), 'exports', 7)
    assert ok


def test_aged_entry_young_content_protects_dir(tmp_path):
    import os, time
    full = _mk_aged(tmp_path, 'jobs/P2/Build_x__NP__2026-01-01T00-00-00.000', 90)
    fresh = tmp_path / 'jobs/P2/Build_x__NP__2026-01-01T00-00-00.000/still-writing.log'
    fresh.write_bytes(b'live')
    ok, reason = fs_paths.is_deletable_aged_entry(full, str(tmp_path), 'joblogs', 15)
    assert not ok and 'too-young' in reason


# ── orphan projects ─────────────────────────────────────────────────────────
#
# Live shape verified on akaos: DSS reports PLEASE and YEA (dead projects with
# a jupyter-run/dku-workdirs/ leftover) alongside `uploads`, which is NOT a
# project at all — it is managed_datasets/uploads, a shared bucket whose
# children are named after LIVE projects. The children test below is the one
# that stops us destroying that.

LIVE = frozenset({'SOL_DEMAND_FORECAST', 'QS_DATA_PREP_1', 'ALIVE'})


def _mk_orphan(tmp_path, rel, children=(), size=512):
    """A <area root>/<KEY> directory with optional named children."""
    full = tmp_path / rel
    full.mkdir(parents=True)
    (full / 'payload.bin').write_bytes(b'x' * size)
    for child in children:
        (full / child).mkdir()
    return str(full)


def test_orphan_unknown_area_refused(tmp_path):
    """config/projects/<KEY> is a ProjectList to DSS too, so an orphan item can
    carry a `config` area — but a surviving config dir is a project definition
    DSS failed to load, not debris. Refuse with an explanation."""
    path = _mk_orphan(tmp_path, 'config/projects/DEADPROJ')
    ok, reason = fs_paths.is_deletable_orphan_dir(path, str(tmp_path), LIVE)
    assert not ok and reason == 'unsupported-area (config)'


def test_orphan_symlink_refused(tmp_path):
    real = _mk_orphan(tmp_path, 'elsewhere/DEADPROJ')
    (tmp_path / 'jupyter-run/dku-workdirs').mkdir(parents=True)
    link = str(tmp_path / 'jupyter-run/dku-workdirs/DEADPROJ')
    os.symlink(real, link)
    ok, reason = fs_paths.is_deletable_orphan_dir(link, str(tmp_path), LIVE)
    assert not ok and reason == 'symlink'


def test_orphan_root_itself_refused(tmp_path):
    """The area root is never a deletable unit — only <root>/<KEY> is."""
    _mk_orphan(tmp_path, 'jupyter-run/dku-workdirs/DEADPROJ')
    root = str(tmp_path / 'jupyter-run/dku-workdirs')
    ok, reason = fs_paths.is_deletable_orphan_dir(root, str(tmp_path), LIVE)
    assert not ok and reason == 'outside-allowed-depth'


def test_orphan_depth_too_deep_refused(tmp_path):
    """No per-file drill-in: one level below the key is out of scope."""
    _mk_orphan(tmp_path, 'jupyter-run/dku-workdirs/DEADPROJ', children=('ipythondir',))
    deeper = str(tmp_path / 'jupyter-run/dku-workdirs/DEADPROJ/ipythondir')
    ok, reason = fs_paths.is_deletable_orphan_dir(deeper, str(tmp_path), LIVE)
    assert not ok and reason == 'outside-allowed-depth'


def test_orphan_live_project_key_refused(tmp_path):
    path = _mk_orphan(tmp_path, 'jupyter-run/dku-workdirs/ALIVE')
    ok, reason = fs_paths.is_deletable_orphan_dir(path, str(tmp_path), LIVE)
    assert not ok and reason == 'live-project'


def test_orphan_reserved_name_refused(tmp_path):
    """Secondary defense: DSS-internal names that sit at the key position."""
    path = _mk_orphan(tmp_path, 'managed_datasets/tmp_upload_box')
    ok, reason = fs_paths.is_deletable_orphan_dir(path, str(tmp_path), LIVE)
    assert not ok and reason == 'reserved-name'


def test_orphan_bad_key_shape_refused(tmp_path):
    path = _mk_orphan(tmp_path, 'managed_datasets/not-a-key')
    ok, reason = fs_paths.is_deletable_orphan_dir(path, str(tmp_path), LIVE)
    assert not ok and reason == 'not-a-project-key-shape'


def test_orphan_with_live_project_children_refused(tmp_path):
    """THE akaos `uploads` case, under a name the reserved list does NOT cover,
    so the generic rule is what does the work: a directory at the key position
    whose own children name live projects is a shared bucket, not a dead
    project."""
    path = _mk_orphan(tmp_path, 'managed_datasets/sharedbucket',
                      children=('SOL_DEMAND_FORECAST', 'QS_DATA_PREP_1'))
    ok, reason = fs_paths.is_deletable_orphan_dir(path, str(tmp_path), LIVE)
    assert not ok
    assert reason == 'contains-live-projects (QS_DATA_PREP_1, SOL_DEMAND_FORECAST)'
    # and the <KEY>.<dataset> layout DSS uses for shaker samples matches too
    dotted = _mk_orphan(tmp_path, 'managed_datasets/otherbucket',
                        children=('ALIVE.sales_monthly',))
    ok, reason = fs_paths.is_deletable_orphan_dir(dotted, str(tmp_path), LIVE)
    assert not ok and reason == 'contains-live-projects (ALIVE)'


def test_orphan_empty_live_key_set_fails_closed(tmp_path):
    """Without the live project list every directory looks orphaned — refuse."""
    path = _mk_orphan(tmp_path, 'jupyter-run/dku-workdirs/DEADPROJ')
    ok, reason = fs_paths.is_deletable_orphan_dir(path, str(tmp_path), set())
    assert not ok and reason == 'live-project-list-unavailable'


def test_orphan_happy_path_deletable(tmp_path):
    path = _mk_orphan(tmp_path, 'jupyter-run/dku-workdirs/PLEASE',
                      children=('ipythondir',))
    ok, reason = fs_paths.is_deletable_orphan_dir(path, str(tmp_path), LIVE)
    assert ok, reason


def test_scan_orphans_reports_blocked_with_reason(tmp_path):
    _mk_orphan(tmp_path, 'jupyter-run/dku-workdirs/PLEASE', size=100)
    _mk_orphan(tmp_path, 'jupyter-run/dku-workdirs/ALIVE', size=100)  # live: not an orphan
    _mk_orphan(tmp_path, 'managed_datasets/uploads', size=200,
               children=('SOL_DEMAND_FORECAST',))
    scan = fs_paths.scan_orphans(str(tmp_path), LIVE)

    assert scan['projectKeys'] == ['PLEASE', 'uploads']  # ALIVE never surfaces
    assert scan['totalDirs'] == 1
    assert scan['totalBytes'] == 100  # blocked bytes are reported, not counted

    please = scan['orphans']['PLEASE']
    assert please['deletableAreas'] == 1 and please['blockedAreas'] == 0
    assert please['areas'][0]['area'] == 'dkuWorkdirs'
    assert please['areas'][0]['path'].endswith('/jupyter-run/dku-workdirs/PLEASE')

    uploads = scan['orphans']['uploads']
    assert uploads['deletableAreas'] == 0 and uploads['blockedAreas'] == 1
    assert uploads['bytes'] == 200
    # `uploads` is hard-refused twice over (live children AND reserved name).
    # The generic rule reports, because it is the one that generalises.
    assert uploads['areas'][0]['deletable'] is False
    assert uploads['areas'][0]['reason'] == 'contains-live-projects (SOL_DEMAND_FORECAST)'


def test_scan_orphans_project_key_filter(tmp_path):
    _mk_orphan(tmp_path, 'jupyter-run/dku-workdirs/PLEASE')
    _mk_orphan(tmp_path, 'jupyter-run/dku-workdirs/YEA')
    scan = fs_paths.scan_orphans(str(tmp_path), LIVE, project_key='YEA')
    assert scan['projectKeys'] == ['YEA']


def test_orphan_policy_roots_cover_every_area(tmp_path):
    """POLICY_ROOTS['orphans'] is derived from ORPHAN_AREA_ROOTS — a new area
    must not be able to drift out of the containment floor."""
    assert set(fs_paths.POLICY_ROOTS['orphans']) == set(fs_paths.ORPHAN_AREA_ROOTS.values())
