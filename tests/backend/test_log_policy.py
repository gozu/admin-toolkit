"""Unit tests for the rotated-log deletion policy (the authoritative layer)."""

import gzip
import os
import time

import pytest

from atk_agent_common.policies import log_files


@pytest.fixture()
def dip_home(tmp_path):
    """A fake DIP_HOME with a run/ root."""
    (tmp_path / 'run').mkdir()
    return str(tmp_path)


def _write(path, age_days=10.0, content=b'x' * 128):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    old = time.time() - age_days * 86400
    os.utime(str(path), (old, old))
    return str(path)


# ---- rotated-name matrix ----

@pytest.mark.parametrize('name,expected', [
    ('backend.log.1', True),
    ('backend.log.12', True),
    ('backend.log.1.gz', True),
    ('backend.log.gz', True),
    ('backend.log.zip', True),
    ('backend.log.bz2', True),
    ('backend.log.xz', True),
    ('access.log-2026-05-01', True),
    ('access.log.2026-05-01', True),
    ('access.log-2026-05-01.gz', True),
    ('kernel.out.3', True),
    ('kernel.out.3.gz', True),
    # live files and non-logs must NEVER match
    ('backend.log', False),
    ('kernel.out', False),
    ('backend.log1', False),
    ('data.csv', False),
    ('project.zip', False),
    ('log.txt', False),
    ('backend.log.old', False),
])
def test_rotated_name_matrix(name, expected):
    assert log_files.is_rotated_name(name) is expected


# ---- is_deletable ----

def test_live_log_refused(tmp_path, dip_home):
    p = _write(tmp_path / 'run' / 'backend.log')
    ok, reason = log_files.is_deletable(p, dip_home)
    assert not ok and reason == 'not-a-rotated-log'


def test_rotated_old_log_accepted(tmp_path, dip_home):
    p = _write(tmp_path / 'run' / 'backend.log.1', age_days=10)
    ok, reason = log_files.is_deletable(p, dip_home, min_age_days=3)
    assert ok, reason


def test_min_age_refusal(tmp_path, dip_home):
    p = _write(tmp_path / 'run' / 'backend.log.1', age_days=1)
    ok, reason = log_files.is_deletable(p, dip_home, min_age_days=3)
    assert not ok and reason.startswith('too-young')


def test_symlink_refused(tmp_path, dip_home):
    real = _write(tmp_path / 'run' / 'backend.log.1', age_days=10)
    link = tmp_path / 'run' / 'evil.log.2'
    os.symlink(real, str(link))
    ok, reason = log_files.is_deletable(str(link), dip_home)
    assert not ok and reason == 'symlink'


def test_symlink_escape_refused(tmp_path, dip_home):
    """A symlink whose realpath escapes the roots is refused even pre-lstat
    check ordering — belt and suspenders via containment."""
    outside = _write(tmp_path / 'outside' / 'secret.log.1', age_days=10)
    link = tmp_path / 'run' / 'alias.log.1'
    os.symlink(outside, str(link))
    ok, reason = log_files.is_deletable(str(link), dip_home)
    assert not ok and reason in ('symlink', 'outside-allowed-roots')


def test_dotdot_traversal_refused(tmp_path, dip_home):
    _write(tmp_path / 'config' / 'creds.log.1', age_days=10)
    sneaky = os.path.join(dip_home, 'run', '..', 'config', 'creds.log.1')
    ok, reason = log_files.is_deletable(sneaky, dip_home)
    assert not ok and reason == 'outside-allowed-roots'


def test_outside_root_refused(tmp_path, dip_home):
    p = _write(tmp_path / 'databases' / 'db.log.1', age_days=10)
    ok, reason = log_files.is_deletable(p, dip_home)
    assert not ok and reason == 'outside-allowed-roots'


def test_nested_allowed_root(tmp_path, dip_home):
    p = _write(tmp_path / 'jobs' / 'PROJ' / 'job123' / 'run.log.2', age_days=10)
    ok, reason = log_files.is_deletable(p, dip_home)
    assert ok, reason


# ---- resolve_roots / scan ----

def test_resolve_roots_refuses_unknown():
    allowed, refused = log_files.resolve_roots('/nonexistent', ['run', 'config', '../etc'])
    assert [rel for rel, _ in allowed] == ['run']
    assert {r['root'] for r in refused} == {'config', '../etc'}


def test_scan_aggregates_and_samples(tmp_path, dip_home):
    for i in range(8):
        _write(tmp_path / 'run' / ('backend.log.%d' % (i + 1)), age_days=10)
    _write(tmp_path / 'run' / 'backend.log', age_days=10)     # live — excluded
    _write(tmp_path / 'run' / 'fresh.log.1', age_days=0.5)     # young — excluded
    result = log_files.scan(dip_home, roots=['run', 'bogus'], min_age_days=3)
    run = result['roots']['run']
    assert run['files'] == 8
    assert run['bytes'] == 8 * 128
    assert len(run['sample']) == log_files.SAMPLE_LIMIT
    assert result['refusedRoots'] == [{'root': 'bogus', 'reason': 'not-in-whitelist'}]
    assert result['totalFiles'] == 8


def test_scan_ignores_compressed_content_name(tmp_path, dip_home):
    """A real gz rotated log counts; naming is what gates, not content."""
    p = tmp_path / 'run' / 'backend.log.2.gz'
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(str(p), 'wb') as fh:
        fh.write(b'payload')
    old = time.time() - 10 * 86400
    os.utime(str(p), (old, old))
    result = log_files.scan(dip_home, roots=['run'])
    assert result['roots']['run']['files'] == 1
