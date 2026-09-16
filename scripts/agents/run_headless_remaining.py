#!/usr/bin/env python3
"""Explicit akaos-only remaining checks, with a durable private recovery journal.

No automatic write retries. Cloud inspection and termination-protection changes
run in a macro on the selected host. Supply an existing-VPC eksctl specification;
this controller enforces one t3.small node and deletes its owned fixture.
"""
import argparse
import copy
import datetime
import io
import json
import pathlib
import secrets
import time
import zipfile

from cobuild_compare import ROOT
from run_cobuild_model_suite import package as base_package, private_write, delete_owned_plugin


def validate_spec(spec):
    if set(spec) - {'apiVersion', 'kind', 'metadata', 'vpc', 'managedNodeGroups'}:
        raise ValueError('Unexpected provisioning options outside the bounded fixture')
    groups = spec.get('managedNodeGroups', [])
    if (len(groups) != 1 or spec.get('nodeGroups') or spec.get('fargateProfiles')
            or spec.get('karpenter') or spec.get('autoModeConfig', {}).get('enabled')):
        raise ValueError('Only one managed node group is permitted')
    group = groups[0]
    if set(group) - {'name', 'instanceType', 'desiredCapacity', 'minSize', 'maxSize',
                     'volumeSize', 'volumeType', 'privateNetworking', 'volumeEncrypted', 'tags'}:
        raise ValueError('Unexpected node provisioning options')
    if (group.get('instanceType') != 't3.small' or group.get('instanceTypes')
            or group.get('desiredCapacity') != 1 or group.get('minSize') not in (0, 1)
            or group.get('maxSize') != 1 or group.get('name') != 'one-small-node'):
        raise ValueError('Fixture must contain exactly one t3.small node, maxSize=1')
    if not spec.get('vpc', {}).get('id'):
        raise ValueError('Use an existing VPC; do not create a second network')


def package(plugin, manifest):
    source = base_package(plugin)
    output = io.BytesIO()
    base = ROOT / 'scripts' / 'agents'
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as new:
        for info in old.infolist():
            if info.filename.startswith('python-runnables/checks/'):
                continue
            new.writestr(info, old.read(info.filename))
        new.writestr('python-lib/fixture.json', json.dumps(manifest))
        new.write(base / 'headless_remaining_macro.py', 'python-runnables/checks/runnable.py')
        new.write(base / 'headless_remaining_fixtures.py', 'python-lib/headless_remaining_fixtures.py')
        new.writestr('python-runnables/checks/runnable.json', json.dumps({
            'meta': {'label': 'Owned Headless remaining checks', 'icon': 'icon-beaker'},
            'impersonate': False, 'requiresGlobalAdmin': True, 'resultType': 'JSON_OBJECT',
            'params': [{'name': n, 'label': n, 'type': 'STRING'}
                       for n in ('operation', 'capability', 'provider')]}))
    output.seek(0)
    return output


class Controller:
    def __init__(self, dss, output, manifest):
        self.dss, self.output, self.manifest = dss, output, manifest
        self.state = {'manifest': manifest, 'runs': [], 'active': None, 'plugin_installed': False}
        self.macro = dss.get_project('ADMINTOOLKIT').get_macro('pyrunnable_' + manifest['plugin'] + '_checks')
        self.save()

    def save(self):
        private_write(self.output / 'state.json', self.state)

    def call(self, operation, **kwargs):
        # Journal submission before the API request; a lost submission reply is
        # an unknown outcome, never permission to submit the write again.
        entry = {'operation': operation, **kwargs, 'status': 'submitting',
                 'submitted_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        self.state['runs'].append(entry)
        self.state['active'] = len(self.state['runs']) - 1
        self.save()
        entry['id'] = self.macro.run(params={'operation': operation, **kwargs}, wait=False)
        entry['status'] = 'running'
        self.save()
        started = time.monotonic()
        deadline = started + (2100 if operation == 'trial' else 1200)
        while self.macro.get_status(entry['id']).get('running'):
            if time.monotonic() > deadline:
                entry['status'] = 'unknown'
                self.save()
                raise TimeoutError('Macro still running; retained recovery journal, no mutation retry')
            time.sleep(10)
        result = self.macro.get_result(entry['id'], as_type='json')
        entry.update(status='finished', seconds=round(time.monotonic() - started, 3))
        self.state['active'] = None
        filename = '%02d-%s%s.json' % (len(self.state['runs']), operation,
            ('-' + kwargs.get('capability', '') + '-' + kwargs.get('provider', '')) if kwargs else '')
        entry['result'] = filename
        private_write(self.output / filename, result)
        self.save()
        if result.get('error'):
            raise RuntimeError('Fixture failed; inspect private result ' + filename)
        return result

    def wait_absent(self):
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline:
            value = self.call('cloud')
            if value['cloud_absent']:
                return value
            print('Waiting for owned EKS/EC2/CloudFormation resources to disappear', flush=True)
            time.sleep(20)
        raise TimeoutError('Cloud teardown not verified')

    def ready(self):
        cluster = self.dss.get_cluster(self.manifest['cluster'])
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            value = cluster.run_kubectl('get nodes -o json')
            if value['returnValue'] == 0:
                nodes = json.loads(value['output'])['items']
                if len(nodes) > 1:
                    raise RuntimeError('More than one node')
                if len(nodes) == 1:
                    node = nodes[0]
                    assert node['metadata']['labels']['node.kubernetes.io/instance-type'] == 't3.small'
                    if any(c['type'] == 'Ready' and c['status'] == 'True' for c in node['status']['conditions']):
                        return {'nodes': 1, 'instance_type': 't3.small', 'ready': True}
            time.sleep(5)
        raise TimeoutError('One Ready t3.small node was not observed')

    def cleanup(self):
        if self.state['active'] is not None:
            print('Active macro outcome needs reconciliation; recovery state retained', flush=True)
            return
        if self.state.get('definition_created'):
            cloud = self.call('cloud')
            if not cloud['cloud_absent']:
                self.call('prepare-stop')
                self.state['cleanup_stop'] = 'submitting'
                self.save()
                # An explicit teardown after observed resources is distinct
                # from retrying an uncertain benchmark write.
                self.dss.get_cluster(self.manifest['cluster']).stop(terminate=False, force_stop=True)
                self.state['cleanup_stop'] = 'returned'
                self.save()
                self.wait_absent()
            if any(c['id'] == self.manifest['cluster'] for c in self.dss.list_clusters()):
                self.dss.get_cluster(self.manifest['cluster']).delete()
            assert not any(c['id'] == self.manifest['cluster'] for c in self.dss.list_clusters())
            self.state['cluster_deleted'] = True
            self.save()
        if self.state['plugin_installed']:
            delete_owned_plugin(self.dss, self.manifest['plugin'])
            self.state['plugin_deleted'] = True
            self.save()


def execute_checks(ctl):
    import yaml
    dss, manifest = ctl.dss, ctl.manifest
    cid, spec = manifest["cluster"], manifest["spec"]
    preflight = ctl.call('preflight')
    assert preflight['cloud']['cloud_absent']
    print('Read-only preflight passed; creating the one-node DSS definition', flush=True)
    config = {'connectionInfo': {'mode': 'INLINE', 'inlinedConfig': {'region': manifest['region']}, 'inlinedPluginConfig': {}},
              'networkingSettings': {'mode': 'INLINE', 'inlinedConfig': {}, 'inlinedPluginConfig': {}},
              'advanced': True, 'advancedYaml': yaml.safe_dump(spec), 'clusterAutoScaling': False,
              'advancedGPU': False, 'installMetricsServer': False}
    ctl.state['definition_created'] = True  # Reconcile even if create reply is lost.
    ctl.save()
    dss.create_cluster(cid, 'pycluster_eks-clusters_create-eks-cluster', {'config': config}, 'KUBERNETES')
    for provider in ('existing', 'headless'):
        print('Starting whole-task cluster-start: ' + provider, flush=True)
        result = ctl.call('trial', capability='cluster-start', provider=provider)
        trial_result = result['trial_private']
        if trial_result['status'] != 'completed':
            raise RuntimeError('Cluster start comparison did not complete; reconcile then clean up')
        evidence = ctl.ready()
        private_write(ctl.output / ('ready-' + provider + '.json'), evidence)
        print('Verified one Ready t3.small node: ' + provider, flush=True)
        if provider == 'existing':
            ctl.call('kubernetes')
            for python_provider in ('existing', 'headless'):
                ctl.call('trial', capability='python-run', provider=python_provider)
        print('Starting whole-task cluster-stop: ' + provider, flush=True)
        result = ctl.call('trial', capability='cluster-stop', provider=provider)
        ctl.wait_absent()
        if result['trial_private']['status'] != 'completed':
            raise RuntimeError('Cluster stop explanation did not complete; resources reconciled')
        print('Verified cloud teardown: ' + provider, flush=True)


def main():
    import dataikuapi
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--spec', type=pathlib.Path, required=True)
    parser.add_argument('--output', type=pathlib.Path, required=True)
    args = parser.parse_args()
    if not args.execute:
        parser.error('--execute is required; authorizes the bounded fixture and print(6 * 7) twice')
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    dss = dataikuapi.DSSClient((ROOT / '.dss-url').read_text().strip(), (ROOT / '.dss-api-key').read_text().strip())
    from urllib.parse import urlsplit
    assert urlsplit(dss.host).hostname == 'akaos.fe-aws.dkucloud-dev.com', 'This fixture is akaos-only'
    run_id = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d') + '-' + secrets.token_hex(3)
    spec = copy.deepcopy(json.loads(args.spec.read_text())['spec'])
    validate_spec(spec)
    cid, plugin = 'atk-headless-' + run_id, 'atk-hl-checks-' + secrets.token_hex(4)
    tags = {'Purpose': 'ADTK-Headless-validation', 'ADTKRun': run_id,
            'DeleteAfter': datetime.datetime.now(datetime.timezone.utc).date().isoformat()}
    spec['metadata'].update(name=cid, tags=tags)
    spec['managedNodeGroups'][0]['tags'] = tags
    manifest = {'run': run_id, 'cluster': cid, 'plugin': plugin, 'region': spec['metadata']['region'],
                'spec': spec, 'vpc': spec['vpc']['id'], 'python_code': 'print(6 * 7)',
                'subnets': [s['id'] for s in spec['vpc']['subnets']['private'].values()]}
    ctl = Controller(dss, args.output, manifest)
    try:
        dss.install_plugin_from_archive(package(plugin, manifest))
        ctl.state['plugin_installed'] = True
        ctl.save()
        print('Installed owned macro; creating its isolated environment', flush=True)
        obj = dss.get_plugin(plugin)
        env = obj.create_code_env(python_interpreter='PYTHON311').wait_for_result()['envName']
        assert env.startswith('plugin_' + plugin + '_')
        settings = obj.get_settings()
        settings.set_code_env(env)
        settings.save()
        execute_checks(ctl)
    finally:
        ctl.cleanup()
    print('Owned cluster, macro plugin, and isolated environment removed', flush=True)


if __name__ == '__main__':
    main()
