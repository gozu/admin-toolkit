"""/api/mode's stale-backend verdict.

DSS leaves a running webapp backend untouched when its plugin is updated, so
the installed version (read live from the DSS API) and the version this process
was built from can disagree. The route turns that into `backendStale`; these
tests pin the fail-open half of the rule, which is the half a false alarm would
come from — the gate it feeds is not dismissible.
"""

from __future__ import annotations

import unittest
from unittest import mock

from flask import Flask

from adk_backend.routes import misc


def call_mode(installed, build_version='0.4.900'):
    """The route's JSON, with both version sources controlled."""
    app = Flask(__name__)
    app.register_blueprint(misc.bp)
    with mock.patch.object(misc, '_plugin_version', return_value=installed), \
            mock.patch.object(misc, 'BUILD_VERSION', build_version):
        with app.test_client() as client:
            return client.get('/api/mode').get_json()


class ModeBackendStalenessTest(unittest.TestCase):
    def test_matching_versions_are_not_stale(self):
        body = call_mode('0.4.900', '0.4.900')
        self.assertFalse(body['backendStale'])
        self.assertEqual(body['runningVersion'], '0.4.900')
        self.assertEqual(body['version'], '0.4.900')

    def test_installed_ahead_of_running_is_stale(self):
        # The upgrade-without-restart case: new plugin on disk, old code live.
        body = call_mode('0.4.901', '0.4.900')
        self.assertTrue(body['backendStale'])

    def test_unknown_installed_version_never_claims_stale(self):
        # _plugin_version() returns '' when the DSS plugin lookup fails. An
        # unreachable API must not brick the app behind the gate.
        self.assertFalse(call_mode('')['backendStale'])

    def test_running_version_is_always_reported(self):
        # Absence of this field is itself the frontend's staleness signal for
        # pre-0.4.797 backends, so a live backend must never omit it.
        self.assertIn('runningVersion', call_mode(''))

    def test_mode_stays_live(self):
        self.assertEqual(call_mode('0.4.900')['mode'], 'live')


if __name__ == '__main__':
    unittest.main()
