"""WARN_GIT_PROJECT_NOT_MIGRATED (post-upgrade 'check out each branch to
migrate it' housekeeping) is score-exempt by default: it never lowers the
sanity component or emits an issue, with no whitelist entry required. Every
other sanity code keeps scoring normally.
"""

from atk_agent_common import health

NO_WHITELIST = lambda rule, item: False  # noqa: E731


def _msg(code, severity='WARNING', title=None):
    return {'code': code, 'severity': severity,
            'title': title or code, 'message': '%s details' % code}


def test_git_migration_warning_alone_scores_100():
    msgs = [_msg('WARN_GIT_PROJECT_NOT_MIGRATED')]
    assert health._score_sanity_check(msgs, NO_WHITELIST) == (100, [])


def test_other_warnings_still_score():
    msgs = [_msg('WARN_GIT_PROJECT_NOT_MIGRATED'),
            _msg('WARN_CONNECTION_SNOWFLAKE_NO_AUTOFASTWRITE')]
    score, issues = health._score_sanity_check(msgs, NO_WHITELIST)
    assert score == 75
    assert [i['id'] for i in issues] == \
        ['sanity-warning-WARN_CONNECTION_SNOWFLAKE_NO_AUTOFASTWRITE']


def test_exemption_is_severity_independent():
    msgs = [_msg('WARN_GIT_PROJECT_NOT_MIGRATED', severity='ERROR'),
            _msg('WARN_CONNECTION_SNOWFLAKE_NO_AUTOFASTWRITE')]
    score, issues = health._score_sanity_check(msgs, NO_WHITELIST)
    assert score == 75  # exempt ERROR must not drag the component to 40
    assert [i['id'] for i in issues] == \
        ['sanity-warning-WARN_CONNECTION_SNOWFLAKE_NO_AUTOFASTWRITE']
