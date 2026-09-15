#!/usr/bin/env python3
"""Live comparisons using owned connections, data, webapps and scenarios."""
import argparse
import io
import secrets
import time

from cobuild_compare import Comparison, ROOT
from cobuild_fixture_compare import wait_for


def fixtures(run, selected=None):
    c, tk = run.dss, run.tk
    suffix = secrets.token_hex(4)
    key = 'ATKAB' + suffix.upper()
    name = 'atk_ab_' + suffix
    project = connection = None
    relay = None
    cases = {}
    try:
        project = c.create_project(key, 'ADTK data comparison ' + suffix, 'admin')
        run.save({'capability': '_fixtures', 'status': 'created', 'project': key})
        folder = project.create_managed_folder('comparison', folder_type='Filesystem')
        folder.put_file('fixture.csv', io.BytesIO(b'value\n7\n'))
        fsroot = c.get_connection('filesystem_folders').get_settings().get_raw()['params']['root']
        params = {'root': fsroot + '/' + key + '/' + folder.odb_id, 'dkuProperties': []}

        def reset_connection():
            nonlocal connection
            if name not in c.list_connections():
                connection = c.create_connection(name, 'Filesystem', params=params,
                                                 usable_by='ALLOWED', allowed_groups=['administrators'],
                                                 description='ADTK comparison before')
            else:
                connection = c.get_connection(name)
                settings = connection.get_settings()
                settings.get_raw()['description'] = 'ADTK comparison before'
                settings.save()
            tk.post('/api/cache/clear')

        reset_connection()

        def verify_connection_test(result):
            data = result.get('result', {})
            assert data.get('connectionOK') is True or (data.get('result') or {}).get('connectionOK') is True
            return {'connectionOK': True}

        def verify_connection_update(_):
            assert connection.get_settings().get_raw()['description'] == 'ADTK comparison after'
            return {'description_updated': True}

        def verify_connection_delete(_):
            assert name not in c.list_connections()
            return {'owned_connection_deleted': True}

        # DSS does not implement a positive connectivity test for every
        # filesystem connection. Use its existing SQL test, which is read-only.
        cases['connection-test'] = ({'name': 'kaosdb'}, lambda: None, verify_connection_test,
                                    'Read-only DSS connectivity test of akaos kaosdb connection')
        cases['connection-update'] = ({'name': name, 'path': 'description', 'newValue': 'ADTK comparison after'},
                                      reset_connection, verify_connection_update, 'Description of disposable connection')
        cases['connection-delete'] = ({'name': name}, reset_connection, verify_connection_delete,
                                      'Unused disposable connection; definition backup retained by ADTK')

        dataset = None

        def reset_dataset():
            nonlocal dataset
            if 'comparison' not in [d['name'] for d in project.list_datasets()]:
                dataset = project.create_upload_dataset('comparison')
            else:
                dataset = project.get_dataset('comparison')
            dataset.uploaded_add_file(io.BytesIO(b'value\n7\n'), 'comparison.csv')

        def verify_clear(_):
            assert dataset.uploaded_list_files() == []
            assert 'comparison' in [d['name'] for d in project.list_datasets()]
            return {'definition_present': True, 'uploaded_files': 0}

        def verify_delete(_):
            assert 'comparison' not in [d['name'] for d in project.list_datasets()]
            return {'dataset_deleted': True}

        cases['dataset-clear'] = ({'projectKey': key, 'datasetName': 'comparison'}, reset_dataset,
                                  verify_clear, 'Uploaded dataset containing only synthetic CSV')
        cases['dataset-delete'] = ({'projectKey': key, 'datasetName': 'comparison', 'dropData': True}, reset_dataset,
                                   verify_delete, 'Unreferenced synthetic CSV dataset')

        webapp = project.create_webapp('Comparison backend')
        settings = webapp.get_settings()
        settings.get_raw()['params'].update({'backendEnabled': True, 'autoStartBackend': False,
                                             'backendAPIAccessEnabled': True,
                                             'python': "import uuid\nfrom flask import jsonify\nRUN_ID = str(uuid.uuid4())\n@app.route('/probe')\ndef probe():\n    return jsonify(run=RUN_ID)\n"})
        settings.save()

        def start_webapp():
            if not webapp.get_state().running:
                webapp.start_or_restart_backend().wait_for_result()
            wait_for(lambda: webapp.get_state().running)

        def verify_stopped(_):
            wait_for(lambda: not webapp.get_state().running)
            return {'backend_running': False}

        def stop_webapp():
            webapp.stop_backend()
            wait_for(lambda: not webapp.get_state().running)

        def verify_running(_):
            wait_for(lambda: webapp.get_state().running)
            backend = webapp.get_backend_client()
            def probe_ready():
                response = backend.session.get(backend.url_for_path('probe'), timeout=15)
                return response.status_code == 200 and response.json().get('run')
            assert wait_for(probe_ready, timeout=90)
            return {'backend_running': True, 'probe_responded': True}

        cases['webapp-backend-stop'] = ({'projectKey': key, 'webappId': webapp.webapp_id}, start_webapp,
                                       verify_stopped, 'Disposable Flask backend')
        cases['webapp-backend-restart'] = ({'projectKey': key, 'webappId': webapp.webapp_id}, stop_webapp,
                                          verify_running, 'Start disposable Flask backend from stopped state')

        scenario = project.create_scenario('Comparison manual run', 'step_based',
                                           {'active': False, 'triggers': [], 'params': {'steps': []}})
        before_runs = set()

        def reset_scenario():
            nonlocal before_runs
            assert not scenario.get_status().running
            before_runs = {r.id for r in scenario.get_last_runs()}

        def verify_run(_):
            def new_finished():
                for r in scenario.get_last_runs():
                    if r.id not in before_runs and not r.running:
                        assert r.outcome == 'SUCCESS'
                        return {'new_run_finished': True, 'outcome': r.outcome}
                return False
            return wait_for(new_finished)

        cases['scenario-run'] = ({'projectKey': key, 'scenarioId': scenario.id}, reset_scenario,
                                 verify_run, 'Empty trigger-free disposable scenario')

        relay_name = 'ADTK comparison ' + suffix

        def reset_relay():
            nonlocal relay
            for item in c.get_project('ADMINTOOLKIT').list_scenarios():
                if item.get('name') == relay_name:
                    c.get_project('ADMINTOOLKIT').get_scenario(item['id']).delete()
            relay = None

        def verify_relay(_):
            nonlocal relay
            rows = [s for s in c.get_project('ADMINTOOLKIT').list_scenarios() if s.get('name') == relay_name]
            assert len(rows) == 1
            relay = c.get_project('ADMINTOOLKIT').get_scenario(rows[0]['id'])
            raw = relay.get_settings().get_raw()
            assert raw['active'] is False and not raw.get('triggers') and not raw['params'].get('steps')
            return {'scenario_created': True, 'active': False, 'steps': 0, 'triggers': 0}

        cases['toolkit-scenario-write'] = ({'name': relay_name, 'steps': [], 'active': False}, reset_relay,
                                           verify_relay, 'New empty inactive scenario in ADMINTOOLKIT; removed afterward')
        tk.post('/api/cache/clear')
        chosen = {name: case for name, case in cases.items() if selected is None or name in selected}
        with run.gates(list(chosen)):
            for action, case in chosen.items():
                run.action(action, *case)
    finally:
        errors = []
        if relay is not None:
            try:
                relay.delete()
            except Exception as exc:
                errors.append('relay: ' + type(exc).__name__)
        if connection is not None:
            try:
                if name in c.list_connections():
                    c.get_connection(name).delete()
            except Exception as exc:
                errors.append('connection: ' + type(exc).__name__)
        if project is not None:
            try:
                project.delete(clear_managed_datasets=True, clear_output_managed_folders=True)
            except Exception as exc:
                errors.append('project: ' + type(exc).__name__)
        run.save({'capability': '_fixtures', 'status': 'cleanup_failed' if errors else 'deleted',
                  'project': key, 'errors': errors})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--output', required=True)
    parser.add_argument('--action', action='append')
    parser.add_argument('--legacy-operation-bridge', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        parser.error('--execute is required')
    if not args.legacy_operation_bridge:
        parser.error('Use run_cobuild_model_suite.py --execute --group data --output <directory>; '
                     'the old local bridge requires --legacy-operation-bridge')
    fixtures(Comparison(ROOT / '.dss-url', ROOT / '.dss-api-key', args.output), args.action)
