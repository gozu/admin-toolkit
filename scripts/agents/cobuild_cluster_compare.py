"""Compare owned Kubernetes fixtures; never target a customer cluster.

The caller supplies the already authorized, single-node test cluster.
Provision/stop comparisons are coordinated separately so cleanup is unconditional.
"""
import json
import time


def fixtures(run):
    cid = 'atk-cobuild-20260914'
    cluster = run.dss.get_cluster(cid)
    namespace = 'atk-comparison'

    def kubectl(command):
        result = cluster.run_kubectl(command)
        assert result['returnValue'] == 0, result.get('output', '')[:300]
        return result.get('output', '')

    nodes = json.loads(kubectl('get nodes -o json'))['items']
    assert len(nodes) == 1
    assert nodes[0]['metadata']['labels']['node.kubernetes.io/instance-type'] == 't3.small'
    kubectl('create namespace ' + namespace)
    try:
        def reset_map():
            kubectl('-n ' + namespace + ' delete configmap comparison --ignore-not-found=true')
            kubectl('-n ' + namespace + ' create configmap comparison --from-literal=value=before')

        def verify_map(_):
            value = json.loads(kubectl('-n ' + namespace + ' get configmap comparison -o json'))
            assert value['metadata']['labels']['comparison'] == 'after'
            return {'owned_configmap_label': 'after', 'nodes': 1, 'instance_type': 't3.small'}

        def reset_pod():
            kubectl('-n ' + namespace + ' delete pod finished --ignore-not-found=true --wait=true')
            kubectl('-n ' + namespace + ' run finished --image=public.ecr.aws/docker/library/busybox:1.37 --restart=Never --command -- true')
            end = time.monotonic() + 120
            while time.monotonic() < end:
                pod = json.loads(kubectl('-n ' + namespace + ' get pod finished -o json'))
                if pod['status'].get('phase') == 'Succeeded':
                    return
                time.sleep(2)
            raise RuntimeError('Owned pod did not complete')

        def verify_pod(_):
            pods = json.loads(kubectl('-n ' + namespace + ' get pods -o json'))['items']
            assert not any(p['metadata']['name'] == 'finished' for p in pods)
            return {'completed_owned_pod_deleted': True, 'nodes': 1, 'instance_type': 't3.small'}

        with run.gates(['k8s-apply-fix', 'cluster-pods-cleanup']):
            run.action('k8s-apply-fix', {'clusterId': cid, 'commands': [
                'label configmap comparison comparison=after --overwrite -n ' + namespace]},
                reset_map, verify_map, 'Label one owned ConfigMap on the single t3.small test cluster')
            run.action('cluster-pods-cleanup', {'clusterId': cid}, reset_pod, verify_pod,
                       'Delete one completed owned pod on a disposable cluster')
    finally:
        kubectl('delete namespace ' + namespace + ' --wait=false')
