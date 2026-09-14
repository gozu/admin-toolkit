"""Move one unused recipe between two owned environments, with no code run."""
import secrets


def fixtures(run):
    c, tk = run.dss, run.tk
    suffix = secrets.token_hex(4)
    key = 'ATKAB' + suffix.upper()
    names = ['atk_ab_src_' + suffix, 'atk_ab_dst_' + suffix]
    project = None
    try:
        for name in names:
            c.create_code_env('PYTHON', name, 'DESIGN_MANAGED', params={
                'pythonInterpreter': 'PYTHON311', 'installCorePackages': False,
                'installJupyterSupport': False, 'desc': {'installCorePackages': False,
                                                       'installJupyterSupport': False}})
        project = c.create_project(key, 'ADTK environment migration ' + suffix, 'admin')
        project.new_managed_dataset('output').with_store_into('filesystem_managed').create()
        recipe = project.new_recipe('python', 'comparison').with_output('output').create()

        def reset():
            settings = recipe.get_settings()
            settings.set_code_env(names[0])
            settings.save()
            tk.post('/api/cache/clear')

        def verify(_):
            # This SDK read is independent of the action response.
            selection = recipe.get_settings().get_recipe_params()['envSelection']
            assert selection['envName'] == names[1]
            return {'owned_recipe_uses_target_env': True, 'source_retained': True,
                    'recipe_executed': False}

        def verify_index(result):
            assert result['result']['indexed'] == ['kaosdb']
            assert result['result']['ok']
            return {'requested_connection_index_completed': True, 'connection': 'kaosdb'}

        with run.gates(['code-env-consolidate', 'connection-index']):
            run.action('code-env-consolidate', {'sourceEnvName': names[0], 'targetEnvName': names[1],
                'language': 'python', 'projectKeys': [key], 'retireSource': False}, reset, verify,
                'One unused Python recipe moved between owned environments; no code executed')
            run.action('connection-index', {'connectionNames': ['kaosdb']}, lambda: None, verify_index,
                'Read-only metadata crawl of kaosdb; completion checked, catalog contents not compared')
    finally:
        errors = []
        if project:
            try:
                project.delete()
            except Exception as exc:
                errors.append('project: ' + type(exc).__name__)
        for name in names:
            try:
                if name in {r['envName'] for r in c.list_code_envs()}:
                    c.get_code_env('PYTHON', name).delete()
            except Exception as exc:
                errors.append('environment: ' + type(exc).__name__)
        run.save({'capability': '_fixtures', 'status': 'cleanup_failed' if errors else 'deleted',
                  'project': key, 'errors': errors})
