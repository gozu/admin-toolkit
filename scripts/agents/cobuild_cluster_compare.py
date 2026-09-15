"""Compare owned Kubernetes fixtures; never target a customer cluster.

The caller supplies the already authorized, single-node test cluster.
Provision/stop comparisons are coordinated separately so cleanup is unconditional.
"""
import json
import time


def fixtures(run, only=None):
    cid = 'atk-cobuild-20260914'
    cluster = run.dss.get_cluster(cid)
    namespace = 'atk-comparison'
    # DSS delete_finished_pods() uses kubectl's current namespace, not all
    # namespaces. This cluster is owned exclusively by the comparison.
    pod_namespace = 'default'
    pod_name = 'atk-comparison-finished'

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
            kubectl('-n ' + pod_namespace + ' delete pod ' + pod_name + ' --ignore-not-found=true --wait=true')
            kubectl('-n ' + pod_namespace + ' run ' + pod_name + ' --image=public.ecr.aws/docker/library/busybox:1.37 --restart=Never --command -- true')
            end = time.monotonic() + 120
            while time.monotonic() < end:
                pod = json.loads(kubectl('-n ' + pod_namespace + ' get pod ' + pod_name + ' -o json'))
                if pod['status'].get('phase') == 'Succeeded':
                    return
                time.sleep(2)
            raise RuntimeError('Owned pod did not complete')

        def verify_pod(_):
            # DSS deletion requests return before Kubernetes removes the object.
            end = time.monotonic() + 90
            while time.monotonic() < end:
                pods = json.loads(kubectl('-n ' + pod_namespace + ' get pods -o json'))['items']
                if not any(p['metadata']['name'] == pod_name for p in pods):
                    return {'completed_owned_pod_deleted': True, 'nodes': 1, 'instance_type': 't3.small'}
                time.sleep(2)
            raise AssertionError('Completed owned pod was not deleted within 90 seconds')

        with run.gates(['k8s-apply-fix', 'cluster-pods-cleanup']):
            if only is None or 'k8s-apply-fix' in only:
                run.action('k8s-apply-fix', {'clusterId': cid, 'commands': [
                    'label configmap comparison comparison=after --overwrite -n ' + namespace]},
                    reset_map, verify_map, 'Label one owned ConfigMap on the single t3.small test cluster')
            if only is None or 'cluster-pods-cleanup' in only:
                run.action('cluster-pods-cleanup', {'clusterId': cid}, reset_pod, verify_pod,
                           'Delete one completed owned pod in the current namespace on a disposable cluster')
    finally:
        kubectl('-n ' + pod_namespace + ' delete pod ' + pod_name + ' --ignore-not-found=true --wait=false')
        kubectl('delete namespace ' + namespace + ' --wait=false')
