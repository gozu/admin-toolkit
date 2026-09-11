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


def test_unknown_demand_keeps_its_server_and_all_colocated_pods_at_full_rent():
    nodes, pods = _three_node_cluster()
    finding = Rule21ClusterFloorProjection().evaluate(bundle(nodes, pods))[0]
    ev = finding.evidence
    assert len(ev['placementNodes']) == 1
    assert ev['podsWithoutRequestsOrUsage'] == 2
    assert ev['placementComplete'] is True
    assert ev['floorMonthly'] == round(PRICES['m8i.2xlarge'] * 730, 2)
    assert ev['savingsMonthly'] == round((PRICES['t3.medium'] + PRICES['m8i.xlarge']) * 730, 2)
    assert finding.cost_impact_per_month == ev['savingsMonthly']
    assert set(ev['unknownSizingPods']) == {'default/dku-mad-fraud', 'saslanov-api/dku-mad-prediction'}
    for projection in ev['projections'].values():
        retained = projection['placementNodes'][0]
        assert retained['id'] == 'node-c'
        assert retained['instanceType'] == 'm8i.2xlarge'
        assert retained['retainedForPods'] == ev['unknownSizingPods']
        assert {p['key'] for p in retained['pods']} == {
            'default/dku-mad-fraud', 'saslanov-api/dku-mad-prediction',
            'kube-system/coredns-1', 'kube-system/aws-node-1',
        }


def test_crashloop_keeps_its_server_while_healthy_servers_consolidate():
    nodes = [make_node(n, 'm8i.2xlarge', '7900m', '30000Mi') for n in ['a', 'b', 'c']]
    broken = make_pod('default', 'code-studio', 'a', owner='ReplicaSet')
    broken['status']['containerStatuses'] = [{
        'restartCount': 314, 'ready': False,
        'state': {'waiting': {'reason': 'CrashLoopBackOff'}},
    }]
    pods = [broken, make_pod('work', 'neighbor', 'a', cpu='100m', mem='100Mi')]
    pods += [make_pod('work', n, n, cpu='100m', mem='100Mi') for n in ['b', 'c']]
    probes = bundle(nodes, pods)
    ev = Rule21ClusterFloorProjection().evaluate(probes)[0].evidence
    for projection in ev['projections'].values():
        assert projection['placementComplete'] is True
        assert len(projection['placementNodes']) == 2
        retained, consolidated = projection['placementNodes']
        assert retained['id'] == 'a'
        assert retained['instanceType'] == 'm8i.2xlarge'
        assert {p['key'] for p in retained['pods']} == {'default/code-studio', 'work/neighbor'}
        assert retained['pods'][0]['statusReason'] == 'CrashLoopBackOff'
        assert retained['pods'][0]['realMemMib'] is None
        assert {p['key'] for p in consolidated['pods']} == {'work/b', 'work/c'}
        assert consolidated['instanceType'] == 'm8i.xlarge'
        assert projection['floorMonthly'] == round(sum(round(n['hourly'] * 730, 2) for n in projection['placementNodes']), 2)
        assert projection['savingsMonthly'] == round(ev['currentMonthly'] - projection['floorMonthly'], 2)
    # Repeating the audit with the same crash state never blanks the estimate.
    assert Rule21ClusterFloorProjection().evaluate(probes)[0].evidence == ev
    # Once metrics arrive, the server can participate in consolidation again.
    probes['probe_top_pods'] = {'ok': True, 'data': [
        {'namespace': 'default', 'pod': 'code-studio', 'cpuMilli': 100, 'memMib': 100},
    ]}
    recovered = Rule21ClusterFloorProjection().evaluate(probes)[0].evidence
    assert recovered['unknownSizingPods'] == []
    assert all(not n.get('retainedForPods') for n in recovered['placementNodes'])
    assert recovered['savingsMonthly'] > ev['savingsMonthly']


def test_unknown_unscheduled_pod_still_blocks_savings():
    nodes = [make_node('a', 'm8i.xlarge', '3900m', '14000Mi')]
    pods = [make_pod('default', 'pending', '', phase='Pending')]
    ev = Rule21ClusterFloorProjection().evaluate(bundle(nodes, pods))[0].evidence
    assert ev['placementComplete'] is False
    assert ev['floorMonthly'] is None
    assert ev['savingsMonthly'] is None
    assert ev['unknownSizingPods'] == ['default/pending']


def test_unknown_per_node_service_keeps_its_server_and_cannot_be_cloned():
    nodes = [make_node(n, 'm8i.xlarge', '3900m', '14000Mi') for n in ['a', 'b']]
    pods = [make_pod('kube-system', 'helper', 'a', owner='DaemonSet'),
            make_pod('work', 'job', 'b', cpu='100m', mem='100Mi')]
    ev = Rule21ClusterFloorProjection().evaluate(bundle(nodes, pods))[0].evidence
    assert ev['placementComplete'] is True
    assert ev['idleNodeCount'] == 0
    assert ev['savingsMonthly'] == 0
    assert ev['placementNodes'][0]['id'] == 'a'
    assert ev['placementNodes'][0]['retainedForPods'] == ['kube-system/helper']
    assert len([p for n in ev['placementNodes'] for p in n['pods'] if p['key'] == 'kube-system/helper']) == 1


def test_retained_server_with_missing_price_still_blocks_estimate():
    nodes = [make_node('a', 'unknown.large', '2000m', '8000Mi')]
    ev = Rule21ClusterFloorProjection().evaluate(bundle(nodes, [make_pod('work', 'unknown', 'a')]))[0].evidence
    assert ev['placementComplete'] is False
    assert ev['floorMonthly'] is None
    assert ev['unpricedNodes'] == ['a']


def test_all_servers_unsized_reports_full_rent_and_zero_savings():
    nodes = [make_node(n, 'm8i.xlarge', '3900m', '14000Mi') for n in ['a', 'b']]
    pods = [make_pod('work', n, n) for n in ['a', 'b']]
    ev = Rule21ClusterFloorProjection().evaluate(bundle(nodes, pods))[0].evidence
    assert ev['placementComplete'] is True
    assert ev['floorMonthly'] == ev['currentMonthly']
    assert ev['savingsMonthly'] == 0
    assert [n['id'] for n in ev['placementNodes']] == ['a', 'b']


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
    assert 'observed usage +33%' in rs['summary']
    assert 'current requests or observed usage' in req['summary']


def test_assignments_conserve_workloads_measurements_and_rent():
    nodes, pods = _three_node_cluster()
    usage = [
        {'namespace': p['metadata']['namespace'], 'pod': p['metadata']['name'], 'cpuMilli': 10 + i, 'memMib': 100 + i}
        for i, p in enumerate(pods) if p['status']['phase'] == 'Running'
    ]
    ev = Rule21ClusterFloorProjection().evaluate(bundle(nodes, pods, usage))[0].evidence
    for projection in ev['projections'].values():
        assert projection['placementComplete'] is True
        placed = [p for n in projection['placementNodes'] for p in n['pods'] if not p['perNodeService']]
        assert sorted(p['key'] for p in placed) == ['default/dku-mad-fraud', 'kube-system/coredns-1', 'saslanov-api/dku-mad-prediction']
        measured = {f"{r['namespace']}/{r['pod']}": r for r in usage}
        for n in projection['placementNodes']:
            assert sum(p['reservedCpuMilli'] for p in n['pods']) <= n['cpuCapacityMilli']
            assert sum(p['reservedMemMib'] for p in n['pods']) <= n['memoryCapacityMib']
            for p in n['pods']:
                assert p['realCpuMilli'] == measured[p['key']]['cpuMilli']
                assert p['realMemMib'] == measured[p['key']]['memMib']
                assert p['sourceNode'] == 'node-c'
        assert projection['floorMonthly'] == round(sum(n['hourly'] for n in projection['placementNodes']) * 730, 2)
        assert projection['savingsMonthly'] == round(ev['currentMonthly'] - projection['floorMonthly'], 2)


def test_per_node_services_consume_capacity_in_every_proposed_node():
    nodes = [make_node('a', 'custom.xlarge', '4000m', '10000Mi'), make_node('b', 'custom.xlarge', '4000m', '10000Mi')]
    pods = [make_pod('work', f'job-{n}', n, cpu='100m', mem='5000Mi') for n in ['a', 'b']]
    pods += [make_pod('kube-system', f'helper-{n}', n, cpu='100m', mem='1000Mi', owner='DaemonSet') for n in ['a', 'b']]
    ev = Rule21ClusterFloorProjection().evaluate(bundle(nodes, pods, prices={'custom.xlarge': 1}))[0].evidence
    projection = ev['projections']['requests']
    assert len(projection['placementNodes']) == 2
    assert projection['savingsMonthly'] == 0
    for n in projection['placementNodes']:
        assert sum(p['perNodeService'] for p in n['pods']) == 1
        assert sum(p['reservedMemMib'] for p in n['pods']) == 6000


def test_same_instance_type_different_selectors_keep_separate_assignments():
    nodes = [make_node(n, 'custom.xlarge', '4000m', '10000Mi', labels={'pool': n}) for n in ['a', 'b']]
    pods = [make_pod('work', f'job-{n}', n, cpu='100m', mem='1000Mi', selector={'pool': n}) for n in ['a', 'b']]
    ev = Rule21ClusterFloorProjection().evaluate(bundle(nodes, pods, prices={'custom.xlarge': 1}))[0].evidence
    assert ev['unplaceablePods'] == []
    assert len(ev['placementNodes']) == 2
    assert all(len(n['pods']) == 1 for n in ev['placementNodes'])


def test_actual_usage_cannot_be_packed_below_its_measured_size():
    nodes = [make_node(n, 'custom.xlarge', '4000m', '1000Mi') for n in ['a', 'b']]
    pods = [make_pod('work', n, n, cpu='1m', mem='1Mi') for n in ['a', 'b']]
    usage = [{'namespace': 'work', 'pod': n, 'cpuMilli': 20, 'memMib': 600} for n in ['a', 'b']]
    ev = Rule21ClusterFloorProjection().evaluate(bundle(nodes, pods, usage, prices={'custom.xlarge': 1}))[0].evidence
    assert len(ev['projections']['requests']['placementNodes']) == 2
    assert ev['projections']['requests']['savingsMonthly'] == 0


def test_unplaceable_pods_never_turn_into_savings():
    nodes = [make_node('a', 'custom.xlarge', '4000m', '1000Mi')]
    pods = [make_pod('work', 'too-big', 'a', cpu='1m', mem='2000Mi')]
    ev = Rule21ClusterFloorProjection().evaluate(bundle(nodes, pods, prices={'custom.xlarge': 1}))[0].evidence
    assert ev['unplaceablePods'] == ['work/too-big']
    assert ev['placementComplete'] is False
    assert ev['savingsMonthly'] is None
    assert ev['floorMonthly'] is None


def test_system_deployment_is_not_discarded_as_an_idle_server():
    nodes = [make_node('a', 'custom.xlarge', '4000m', '1000Mi')]
    pods = [make_pod('kube-system', 'dns', 'a', cpu='100m', mem='100Mi')]
    ev = Rule21ClusterFloorProjection().evaluate(bundle(nodes, pods, prices={'custom.xlarge': 1}))[0].evidence
    assert ev['idleNodeCount'] == 0
    assert ev['placementNodes'][0]['pods'][0]['key'] == 'kube-system/dns'
    assert ev['placementNodes'][0]['pods'][0]['isSystem'] is True


def test_monthly_totals_equal_the_displayed_node_rents():
    nodes = [make_node('a', 'custom.large', '2000m', '1000Mi', labels={'pool': 'a'}),
             make_node('b', 'custom.2xlarge', '8000m', '4000Mi', labels={'pool': 'b'})]
    pods = [make_pod('work', n, n, cpu='100m', mem='2000Mi' if n == 'b' else '100Mi', selector={'pool': n}) for n in ['a', 'b']]
    ev = Rule21ClusterFloorProjection().evaluate(bundle(nodes, pods, prices={'custom.large': .10584, 'custom.2xlarge': .42336}))[0].evidence
    assert ev['floorMonthly'] == 386.31
    assert ev['currentMonthly'] == 386.31
    assert ev['floorMonthly'] == round(sum(round(n['hourly'] * 730, 2) for n in ev['placementNodes']), 2)
    assert ev['savingsMonthly'] == 0


def test_oversized_per_node_services_do_not_produce_a_zero_cost_cluster():
    nodes = [make_node('a', 'custom.large', '2000m', '1000Mi')]
    pods = [make_pod('kube-system', 'helper', 'a', cpu='100m', mem='2000Mi', owner='DaemonSet')]
    ev = Rule21ClusterFloorProjection().evaluate(bundle(nodes, pods, prices={'custom.large': 1}))[0].evidence
    assert ev['placementComplete'] is False
    assert ev['unplaceablePods'] == ['kube-system/helper']
    assert ev['floorMonthly'] is None


def test_size_reason_exposes_memory_reservation_hidden_by_low_usage():
    nodes = [make_node('a', 'm8i.2xlarge', '7900m', '30000Mi', capacity=('8', '32768Mi'))]
    pods = [make_pod('work', 'notebook', 'a', cpu='100m', mem='20000Mi')]
    usage = [{'namespace': 'work', 'pod': 'notebook', 'cpuMilli': 10, 'memMib': 1000}]
    ev = Rule21ClusterFloorProjection().evaluate(bundle(nodes, pods, usage))[0].evidence
    node = ev['projections']['requests']['placementNodes'][0]
    assert node['instanceType'] == 'm8i.2xlarge'
    assert node['pods'][0]['realMemMib'] == 1000
    assert node['sizeChecks'] == [{
        'instanceType': 'm8i.xlarge',
        'blockers': [{'kind': 'memory', 'required': 20000, 'capacity': 13616}],
    }]
    assert ev['projections']['rightsized']['placementNodes'][0]['sizeChecks'] == []


def test_size_reason_reports_cpu_even_with_little_memory_use_and_includes_services():
    nodes = [make_node('a', 'm8i.2xlarge', '7900m', '30000Mi', capacity=('8', '32768Mi'))]
    pods = [make_pod('work', 'notebook', 'a', cpu='3800m', mem='100Mi'),
            make_pod('kube-system', 'helper', 'a', cpu='200m', mem='100Mi', owner='DaemonSet')]
    ev = Rule21ClusterFloorProjection().evaluate(bundle(nodes, pods))[0].evidence
    assert ev['placementNodes'][0]['sizeChecks'][0]['blockers'] == [
        {'kind': 'cpu', 'required': 4000, 'capacity': 3900},
    ]


def test_size_reason_identifies_pod_with_instance_type_restriction():
    nodes = [make_node('a', 'm8i.2xlarge', '7900m', '30000Mi', capacity=('8', '32768Mi'))]
    pods = [make_pod('work', 'pinned', 'a', cpu='100m', mem='100Mi',
                     selector={'node.kubernetes.io/instance-type': 'm8i.2xlarge'})]
    ev = Rule21ClusterFloorProjection().evaluate(bundle(nodes, pods))[0].evidence
    assert ev['placementNodes'][0]['sizeChecks'][0]['blockers'] == [
        {'kind': 'selector', 'pods': ['work/pinned']},
    ]


def test_size_reason_does_not_compare_to_different_pool_or_unpriced_shapes():
    nodes = [make_node('a', 'm8i.2xlarge', '7900m', '30000Mi', labels={'pool': 'a'}, capacity=('8', '32768Mi')),
             make_node('b', 'm8i.xlarge', '3900m', '14000Mi', labels={'pool': 'b'}, capacity=('4', '16384Mi'))]
    pods = [make_pod('work', n, n, cpu='100m', mem='20000Mi' if n == 'a' else '100Mi', selector={'pool': n}) for n in ['a', 'b']]
    ev = Rule21ClusterFloorProjection().evaluate(bundle(nodes, pods))[0].evidence
    checks = ev['placementNodes'][0]['sizeChecks']
    assert len(checks) == 1
    assert checks[0]['blockers'] == [{'kind': 'memory', 'required': 20000, 'capacity': 13616}]


def test_size_checks_honestly_report_fit_and_scheduling_restrictions():
    from binpack import NodeGroup, PodReq, placement_size_checks
    pod = PodReq('work/job', 'work', 100, 100)
    small = NodeGroup('small', 'm8i.large', 2000, 6000, 0, {}, [])
    assert placement_size_checks([pod], [small]) == [{'instanceType': 'm8i.large', 'blockers': []}]
    small.taints = [{'key': 'dedicated', 'value': 'system', 'effect': 'NoSchedule'}]
    assert placement_size_checks([pod], [small])[0]['blockers'] == [{'kind': 'taint', 'pods': ['work/job']}]
