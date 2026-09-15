"""DSS runnable entry point, packaged by run_cobuild_model_suite.py."""
import json
import os
import pathlib
import sys
import traceback

import dataiku
from dataiku.runnables import Runnable


class ModelChecks(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.config = config

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        installed = pathlib.Path(os.environ['DIP_HOME']) / 'plugins' / 'installed'
        sys.path.insert(0, str(installed / 'admin-toolkit' / 'python-lib'))
        # __file__ is python-runnables/checks/runnable.py inside this plugin.
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / 'python-lib'))
        from cobuild_model_compare import ModelComparison
        from cobuild_model_suite import run_group
        from atk_agent_common import config
        from atk_agent_common.client import ToolkitClient
        run = ModelComparison.__new__(ModelComparison)
        run.dss = dataiku.api_client()
        raw = run.dss.get_plugin('admin-toolkit').get_settings().get_raw()['config']
        run.tk = ToolkitClient(config.resolve(raw))
        run.output, run.rows, run.only = pathlib.Path('results.json'), [], None
        run.model = run.dss.get_general_settings().get_raw()['localAIServerSettings']['mainLLMId']
        run.project = run.dss.get_project('ADMINTOOLKIT')
        error = None
        diagnostic = None
        try:
            selected = self.config.get('cases')
            run_group(run, self.config['group'], selected.split(',') if selected else None)
        except Exception as exc:
            error = type(exc).__name__
            diagnostic = traceback.format_exc()
        # Copy failed diagnostics only to the controller's private output. The
        # report renderer never publishes this field or full transcripts.
        failures = {}
        for path in pathlib.Path('.').glob('results-*-private.json'):
            value = json.loads(path.read_text())
            if value.get('status') != 'completed':
                failures[path.name] = value
        return json.dumps({'rows': run.rows, 'error': error, 'diagnostic': diagnostic,
                           'private_failures': failures})
