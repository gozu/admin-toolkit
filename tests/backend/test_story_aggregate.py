"""Story audit-dir aggregation — day bucketing, gz, malformed lines, cursors."""
import gzip
import json
import os

import pytest

from adk_backend.story.aggregate import FORMAT_VERSION, aggregate_audit_dir
from adk_backend.story.classification import VOCAB_VERSION


def _line(ts, msg_type='dataset-save', login='alice', project='PROJ', **extra):
    message = {'authSource': 'USER_FROM_UI', 'msgType': msg_type,
               'authUser': login, 'projectKey': project}
    message.update(extra)
    return json.dumps({'topic': 'generic', 'message': message, 'timestamp': ts})


def _write(path, lines):
    if path.endswith('.gz'):
        with gzip.open(path, 'wt', encoding='utf-8') as fh:
            fh.write('\n'.join(lines) + '\n')
    else:
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines) + '\n')


TODAY = '2026-06-12'


def test_basic_aggregation_and_shape(tmp_path):
    _write(str(tmp_path / 'audit.log'), [
        _line('2026-06-11T10:00:00.000+0000', msg_type='dataset-save'),
        _line('2026-06-11T11:00:00.000+0000', msg_type='flow-read'),
        _line('2026-06-11T12:00:00.000+0000', msg_type='recipe-run', login='bob'),
    ])
    result = aggregate_audit_dir(str(tmp_path), today=TODAY)
    assert result['ok'] is True
    assert result['formatVersion'] == FORMAT_VERSION
    assert result['vocabVersion'] == VOCAB_VERSION
    assert result['filesRead'] == 1
    assert result['parseErrors'] == 0
    day = result['days']['2026-06-11']
    by_login = {row['login']: row for row in day['userActivity']}
    assert by_login['alice']['viewingActions'] == 2
    assert by_login['alice']['developingActions'] == 1  # flow-read is viewing-only
    assert by_login['bob']['developingActions'] == 1
    counts = {(row['projectKey'], row['msgType']): row['count'] for row in day['eventCounts']}
    assert counts[('PROJ', 'dataset-save')] == 1
    assert counts[('PROJ', 'flow-read')] == 1


def test_utc_midnight_day_bucketing(tmp_path):
    _write(str(tmp_path / 'audit.log'), [
        _line('2026-06-10T23:59:59.999+0000'),
        _line('2026-06-11T00:00:00.000+0000'),
        # +02:00 offset: 2026-06-11T01:30+02:00 is 2026-06-10T23:30 UTC
        _line('2026-06-11T01:30:00.000+0200'),
    ])
    result = aggregate_audit_dir(str(tmp_path), today=TODAY)
    assert set(result['days']) == {'2026-06-10', '2026-06-11'}
    assert result['days']['2026-06-10']['userActivity'][0]['viewingActions'] == 2


def test_gz_files_are_read(tmp_path):
    _write(str(tmp_path / 'audit.log'), [_line('2026-06-11T10:00:00.000+0000')])
    _write(str(tmp_path / 'audit.log.1.gz'), [_line('2026-06-10T10:00:00.000+0000')])
    result = aggregate_audit_dir(str(tmp_path), today=TODAY)
    assert result['filesRead'] == 2
    assert set(result['days']) == {'2026-06-10', '2026-06-11'}


def test_malformed_lines_counted_not_fatal(tmp_path):
    _write(str(tmp_path / 'audit.log'), [
        '{"topic": "generic", truncated garbage',
        _line('2026-06-11T10:00:00.000+0000'),
        json.dumps({'topic': 'generic', 'message': {
            'authSource': 'USER_FROM_UI', 'msgType': 'x-save', 'authUser': 'a',
        }, 'timestamp': 'not-a-timestamp'}),
    ])
    result = aggregate_audit_dir(str(tmp_path), today=TODAY)
    assert result['parseErrors'] == 2
    assert '2026-06-11' in result['days']


def test_unreadable_file_raises(tmp_path):
    bad = tmp_path / 'audit.log.1.gz'
    bad.write_bytes(b'this is not gzip')
    _write(str(tmp_path / 'audit.log'), [_line('2026-06-11T10:00:00.000+0000')])
    with pytest.raises(Exception):
        aggregate_audit_dir(str(tmp_path), today=TODAY)


def test_since_day_excludes_days_at_or_before_cursor(tmp_path):
    _write(str(tmp_path / 'audit.log'), [
        _line('2026-06-09T10:00:00.000+0000'),
        _line('2026-06-10T10:00:00.000+0000'),
        _line('2026-06-11T10:00:00.000+0000'),
    ])
    result = aggregate_audit_dir(str(tmp_path), since_day='2026-06-10', today=TODAY)
    assert set(result['days']) == {'2026-06-11'}


def test_lookback_window_bounds_history(tmp_path):
    _write(str(tmp_path / 'audit.log'), [
        _line('2026-05-01T10:00:00.000+0000'),
        _line('2026-06-11T10:00:00.000+0000'),
    ])
    result = aggregate_audit_dir(str(tmp_path), lookback_days=14, today=TODAY)
    assert set(result['days']) == {'2026-06-11'}


def test_future_days_excluded(tmp_path):
    _write(str(tmp_path / 'audit.log'), [_line('2026-07-01T10:00:00.000+0000')])
    result = aggregate_audit_dir(str(tmp_path), today=TODAY)
    assert result['days'] == {}


def test_non_ui_events_ignored(tmp_path):
    _write(str(tmp_path / 'audit.log'), [
        _line('2026-06-11T10:00:00.000+0000', jobId='job-123'),
        json.dumps({'topic': 'compute-resource-usage', 'message': {},
                    'timestamp': '2026-06-11T10:00:00.000+0000'}),
    ])
    result = aggregate_audit_dir(str(tmp_path), today=TODAY)
    assert result['days'] == {}


def test_max_files_limits_reads(tmp_path):
    _write(str(tmp_path / 'audit.log'), [_line('2026-06-11T10:00:00.000+0000')])
    _write(str(tmp_path / 'audit.log.1'), [_line('2026-06-10T10:00:00.000+0000')])
    result = aggregate_audit_dir(str(tmp_path), max_files=1, today=TODAY)
    assert result['filesRead'] == 1


def test_empty_dir(tmp_path):
    result = aggregate_audit_dir(str(tmp_path), today=TODAY)
    assert result['ok'] is True
    assert result['days'] == {}
    assert result['filesRead'] == 0
