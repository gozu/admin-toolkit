"""Feedback sender resolution.

The feedback RECIPIENT is fixed (it reaches the toolkit author); the SENDER is
whoever is using the toolkit. These tests pin the resolution order — configured
override → the browsing admin's DSS email → the mail channel's own sender — and
the compat guard that keeps feedback sending on DSS builds whose messaging
client has no `sender` kwarg.
"""

from __future__ import annotations

import unittest
from unittest import mock

from flask import Flask

from adk_backend.routes import feedback


class _Settings:
    def __init__(self, config):
        self._raw = {'config': dict(config)}
        self.saved = False

    def get_raw(self):
        return self._raw

    def save(self):
        self.saved = True


class _Client:
    """Local DSS client stub: plugin settings + browser-header identity."""

    def __init__(self, config=None, login='admin', email='admin@corp.example'):
        self.settings = _Settings(config or {})
        self._login = login
        self._email = email

    def get_plugin(self, _plugin_id):
        return mock.Mock(get_settings=lambda: self.settings)

    def get_auth_info_from_browser_headers(self, _headers):
        return {'authIdentifier': self._login} if self._login else {}

    def get_user(self, login):
        assert login == self._login
        raw = {'login': login, 'email': self._email}
        return mock.Mock(get_settings=lambda: mock.Mock(get_raw=lambda: raw))


def resolve(client):
    """_resolve_sender() inside a request context, against one client stub."""
    app = Flask(__name__)
    with app.test_request_context('/api/feedback'):
        with mock.patch.object(feedback, '_local_thread_client', return_value=client):
            return feedback._resolve_sender()


class ResolveSenderTest(unittest.TestCase):
    def test_defaults_to_the_browsing_admin_email(self):
        info = resolve(_Client(email='dana@corp.example'))
        self.assertEqual(info['sender'], 'dana@corp.example')
        self.assertEqual(info['source'], 'user')
        self.assertEqual(info['override'], '')

    def test_configured_override_wins(self):
        info = resolve(_Client({'feedback_sender_email': 'noreply@corp.example'},
                               email='dana@corp.example'))
        self.assertEqual(info['sender'], 'noreply@corp.example')
        self.assertEqual(info['source'], 'override')
        # The signed-in user is still reported, so the UI can show both.
        self.assertEqual(info['currentUserEmail'], 'dana@corp.example')

    def test_user_without_an_email_falls_back_to_the_channel(self):
        # DSS accounts may carry no email; send() reads '' as "channel sender".
        info = resolve(_Client(email=''))
        self.assertEqual(info['sender'], '')
        self.assertEqual(info['source'], 'channel')

    def test_malformed_dss_email_is_not_used_as_a_sender(self):
        self.assertEqual(resolve(_Client(email='not-an-address'))['sender'], '')

    def test_unresolvable_identity_degrades_instead_of_raising(self):
        client = _Client()
        client.get_auth_info_from_browser_headers = mock.Mock(side_effect=RuntimeError('no session'))
        info = resolve(client)
        self.assertEqual(info['sender'], '')
        self.assertEqual(info['currentUser'], '')

    def test_a_hardcoded_author_address_is_never_the_sender(self):
        # The author's address is the destination only — regression guard for
        # feedback going out as the toolkit author from a customer's relay.
        self.assertNotEqual(resolve(_Client())['sender'], feedback.FEEDBACK_RECIPIENT)


class SendWithSenderTest(unittest.TestCase):
    def test_sender_is_passed_when_the_dss_build_supports_it(self):
        calls = {}

        class _Channel:
            def send(self, project_key, to, subject, body, sender=None, plain_text=False):
                calls.update(project_key=project_key, to=to, sender=sender)

        applied = feedback._send_with_sender(
            _Channel(), 'dana@corp.example', 'PROJ', ['author@x.example'], 'subj', 'body',
            plain_text=True,
        )
        self.assertEqual(applied, 'dana@corp.example')
        self.assertEqual(calls['sender'], 'dana@corp.example')

    def test_older_send_signature_still_sends(self):
        calls = {}

        class _OldChannel:
            def send(self, project_key, to, subject, body, plain_text=False):
                calls.update(project_key=project_key, to=to)

        applied = feedback._send_with_sender(
            _OldChannel(), 'dana@corp.example', 'PROJ', ['author@x.example'], 'subj', 'body',
            plain_text=True,
        )
        # Reported as the channel default, not silently claimed as the user.
        self.assertEqual(applied, '')
        self.assertEqual(calls['to'], ['author@x.example'])

    def test_empty_sender_never_reaches_send(self):
        seen = {}

        class _Channel:
            def send(self, project_key, to, subject, body, sender=None, plain_text=False):
                seen['kwargs_sender'] = sender

        feedback._send_with_sender(_Channel(), '', 'PROJ', ['a@x.example'], 's', 'b')
        self.assertIsNone(seen['kwargs_sender'])


class SenderRouteTest(unittest.TestCase):
    def _app(self, client):
        app = Flask(__name__)
        app.register_blueprint(feedback.bp)
        return app, mock.patch.object(feedback, '_local_thread_client', return_value=client)

    def test_get_reports_the_resolved_sender(self):
        client = _Client(email='dana@corp.example')
        app, patch = self._app(client)
        with patch:
            body = app.test_client().get('/api/feedback/sender').get_json()
        self.assertEqual(body['sender'], 'dana@corp.example')
        self.assertEqual(body['source'], 'user')

    def test_post_persists_the_override(self):
        client = _Client()
        app, patch = self._app(client)
        with patch:
            body = app.test_client().post(
                '/api/feedback/sender', json={'email': 'noreply@corp.example'},
            ).get_json()
        self.assertEqual(client.settings.get_raw()['config']['feedback_sender_email'],
                         'noreply@corp.example')
        self.assertTrue(client.settings.saved)
        self.assertEqual(body['source'], 'override')

    def test_post_rejects_a_malformed_address(self):
        client = _Client()
        app, patch = self._app(client)
        with patch:
            res = app.test_client().post('/api/feedback/sender', json={'email': 'nope'})
        self.assertEqual(res.status_code, 400)
        self.assertNotIn('feedback_sender_email', client.settings.get_raw()['config'])

    def test_empty_post_clears_the_override(self):
        client = _Client({'feedback_sender_email': 'noreply@corp.example'})
        app, patch = self._app(client)
        with patch:
            body = app.test_client().post('/api/feedback/sender', json={'email': ''}).get_json()
        self.assertEqual(client.settings.get_raw()['config']['feedback_sender_email'], '')
        self.assertEqual(body['source'], 'user')

    def test_the_write_route_is_advanced_gated(self):
        # Anyone reaching the webapp could otherwise redirect the sender.
        self.assertTrue(getattr(feedback.api_feedback_sender_set,
                                '_admin_toolkit_advanced', False))


if __name__ == '__main__':
    unittest.main()
