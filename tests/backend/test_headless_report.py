"""Public evidence must neither import historical passes nor leak private fields."""
import json
from pathlib import Path
import sys

import conftest  # noqa
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts' / 'agents'))
from render_headless_suite import render, TRANSPORT


def test_fresh_report_allowlists_aggregate_evidence(tmp_path):
    private = 'PRIVATE_TOKEN_TRANSCRIPT_AND_HOST'
    (tmp_path / 'reads.json').write_text(json.dumps({'private_failures': private, 'rows': [{
        'capability': 'list_hosts', 'transport': TRANSPORT, 'status': 'passed',
        'reason': private, 'scope': private, 'postcondition': private,
        'legacy_seconds': 10, 'headless_seconds': 23,
    }]}))
    report, markdown = render(tmp_path, 'test')
    assert len(report['rows']) == 64
    assert report['counts'] == {'passed': 1, 'blocked': 62, 'excluded': 1}
    assert report['median_paired_time_ratio'] == 2.3
    assert private not in json.dumps(report) + markdown
    assert '**2.30×**' in markdown


def test_report_rejects_historical_transport(tmp_path):
    (tmp_path / 'reads.json').write_text(json.dumps({'rows': [{
        'capability': 'list_hosts', 'transport': 'direct-cobuild', 'status': 'passed'}]}))
    with pytest.raises(ValueError, match='another comparison transport'):
        render(tmp_path, 'test')


def test_later_failure_is_not_hidden_by_an_earlier_pass(tmp_path):
    (tmp_path / 'reads.json').write_text(json.dumps({'rows': [
        {'capability': 'list_hosts', 'transport': TRANSPORT, 'status': status}
        for status in ('passed', 'failed')]}))
    report, markdown = render(tmp_path, 'test')
    row = next(r for r in report['rows'] if r['capability'] == 'list_hosts')
    assert row['status'] == 'failed'
    assert [a['status'] for a in row['attempts']] == ['passed', 'failed']
    assert 'passed → failed' in markdown
