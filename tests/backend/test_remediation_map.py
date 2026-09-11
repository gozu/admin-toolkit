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


# ── 2026-08-24 sanity-code routes: the four codes the agent kept calling
#    "manual" despite full authority (advisory-item drill). Each carries a
#    playbook in its why prose; the sanity-* catch-all still owns unknown
#    codes.


def _only_spec(issue_id, action):
    specs = remediation_map.remediations_for(issue_id)
    assert [s['action'] for s in specs] == [action], issue_id
    return specs[0]


def test_snowflake_autofastwrite_routes_to_connection_update():
    why = _only_spec('sanity-warning-WARN_CONNECTION_SNOWFLAKE_NO_AUTOFASTWRITE',
                     'connection-update')['why']
    assert 'connection-delete' in why       # keep-or-delete gate
    assert 'staging' in why                 # cloud staging prerequisite
    assert 'connection-test' in why         # verification step


def test_spark_details_read_routes_to_connection_update():
    why = _only_spec(
        'sanity-warning-WARN_CONNECTION_SPARK_NO_GROUP_WITH_DETAILS_READ_ACCESS',
        'connection-update')['why']
    assert 'connections-usage' in why       # derive groups from real usage
    assert 'ADVISORY' in why                # ambiguity stays a policy call


def test_app_as_recipe_orphans_route_to_project_delete():
    for code in ('WARN_APP_AS_RECIPE_HAS_ORPHAN_INSTANCES',
                 'WARN_APP_AS_RECIPE_TOO_MANY_INSTANCES'):
        why = _only_spec('sanity-warning-%s' % code, 'project-delete')['why']
        assert 'app-instances' in why       # the new read domain
        assert 'orphanDeterminable' in why  # unknown ≠ zero
        assert 'keepInstance' in why        # cause vs symptom


def test_git_migration_routes_to_notify_with_checklist():
    spec = _only_spec('sanity-warning-WARN_GIT_PROJECT_NOT_MIGRATED',
                      'notification-send')
    why = spec['why']
    assert 'score-exempt' in why            # honest framing (v0.4.821)
    assert 'Version Control' in why
    assert not spec['auto']                 # never autonomous


def test_unknown_sanity_codes_still_fall_to_documented_gap():
    issue_id = 'sanity-warning-WARN_SOME_FUTURE_CODE'
    assert remediation_map.remediations_for(issue_id) == []
    assert remediation_map.is_documented_gap(issue_id)


def test_project_codenv_route_carries_drill_steps():
    why = _only_spec('project-codenv-info-group', 'code-env-consolidate')['why']
    assert 'sourceEnvName' in why and 'targetEnvName' in why
    assert 'name_filter=<projectKey>' in why   # the code-envs drill read
    assert 'retireSource' in why
    assert 'whitelisting' in why               # the worth-it gate
