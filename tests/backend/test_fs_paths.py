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
    with pytest.raises(fs_paths.FsPolicyError):
        fs_paths.resolve_roots(dip_home, 'exports')


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
