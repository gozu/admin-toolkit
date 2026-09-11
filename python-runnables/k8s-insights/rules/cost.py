"""Cost / bin-packing rules (14–21).

Most expensive/lasting savings live here, especially:
- Rule 15: a single oversized pod locking a node open
- Rule 21: full bin-pack floor projection ("you could go from 16 to 2 nodes")
"""
from math import ceil
from typing import Any, Dict, List, Optional, Tuple

from finding import Finding  # type: ignore
from binpack import (  # type: ignore
    PodReq, NodeGroup, compute_floor, downsize_factor, family_downsize_types,
    parse_cpu_milli, parse_mem_mib, placement_size_checks,
)
from .base import (
    Rule, ProbeBundle, items, pod_namespace, pod_name, pod_node, pod_phase,
    pod_owner_kind, pod_total_requests, node_name, node_instance_type,
    node_allocatable, node_taints, node_labels, node_is_gpu,
    kubectl_remediation, doc_link_remediation, is_kube_system_ns, make_id,
    minutes_since, _DURATION_GATE_MIN,
)


def _price_map(probes: ProbeBundle) -> Dict[str, float]:
    """The pre-resolved on-demand USD/hr map produced by runnable._resolve_pricing.

    Empty dict when pricing failed; rules that declare `requires_probes=['_pricing']`
    are skipped before evaluate() runs, so this only returns {} when a rule
    forgot to declare the dependency and the source happened to fail anyway.
    """
    p = probes.get('_pricing') or {}
    if not p.get('ok'):
        return {}
    data = p.get('data') or {}
    return data.get('priceByType') or {}


def _hourly(probes: ProbeBundle, instance_type: str) -> Optional[float]:
    return _price_map(probes).get(instance_type)


def _monthly(probes: ProbeBundle, instance_type: str) -> Optional[float]:
    price = _hourly(probes, instance_type)
    return price * 730.0 if price is not None else None


class Rule14NodeOverProvisioned(Rule):
    id = 'node-over-provisioned'
    category = 'cost'
    severity = 'medium'
    requires_probes = ['probe_pods', 'probe_nodes', '_pricing']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        nodes = items(probes, 'probe_nodes')
        pods = items(probes, 'probe_pods')
        sums = _sum_requests_by_node(pods)
        for n in nodes:
            name = node_name(n)
            if node_is_gpu(n):
                continue
            # Duration gate (node-age proxy): a node scaled up < 10 min ago is
            # expected to look under-utilized while pods are still scheduling.
            # Node age is only a *proxy* — it excludes freshly scaled-up nodes
            # but cannot prove the node has been under-used the whole time.
            observed_min = minutes_since((n.get('metadata') or {}).get('creationTimestamp'))
            if observed_min < _DURATION_GATE_MIN:
                continue
            alloc_cpu, alloc_mem, _ = node_allocatable(n)
            cpu_req, mem_req = sums.get(name, (0, 0))
            if alloc_cpu == 0 or alloc_mem == 0:
                continue
            cpu_pct = cpu_req / alloc_cpu
            mem_pct = mem_req / alloc_mem
            worst = max(cpu_pct, mem_pct)
            if worst >= 0.25:
                continue
            instance = node_instance_type(n)
            cost = _monthly(probes, instance)
            if cost is None:
                continue
            out.append(Finding(
                id=make_id(self.id, name),
                rule=self.id,
                severity='medium',
                category=self.category,
                title=f'Node "{name}" is heavily over-provisioned ({worst*100:.0f}% peak)',
                summary=(
                    f'{instance} node uses only {cpu_req}m CPU / {mem_req}MiB memory out of '
                    f'{alloc_cpu}m / {alloc_mem}MiB allocatable. Drain candidate.'
                ),
                evidence={
                    'node': name,
                    'instanceType': instance,
                    'cpuRequestedMilli': cpu_req,
                    'cpuAllocatableMilli': alloc_cpu,
                    'memRequestedMib': mem_req,
                    'memAllocatableMib': alloc_mem,
                    'cpuPct': round(cpu_pct, 3),
                    'memPct': round(mem_pct, 3),
                    'observedForMinutes': round(observed_min, 1),
                },
                cost_impact_per_month=round(cost, 2),
                remediation=[
                    kubectl_remediation('Cordon', f'kubectl cordon {name}'),
                    kubectl_remediation('Drain', f'kubectl drain {name} --ignore-daemonsets --delete-emptydir-data'),
                ],
            ))
        return out


class Rule15NodeLockedBySinglePod(Rule):
    id = 'node-locked-by-single-pod'
    category = 'cost'
    severity = 'medium'
    requires_probes = ['probe_pods', 'probe_nodes', '_pricing']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        nodes_by_name = {node_name(n): n for n in items(probes, 'probe_nodes')}
        pods_by_node: Dict[str, List[Dict[str, Any]]] = {}
        for p in items(probes, 'probe_pods'):
            n = pod_node(p)
            if not n:
                continue
            if is_kube_system_ns(pod_namespace(p)):
                continue
            if pod_owner_kind(p) == 'DaemonSet':
                continue
            pods_by_node.setdefault(n, []).append(p)
        # Optional real usage
        top_pods = (probes.get('probe_top_pods') or {}).get('data') or []
        usage_by_pod: Dict[str, Tuple[int, int]] = {}
        for row in top_pods:
            key = f"{row.get('namespace')}/{row.get('pod')}"
            cpu, mem = usage_by_pod.get(key, (0, 0))
            usage_by_pod[key] = (cpu + int(row.get('cpuMilli') or 0), mem + int(row.get('memMib') or 0))

        for node_n, pods in pods_by_node.items():
            if len(pods) != 1:
                continue
            pod = pods[0]
            node = nodes_by_name.get(node_n)
            if not node:
                continue
            alloc_cpu, alloc_mem, _ = node_allocatable(node)
            cpu_req, mem_req, _ = pod_total_requests(pod)
            if alloc_cpu == 0 or alloc_mem == 0:
                continue
            worst_req = max(cpu_req / alloc_cpu, mem_req / alloc_mem)
            if worst_req < 0.70:
                continue
            key = f'{pod_namespace(pod)}/{pod_name(pod)}'
            usage = usage_by_pod.get(key)
            usage_evidence: Dict[str, Any] = {}
            if usage is not None:
                usage_evidence = {'realCpuMilli': usage[0], 'realMemMib': usage[1]}
            instance = node_instance_type(node)
            monthly = _monthly(probes, instance)
            if monthly is None:
                continue
            out.append(Finding(
                id=make_id(self.id, node_n),
                rule=self.id,
                severity='medium',
                category=self.category,
                title=f'Node "{node_n}" pinned open by single pod "{pod_name(pod)}"',
                summary=(
                    f'Node {node_n} ({instance}) is held open by one pod from namespace '
                    f'{pod_namespace(pod)} requesting {cpu_req}m CPU and {mem_req}MiB memory '
                    f'(~{worst_req*100:.0f}% of node). Check whether the user actually needs that much.'
                ),
                evidence={
                    'node': node_n,
                    'pod': key,
                    'instanceType': instance,
                    'requestedCpuMilli': cpu_req,
                    'requestedMemMib': mem_req,
                    'allocatableCpuMilli': alloc_cpu,
                    'allocatableMemMib': alloc_mem,
                    **usage_evidence,
                },
                cost_impact_per_month=round(monthly, 2),
                remediation=[
                    doc_link_remediation(
                        'Lower the execution config memRequestMB',
                        'https://doc.dataiku.com/dss/latest/containers/setup.html#execution-configurations',
                    ),
                    kubectl_remediation(
                        'See exactly what the pod is consuming',
                        f'kubectl -n {pod_namespace(pod)} top pod {pod_name(pod)} --containers',
                        namespace=pod_namespace(pod),
                    ),
                ],
            ))
        return out


def _build_usage_index(probes: ProbeBundle) -> Dict[str, Tuple[int, int]]:
    rows = (probes.get('probe_top_pods') or {}).get('data') or []
    out: Dict[str, Tuple[int, int]] = {}
    for r in rows:
        key = f"{r.get('namespace')}/{r.get('pod')}"
        cpu, mem = out.get(key, (0, 0))
        out[key] = (cpu + int(r.get('cpuMilli') or 0), mem + int(r.get('memMib') or 0))
    return out


def _pod_age_hours(pod: Dict[str, Any]) -> float:
    import datetime
    ts = ((pod.get('metadata') or {}).get('creationTimestamp'))
    if not ts:
        return 0.0
    try:
        dt = datetime.datetime.strptime(ts, '%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        return 0.0
    now = datetime.datetime.utcnow()
    return max(0.0, (now - dt).total_seconds() / 3600.0)


class Rule16PodOverRequestedMemory(Rule):
    id = 'pod-overrequested-memory'
    category = 'cost'
    severity = 'low'
    requires_probes = ['probe_pods', 'probe_top_pods']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        usage = _build_usage_index(probes)
        for pod in items(probes, 'probe_pods'):
            if is_kube_system_ns(pod_namespace(pod)):
                continue
            if pod_phase(pod) != 'Running':
                continue
            age_h = _pod_age_hours(pod)
            if age_h < 6:
                continue
            _, mem_req, _ = pod_total_requests(pod)
            if mem_req <= 0:
                continue
            real = usage.get(f'{pod_namespace(pod)}/{pod_name(pod)}')
            if not real:
                continue
            real_mem = real[1]
            if real_mem >= mem_req * 0.10:
                continue
            recommended = max(real_mem * 2, 512)
            out.append(Finding(
                id=make_id(self.id, f'{pod_namespace(pod)}/{pod_name(pod)}'),
                rule=self.id,
                severity='low',
                category=self.category,
                title=f'Pod "{pod_name(pod)}" requests ~{mem_req}MiB but uses {real_mem}MiB',
                summary=(
                    f'Memory request is more than 10x real usage after {age_h:.1f}h. Lowering the '
                    'request unlocks bin-packing.'
                ),
                evidence={
                    'pod': f'{pod_namespace(pod)}/{pod_name(pod)}',
                    'memRequestMib': mem_req,
                    'memUsageMib': real_mem,
                    'ageHours': round(age_h, 1),
                    'recommendedMemMib': int(recommended),
                },
            ))
        return out


class Rule17PodOverRequestedCpu(Rule):
    id = 'pod-overrequested-cpu'
    category = 'cost'
    severity = 'low'
    requires_probes = ['probe_pods', 'probe_top_pods']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        usage = _build_usage_index(probes)
        for pod in items(probes, 'probe_pods'):
            if is_kube_system_ns(pod_namespace(pod)):
                continue
            if pod_phase(pod) != 'Running':
                continue
            age_h = _pod_age_hours(pod)
            if age_h < 6:
                continue
            cpu_req, _, _ = pod_total_requests(pod)
            if cpu_req <= 0:
                continue
            real = usage.get(f'{pod_namespace(pod)}/{pod_name(pod)}')
            if not real:
                continue
            real_cpu = real[0]
            if real_cpu >= cpu_req * 0.10:
                continue
            recommended = max(real_cpu * 2, 100)
            out.append(Finding(
                id=make_id(self.id, f'{pod_namespace(pod)}/{pod_name(pod)}'),
                rule=self.id,
                severity='low',
                category=self.category,
                title=f'Pod "{pod_name(pod)}" requests {cpu_req}m CPU but uses {real_cpu}m',
                summary='Lower CPU request to free up capacity for bin-packing.',
                evidence={
                    'pod': f'{pod_namespace(pod)}/{pod_name(pod)}',
                    'cpuRequestMilli': cpu_req,
                    'cpuUsageMilli': real_cpu,
                    'recommendedCpuMilli': int(recommended),
                },
            ))
        return out


class Rule18PodUnderRequestedMemory(Rule):
    id = 'pod-underrequested-memory'
    category = 'cost'
    severity = 'high'
    requires_probes = ['probe_pods', 'probe_top_pods']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        usage = _build_usage_index(probes)
        for pod in items(probes, 'probe_pods'):
            if is_kube_system_ns(pod_namespace(pod)):
                continue
            if pod_phase(pod) != 'Running':
                continue
            _, mem_req, _ = pod_total_requests(pod)
            if mem_req <= 0:
                continue
            real = usage.get(f'{pod_namespace(pod)}/{pod_name(pod)}')
            if not real:
                continue
            real_mem = real[1]
            if real_mem <= mem_req:
                continue
            new_req = int(real_mem * 1.3)
            out.append(Finding(
                id=make_id(self.id, f'{pod_namespace(pod)}/{pod_name(pod)}'),
                rule=self.id,
                severity='high',
                category=self.category,
                title=f'Pod "{pod_name(pod)}" uses more memory than it requests',
                summary=(
                    f'Real usage {real_mem}MiB exceeds request {mem_req}MiB. Risks node OOM '
                    'when other pods on the node bin-pack against the stated request.'
                ),
                evidence={
                    'pod': f'{pod_namespace(pod)}/{pod_name(pod)}',
                    'memRequestMib': mem_req,
                    'memUsageMib': real_mem,
                    'recommendedMemMib': new_req,
                },
            ))
        return out


class Rule19IdleLongRunningPod(Rule):
    id = 'idle-long-running-pod'
    category = 'cost'
    severity = 'medium'
    requires_probes = ['probe_pods', 'probe_top_pods']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        usage = _build_usage_index(probes)
        for pod in items(probes, 'probe_pods'):
            if is_kube_system_ns(pod_namespace(pod)):
                continue
            if pod_phase(pod) != 'Running':
                continue
            age = _pod_age_hours(pod)
            if age < 24:
                continue
            cpu_req, mem_req, _ = pod_total_requests(pod)
            if cpu_req <= 0 and mem_req <= 0:
                continue
            real = usage.get(f'{pod_namespace(pod)}/{pod_name(pod)}')
            if not real:
                continue
            cpu_pct = real[0] / cpu_req if cpu_req > 0 else 0
            mem_pct = real[1] / mem_req if mem_req > 0 else 0
            worst = max(cpu_pct, mem_pct)
            if worst >= 0.01:
                continue
            out.append(Finding(
                id=make_id(self.id, f'{pod_namespace(pod)}/{pod_name(pod)}'),
                rule=self.id,
                severity='medium',
                category=self.category,
                title=f'Pod "{pod_name(pod)}" idle for {age:.0f}h ({worst*100:.1f}% of request)',
                summary=(
                    f'Pod in namespace {pod_namespace(pod)} has been running for {age:.0f}h with '
                    'near-zero usage. Likely abandoned user session.'
                ),
                evidence={
                    'pod': f'{pod_namespace(pod)}/{pod_name(pod)}',
                    'ageHours': round(age, 1),
                    'cpuPctOfRequest': round(cpu_pct, 4),
                    'memPctOfRequest': round(mem_pct, 4),
                },
                remediation=[
                    kubectl_remediation(
                        'Delete the pod (DSS will recreate if still needed)',
                        f'kubectl -n {pod_namespace(pod)} delete pod {pod_name(pod)}',
                        namespace=pod_namespace(pod),
                    ),
                ],
            ))
        return out


class Rule20GpuNodeIdle(Rule):
    id = 'gpu-node-idle'
    category = 'cost'
    severity = 'high'
    requires_probes = ['probe_nodes', 'probe_pods', '_pricing']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        gpu_use_by_node: Dict[str, int] = {}
        for pod in items(probes, 'probe_pods'):
            _, _, gpu = pod_total_requests(pod)
            if gpu <= 0:
                continue
            gpu_use_by_node[pod_node(pod)] = gpu_use_by_node.get(pod_node(pod), 0) + gpu
        for node in items(probes, 'probe_nodes'):
            if not node_is_gpu(node):
                continue
            name = node_name(node)
            used = gpu_use_by_node.get(name, 0)
            if used > 0:
                continue
            # Duration gate (node-age proxy): a GPU node scaled up < 10 min ago
            # may simply not have had its GPU pod scheduled yet. Node age is a
            # *proxy* — it excludes freshly scaled-up nodes but cannot prove the
            # node has been GPU-idle the whole time (best single-snapshot signal).
            observed_min = minutes_since((node.get('metadata') or {}).get('creationTimestamp'))
            if observed_min < _DURATION_GATE_MIN:
                continue
            instance = node_instance_type(node)
            monthly = _monthly(probes, instance)
            if monthly is None:
                continue
            out.append(Finding(
                id=make_id(self.id, name),
                rule=self.id,
                severity='high',
                category=self.category,
                title=f'GPU node "{name}" is idle ({instance})',
                summary=(
                    f'No pod on {name} is consuming nvidia.com/gpu. GPU nodes are extremely '
                    'expensive — if no one is using this, it should scale down.'
                ),
                evidence={'node': name, 'instanceType': instance, 'gpuUsage': used,
                          'observedForMinutes': round(observed_min, 1)},
                cost_impact_per_month=round(monthly, 2),
                remediation=[
                    kubectl_remediation('Cordon and drain', f'kubectl cordon {name} && kubectl drain {name} --ignore-daemonsets'),
                    doc_link_remediation(
                        'Scale GPU nodepool minSize down in DSS GUI',
                        'https://doc.dataiku.com/dss/latest/containers/eks/clusters.html',
                    ),
                ],
            ))
        return out


# Kubecost-style right-sizing: recommended request = observed usage / target
# utilization. 0.75 target == +33% headroom over the live sample.
RIGHTSIZE_TARGET_UTIL = 0.75


def _node_capacity(node: Dict[str, Any]) -> Tuple[int, int]:
    """(cpu_milli, mem_mib) from status.capacity; falls back to allocatable."""
    cap = ((node or {}).get('status') or {}).get('capacity') or {}
    cpu = parse_cpu_milli(cap.get('cpu'))
    mem = parse_mem_mib(cap.get('memory'))
    if cpu <= 0 or mem <= 0:
        acpu, amem, _ = node_allocatable(node)
        cpu = cpu if cpu > 0 else acpu
        mem = mem if mem > 0 else amem
    return cpu, mem


class Rule21ClusterFloorProjection(Rule):
    id = 'cluster-floor-projection'
    category = 'cost'
    severity = 'high'
    requires_probes = ['probe_pods', 'probe_nodes', '_pricing']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        nodes = items(probes, 'probe_nodes')
        pods = items(probes, 'probe_pods')
        if not nodes:
            return []
        price_by_type = _price_map(probes)

        usage = _build_usage_index(probes)
        active_pods = [p for p in pods if pod_phase(p) in ('Running', 'Pending')]
        pod_by_key = {f'{pod_namespace(p)}/{pod_name(p)}': p for p in active_pods}
        # Missing measurements are not zero demand (for example, a BestEffort
        # Code Studio between crash-loop restarts). Keep its entire current
        # server, including all colocated pods and rent, outside consolidation.
        # Unknown demand without an observed server still blocks the estimate.
        unknown_sizing = {
            key for key, p in pod_by_key.items()
            if key not in usage and not any(pod_total_requests(p)[:2])
        }
        observed_node_names = {node_name(n) for n in nodes}
        retained_node_names = {
            pod_node(pod_by_key[key]) for key in unknown_sizing
            if pod_node(pod_by_key[key]) in observed_node_names
        }
        unresolved_unknown = {
            key for key in unknown_sizing
            if pod_node(pod_by_key[key]) not in retained_node_names
        }
        packable_by_node: Dict[str, int] = {}
        for p in active_pods:
            if pod_owner_kind(p) != 'DaemonSet' and pod_node(p):
                packable_by_node[pod_node(p)] = packable_by_node.get(pod_node(p), 0) + 1
        # System deployments are movable workloads too; only per-node services
        # are replicated as overhead on each proposed server.
        idle_node_names = {node_name(n) for n in nodes
                           if not packable_by_node.get(node_name(n))
                           and node_name(n) not in retained_node_names}

        def overhead_for(node):
            out = []
            for p in active_pods:
                if pod_node(p) != node_name(node) or pod_owner_kind(p) != 'DaemonSet':
                    continue
                key = f'{pod_namespace(p)}/{pod_name(p)}'
                cpu, mem, gpu = pod_total_requests(p)
                real = usage.get(key)
                out.append(PodReq(key, pod_namespace(p), max(cpu, real[0] if real else 0),
                                  max(mem, real[1] if real else 0), gpu,
                                  node_selector=((p.get('spec') or {}).get('nodeSelector') or {}),
                                  tolerations=((p.get('spec') or {}).get('tolerations') or [])))
            return out

        # Only workload nodes feed the bin-pack.
        groups_by_node: Dict[str, NodeGroup] = {}
        sample_node_by_group: Dict[str, Dict[str, Any]] = {}
        full_count_by_instance: Dict[str, int] = {}
        for n in nodes:
            instance = node_instance_type(n)
            if not instance:
                continue
            full_count_by_instance[instance] = full_count_by_instance.get(instance, 0) + 1
            if node_name(n) in retained_node_names:
                continue
            if node_name(n) in idle_node_names and any(packable_by_node.values()):
                continue
            group_key = node_name(n)
            cpu, mem, gpu = node_allocatable(n)
            sample_node_by_group[group_key] = n
            groups_by_node[group_key] = NodeGroup(
                name=group_key,
                instance_type=instance,
                cpu_alloc_milli=cpu,
                mem_alloc_mib=mem,
                gpu_alloc=gpu,
                labels=node_labels(n),
                taints=node_taints(n),
                overhead_pods=overhead_for(n),
            )

        # Downsized same-family candidates (priced by _resolve_pricing): the
        # floor may keep a pool alive on a cheaper shape instead of only
        # deciding "keep or drop" the shapes that happen to exist today.
        # Allocatable = capacity * size-ratio - the observed node's fixed
        # overhead (capacity - allocatable), which under-credits small nodes
        # slightly — the conservative direction for a savings estimate.
        group_instance: Dict[str, str] = {g.name: g.instance_type for g in groups_by_node.values()}
        source_group = {g.name: g.name for g in groups_by_node.values()}
        node_groups: List[NodeGroup] = list(groups_by_node.values())
        for group_key, obs in groups_by_node.items():
            instance = obs.instance_type
            if obs.gpu_alloc > 0:
                continue  # GPU counts don't scale linearly with the size token
            cap_cpu, cap_mem = _node_capacity(sample_node_by_group[group_key])
            over_cpu = max(0, cap_cpu - obs.cpu_alloc_milli)
            over_mem = max(0, cap_mem - obs.mem_alloc_mib)
            for cand in family_downsize_types(instance):
                if cand not in price_by_type:
                    continue
                factor = downsize_factor(instance, cand)
                if not factor or factor >= 1:
                    continue
                alloc_cpu = int(cap_cpu * factor) - over_cpu
                alloc_mem = int(cap_mem * factor) - over_mem
                if alloc_cpu <= 0 or alloc_mem <= 0:
                    continue
                labels = dict(obs.labels)
                labels['node.kubernetes.io/instance-type'] = cand
                labels.pop('beta.kubernetes.io/instance-type', None)
                name = f'{cand}~{group_key}'
                node_groups.append(NodeGroup(
                    name=name,
                    instance_type=cand,
                    cpu_alloc_milli=alloc_cpu,
                    mem_alloc_mib=alloc_mem,
                    gpu_alloc=0,
                    labels=labels,
                    taints=list(obs.taints or []),
                    overhead_pods=obs.overhead_pods,
                ))
                group_instance[name] = cand
                source_group[name] = group_key

        packable = [p for p in active_pods if pod_owner_kind(p) != 'DaemonSet']

        def _pod_req(p: Dict[str, Any], cpu_req: int, mem_req: int, gpu_req: int) -> PodReq:
            return PodReq(
                name=f'{pod_namespace(p)}/{pod_name(p)}',
                namespace=pod_namespace(p),
                cpu_milli=cpu_req,
                mem_mib=mem_req,
                gpu=gpu_req,
                node_selector=((p.get('spec') or {}).get('nodeSelector') or {}),
                tolerations=((p.get('spec') or {}).get('tolerations') or []),
            )

        # -- "requests" sizing (Karpenter-style): declared requests are hard
        # constraints. Zero-request pods (DSS exec configs / API deployments
        # often set none) still occupy a node: pack them by live usage when
        # metrics exist. Unsized pods retain their current server; without a
        # known current server they keep the projection incomplete.
        req_pods: List[PodReq] = []
        usage_packed = 0
        unsized_packed = 0
        for p in packable:
            cpu_req, mem_req, gpu_req = pod_total_requests(p)
            if cpu_req <= 0 and mem_req <= 0 and gpu_req <= 0:
                real = usage.get(f'{pod_namespace(p)}/{pod_name(p)}')
                if real:
                    cpu_req, mem_req = real
                    usage_packed += 1
                else:
                    unsized_packed += 1
            real = usage.get(f'{pod_namespace(p)}/{pod_name(p)}')
            if real is not None:
                cpu_req, mem_req = max(cpu_req, real[0]), max(mem_req, real[1])
            req_pods.append(_pod_req(p, cpu_req, mem_req, gpu_req))

        # -- "rightsized" sizing (Kubecost-style): every pod with usage
        # metrics is sized at observed usage / target utilization, treating
        # requests as adjustable (they are — DSS containerized execution
        # configs own them). Pods without usage keep declared requests; GPU
        # requests are never right-sized.
        rs_pods: List[PodReq] = []
        rightsized = 0
        for p in packable:
            cpu_req, mem_req, gpu_req = pod_total_requests(p)
            real = usage.get(f'{pod_namespace(p)}/{pod_name(p)}')
            if real is not None and not is_kube_system_ns(pod_namespace(p)):
                cpu_req = ceil(real[0] / RIGHTSIZE_TARGET_UTIL)
                mem_req = ceil(real[1] / RIGHTSIZE_TARGET_UTIL)
                rightsized += 1
            if real is not None:
                cpu_req, mem_req = max(cpu_req, real[0]), max(mem_req, real[1])
            rs_pods.append(_pod_req(p, cpu_req, mem_req, gpu_req))

        current_hourly = sum((price_by_type.get(inst) or 0.0) * cnt for inst, cnt in full_count_by_instance.items())
        current_monthly = round(sum(round((price_by_type.get(inst) or 0.0) * 730, 2) * cnt for inst, cnt in full_count_by_instance.items()), 2)
        total_nodes = sum(full_count_by_instance.values())

        def _project(mode: str, pod_reqs: List[PodReq]) -> Dict[str, Any]:
            movable = [p for p in pod_reqs
                       if pod_node(pod_by_key[p.name]) not in retained_node_names
                       and p.name not in unresolved_unknown]
            result = compute_floor(movable, node_groups, price_by_type)
            floor_hourly = 0.0
            by_instance: Dict[str, int] = {}
            for n in nodes:
                if node_name(n) in retained_node_names:
                    inst = node_instance_type(n)
                    by_instance[inst] = by_instance.get(inst, 0) + 1
                    floor_hourly += price_by_type.get(inst) or 0.0
            for grp, count in result.by_group.items():
                if count <= 0:
                    continue
                inst = group_instance.get(grp, grp)
                by_instance[inst] = by_instance.get(inst, 0) + count
                floor_hourly += (price_by_type.get(inst) or 0.0) * count
            floor_breakdown = [
                {'instanceType': inst, 'count': cnt, 'hourly': (price_by_type.get(inst) or 0.0) * cnt}
                for inst, cnt in by_instance.items()
            ]
            total_savings_hourly = max(0.0, current_hourly - floor_hourly)
            floor_nodes = sum(by_instance.values())

            floor_monthly = round(sum(round((price_by_type.get(inst) or 0.0) * 730, 2) * cnt for inst, cnt in by_instance.items()), 2)
            savings_monthly = round(max(0, current_monthly - floor_monthly), 2)
            idle_monthly = min(savings_monthly, round(sum(round((price_by_type.get(node_instance_type(n)) or 0.0) * 730, 2) for n in nodes if node_name(n) in idle_node_names), 2))
            group_by_name = {g.name: g for g in node_groups}
            sized_by_name = {p.name: p for p in pod_reqs}

            def projected_pod(sized):
                source = pod_by_key[sized.name]
                real = usage.get(sized.name)
                statuses = (source.get('status') or {}).get('containerStatuses') or []
                waiting_reasons = [((s.get('state') or {}).get('waiting') or {}).get('reason')
                                   for s in statuses]
                return {
                    'key': sized.name, 'name': pod_name(source), 'ns': pod_namespace(source),
                    'sourceNode': pod_node(source),
                    'isSystem': is_kube_system_ns(pod_namespace(source)) or pod_owner_kind(source) == 'DaemonSet',
                    'perNodeService': pod_owner_kind(source) == 'DaemonSet',
                    'statusReason': next((r for r in waiting_reasons if r), pod_phase(source)),
                    'realCpuMilli': real[0] if real is not None else None,
                    'realMemMib': real[1] if real is not None else None,
                    'reservedCpuMilli': sized.cpu_milli, 'reservedMemMib': sized.mem_mib,
                }

            projected_nodes = []
            for n in nodes:
                if node_name(n) not in retained_node_names:
                    continue
                cpu, mem, _ = node_allocatable(n)
                assigned = [p for p in pod_reqs if pod_node(pod_by_key[p.name]) == node_name(n)]
                assigned += overhead_for(n)
                projected_nodes.append({
                    'id': node_name(n), 'instanceType': node_instance_type(n),
                    'hourly': price_by_type.get(node_instance_type(n)),
                    'cpuCapacityMilli': cpu, 'memoryCapacityMib': mem,
                    'pods': [projected_pod(p) for p in assigned],
                    'retainedForPods': sorted(key for key in unknown_sizing
                                              if pod_node(pod_by_key[key]) == node_name(n)),
                    'sizeChecks': [],
                })
            for index, placement in enumerate(result.placements):
                group = group_by_name[placement.group]
                assigned = [sized_by_name[key] for key in placement.pods] + group.overhead_pods
                projected_nodes.append({
                    'id': f'proposed-{index + 1}', 'instanceType': group.instance_type,
                    'hourly': price_by_type.get(group.instance_type),
                    'cpuCapacityMilli': group.cpu_alloc_milli, 'memoryCapacityMib': group.mem_alloc_mib,
                    'pods': [projected_pod(p) for p in assigned],
                    'sizeChecks': placement_size_checks(
                        [sized_by_name[key] for key in placement.pods],
                        [candidate for candidate in node_groups
                         if source_group[candidate.name] == source_group[group.name]
                         and candidate.instance_type in family_downsize_types(group.instance_type)],
                    ),
                })
            missing_prices = [node_name(n) for n in nodes if node_instance_type(n) not in price_by_type]
            placement_complete = not result.unplaceable and not unresolved_unknown and not missing_prices
            title = f'Server consolidation: {total_nodes} → {floor_nodes} nodes'
            basis = 'observed usage +33%' if mode == 'rightsized' else 'current requests or observed usage, whichever is higher'
            summary = (
                f'{total_nodes} current nodes (${current_monthly:.2f}/mo); '
                f'{floor_nodes} proposed nodes (${floor_monthly:.2f}/mo). '
                f'Placement uses {basis}; system deployments and per-node services are included. '
                'Pod bars use the selected sizing on both sides. '
                'Node selectors, taints, CPU, memory and GPU capacity are checked; '
                'affinity, storage topology, disruption budgets and peak demand require review.'
            )
            if retained_node_names:
                summary += (
                    f' {len(retained_node_names)} current servers are kept unchanged because pod sizing is unavailable; '
                    'all their pods and full rental costs remain in the proposal. Savings come only from other servers.'
                )
            if not placement_complete:
                summary = 'Projection incomplete: some pods could not be sized or placed, or node pricing is missing. No savings estimate is available.'
            return {
                'title': title,
                'summary': summary,
                'floorHourly': round(floor_hourly, 3) if placement_complete else None,
                'floorMonthly': floor_monthly if placement_complete else None,
                'savingsHourly': round(total_savings_hourly, 3) if placement_complete else None,
                'savingsMonthly': savings_monthly if placement_complete else None,
                'consolidationSavingsMonthly': round(savings_monthly - idle_monthly, 2) if placement_complete else None,
                'idleNodeSavingsMonthly': idle_monthly if placement_complete else None,
                'floorBreakdown': floor_breakdown,
                'placementNodes': projected_nodes,
                'placementComplete': placement_complete,
                'unknownSizingPods': sorted(unknown_sizing),
                'unpricedNodes': missing_prices,
                'unplaceablePods': result.unplaceable,
                'podsPackedByUsage': usage_packed if mode == 'requests' else None,
                'podsRightsized': rightsized if mode == 'rightsized' else None,
                'podsWithoutRequestsOrUsage': unsized_packed,
            }

        projections = {
            'rightsized': _project('rightsized', rs_pods),
            'requests': _project('requests', req_pods),
        }
        default = projections['rightsized']
        # Keep a zero-savings result so the comparison still has real assignments.

        evidence: Dict[str, Any] = {
            'currentHourly': round(current_hourly, 3),
            'currentMonthly': current_monthly,
            'idleNodeCount': len(idle_node_names),
            'currentByInstance': full_count_by_instance,
            'projections': projections,
            'defaultProjection': 'rightsized',
        }
        # Top-level scalars mirror the default projection so older frontends
        # (and the overview card fallback) keep reading the same keys.
        evidence.update({k: v for k, v in default.items() if k not in ('title', 'summary')})
        return [Finding(
            id=make_id(self.id, 'cluster'),
            rule=self.id,
            severity='high' if (default['savingsMonthly'] or 0) > 0 else 'info',
            category='cost',
            title=default['title'],
            summary=default['summary'],
            evidence=evidence,
            cost_impact_per_month=default['savingsMonthly'],
            remediation=[
                doc_link_remediation(
                    'Action plan: address the per-pod overrequest findings, then cordon empty nodes',
                    'https://kubernetes.io/docs/tasks/administer-cluster/cluster-management/#decommissioning-a-node',
                ),
            ],
        )]


def _sum_requests_by_node(pods: List[Dict[str, Any]]) -> Dict[str, Tuple[int, int]]:
    out: Dict[str, Tuple[int, int]] = {}
    for p in pods:
        n = pod_node(p)
        if not n:
            continue
        cpu, mem, _ = pod_total_requests(p)
        cur_cpu, cur_mem = out.get(n, (0, 0))
        out[n] = (cur_cpu + cpu, cur_mem + mem)
    return out


RULES = [
    Rule14NodeOverProvisioned(),
    Rule15NodeLockedBySinglePod(),
    Rule16PodOverRequestedMemory(),
    Rule17PodOverRequestedCpu(),
    Rule18PodUnderRequestedMemory(),
    Rule19IdleLongRunningPod(),
    Rule20GpuNodeIdle(),
    Rule21ClusterFloorProjection(),
]
