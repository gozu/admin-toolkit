"""Extra fixtures intended to run in DSS, where dataiku.api_client is real."""
import copy
import io
import secrets
import zipfile
from urllib.parse import urlsplit


def fixtures(run):
    c, tk = run.dss, run.tk
    suffix = secrets.token_hex(4)
    key = 'ATKAB' + suffix.upper()
    config_name = 'atk-ab-' + suffix
    project = None
    try:
        project = c.create_project(key, 'ADTK native comparison ' + suffix, 'admin')
        run.save({'capability': '_fixtures', 'status': 'created', 'project': key})
        general = c.get_general_settings()
        configs = general.get_raw()['containerSettings'].setdefault('executionConfigs', [])
        template = next(row for row in configs if row.get('type') == 'KUBERNETES')
        owned = copy.deepcopy(template)
        owned['name'] = config_name
        owned['usableBy'] = 'ALLOWED'
        owned['allowedGroups'] = ['administrators']
        configs.append(owned)
        general.save()

        def reset_config():
            settings = c.get_general_settings()
            row = next(x for x in settings.get_raw()['containerSettings']['executionConfigs'] if x['name'] == config_name)
            row.setdefault('kubernetesRuntimeConfig', {}).setdefault('kubernetesResources', {}).update(
                {'memRequestMB': 32, 'memLimitMB': 64, 'cpuRequest': 0.1, 'cpuLimit': 0.2})
            settings.save()
            tk.post('/api/cache/clear')

        def config_target():
            rows = c.get_general_settings().get_raw()['containerSettings']['executionConfigs']
            i = next(i for i, x in enumerate(rows) if x['name'] == config_name)
            return {'path': 'containerSettings.executionConfigs[%d].kubernetesRuntimeConfig.kubernetesResources.memLimitMB' % i,
                    'newValue': 128}

        def verify_config(_):
            rows = c.get_general_settings().get_raw()['containerSettings']['executionConfigs']
            resources = next(x for x in rows if x['name'] == config_name)['kubernetesRuntimeConfig']['kubernetesResources']
            assert resources['memLimitMB'] == 128
            return {'owned_execution_config_memLimitMB': 128}

        def reset_variable():
            project.set_variables({'standard': {'audit_test': 'before'}, 'local': {}})
            tk.post('/api/cache/clear')

        def verify_audit(result):
            assert project.get_variables()['standard']['audit_test'] == 'after'
            assert result.get('auditId') is not None and result.get('settingsHistory', {}).get('recorded') == 1
            return {'value_changed': True, 'audit_written': True, 'history_written': True}

        def verify_export(result):
            filename = result['result']['backupFile']
            folder_id = tk.get('/api/managed-folders')['folders'][0]['id']
            parts = urlsplit(tk.base_url).path.strip('/').split('/')
            support_key = parts[parts.index('web-apps-backends') + 1]
            with c.get_project(support_key).get_managed_folder(folder_id).get_file(filename) as stream:
                archive = zipfile.ZipFile(io.BytesIO(stream.content))
                assert 'export-manifest.json' in archive.namelist()
            return {'archive_readable': True, 'project_definition_present': True}

        cases = {
            'project-variables-set': ({'projectKey': key, 'path': 'standard.audit_test', 'newValue': 'after'},
                                      reset_variable, verify_audit, 'DSS runtime: value, audit row and settings history checked'),
            'settings-set': (config_target, reset_config, verify_config, 'Memory limit on unused disposable execution config'),
            'k8s-exec-config-tune': ({'configName': config_name, 'changes': {'memLimitMB': 128}}, reset_config,
                                     verify_config, 'Memory limit on unused disposable execution config'),
            'project-export': ({'projectKey': key}, lambda: tk.post('/api/cache/clear'), verify_export,
                                'Export disposable project and inspect generated ZIP'),
        }
        cluster_id = 'atk-cobuild-20260914'
        if any(row['id'] == cluster_id for row in c.list_clusters()):
            def reset_cluster():
                s = project.get_settings()
                s.get_raw().setdefault('settings', {}).pop('k8sCluster', None)
                s.save()
                tk.post('/api/cache/clear')

            def verify_cluster(_):
                assert project.get_settings().get_raw()['settings']['k8sCluster']['clusterId'] == cluster_id
                return {'project_selected_fixture_cluster': True}

            cases['project-set-cluster'] = ({'projectKey': key, 'clusterId': cluster_id}, reset_cluster,
                                             verify_cluster, 'Select test cluster on disposable project; no workload launched')
        with run.gates(list(cases)):
            for action, case in cases.items():
                if getattr(run, 'only', None) and action not in run.only:
                    continue
                run.action(action, *case)
    finally:
        errors = []
        try:
            general = c.get_general_settings()
            configs = general.get_raw()['containerSettings']['executionConfigs']
            configs[:] = [row for row in configs if row.get('name') != config_name]
            general.save()
        except Exception as exc:
            errors.append('execution config: ' + type(exc).__name__)
        if project:
            try:
                project.delete(clear_managed_datasets=True, clear_output_managed_folders=True)
            except Exception as exc:
                errors.append('project: ' + type(exc).__name__)
        run.save({'capability': '_fixtures', 'status': 'cleanup_failed' if errors else 'deleted',
                  'project': key, 'errors': errors})
