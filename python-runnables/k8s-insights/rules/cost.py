"""Cost / bin-packing rules (14–21).

Most expensive/lasting savings live here, especially:
- Rule 15: a single oversized pod locking a node open
- Rule 21: full bin-pack floor projection ("you could go from 16 to 2 nodes")
"""
from typing import Any, Dict, List, Optional, Tuple

from finding import Finding  # type: ignore
from binpack import (  # type: ignore
    PodReq, NodeGroup, compute_floor, downsize_factor, family_downsize_types,
    parse_cpu_milli, parse_mem_mib,
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

        # GPU usage + packable-pod count per node, to flag idle nodes.
        gpu_use_by_node: Dict[str, int] = {}
        packable_by_node: Dict[str, int] = {}
        for p in pods:
            node_n = pod_node(p)
            _, _, gpu_req = pod_total_requests(p)
            if gpu_req > 0 and node_n:
                gpu_use_by_node[node_n] = gpu_use_by_node.get(node_n, 0) + gpu_req
            if pod_owner_kind(p) == 'DaemonSet' or is_kube_system_ns(pod_namespace(p)):
                continue
            if node_n:
                packable_by_node[node_n] = packable_by_node.get(node_n, 0) + 1

        # Idle nodes: an idle GPU node (nothing consumes its GPU) or an empty node
        # (no non-DaemonSet, non-kube-system pod). These are reclaimable outright —
        # "saving if the node weren't running" = its full price — so we pull them
        # out of the bin-pack and credit their full cost separately, instead of
        # letting the floor mislabel them as workload consolidation.
        idle_node_names: set = set()
        idle_hourly = 0.0
        for n in nodes:
            name = node_name(n)
            instance = node_instance_type(n)
            if not instance:
                continue
            is_idle_gpu = node_is_gpu(n) and gpu_use_by_node.get(name, 0) <= 0
            is_empty = packable_by_node.get(name, 0) == 0
            if is_idle_gpu or is_empty:
                idle_node_names.add(name)
                idle_hourly += (price_by_type.get(instance) or 0.0)

        # Only workload nodes feed the bin-pack.
        groups_by_instance: Dict[str, NodeGroup] = {}
        sample_node_by_instance: Dict[str, Dict[str, Any]] = {}
        workload_count_by_instance: Dict[str, int] = {}
        full_count_by_instance: Dict[str, int] = {}
        for n in nodes:
            instance = node_instance_type(n)
            if not instance:
                continue
            full_count_by_instance[instance] = full_count_by_instance.get(instance, 0) + 1
            if node_name(n) in idle_node_names:
                continue
            workload_count_by_instance[instance] = workload_count_by_instance.get(instance, 0) + 1
            if instance in groups_by_instance:
                continue
            cpu, mem, gpu = node_allocatable(n)
            sample_node_by_instance[instance] = n
            groups_by_instance[instance] = NodeGroup(
                name=instance,
                instance_type=instance,
                cpu_alloc_milli=cpu,
                mem_alloc_mib=mem,
                gpu_alloc=gpu,
                labels=node_labels(n),
                taints=node_taints(n),
            )

        # Downsized same-family candidates (priced by _resolve_pricing): the
        # floor may keep a pool alive on a cheaper shape instead of only
        # deciding "keep or drop" the shapes that happen to exist today.
        # Allocatable = capacity * size-ratio - the observed node's fixed
        # overhead (capacity - allocatable), which under-credits small nodes
        # slightly — the conservative direction for a savings estimate.
        group_instance: Dict[str, str] = {g.name: g.instance_type for g in groups_by_instance.values()}
        node_groups: List[NodeGroup] = list(groups_by_instance.values())
        for instance, obs in groups_by_instance.items():
            if obs.gpu_alloc > 0:
                continue  # GPU counts don't scale linearly with the size token
            cap_cpu, cap_mem = _node_capacity(sample_node_by_instance[instance])
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
                name = f'{cand}~{instance}'
                node_groups.append(NodeGroup(
                    name=name,
                    instance_type=cand,
                    cpu_alloc_milli=alloc_cpu,
                    mem_alloc_mib=alloc_mem,
                    gpu_alloc=0,
                    labels=labels,
                    taints=list(obs.taints or []),
                ))
                group_instance[name] = cand

        usage = _build_usage_index(probes)
        packable = [
            p for p in pods
            if pod_owner_kind(p) != 'DaemonSet'
            # kube-system is required overhead; bin-pack assumes one of each group hosts it.
            and not is_kube_system_ns(pod_namespace(p))
            and pod_phase(p) in ('Running', 'Pending')
        ]

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
        # metrics exist, else at zero size — either way they keep the floor
        # at >= 1 node instead of letting it reach 0 and claim ~100% savings.
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
            if real and (real[0] > 0 or real[1] > 0):
                cpu_req = int(real[0] / RIGHTSIZE_TARGET_UTIL)
                mem_req = int(real[1] / RIGHTSIZE_TARGET_UTIL)
                rightsized += 1
            rs_pods.append(_pod_req(p, cpu_req, mem_req, gpu_req))

        current_hourly = sum((price_by_type.get(inst) or 0.0) * cnt for inst, cnt in full_count_by_instance.items())
        workload_current_hourly = sum((price_by_type.get(inst) or 0.0) * cnt for inst, cnt in workload_count_by_instance.items())
        total_nodes = sum(full_count_by_instance.values())
        workload_nodes = sum(workload_count_by_instance.values())
        headroom_pct = int(round((1.0 / RIGHTSIZE_TARGET_UTIL - 1.0) * 100))

        def _project(mode: str, pod_reqs: List[PodReq]) -> Dict[str, Any]:
            result = compute_floor(pod_reqs, node_groups, price_by_type)
            floor_hourly = 0.0
            by_instance: Dict[str, int] = {}
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
            consolidation_hourly = max(0.0, workload_current_hourly - floor_hourly)
            total_savings_hourly = idle_hourly + consolidation_hourly
            floor_nodes = sum(by_instance.values())

            caveats = ''
            if mode == 'rightsized':
                if rightsized:
                    caveats += (
                        f' {rightsized} pod(s) were sized from live usage +{headroom_pct}% headroom; '
                        f'{len(pod_reqs) - rightsized} had no usage metrics and kept their declared requests.'
                    )
            elif usage_packed:
                caveats += (
                    f' {usage_packed} pod(s) declare no resource requests and were packed by '
                    'live usage instead — set real requests to firm up this estimate.'
                )
            if unsized_packed:
                caveats += (
                    f' {unsized_packed} pod(s) have no requests and no usage metrics; they were '
                    'held at zero size, so the floor may be optimistic.'
                )

            if mode == 'rightsized':
                title = (
                    f'Cluster floor: ~${total_savings_hourly:.2f}/hr (${total_savings_hourly*730:.0f}/mo) '
                    'savings via idle reclaim + right-sized bin-pack'
                )
                pack_clause = (
                    f'bin-pack to {floor_nodes} node(s) (~${floor_hourly:.2f}/hr) once pod requests are '
                    f'right-sized to observed usage +{headroom_pct}% headroom (Kubecost-style; requests are '
                    'adjustable in containerized execution configs).'
                )
            else:
                title = (
                    f'Cluster floor: ~${total_savings_hourly:.2f}/hr (${total_savings_hourly*730:.0f}/mo) '
                    'savings via idle reclaim + bin-pack (requests honored)'
                )
                pack_clause = (
                    f'bin-pack to {floor_nodes} node(s) (~${floor_hourly:.2f}/hr) with declared pod requests '
                    'honored as-is (Karpenter-style — no workload config changes).'
                )
            summary = (
                f'Current spend ~${current_hourly:.2f}/hr across {total_nodes} nodes. '
                f'{len(idle_node_names)} idle/empty node(s) (~${idle_hourly:.2f}/hr) are reclaimable '
                f'outright; the remaining {workload_nodes} workload node(s) {pack_clause}'
                + caveats
            )
            return {
                'title': title,
                'summary': summary,
                'floorHourly': round(floor_hourly, 3),
                'floorMonthly': round(floor_hourly * 730, 2),
                'savingsHourly': round(total_savings_hourly, 3),
                'savingsMonthly': round(total_savings_hourly * 730, 2),
                'consolidationSavingsMonthly': round(consolidation_hourly * 730, 2),
                'idleNodeSavingsMonthly': round(idle_hourly * 730, 2),
                'floorBreakdown': floor_breakdown,
                'unplaceablePods': result.unplaceable[:20],
                'podsPackedByUsage': usage_packed if mode == 'requests' else None,
                'podsRightsized': rightsized if mode == 'rightsized' else None,
                'podsWithoutRequestsOrUsage': unsized_packed,
            }

        projections = {
            'rightsized': _project('rightsized', rs_pods),
            'requests': _project('requests', req_pods),
        }
        default = projections['rightsized']
        if max(p['savingsHourly'] for p in projections.values()) <= 0:
            return []

        evidence: Dict[str, Any] = {
            'currentHourly': round(current_hourly, 3),
            'currentMonthly': round(current_hourly * 730, 2),
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
            severity='high',
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
