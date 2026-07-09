"""Remediation-map guardrail tests.

Fleet-audit regression: cap-connection-broken mapped straight to
connection-update with "a blank host" as the canonical example, so the agent
proposed "repairs" for never-configured junk connections with no known-good
value to write. The map entry must gate the action on the evidence supplying
a concrete newValue and name the junk-connection alternative (delete after a
usage review) — the same actionable-vs-advisory guardrail code-env-consolidate
already carries.
"""

from atk_agent_common import remediation_map


def _update_spec(issue_id):
    specs = remediation_map.remediations_for(issue_id)
    return next(s for s in specs if s['action'] == 'connection-update')


def test_connection_update_repair_requires_known_good_value():
    why = _update_spec('cap-connection-broken')['why']
    assert 'newValue' in why
    assert 'ADVISORY' in why
    assert 'guess' in why.lower()
    # junk-connection escape hatch: blank required fields are not drift
    assert 'connection-delete' in why
    assert 'blank' in why.lower()


def test_connection_update_guardrail_reaches_prompt_table():
    table = remediation_map.prompt_table()
    assert 'newValue' in table
    assert 'ADVISORY' in table
