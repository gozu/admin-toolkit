"""Bounded, host-local live fixtures; credentials and transcripts stay private."""
import hashlib
import json
import pathlib
import subprocess
import time


def aws(manifest, args, missing=False):
    proc = subprocess.run(['aws', '--region', manifest['region']] + args + ['--output', 'json'],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode:
        if missing and ('ResourceNotFoundException' in proc.stderr or 'does not exist' in proc.stderr):
            return None
        raise RuntimeError(proc.stderr[:500])
    return json.loads(proc.stdout or '{}')


def cloud(manifest, prepare_stop=False):
    cid = manifest['cluster']
    stacks = []
    for name in ('eksctl-' + cid + '-cluster', 'eksctl-' + cid + '-nodegroup-one-small-node'):
        response = aws(manifest, ['cloudformation', 'describe-stacks', '--stack-name', name], True)
        row = response['Stacks'][0] if response else None
        if row:
            tags = {t['Key']: t['Value'] for t in row.get('Tags', [])}
            if tags.get('ADTKRun') != manifest['run']:
                raise PermissionError('Cloud stack ownership mismatch')
            if prepare_stop:
                aws(manifest, ['cloudformation', 'update-termination-protection', '--stack-name', name,
                               '--no-enable-termination-protection'])
        stacks.append(row['StackStatus'] if row else None)
    response = aws(manifest, ['ec2', 'describe-instances', '--filters', 'Name=tag:eks:cluster-name,Values=' + cid])
    instances = [{'id': i['InstanceId'], 'type': i['InstanceType'], 'state': i['State']['Name']}
                 for r in response['Reservations'] for i in r['Instances'] if i['State']['Name'] != 'terminated']
    if len(instances) > 1 or any(i['type'] != 't3.small' for i in instances):
        raise RuntimeError('One t3.small node limit violated')
    eks = aws(manifest, ['eks', 'describe-cluster', '--name', cid, '--query', 'cluster.status'], True)
    return {'eks_state': eks, 'instances': instances, 'stacks': stacks,
            'cloud_absent': eks is None and not instances and not any(stacks)}


def comparison():
    import dataiku
    from headless_model_compare import HeadlessComparison
    from atk_agent_common import config
    from atk_agent_common.client import ToolkitClient
    run = HeadlessComparison.__new__(HeadlessComparison)
    run.dss = dataiku.api_client()
    raw = run.dss.get_plugin('admin-toolkit').get_settings().get_raw()['config']
    run.tk = ToolkitClient(config.resolve(raw))
    run.output, run.rows, run.only = pathlib.Path('results.json'), [], None
    run.model = run.dss.get_general_settings().get_raw()['localAIServerSettings']['mainLLMId']
    run.project = run.dss.get_project('ADMINTOOLKIT')
    return run


def trial(run, name, target, provider):
    from cobuild_model_compare import TaskExecutor, task_loop, canonical
    from render_cobuild_comparison import PURPOSE
    instructions, schemas = run._context()
    executor = TaskExecutor(run.tk, name, target, action=True, approve_fixture=True)
    question = ('Objective: ' + PURPOSE[name] + '.\nFixture target: ' + json.dumps(target)
                + '\nScope: Only this operator-authorized disposable fixture. '
                'Choose ADTK plan and execute tools; await controller authorization; '
                'never repeat an attempted execution. Give a factual final answer.')
    with run.gates([name]):
        result = task_loop(run.project, run.model, provider, instructions, schemas, question, executor)
    result['context_sha256'] = hashlib.sha256((instructions + canonical(schemas)).encode()).hexdigest()
    result['operation_result'] = executor.result
    return result


def kubernetes(run, manifest):
    cid = manifest['cluster']
    cluster = run.dss.get_cluster(cid)
    namespace = 'atk-headless-test'
    pod_name = 'atk-headless-finished'
    def kubectl(command):
        value = cluster.run_kubectl(command)
        if value['returnValue'] != 0:
            raise RuntimeError(value.get('output', '')[:500])
        return value.get('output', '')
    nodes = json.loads(kubectl('get nodes -o json'))['items']
    assert len(nodes) == 1 and nodes[0]['metadata']['labels']['node.kubernetes.io/instance-type'] == 't3.small'
    kubectl('create namespace ' + namespace)
    def reset_map():
        kubectl('delete configmap comparison -n ' + namespace + ' --ignore-not-found=true')
        kubectl('create configmap comparison -n ' + namespace + ' --from-literal=value=before')
    def verify_map(_):
        value = json.loads(kubectl('get configmap comparison -n ' + namespace + ' -o json'))
        assert value['metadata']['labels']['comparison'] == 'after'
        return {'owned_configmap_label': 'after', 'nodes': 1, 'instance_type': 't3.small'}
    def reset_pod():
        kubectl('delete pod ' + pod_name + ' -n default --ignore-not-found=true --wait=true')
        kubectl('run ' + pod_name + ' -n default --image=public.ecr.aws/docker/library/busybox:1.37 --restart=Never --command -- true')
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            pods = json.loads(kubectl('get pods -n default -o json'))['items']
            finished = [p for p in pods if p['status'].get('phase') in ('Succeeded', 'Failed')]
            if any(p['metadata']['name'] != pod_name for p in finished):
                raise PermissionError('Cleanup would include unowned finished pods')
            jobs = json.loads(kubectl('get jobs -n default -o json'))['items']
            if jobs:
                raise PermissionError('Unexpected jobs in cleanup namespace')
            if any(p['metadata']['name'] == pod_name and p['status'].get('phase') == 'Succeeded' for p in pods):
                return
            time.sleep(3)
        raise TimeoutError('Owned pod did not complete')
    def verify_pod(_):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            pods = json.loads(kubectl('get pods -n default -o json'))['items']
            if not any(p['metadata']['name'] == pod_name for p in pods):
                return {'completed_owned_pod_deleted': True, 'nodes': 1, 'instance_type': 't3.small'}
            time.sleep(2)
        raise TimeoutError('Pod deletion not observed')
    try:
        with run.gates(['k8s-apply-fix', 'cluster-pods-cleanup']):
            run.action('k8s-apply-fix', {'clusterId': cid, 'commands': [
                'label configmap comparison comparison=after --overwrite -n ' + namespace]},
                reset_map, verify_map, 'Label one owned ConfigMap on the one t3.small node cluster')
            run.action('cluster-pods-cleanup', {'clusterId': cid}, reset_pod, verify_pod,
                       'Delete only the finished test pod on the owned cluster')
    finally:
        kubectl('delete pod ' + pod_name + ' -n default --ignore-not-found=true --wait=false')
        kubectl('delete namespace ' + namespace + ' --wait=false')
    private = {p.name: json.loads(p.read_text()) for p in pathlib.Path('.').glob('results-*-private.json')}
    return {'rows': run.rows, 'private_trials': private}


def dispatch(manifest, config):
    operation = config['operation']
    if operation in ('cloud', 'prepare-stop'):
        return cloud(manifest, operation == 'prepare-stop')
    run = comparison()
    if operation == 'preflight':
        subnets = aws(manifest, ['ec2', 'describe-subnets', '--subnet-ids'] + manifest['subnets'])
        assert len(subnets['Subnets']) == len(manifest['subnets'])
        assert all(s['VpcId'] == manifest['vpc'] and s['State'] == 'available' for s in subnets['Subnets'])
        assert not run.dss.list_clusters(), 'akaos must have no existing clusters for this fixture'
        eks = aws(manifest, ['eks', 'list-clusters'])
        assert manifest['cluster'] not in eks['clusters']
        repositories = aws(manifest, ['ecr', 'describe-repositories'])['repositories']
        install_id = run.dss.get_instance_info().raw['installId'].lower()
        own_repos = {'dku-exec-base-' + install_id, 'dku-spark-base-' + install_id}
        images = []
        for repo in repositories:
            name = repo['repositoryName']
            if name not in own_repos:
                continue
            details = aws(manifest, ['ecr', 'describe-images', '--repository-name', name])['imageDetails']
            images.append({'repo': name, 'tags': repo.get('tags', []), 'images': details})
        return {'mode': run.tk.get('/api/mode'), 'cloud': cloud(manifest), 'subnets_valid': True,
                'eks_clusters': eks['clusters'], 'images_private': images,
                'release': run.tk.get('/api/tools/image-cleaner/release-date'),
                'settings_private': run.tk.get('/api/agents/action-settings')}
    if operation == 'kubernetes':
        return kubernetes(run, manifest)
    if operation == 'trial':
        name, provider = config['capability'], config['provider']
        assert provider in ('existing', 'headless')
        assert name in ('cluster-start', 'cluster-stop', 'python-run')
        if name == 'python-run':
            assert manifest['python_code'] == 'print(6 * 7)'
            target = {'code': manifest['python_code'], 'purpose': 'User-authorized bounded comparison; expected stdout 42'}
        else:
            definition = run.dss.get_cluster(manifest['cluster']).get_definition()
            import yaml
            spec = yaml.safe_load(definition['params']['config']['advancedYaml'])
            assert spec == manifest['spec'], 'Cluster definition changed since authorization'
            target = {'clusterId': manifest['cluster']}
            if name == 'cluster-stop':
                target['terminate'] = False
                cloud(manifest, prepare_stop=True)
            else:
                assert cloud(manifest)['cloud_absent'], 'Cannot start while cloud resources exist'
        result = trial(run, name, target, provider)
        if name == 'python-run':
            out = (result.get('operation_result') or {}).get('result', {})
            result['verified'] = (out.get('stdout') == '42\n' and out.get('stderr') == ''
                                  and out.get('exitCode') == 0 and out.get('timedOut') is False)
        return {'capability': name, 'provider': provider, 'trial_private': result}
    raise ValueError('Unknown fixture operation')
