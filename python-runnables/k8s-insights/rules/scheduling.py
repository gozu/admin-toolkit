"""Scheduling rules (1–8).

Cover: DaemonSet placement, pending pods, GPU/CPU pool mixing, system-pod
co-location. The headliner is rule 1, the nvidia-device-plugin DS-everywhere
issue.
"""
from typing import Any, Dict, List, Optional

from finding import Finding  # type: ignore
from .base import (
    Rule, ProbeBundle, items, pod_namespace, pod_name, pod_node, pod_phase,
    pod_containers, pod_total_requests, node_name, node_labels,
    node_instance_type, node_allocatable, node_taints, node_is_gpu,
    kubectl_remediation, file_edit_remediation, doc_link_remediation,
    is_kube_system_ns, make_id, minutes_since, _DURATION_GATE_MIN,
)
from .cost import _price_map


NVIDIA_AFFINITY_PATCH = '''spec:
  template:
    spec:
      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
              - matchExpressions:
                  - key: feature.node.kubernetes.io/pci-10de.present
                    operator: In
                    values: ["true"]
              - matchExpressions:
                  - key: nvidia.com/gpu.present
                    operator: In
                    values: ["true"]
              - matchExpressions:
                  - key: node.kubernetes.io/instance-type
                    operator: In
                    values:
                      - g4dn.xlarge
                      - g5.xlarge
                      - p3.2xlarge
'''


class Rule01DaemonSetNoNodeTargeting(Rule):
    id = 'daemonset-no-node-targeting'
    category = 'scheduling'
    severity = 'high'
    requires_probes = ['probe_daemonsets', 'probe_nodes']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        ds_items = items(probes, 'probe_daemonsets')
        node_items = items(probes, 'probe_nodes')
        if not ds_items or not node_items:
            return out

        # Are there node groups likely to be GPU vs CPU distinct?
        gpu_nodes = [n for n in node_items if node_is_gpu(n)]
        cpu_nodes = [n for n in node_items if not node_is_gpu(n)]
        mixed_cluster = bool(gpu_nodes) and bool(cpu_nodes)

        for ds in ds_items:
            meta = (ds.get('metadata') or {})
            name = meta.get('name') or ''
            ns = meta.get('namespace') or ''
            # GPU-related DaemonSets are the dangerous ones.
            gpu_related = any(s in name.lower() for s in ('nvidia', 'gpu', 'device-plugin'))
            spec_template = ((ds.get('spec') or {}).get('template') or {}).get('spec') or {}
            node_selector = spec_template.get('nodeSelector') or {}
            affinity = spec_template.get('affinity') or {}
            tolerations = spec_template.get('tolerations') or []

            if node_selector or affinity:
                continue
            # If the DS tolerates the nvidia.com/gpu taint, it'll only land on
            # GPU nodes anyway (assuming the GPU pool is tainted). If neither
            # the DS nor the pool is constraining, this is the bug.
            tolerates_gpu = any(
                (t.get('key') == 'nvidia.com/gpu') for t in tolerations
            )

            if not (gpu_related and mixed_cluster):
                continue

            status = ds.get('status') or {}
            desired = status.get('desiredNumberScheduled') or 0
            cpu_only_nodes = len(cpu_nodes)
            severity = 'high' if not tolerates_gpu else 'medium'

            out.append(Finding(
                id=make_id(self.id, f'{ns}/{name}'),
                rule=self.id,
                severity=severity,
                category=self.category,
                title=f'DaemonSet "{name}" runs on every node, no GPU/CPU targeting',
                summary=(
                    f'The {name} DaemonSet in {ns} has no nodeSelector or affinity, '
                    f'so it schedules on all {desired} nodes — including the {cpu_only_nodes} '
                    'CPU-only nodes where it has no purpose. This wastes a pod slot on every '
                    'CPU node and can interact badly with cluster-autoscaler scale-down.'
                ),
                evidence={
                    'daemonset': f'{ns}/{name}',
                    'desiredNumberScheduled': desired,
                    'currentNumberScheduled': status.get('currentNumberScheduled'),
                    'nodeSelector': node_selector,
                    'tolerationsCount': len(tolerations),
                    'gpuNodeCount': len(gpu_nodes),
                    'cpuNodeCount': len(cpu_nodes),
                },
                remediation=[
                    file_edit_remediation(
                        'Patch the DaemonSet with the upstream NVIDIA affinity block',
                        path=f'kubectl -n {ns} edit ds {name}',
                        snippet=NVIDIA_AFFINITY_PATCH,
                    ),
                    kubectl_remediation(
                        'One-shot patch',
                        f'kubectl -n {ns} patch ds {name} --type strategic --patch-file affinity.yaml',
                        namespace=ns,
                    ),
                    doc_link_remediation(
                        'NVIDIA device-plugin Helm chart (canonical affinity)',
                        'https://github.com/NVIDIA/k8s-device-plugin',
                    ),
                ],
            ))
        return out


class Rule02DaemonSetCrashLoop(Rule):
    id = 'daemonset-crashloop'
    category = 'scheduling'
    severity = 'high'
    requires_probes = ['probe_daemonsets', 'probe_pods']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        ds_items = items(probes, 'probe_daemonsets')
        pod_items = items(probes, 'probe_pods')
        if not ds_items:
            return out
        for ds in ds_items:
            meta = ds.get('metadata') or {}
            name = meta.get('name')
            ns = meta.get('namespace')
            uid = meta.get('uid')
            selector = ((ds.get('spec') or {}).get('selector') or {}).get('matchLabels') or {}
            ds_pods = [p for p in pod_items if _pod_owned_by(p, uid)]
            if not ds_pods:
                continue
            crashing = []
            for p in ds_pods:
                for cs in ((p.get('status') or {}).get('containerStatuses') or []):
                    waiting = (cs.get('state') or {}).get('waiting') or {}
                    if waiting.get('reason') in ('CrashLoopBackOff', 'ImagePullBackOff', 'CreateContainerError'):
                        crashing.append((pod_name(p), waiting.get('reason')))
                        break
            if not crashing:
                continue
            ratio = len(crashing) / max(len(ds_pods), 1)
            if ratio < 0.5 and len(crashing) < 3:
                continue
            out.append(Finding(
                id=make_id(self.id, f'{ns}/{name}'),
                rule=self.id,
                severity='critical' if ratio >= 0.9 else 'high',
                category=self.category,
                title=f'DaemonSet "{name}" crashing on {len(crashing)}/{len(ds_pods)} nodes',
                summary=(
                    f'{ns}/{name} pods are stuck in CrashLoopBackOff on most nodes — '
                    'usually means the container image is broken, the node lacks the kernel '
                    'driver the DS expects, or RBAC is denying API access.'
                ),
                evidence={
                    'daemonset': f'{ns}/{name}',
                    'totalPods': len(ds_pods),
                    'crashingPods': crashing[:20],
                    'selector': selector,
                },
                remediation=[
                    kubectl_remediation(
                        'Inspect a failing pod',
                        f'kubectl -n {ns} describe pod <one-of-the-crashing-pods>',
                        namespace=ns,
                    ),
                    kubectl_remediation(
                        'Tail the container log',
                        f'kubectl -n {ns} logs <one-of-the-crashing-pods> --previous',
                        namespace=ns,
                    ),
                ],
            ))
        return out


class Rule03PodPendingInsufficientResources(Rule):
    id = 'pod-pending-insufficient-resources'
    category = 'scheduling'
    severity = 'high'
    requires_probes = ['probe_pods', 'probe_events']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        pod_items = items(probes, 'probe_pods')
        event_items = items(probes, 'probe_events')
        # Index events by involved object name
        msgs_by_pod: Dict[str, List[str]] = {}
        for e in event_items:
            if e.get('reason') != 'FailedScheduling':
                continue
            obj = e.get('involvedObject') or {}
            if obj.get('kind') != 'Pod':
                continue
            key = f"{obj.get('namespace')}/{obj.get('name')}"
            msgs_by_pod.setdefault(key, []).append(e.get('message') or '')

        for pod in pod_items:
            if pod_phase(pod) != 'Pending':
                continue
            key = f"{pod_namespace(pod)}/{pod_name(pod)}"
            msgs = msgs_by_pod.get(key) or []
            if not msgs:
                continue
            # Duration gate: a pod Pending for < 10 min is normal autoscaling
            # churn (the cluster-autoscaler is still spinning up a node). Only
            # flag once the Pending state has persisted past the transient window.
            observed_min = minutes_since((pod.get('metadata') or {}).get('creationTimestamp'))
            if observed_min < _DURATION_GATE_MIN:
                continue
            cpu_req, mem_req, gpu_req = pod_total_requests(pod)
            out.append(Finding(
                id=make_id(self.id, key),
                rule=self.id,
                severity='high',
                category=self.category,
                title=f'Pod "{pod_name(pod)}" pending — insufficient cluster capacity',
                summary=(
                    f'Pod {key} cannot be scheduled. Scheduler events suggest no node has '
                    f'enough free CPU/memory/GPU. Requested: cpu={cpu_req}m mem={mem_req}MiB gpu={gpu_req}. '
                    f'Pending for ≥{_DURATION_GATE_MIN}min (transient autoscale window excluded).'
                ),
                evidence={
                    'pod': key,
                    'requestedCpuMilli': cpu_req,
                    'requestedMemMib': mem_req,
                    'requestedGpu': gpu_req,
                    'recentSchedulerMessages': msgs[-3:],
                    'observedForMinutes': round(observed_min, 1),
                },
                remediation=[
                    kubectl_remediation(
                        'Inspect the pod',
                        f'kubectl -n {pod_namespace(pod)} describe pod {pod_name(pod)}',
                        namespace=pod_namespace(pod),
                    ),
                    doc_link_remediation(
                        'DSS: lower memRequestMB on the matching execution config',
                        'https://doc.dataiku.com/dss/latest/containers/setup.html#execution-configurations',
                    ),
                ],
            ))
        return out


class Rule04PodPendingNoMatchingSelector(Rule):
    id = 'pod-pending-no-matching-selector'
    category = 'scheduling'
    severity = 'high'
    requires_probes = ['probe_pods', 'probe_nodes']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        node_items = items(probes, 'probe_nodes')
        node_label_sets = [node_labels(n) for n in node_items]
        for pod in items(probes, 'probe_pods'):
            if pod_phase(pod) != 'Pending':
                continue
            selector = ((pod.get('spec') or {}).get('nodeSelector') or {})
            if not selector:
                continue
            if any(_labels_match(selector, ls) for ls in node_label_sets):
                continue
            # Duration gate: a freshly-created pod whose selector matches no
            # *current* node may just be waiting for the autoscaler to bring up
            # the targeted nodepool. Only flag once it has waited past 10 min.
            observed_min = minutes_since((pod.get('metadata') or {}).get('creationTimestamp'))
            if observed_min < _DURATION_GATE_MIN:
                continue
            out.append(Finding(
                id=make_id(self.id, f'{pod_namespace(pod)}/{pod_name(pod)}'),
                rule=self.id,
                severity='high',
                category=self.category,
                title=f'Pod "{pod_name(pod)}" pending — no node matches its nodeSelector',
                summary=(
                    f'The pod targets labels {selector!r}, but no node carries that combination. '
                    'Either the targeted nodepool was deleted, or the labels were never applied. '
                    f'Pending for ≥{_DURATION_GATE_MIN}min (transient autoscale window excluded).'
                ),
                evidence={
                    'pod': f'{pod_namespace(pod)}/{pod_name(pod)}',
                    'nodeSelector': selector,
                    'observedNodeLabelKeys': sorted({k for ls in node_label_sets for k in ls.keys()})[:30],
                    'observedForMinutes': round(observed_min, 1),
                },
                remediation=[
                    kubectl_remediation(
                        'List node labels',
                        'kubectl get nodes --show-labels',
                    ),
                    doc_link_remediation(
                        'DSS GUI: add labels to a managed nodepool',
                        'https://doc.dataiku.com/dss/latest/containers/eks/clusters.html',
                    ),
                ],
            ))
        return out


class Rule05GpuPodOnCpuNode(Rule):
    id = 'gpu-pod-on-cpu-node'
    category = 'scheduling'
    severity = 'high'
    requires_probes = ['probe_pods', 'probe_nodes']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        nodes_by_name = {node_name(n): n for n in items(probes, 'probe_nodes')}
        for pod in items(probes, 'probe_pods'):
            node = pod_node(pod)
            if not node:
                continue
            _, _, gpu_req = pod_total_requests(pod)
            if gpu_req <= 0:
                continue
            n = nodes_by_name.get(node)
            if not n:
                continue
            _, _, alloc_gpu = node_allocatable(n)
            if alloc_gpu >= gpu_req:
                continue
            out.append(Finding(
                id=make_id(self.id, f'{pod_namespace(pod)}/{pod_name(pod)}'),
                rule=self.id,
                severity='high',
                category=self.category,
                title=f'GPU pod "{pod_name(pod)}" landed on a non-GPU node',
                summary=(
                    f'Pod requests {gpu_req} nvidia.com/gpu but node {node} only advertises '
                    f'{alloc_gpu}. Should never happen — admission usually blocks this.'
                ),
                evidence={
                    'pod': f'{pod_namespace(pod)}/{pod_name(pod)}',
                    'node': node,
                    'requestedGpu': gpu_req,
                    'nodeAllocatableGpu': alloc_gpu,
                },
                remediation=[
                    kubectl_remediation('Inspect node', f'kubectl describe node {node}'),
                ],
            ))
        return out


class Rule06CpuPodOnGpuNode(Rule):
    id = 'cpu-pod-on-gpu-node'
    category = 'scheduling'
    severity = 'critical'
    requires_probes = ['probe_pods', 'probe_nodes']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        nodes_by_name = {node_name(n): n for n in items(probes, 'probe_nodes')}
        gpu_nodes_unguarded: Dict[str, Dict[str, Any]] = {}
        for name, n in nodes_by_name.items():
            if not node_is_gpu(n):
                continue
            taints = node_taints(n)
            if any(t.get('key') == 'nvidia.com/gpu' for t in taints):
                continue
            gpu_nodes_unguarded[name] = n
        if not gpu_nodes_unguarded:
            return out
        # Group offending pods by node so each finding is per-node.
        by_node: Dict[str, List[str]] = {}
        for pod in items(probes, 'probe_pods'):
            node = pod_node(pod)
            if node not in gpu_nodes_unguarded:
                continue
            _, _, gpu = pod_total_requests(pod)
            if gpu > 0:
                continue
            if is_kube_system_ns(pod_namespace(pod)):
                continue
            by_node.setdefault(node, []).append(f'{pod_namespace(pod)}/{pod_name(pod)}')
        # Savings = 0.9 × GPU node $/hr × 730: a GPU instance is ~10× a CPU
        # instance, so moving these pods to a CPU node recovers ~90% of the GPU
        # node cost. Attached opportunistically — if pricing is unavailable or
        # the GPU price is unknown, cost stays None and the placement finding
        # still fires (no badge).
        prices = _price_map(probes)
        for node, pod_list in by_node.items():
            summary = (
                f'Node {node} has GPUs but no nvidia.com/gpu:NoSchedule taint, so '
                f'{len(pod_list)} non-GPU pod(s) ended up on it. Add the taint to '
                'reserve GPU nodes for GPU workloads.'
            )
            gpu_instance = node_instance_type(gpu_nodes_unguarded[node])
            gpu_hourly = prices.get(gpu_instance)
            cost: Optional[float] = None
            cost_evidence: Dict[str, Any] = {}
            if gpu_hourly is not None:
                cost = round(0.9 * gpu_hourly * 730.0, 2)
                cost_evidence = {
                    'gpuInstanceType': gpu_instance,
                    'gpuHourly': round(gpu_hourly, 4),
                    'savingsHourly': round(0.9 * gpu_hourly, 4),
                    'savingsMonthly': cost,
                }
                summary += f' Moving these pods to a CPU node would reclaim ~${cost:,.0f}/mo (~90% of the GPU node cost).'
            out.append(Finding(
                id=make_id(self.id, node),
                rule=self.id,
                severity='critical',
                category=self.category,
                title=f'CPU pods squatting on GPU node "{node}" — no taint protecting $/hr pool',
                summary=summary,
                evidence={'node': node, 'cpuPodsOnGpu': pod_list[:20], 'pods': pod_list, **cost_evidence},
                cost_impact_per_month=cost,
                remediation=[
                    kubectl_remediation(
                        'Taint the GPU node (one-shot)',
                        f'kubectl taint node {node} nvidia.com/gpu=true:NoSchedule',
                    ),
                    doc_link_remediation(
                        'DSS GUI: configure nodepool taints',
                        'https://doc.dataiku.com/dss/latest/containers/eks/clusters.html',
                    ),
                ],
            ))
        return out


class Rule08SystemPodReplicasColocated(Rule):
    id = 'system-pod-replicas-colocated'
    category = 'scheduling'
    severity = 'medium'
    requires_probes = ['probe_pods']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        # Group pods by Deployment-ish controller name (via ownerReferences -> ReplicaSet -> Deployment).
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for pod in items(probes, 'probe_pods'):
            if not is_kube_system_ns(pod_namespace(pod)):
                continue
            labels = ((pod.get('metadata') or {}).get('labels') or {})
            controller = labels.get('k8s-app') or labels.get('app') or labels.get('app.kubernetes.io/name')
            if not controller:
                continue
            if controller not in ('coredns', 'metrics-server', 'cluster-autoscaler', 'aws-load-balancer-controller'):
                continue
            groups.setdefault(controller, []).append(pod)
        for controller, pods in groups.items():
            if len(pods) < 2:
                continue
            nodes = {pod_node(p) for p in pods if pod_node(p)}
            if len(nodes) >= len(pods):
                continue
            out.append(Finding(
                id=make_id(self.id, controller),
                rule=self.id,
                severity='medium',
                category=self.category,
                title=f'System component "{controller}" has {len(pods)} replicas colocated on {len(nodes)} node(s)',
                summary=(
                    f'{controller} runs {len(pods)} replicas but they all share {len(nodes)} '
                    'node(s). One node failure takes the component partially or fully down. '
                    'Add a topology-spread or pod-anti-affinity rule.'
                ),
                evidence={
                    'controller': controller,
                    'replicaCount': len(pods),
                    'nodeCount': len(nodes),
                    'pods': [f'{pod_namespace(p)}/{pod_name(p)}@{pod_node(p)}' for p in pods],
                },
                remediation=[
                    kubectl_remediation(
                        'Inspect the deployment',
                        f'kubectl -n kube-system get deploy {controller} -o yaml',
                        namespace='kube-system',
                    ),
                    doc_link_remediation(
                        'Pod topology spread constraints',
                        'https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraints/',
                    ),
                ],
            ))
        return out


def _pod_owned_by(pod: Dict[str, Any], uid: str) -> bool:
    if not uid:
        return False
    for ref in ((pod.get('metadata') or {}).get('ownerReferences') or []):
        if ref.get('uid') == uid:
            return True
    return False


def _labels_match(selector: Dict[str, str], labels: Dict[str, str]) -> bool:
    for k, v in selector.items():
        if labels.get(k) != v:
            return False
    return True


RULES = [
    Rule01DaemonSetNoNodeTargeting(),
    Rule02DaemonSetCrashLoop(),
    Rule03PodPendingInsufficientResources(),
    Rule04PodPendingNoMatchingSelector(),
    Rule05GpuPodOnCpuNode(),
    Rule06CpuPodOnGpuNode(),
    Rule08SystemPodReplicasColocated(),
]
