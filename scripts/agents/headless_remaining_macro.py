"""Owned akaos fixtures for the remaining whole-task Headless comparisons.

Installed only by the explicit live-test controller. No production imports.
The packaged manifest binds cloud mutations to one single-node cluster.
"""
import json
import os
import pathlib
import sys
import traceback

from dataiku.runnables import Runnable


class RemainingChecks(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.config = config

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        installed = pathlib.Path(os.environ['DIP_HOME']) / 'plugins' / 'installed'
        sys.path.insert(0, str(installed / 'admin-toolkit' / 'python-lib'))
        # DSS already adds this plugin's python-lib; runnable.py itself may
        # have been copied into a run directory, so do not derive assets from it.
        import headless_remaining_fixtures as fixtures
        manifest = json.loads(pathlib.Path(fixtures.__file__).with_name('fixture.json').read_text())
        try:
            return json.dumps(fixtures.dispatch(manifest, self.config))
        except Exception:
            return json.dumps({'error': 'fixture-failed', 'diagnostic': traceback.format_exc()})
