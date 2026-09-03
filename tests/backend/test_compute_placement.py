"""Unit tests for Compute Placement (AI Compute → Compute Placement).

Resolution chain and the migration planner are pure; the scan is exercised
against a fake client that mirrors the verified DSS 14.7 shapes (see
docs/dss-api-reference/containerized-execution-object-scan.md).
"""

from adk_backend.routes import compute_placement as cp


# ---- resolution ----

def test_declared_treats_missing_block_as_inherit():
    assert cp._declared(None) == ('INHERIT', None)
    assert cp._declared({}) == ('INHERIT', None)
    assert cp._declared({'containerMode': 'NONE'}) == ('NONE', None)
    assert cp._declared({'containerMode': 'EXPLICIT_CONTAINER', 'containerConf': 'eks'}) == ('EXPLICIT_CONTAINER', 'eks')
    # explicit mode with no config name cannot run in a container
    assert cp._declared({'containerMode': 'EXPLICIT_CONTAINER'}) == ('NONE', None)


def test_object_inherits_through_project_to_instance_default():
    project_default = cp._resolve_project_default({'containerMode': 'INHERIT'}, 'eks-default')
    assert project_default['effectiveConf'] == 'eks-default'
    assert project_default['resolvedFrom'] == 'instance'
    obj = cp._resolve_object({'containerMode': 'INHERIT'}, project_default)
    assert obj == {'containerMode': 'INHERIT', 'containerConf': None,
                   'effectiveConf': 'eks-default', 'resolvedFrom': 'instance'}


def test_project_none_pins_inheriting_objects_local():
    project_default = cp._resolve_project_default({'containerMode': 'NONE'}, 'eks-default')
    obj = cp._resolve_object(None, project_default)
    assert obj['effectiveConf'] is None
    assert obj['resolvedFrom'] == 'project'


def test_no_instance_default_means_inherit_is_local():
    project_default = cp._resolve_project_default(None, None)
    assert project_default['effectiveConf'] is None
    assert cp._resolve_object({'containerMode': 'INHERIT'}, project_default)['effectiveConf'] is None


def test_cluster_resolution_falls_back_to_instance_default():
    explicit = cp._resolve_cluster({'settings': {'k8sCluster': {'clusterMode': 'EXPLICIT_CLUSTER', 'clusterId': 'eks-a'}}}, 'eks-global')
    assert explicit['effectiveClusterId'] == 'eks-a' and explicit['clusterSource'] == 'project'
    inherit = cp._resolve_cluster({'settings': {'k8sCluster': {'clusterMode': 'INHERIT'}}}, 'eks-global')
    assert inherit['effectiveClusterId'] == 'eks-global' and inherit['clusterSource'] == 'instance'
    assert cp._resolve_cluster({}, None)['effectiveClusterId'] is None


def test_kernel_placement_heuristic():
    assert cp._kernel_placement('python3') == 'local'
    assert cp._kernel_placement('py-dku-venv-myenv') == 'local'
    assert cp._kernel_placement('py-dku-containerized-venv-myenv-eks-default') == 'container'


# ---- scan against a fake client ----

class _Settings:
    def __init__(self, raw):
        self._raw = raw
        self.saved = False

    def get_raw(self):
        return self._raw

    def save(self):
        self.saved = True


class _Webapp:
    def __init__(self, raw):
        self._raw = raw

    def get_settings(self):
        return _Settings(self._raw)


class _Project:
    def __init__(self, key, settings_raw, recipes, webapps, notebooks):
        self.key = key
        self.settings = _Settings(settings_raw)
        self._recipes = recipes
        self._webapps = webapps
        self._notebooks = notebooks

    def get_settings(self):
        return self.settings

    def list_recipes(self):
        return [{'name': n, 'type': t, 'versionTag': {'lastModifiedBy': {'login': owner}}}
                for n, t, owner, _ in self._recipes]

    def list_webapps(self):
        return [{'id': wid, 'name': name, 'type': 'STANDARD'} for wid, name, _ in self._webapps]

    def get_webapp(self, wid):
        for w, name, sel in self._webapps:
            if w == wid:
                return _Webapp({'name': name, 'type': 'STANDARD', 'params': {'infra': {'containerSelection': sel}}})
        raise KeyError(wid)

    def list_jupyter_notebooks(self, as_type='listitems'):
        return [{'name': n, 'kernelSpec': {'name': k}} for n, k in self._notebooks]


class _FakeClient:
    def __init__(self):
        self.projects = {
            'SALES': _Project(
                'SALES',
                {'settings': {
                    'container': {'containerMode': 'NONE'},
                    'containerForVisualRecipesWorkloads': {'containerMode': 'INHERIT'},
                    'virtualWebAppBackendSettings': {'infra': {'containerSelection': {'containerMode': 'INHERIT'}}},
                    'k8sCluster': {'clusterMode': 'INHERIT'},
                }},
                recipes=[
                    ('compute_local', 'python', 'ana', {'containerMode': 'INHERIT'}),
                    ('compute_k8s', 'python', 'bob', {'containerMode': 'EXPLICIT_CONTAINER', 'containerConf': 'eks-default'}),
                    ('prep', 'shaker', None, None),
                    ('big_join', 'pyspark', 'ana', None),
                ],
                webapps=[('w1', 'Dash app', {'containerMode': 'NONE'})],
                notebooks=[('explore', 'python3'), ('train', 'py-dku-containerized-venv-ml-eks-default')],
            ),
        }
        self.users = [{'login': 'ana', 'email': 'ana@corp.example', 'displayName': 'Ana'},
                      {'login': 'bob', 'email': 'bob@corp.example', 'displayName': 'Bob'}]

    def get_general_settings(self):
        return _Settings({
            'containerSettings': {
                'executionConfigs': [{'name': 'eks-default', 'type': 'KUBERNETES'},
                                     {'name': 'docker-local', 'type': 'DOCKER'}],
                'defaultExecutionConfig': 'eks-default',
            },
            'defaultK8sClusterId': 'eks-global',
        })

    def list_clusters(self):
        return [{'id': 'eks-global', 'name': 'EKS', 'type': 'eks', 'state': {'state': 'RUNNING'}}]

    def list_users(self):
        return self.users

    def list_projects(self):
        return [{'projectKey': k, 'name': k.title(), 'ownerLogin': 'ana'} for k in self.projects]

    def get_project(self, key):
        return self.projects[key]

    def _perform_json(self, method, path, body=None):
        if path.endswith('/models/lab/'):
            return {'mlTasks': [{'analysisId': 'a1', 'mlTaskId': 't1', 'mlTaskName': 'churn', 'taskType': 'PREDICTION'}]}
        if '/models/lab/' in path:
            return {'containerSelection': {'containerMode': 'INHERIT'}}
        if '/recipes/' in path:
            name = path.rsplit('/', 1)[-1]
            project = self.projects[path.split('/')[2]]
            for n, t, _, sel in project._recipes:
                if n == name:
                    if t == 'shaker':
                        return {'recipe': {'type': t, 'params': {'engineType': 'DSS', 'engineParams': {'containerSelection': {'containerMode': 'INHERIT'}}}}}
                    return {'recipe': {'type': t, 'params': {'containerSelection': sel} if sel else {}}}
        raise AssertionError(path)


def _rows_by_name(scan):
    return {(r['objectType'], r['objectName']): r for r in scan['rows']}


def test_scan_resolves_every_surface(monkeypatch):
    monkeypatch.setattr(cp, '_list_projects_catalog_cheap',
                        lambda client: [{'key': 'SALES', 'name': 'Sales', 'owner': 'ana'}])
    scan = cp._scan(_FakeClient())
    rows = _rows_by_name(scan)

    # project code default is NONE → inheriting python recipe is local
    local = rows[('RECIPE', 'compute_local')]
    assert local['placement'] == 'local' and local['resolvedFrom'] == 'project'
    assert local['migratable'] is True
    assert local['owner'] == 'ana' and local['ownerEmail'] == 'ana@corp.example'

    # explicit object override wins, K8s config → effective cluster from instance default
    k8s = rows[('RECIPE', 'compute_k8s')]
    assert k8s['placement'] == 'container' and k8s['effectiveConf'] == 'eks-default'
    assert k8s['configType'] == 'KUBERNETES'
    assert k8s['clusterId'] == 'eks-global' and k8s['clusterSource'] == 'instance'
    assert k8s['owner'] == 'bob'

    # visual recipe inherits the visual default → instance default → container
    prep = rows[('RECIPE', 'prep')]
    assert prep['surface'] == 'recipe_visual' and prep['placement'] == 'container'
    assert prep['resolvedFrom'] == 'instance'
    # no versionTag login → project owner
    assert prep['owner'] == 'ana' and prep['ownerSource'] == 'project'

    # spark recipe is its own engine, never migratable, carries the cluster
    spark = rows[('RECIPE', 'big_join')]
    assert spark['placement'] == 'spark' and spark['migratable'] is False
    assert spark['clusterId'] == 'eks-global'

    # webapp explicit NONE → local, migratable
    webapp = rows[('WEBAPP', 'Dash app')]
    assert webapp['placement'] == 'local' and webapp['containerMode'] == 'NONE' and webapp['migratable']

    # ML task inherits the CODE default (NONE here) → local
    task = rows[('ML_TASK', 'churn')]
    assert task['placement'] == 'local' and task['objectId'] == 'a1/t1'
    assert task['extra']['taskType'] == 'PREDICTION'

    # notebooks: kernel-derived, informational only
    assert rows[('NOTEBOOK', 'explore')]['placement'] == 'local'
    assert rows[('NOTEBOOK', 'explore')]['migratable'] is False
    assert rows[('NOTEBOOK', 'train')]['placement'] == 'container'

    # three project-default rows, sorted first
    assert [r['surface'] for r in scan['rows'][:3]] == [
        'project_code_default', 'project_visual_default', 'project_webapp_default']

    summary = scan['summary']
    assert summary['objectRowCount'] == 8
    assert summary['byPlacement'] == {'local': 4, 'container': 3, 'spark': 1}
    assert summary['migratableCount'] == 3 + 1  # 3 object rows + the NONE project code default
    assert summary['localOwnerCount'] == 1
    assert scan['configTypes'] == {'eks-default': 'KUBERNETES', 'docker-local': 'DOCKER'}
    assert scan['clusters'][0]['state'] == 'RUNNING'
    assert scan['failedProjectCount'] == 0


# ---- migration planner ----

def _scan_fixture(monkeypatch):
    monkeypatch.setattr(cp, '_list_projects_catalog_cheap',
                        lambda client: [{'key': 'SALES', 'name': 'Sales', 'owner': 'ana'}])
    return cp._scan(_FakeClient())


def test_plan_objects_strategy_pins_each_row_and_sets_cluster(monkeypatch):
    scan = _scan_fixture(monkeypatch)
    rows = _rows_by_name(scan)
    ids = {rows[('RECIPE', 'compute_local')]['id'], rows[('WEBAPP', 'Dash app')]['id'],
           rows[('RECIPE', 'compute_k8s')]['id'],      # already containerized → ignored
           rows[('NOTEBOOK', 'explore')]['id']}        # local but not migratable → ignored
    matched, ops = cp._plan_migration(scan, ids, 'eks-default', 'eks-global', 'objects')
    assert sorted(r['objectName'] for r in matched) == ['Dash app', 'compute_local']
    kinds = sorted((o['kind'], o['objectName']) for o in ops)
    assert kinds == [('object-explicit', 'Dash app'), ('object-explicit', 'compute_local'),
                     ('project-cluster', 'Sales')]
    cluster_op = next(o for o in ops if o['kind'] == 'project-cluster')
    assert cluster_op['from'] == 'INHERIT' and cluster_op['to'] == 'eks-global'
    assert all(o['status'] == 'planned' for o in ops)


def test_plan_project_defaults_strategy_flips_family_and_unpins_none(monkeypatch):
    scan = _scan_fixture(monkeypatch)
    rows = _rows_by_name(scan)
    ids = {rows[('RECIPE', 'compute_local')]['id'], rows[('WEBAPP', 'Dash app')]['id'],
           rows[('ML_TASK', 'churn')]['id']}
    _, ops = cp._plan_migration(scan, ids, 'eks-default', None, 'project-defaults')
    by_kind = {}
    for o in ops:
        by_kind.setdefault(o['kind'], []).append(o)
    # recipe + ML task share the code default → ONE project-default op; the
    # webapp default already resolves to eks-default via INHERIT → no op.
    assert [o['surface'] for o in by_kind['project-default']] == ['project_code_default']
    # explicit NONE webapp is unpinned; inheriting rows are reported unchanged
    assert [o['objectName'] for o in by_kind['object-inherit']] == ['Dash app']
    assert sorted(o['objectName'] for o in by_kind['object-unchanged']) == ['churn', 'compute_local']
    assert 'project-cluster' not in by_kind


def test_plan_skips_cluster_op_when_project_already_explicit(monkeypatch):
    scan = _scan_fixture(monkeypatch)
    for p in scan['projects']:
        p['cluster'] = {'clusterMode': 'EXPLICIT_CLUSTER', 'clusterId': 'eks-global', 'effectiveClusterId': 'eks-global', 'clusterSource': 'project'}
    rows = _rows_by_name(scan)
    _, ops = cp._plan_migration(scan, {rows[('RECIPE', 'compute_local')]['id']}, 'eks-default', 'eks-global', 'objects')
    assert [o['kind'] for o in ops] == ['object-explicit']


def test_apply_op_routes_inherit_and_cluster(monkeypatch):
    calls = []
    monkeypatch.setattr(cp, '_cex_apply_replace_row',
                        lambda client, row, to, browser_ctx=None, diag=None: calls.append((row['surface'], to)))
    client = _FakeClient()
    cp._apply_op(client, {'kind': 'object-inherit', 'projectKey': 'SALES', 'objectId': 'w1',
                          'surface': 'webapp_backend', 'rawPath': 'params.infra.containerSelection'}, 'eks-default', {})
    cp._apply_op(client, {'kind': 'object-explicit', 'projectKey': 'SALES', 'objectId': 'a1/t1',
                          'surface': 'ml_task', 'rawPath': 'containerSelection'}, 'eks-default', {})
    cp._apply_op(client, {'kind': 'project-cluster', 'projectKey': 'SALES', 'to': 'eks-global'}, 'eks-default', {})
    assert calls == [('webapp_backend', '__INHERIT__'), ('ml_task', 'eks-default')]
    saved = client.projects['SALES'].settings
    assert saved.saved is True
    assert saved.get_raw()['settings']['k8sCluster'] == {'clusterMode': 'EXPLICIT_CLUSTER', 'clusterId': 'eks-global'}


def test_filtered_scan_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(cp, '_MAX_FILTERED_SCANS', 2)
    monkeypatch.setattr(cp, '_cache_key', lambda key: key)
    for stale in [k for k in cp._CACHE if str(k).startswith('compute_placement')]:
        cp._CACHE.pop(stale, None)
    cp._store_scan('compute_placement', {'full': True})
    for i, digest in enumerate(('aaa', 'bbb', 'ccc')):
        cp._store_scan(f'compute_placement:{digest}', {'i': i})
        cp._CACHE[f'compute_placement:{digest}']['ts'] = i
    kept = sorted(k for k in cp._CACHE if str(k).startswith('compute_placement'))
    assert kept == ['compute_placement', 'compute_placement:bbb', 'compute_placement:ccc']
