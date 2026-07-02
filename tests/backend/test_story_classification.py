"""Story audit classification — vocab rules and UI-event filtering."""

from adk_backend.story.classification import (
    ACTION_WORDS,
    REMOVE_WORDS,
    VOCAB_VERSION,
    classify_msg_type,
    is_ui_user_event,
    taxonomy_for,
)


def _ui_event(msg_type='dataset-save', login='alice', **message_extra):
    message = {'authSource': 'USER_FROM_UI', 'msgType': msg_type, 'authUser': login}
    message.update(message_extra)
    return {'topic': 'generic', 'message': message, 'timestamp': '2026-06-11T10:00:00.000+0000'}


class TestClassifyMsgType:
    def test_developing_vectors(self):
        for msg_type in ('dataset-save', 'recipe-run', 'scenario-manual-run',
                         'notebook-execute', 'project-create', 'GIT-COMMIT'):
            assert classify_msg_type(msg_type) == 'developing', msg_type

    def test_viewing_vectors(self):
        # No action word at all, or nothing but neutral words.
        for msg_type in ('interests-follow', 'home-page-visit', 'ui-interaction'):
            assert classify_msg_type(msg_type) == 'viewing', msg_type

    def test_remove_word_wins_over_action_word(self):
        # Contains action words ('save'/'clear'/'run') but a remove word vetoes.
        for msg_type in ('dataset-save-schema', 'dataset-clear-samples',
                         'project-save-variables', 'recipe-get', 'flow-read',
                         'scenario-list', 'api-preview'):
            assert classify_msg_type(msg_type) == 'viewing', msg_type

    def test_case_insensitive(self):
        assert classify_msg_type('Dataset-SAVE') == 'developing'
        assert classify_msg_type('DATASET-SAVE-SCHEMA') == 'viewing'

    def test_empty_and_none(self):
        assert classify_msg_type('') == 'viewing'
        assert classify_msg_type(None) == 'viewing'

    def test_vocab_constants_are_nonempty_and_versioned(self):
        assert ACTION_WORDS and REMOVE_WORDS
        assert isinstance(VOCAB_VERSION, int) and VOCAB_VERSION >= 1


class TestTaxonomy:
    def test_buckets(self):
        assert taxonomy_for('dataset-save') == 'Datasets'
        assert taxonomy_for('recipe-run') == 'Recipes'
        assert taxonomy_for('scenario-manual-run') == 'Scenarios'
        assert taxonomy_for('webapp-save') == 'Webapps & Dashboards'
        assert taxonomy_for('something-unmapped') == 'Other'

    def test_case_insensitive(self):
        assert taxonomy_for('DATASET-SAVE') == 'Datasets'


class TestIsUiUserEvent:
    def test_keeps_plain_ui_event(self):
        keep, login, project_key, msg_type = is_ui_user_event(
            _ui_event(msg_type='flow-read', projectKey='PROJ'))
        assert keep is True
        assert login == 'alice'
        assert project_key == 'PROJ'
        assert msg_type == 'flow-read'

    def test_missing_project_key_normalizes_to_empty(self):
        keep, _login, project_key, _mt = is_ui_user_event(_ui_event())
        assert keep is True
        assert project_key == ''

    def test_rejects_non_generic_topic(self):
        evt = _ui_event()
        evt['topic'] = 'compute-resource-usage'
        assert is_ui_user_event(evt)[0] is False

    def test_rejects_non_ui_auth_source(self):
        evt = _ui_event()
        evt['message']['authSource'] = 'API_KEY'
        assert is_ui_user_event(evt)[0] is False

    def test_rejects_scenario_and_job_attributed(self):
        assert is_ui_user_event(_ui_event(jobId='some-job'))[0] is False
        assert is_ui_user_event(_ui_event(scenarioId='PROJ.scn'))[0] is False

    def test_rejects_missing_msg_type_or_login(self):
        evt = _ui_event()
        evt['message'].pop('msgType')
        assert is_ui_user_event(evt)[0] is False
        evt = _ui_event()
        evt['message'].pop('authUser')
        assert is_ui_user_event(evt)[0] is False

    def test_login_fallback_order(self):
        # message.login wins over user / authUser / mdc.user
        evt = _ui_event()
        evt['message'].update({'login': 'l1', 'user': 'l2'})
        evt['mdc'] = {'user': 'l4'}
        assert is_ui_user_event(evt)[1] == 'l1'
        # then message.user
        evt['message'].pop('login')
        assert is_ui_user_event(evt)[1] == 'l2'
        # then message.authUser
        evt['message'].pop('user')
        assert is_ui_user_event(evt)[1] == 'alice'
        # finally mdc.user
        evt['message'].pop('authUser')
        assert is_ui_user_event(evt)[1] == 'l4'

    def test_rejects_malformed_shapes(self):
        assert is_ui_user_event({})[0] is False
        assert is_ui_user_event({'topic': 'generic', 'message': 'not-a-dict'})[0] is False
        assert is_ui_user_event(None)[0] is False
