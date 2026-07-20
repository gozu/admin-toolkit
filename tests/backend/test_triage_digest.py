"""Digest renderer + auto-catalog + expanded auto-remediation tier tests.

The digest is pure (context in, HTML out) so it is tested at the string
level: number consistency, honesty markers (0 fixed, scored-vs-swept), and
email-safety invariants (no external assets beyond the official logo)."""

import re

import pytest

from atk_agent_common import remediation_map
from atk_agent_common.triage import auto_remediate, digest


# ── auto catalog ─────────────────────────────────────────────────────────────

def test_auto_catalog_contents_and_shape():
    catalog = remediation_map.auto_catalog()
    actions = [row['action'] for row in catalog]
    # the expanded v2 set — a regression here silently shrinks the daily
    # agent's capability surface (or worse, grows it unreviewed)
    assert actions == ['connection-test', 'docker-prune', 'job-logs-cleanup',
                       'log-cleanup', 'notebook-kernels-shutdown',
                       'project-clear-webapp-runs']
    for row in catalog:
        assert row['description'], row['action']
        assert row['findings'], row['action']
        assert row['risk'] in ('low', 'medium', 'high')


def test_python_run_can_never_be_auto():
    assert 'python-run' in remediation_map.AUTO_EXCLUDED
    assert 'python-run' not in {r['action'] for r in remediation_map.auto_catalog()}
    cands = remediation_map.auto_candidates(
        [{'id': 'disk-critical-/data'}], {'python-run'}, {})
    assert cands == []


def test_auto_candidates_connection_test_batches_from_items():
    issues = [{'id': 'cap-connection-broken', 'items': ['snow1', 'redshift2']}]
    cands = remediation_map.auto_candidates(issues, {'connection-test'}, {})
    assert len(cands) == 1
    assert cands[0]['action'] == 'connection-test'
    assert cands[0]['target'] == [{'name': 'snow1'}, {'name': 'redshift2'}]


def test_auto_candidates_skip_when_finding_lacks_data():
    # connection finding without items → no target derivable → no candidate
    cands = remediation_map.auto_candidates(
        [{'id': 'cap-connection-broken'}], {'connection-test'}, {})
    assert cands == []


def test_auto_candidates_dedup_per_action():
    issues = [{'id': 'disk-critical-/data'}, {'id': 'disk-warning-/home'}]
    cands = remediation_map.auto_candidates(
        issues, {'log-cleanup', 'job-logs-cleanup'}, {})
    assert sorted(c['action'] for c in cands) == ['job-logs-cleanup', 'log-cleanup']


# ── auto-remediation tier gating ─────────────────────────────────────────────

def _settings(**over):
    base = {'auto_remediate_actions': ['log-cleanup'], 'auto_remediate_enabled': True,
            'auto_remediate_remote_hosts': False, 'enable_red_actions': True,
            'master_password': 'x', 'auto_remediate_max_gb': 20,
            'auto_remediate_max_objects': 25}
    base.update(over)
    return base


_ROWS = [{'host': 'local', 'topIssues': [{'id': 'disk-critical-/data'}]}]


def test_paused_tier_executes_nothing():
    summary = auto_remediate.run_auto_remediation(
        client=None, settings=_settings(auto_remediate_enabled=False),
        rows=_ROWS, run_id='r1')
    assert summary['paused'] is True
    assert summary['executed'] == [] and summary['skipped'] == []


def test_remote_host_skipped_by_default():
    rows = [{'host': 'remote-1', 'topIssues': [{'id': 'disk-critical-/data'}]}]
    summary = auto_remediate.run_auto_remediation(
        client=None, settings=_settings(), rows=rows, run_id='r1')
    assert summary['executed'] == []
    assert len(summary['skipped']) == 1
    assert 'remote-host remediation is OFF' in summary['skipped'][0]['reason']


def test_remote_host_local_only_action_still_skipped_when_remote_enabled():
    rows = [{'host': 'remote-1', 'topIssues': [{'id': 'disk-critical-/data'}]}]
    summary = auto_remediate.run_auto_remediation(
        client=None, settings=_settings(auto_remediate_remote_hosts=True),
        rows=rows, run_id='r1')
    # log-cleanup is LOCAL-ONLY: with remote enabled it is skipped per-action
    assert summary['executed'] == []
    assert any('LOCAL-ONLY' in s['reason'] for s in summary['skipped'])


def test_connection_test_effect_classification():
    ok = {'connectionOK': True}
    fail = {'connectionOK': False}
    batch = {'batch': True, 'okCount': 2, 'errorCount': 0, 'perTarget': [
        {'status': 'ok', 'result': fail}, {'status': 'ok', 'result': fail}]}
    assert auto_remediate._effect('connection-test', ok) == 'fixed'
    assert auto_remediate._effect('connection-test', fail) == 'no-effect'
    assert auto_remediate._effect('connection-test', batch) == 'no-effect'
    assert auto_remediate._detail('connection-test', batch) == \
        '0 connection(s) recovered, 2 still failing'


def test_batch_freed_gb_sums_per_target():
    result = {'batch': True, 'okCount': 2, 'errorCount': 0, 'perTarget': [
        {'status': 'ok', 'result': {'totalReclaimedGB': 1.5}},
        {'status': 'ok', 'result': {'totalReclaimedGB': 0.5}},
        {'status': 'error', 'result': {'totalReclaimedGB': 99}},
    ]}
    assert auto_remediate._freed_gb('job-logs-cleanup', result) == 2.0


# ── digest rendering ─────────────────────────────────────────────────────────

@pytest.fixture()
def ctx():
    c = digest.sample_context()
    c['toolkitUrl'] = 'https://dss.example.com/public-webapps/X/y/'
    return c


def test_subject_reflects_flagged_and_scored(ctx):
    subject = digest.build_subject(ctx)
    assert '2 of 5 scored hosts need attention' in subject
    assert 'reclaimed overnight' in subject


def test_subject_healthy_when_nothing_flagged(ctx):
    ctx['flagged'] = []
    assert digest.build_subject(ctx).startswith('✅ Fleet healthy — 5 hosts scored')


def test_html_number_consistency(ctx):
    html = digest.render_digest_html(ctx)
    total = sum(e['freedGB'] for e in ctx['autoSummary']['executed'])
    assert abs(total - ctx['autoSummary']['totalFreedGB']) < 0.005
    assert ('%.2f' % ctx['autoSummary']['totalFreedGB']) in html  # KPI + meter agree
    # fleet average of scored hosts appears in the hero block
    scores = [r['score'] for r in ctx['hosts'] if isinstance(r.get('score'), (int, float))]
    assert '>%d<' % round(sum(scores) / len(scores)) in html
    # scored-vs-swept split is explicit
    assert 'hosts scored' in html and '/ 6' in html


def test_html_flags_zero_effect_probe(ctx):
    html = digest.render_digest_html(ctx)
    assert '0 fixed' in html
    assert '0 connections recovered, 2 still failing' in html


def test_html_no_external_assets_beyond_logo(ctx):
    html = digest.render_digest_html(ctx)
    srcs = re.findall(r'src="([^"]+)"', html)
    assert srcs == [digest.LOGO_URL]
    # nothing else fetches over the network (xmlns is a namespace, not a fetch)
    stripped = re.sub(r'href="[^"]*"|src="[^"]*"|xmlns="[^"]*"', '', html)
    assert 'https://' not in stripped and 'http://' not in stripped


def test_html_escapes_hostile_strings(ctx):
    ctx['hosts'][0]['recommendation'] = '<script>alert(1)</script>'
    ctx['configWarning'] = '<img src=x onerror=alert(2)>'
    html = digest.render_digest_html(ctx)
    assert '<script>' not in html
    assert '<img src=x' not in html


def test_delta_rendering(ctx):
    html = digest.render_digest_html(ctx)
    assert 'vs yesterday' in html
    assert '&#177;0' in html  # unchanged hosts say ±0 explicitly


def test_text_twin_carries_the_essentials(ctx):
    text = digest.render_digest_text(ctx)
    assert 'prod-emea' in text and 'log-cleanup' in text
    assert 'Total freed: 7.47 GB' in text
