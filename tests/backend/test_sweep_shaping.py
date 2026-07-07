"""Triage-sweep issue shaping: whitelist annotations must never reach LLM
consumers (whitelisted findings are suppressed upstream; the keys would only
teach the model to hedge live findings)."""

from atk_agent_common import shaping
from atk_agent_common.triage import sweep


def _shaped(issue):
    return shaping.pick(issue, sweep._ISSUE_KEYS)


def test_whitelist_keys_stripped():
    issue = {'id': 'cap-connection-broken', 'severity': 'critical',
             'category': 'connections', 'title': '1 actively-used connection failing',
             'recommendation': 'Repair it.', 'description': 'snow1.',
             'value': 1, 'items': ['snow1'],
             'whitelistRule': 'connection-broken', 'whitelistItems': ['snow1']}
    out = _shaped(issue)
    assert 'whitelistRule' not in out and 'whitelistItems' not in out
    assert out['items'] == ['snow1']  # the plain enrichment field survives
    assert out['id'] == 'cap-connection-broken'


def test_details_forwarded():
    issue = {'id': 'features-disabled-several', 'severity': 'warning',
             'category': 'runtime_config', 'title': '3 features disabled', 'value': 3,
             'details': [{'name': 'Spark', 'settingsPath': 'sparkSettings.sparkEnabled',
                          'proposedValue': True}]}
    out = _shaped(issue)
    assert out['details'][0]['settingsPath'] == 'sparkSettings.sparkEnabled'


def test_rubric_wording_updated():
    from atk_agent_common import rubric
    assert 'Never resurface' not in rubric.SEVERITY_RUBRIC
    assert 'removed UPSTREAM' in rubric.SEVERITY_RUBRIC


def test_feature_details_from_health():
    from atk_agent_common import health
    raw = {'sparkSettings': {'sparkEnabled': False},
           'impersonation': {'enabled': False}}
    disabled = health._check_disabled_features(raw)
    assert disabled['Spark']['settingsPath'] == 'sparkSettings.sparkEnabled'
    assert disabled['Impersonation']['sensitive'] is True
    score, issue = health._score_disabled_features(disabled)
    names = {d['name'] for d in issue['details']}
    assert names == {'Spark', 'Impersonation'}
    spark = next(d for d in issue['details'] if d['name'] == 'Spark')
    assert spark['proposedValue'] is True
