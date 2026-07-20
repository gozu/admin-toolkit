"""Rule 21 (cluster-floor-projection) must never project a 0-node floor while
Running workload pods exist.

Regression: pods with no resource requests (common for DSS exec configs / API
deployments) were dropped from the bin-pack, so a cluster whose only workload
pods were zero-request packed to 0 nodes and the finding claimed ~100% of
spend as savings.
"""

import os
import sys

_K8S_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'python-runnables', 'k8s-insights')
)
if _K8S_DIR not in sys.path:
    sys.path.insert(0, _K8S_DIR)

from rules.cost import Rule21ClusterFloorProjection  # noqa: E402


PRICES = {'t3.medium': 0.04, 'm8i.xlarge': 0.21, 'm8i.2xlarge': 0.42}


def make_node(name, instance, cpu, mem):
    return {
        'metadata': {'name': name, 'labels': {'node.kubernetes.io/instance-type': instance}},
        'status': {'allocatable': {'cpu': cpu, 'memory': mem}},
        'spec': {},
    }


def make_pod(ns, name, node, phase='Running', cpu=None, mem=None, owner=None):
    requests = {}
    if cpu is not None:
        requests['cpu'] = cpu
    if mem is not None:
        requests['memory'] = mem
    meta = {'namespace': ns, 'name': name}
    if owner:
        meta['ownerReferences'] = [{'kind': owner}]
    return {
        'metadata': meta,
        'spec': {'nodeName': node, 'containers': [{'resources': {'requests': requests}}]},
        'status': {'phase': phase},
    }


def bundle(nodes, pods, top_pods=None):
    probes = {
        'probe_nodes': {'ok': True, 'data': {'items': nodes}},
        'probe_pods': {'ok': True, 'data': {'items': pods}},
        '_pricing': {'ok': True, 'data': {'priceByType': PRICES}},
    }
    if top_pods is not None:
        probes['probe_top_pods'] = {'ok': True, 'data': top_pods}
    return probes


def _three_node_cluster():
    """The reported cluster: two empty nodes + one workload node whose only
    Running user pods declare zero requests."""
    nodes = [
        make_node('node-a', 't3.medium', '1930m', '3372656Ki'),
        make_node('node-b', 'm8i.xlarge', '3920m', '14389416Ki'),
        make_node('node-c', 'm8i.2xlarge', '7910m', '30620856Ki'),
    ]
    pods = [
        make_pod('default', 'exec-done', 'node-c', phase='Succeeded', mem='500Mi'),
        make_pod('default', 'dku-mad-fraud', 'node-c'),
        make_pod('saslanov-api', 'dku-mad-prediction', 'node-c'),
        make_pod('kube-system', 'coredns-1', 'node-c', cpu='100m', mem='70Mi', owner='ReplicaSet'),
        make_pod('kube-system', 'aws-node-1', 'node-c', cpu='50m', owner='DaemonSet'),
    ]
    return nodes, pods


def test_zero_request_pods_packed_by_usage_floor_stays_at_one_node():
    nodes, pods = _three_node_cluster()
    usage = [
        {'namespace': 'default', 'pod': 'dku-mad-fraud', 'cpuMilli': 2, 'memMib': 434},
        {'namespace': 'saslanov-api', 'pod': 'dku-mad-prediction', 'cpuMilli': 2, 'memMib': 1873},
    ]
    findings = Rule21ClusterFloorProjection().evaluate(bundle(nodes, pods, usage))
    assert len(findings) == 1
    ev = findings[0].evidence
    # The two empty nodes are reclaimable; the workload node is NOT — its pods
    # (packed by live usage) still need it, so consolidation savings are zero.
    assert ev['idleNodeCount'] == 2
    assert ev['idleNodeSavingsMonthly'] == round(0.25 * 730, 2)
    assert ev['consolidationSavingsMonthly'] == 0
    assert ev['floorMonthly'] == round(0.42 * 730, 2)
    assert ev['podsPackedByUsage'] == 2
    assert ev['podsWithoutRequestsOrUsage'] == 0
    # Total savings must equal the split and stay below current spend (was 100%).
    assert ev['savingsMonthly'] == ev['idleNodeSavingsMonthly']
    assert ev['savingsMonthly'] < ev['currentMonthly']
    assert findings[0].cost_impact_per_month == ev['savingsMonthly']


def test_zero_request_pods_without_usage_still_hold_a_node():
    nodes, pods = _three_node_cluster()
    findings = Rule21ClusterFloorProjection().evaluate(bundle(nodes, pods))
    assert len(findings) == 1
    ev = findings[0].evidence
    assert ev['consolidationSavingsMonthly'] == 0
    assert ev['podsWithoutRequestsOrUsage'] == 2
    assert ev['savingsMonthly'] < ev['currentMonthly']


def test_genuine_consolidation_still_reported():
    nodes = [
        make_node('node-a', 'm8i.xlarge', '3920m', '14389416Ki'),
        make_node('node-b', 'm8i.xlarge', '3920m', '14389416Ki'),
    ]
    pods = [
        make_pod('proj-1', 'job-1', 'node-a', cpu='500m', mem='1024Mi'),
        make_pod('proj-2', 'job-2', 'node-b', cpu='500m', mem='1024Mi'),
    ]
    findings = Rule21ClusterFloorProjection().evaluate(bundle(nodes, pods))
    assert len(findings) == 1
    ev = findings[0].evidence
    # Both pods fit one m8i.xlarge: two workload nodes -> one.
    assert ev['idleNodeCount'] == 0
    assert ev['consolidationSavingsMonthly'] == round(0.21 * 730, 2)
    assert ev['savingsMonthly'] == ev['consolidationSavingsMonthly']
    assert ev['savingsMonthly'] < ev['currentMonthly']
