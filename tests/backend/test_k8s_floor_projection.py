"""Rule 21 (cluster-floor-projection) must never project a 0-node floor while
Running workload pods exist.

Regression: pods with no resource requests (common for DSS exec configs / API
deployments) were dropped from the bin-pack, so a cluster whose only workload
pods were zero-request packed to 0 nodes and the finding claimed ~100% of
spend as savings.

The rule now emits TWO projections in one finding:
  - 'rightsized' (default, Kubecost-style): pods with usage metrics are sized
    at observed usage / 0.75 target utilization; requests are treated as
    adjustable.
  - 'requests' (Karpenter-style): declared requests are hard constraints;
    zero-request pods fall back to live usage.
Both may repack pools onto cheaper same-family instance sizes when the pricing
map covers them, and both honor nodeSelector / taints.
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


def make_node(name, instance, cpu, mem, labels=None, capacity=None, taints=None):
    node_labels = {'node.kubernetes.io/instance-type': instance}
    node_labels.update(labels or {})
    status = {'allocatable': {'cpu': cpu, 'memory': mem}}
    if capacity:
        status['capacity'] = {'cpu': capacity[0], 'memory': capacity[1]}
    return {
        'metadata': {'name': name, 'labels': node_labels},
        'status': status,
        'spec': {'taints': taints} if taints else {},
    }


def make_pod(ns, name, node, phase='Running', cpu=None, mem=None, owner=None, selector=None):
    requests = {}
    if cpu is not None:
        requests['cpu'] = cpu
    if mem is not None:
        requests['memory'] = mem
    meta = {'namespace': ns, 'name': name}
    if owner:
        meta['ownerReferences'] = [{'kind': owner}]
    spec = {'nodeName': node, 'containers': [{'resources': {'requests': requests}}]}
    if selector:
        spec['nodeSelector'] = selector
    return {
        'metadata': meta,
        'spec': spec,
        'status': {'phase': phase},
    }


def bundle(nodes, pods, top_pods=None, prices=None):
    probes = {
        'probe_nodes': {'ok': True, 'data': {'items': nodes}},
        'probe_pods': {'ok': True, 'data': {'items': pods}},
        '_pricing': {'ok': True, 'data': {'priceByType': prices or PRICES}},
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
    # The two empty nodes are reclaimable outright; the workload node's pods
    # (tiny) let the pool downsize from m8i.2xlarge to a single m8i.xlarge —
    # but never to 0 nodes.
    assert ev['idleNodeCount'] == 2
    assert ev['idleNodeSavingsMonthly'] == round(0.25 * 730, 2)
    assert ev['consolidationSavingsMonthly'] == round(0.21 * 730, 2)
    assert ev['floorMonthly'] == round(0.21 * 730, 2)
    assert ev['floorBreakdown'] == [{'instanceType': 'm8i.xlarge', 'count': 1, 'hourly': 0.21}]
    # Default projection is the right-sized one; the requests projection keeps
    # the legacy usage-packed accounting.
    assert ev['defaultProjection'] == 'rightsized'
    assert ev['podsRightsized'] == 2
    assert ev['projections']['requests']['podsPackedByUsage'] == 2
    assert ev['projections']['rightsized']['savingsMonthly'] == ev['savingsMonthly']
    # Total savings must equal the split and stay below current spend (was 100%).
    assert ev['savingsMonthly'] == round(ev['idleNodeSavingsMonthly'] + ev['consolidationSavingsMonthly'], 2)
    assert ev['savingsMonthly'] < ev['currentMonthly']
    assert findings[0].cost_impact_per_month == ev['savingsMonthly']


def test_zero_request_pods_without_usage_still_hold_a_node():
    nodes, pods = _three_node_cluster()
    findings = Rule21ClusterFloorProjection().evaluate(bundle(nodes, pods))
    assert len(findings) == 1
    ev = findings[0].evidence
    # Zero-size pods still pin the pool to >= 1 node (downsized, not dropped).
    assert ev['floorMonthly'] == round(0.21 * 730, 2)
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
    # Both pods fit one m8i.xlarge: two workload nodes -> one. (m8i.large is
    # not in the price map, so no downsized candidate exists here.)
    assert ev['idleNodeCount'] == 0
    assert ev['consolidationSavingsMonthly'] == round(0.21 * 730, 2)
    assert ev['savingsMonthly'] == ev['consolidationSavingsMonthly']
    assert ev['savingsMonthly'] < ev['currentMonthly']
    # No usage metrics -> the rightsized projection degrades to declared
    # requests and both projections agree.
    assert ev['projections']['rightsized']['floorMonthly'] == ev['projections']['requests']['floorMonthly']


def test_node_selector_pools_block_merging_but_rightsizing_unlocks_downsizes():
    """fe-sandbox shape: three workerType pools, one node each. Pools can never
    merge (selectors), but each pool may live on a cheaper same-family size.
    Right-sizing the over-requested jupyter pods unlocks more than honoring
    requests does."""
    prices = dict(PRICES, **{'m8i.large': 0.105})
    nodes = [
        make_node('node-sys', 't3.medium', '1930m', '3372656Ki',
                  labels={'workerType': 'system'},
                  taints=[{'key': 'CriticalAddonsOnly', 'value': 'yes', 'effect': 'NoSchedule'}]),
        make_node('node-api', 'm8i.xlarge', '3920m', '14389416Ki',
                  labels={'workerType': 'api'}, capacity=('4', '16777216Ki')),
        make_node('node-ng', 'm8i.2xlarge', '7910m', '30620856Ki',
                  labels={'workerType': 'nongpu'}, capacity=('8', '33554432Ki')),
    ]
    pods = [
        make_pod('default', 'jupyter-exec-1', 'node-ng', cpu='1000m', mem='6113Mi',
                 selector={'workerType': 'nongpu'}),
        make_pod('default', 'jupyter-exec-2', 'node-ng', cpu='1000m', mem='6113Mi',
                 selector={'workerType': 'nongpu'}),
        make_pod('saslanov-api', 'dku-mad-prediction', 'node-ng'),
        make_pod('default', 'dku-mad-api', 'node-api', selector={'workerType': 'api'}),
    ]
    usage = [
        {'namespace': 'default', 'pod': 'jupyter-exec-1', 'cpuMilli': 1, 'memMib': 550},
        {'namespace': 'default', 'pod': 'jupyter-exec-2', 'cpuMilli': 1, 'memMib': 550},
        {'namespace': 'saslanov-api', 'pod': 'dku-mad-prediction', 'cpuMilli': 2, 'memMib': 1890},
        {'namespace': 'default', 'pod': 'dku-mad-api', 'cpuMilli': 2, 'memMib': 500},
    ]
    findings = Rule21ClusterFloorProjection().evaluate(bundle(nodes, pods, usage, prices=prices))
    assert len(findings) == 1
    ev = findings[0].evidence
    req = ev['projections']['requests']
    rs = ev['projections']['rightsized']

    # System node is empty -> idle reclaim in both modes.
    assert ev['idleNodeCount'] == 1

    # Requests mode: the 2x 6113Mi jupyter requests pin the nongpu pool to an
    # xlarge-or-bigger shape; the api pool (tiny live usage) drops to a large.
    req_by_type = {row['instanceType']: row['count'] for row in req['floorBreakdown']}
    assert req_by_type == {'m8i.xlarge': 1, 'm8i.large': 1}
    assert req['unplaceablePods'] == []

    # Right-sized mode: jupyters shrink to ~733Mi, so BOTH pools fit m8i.large —
    # still two nodes (selectors forbid merging), but far cheaper.
    rs_by_type = {row['instanceType']: row['count'] for row in rs['floorBreakdown']}
    assert rs_by_type == {'m8i.large': 2}
    assert rs['savingsMonthly'] > req['savingsMonthly']
    assert rs['unplaceablePods'] == []

    # Default (headline) numbers are the right-sized ones.
    assert findings[0].cost_impact_per_month == rs['savingsMonthly']
    assert findings[0].title == rs['title']
    assert 'Kubecost-style' in rs['summary']
    assert 'Karpenter-style' in req['summary']
