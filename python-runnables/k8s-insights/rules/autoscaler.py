"""Autoscaler rules (9–13).

The headliner here is rule 9: empty nodes pinned open by kube-system pods
without the `safe-to-evict` annotation.
"""
from typing import Any, Dict, List

from finding import Finding  # type: ignore
from .base import (
    Rule, ProbeBundle, items, probe_data, pod_namespace, pod_name, pod_node,
    pod_owner_kind, pod_total_requests, node_name, node_taints, node_ready,
    kubectl_remediation, doc_link_remediation, is_kube_system_ns, make_id,
    minutes_since, _DURATION_GATE_MIN,
)


SAFE_TO_EVICT_KEY = 'cluster-autoscaler.kubernetes.io/safe-to-evict'
SYSTEM_BLOCKERS = ('metrics-server', 'coredns', 'cluster-autoscaler', 'aws-load-balancer-controller')


class Rule09NodeEmptyBlockedBySystemPod(Rule):
    id = 'node-empty-blocked-by-system-pod'
    category = 'autoscaler'
    severity = 'high'
    requires_probes = ['probe_pods', 'probe_nodes', 'probe_deployments_kubesystem']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        node_items = items(probes, 'probe_nodes')
        pods = items(probes, 'probe_pods')
        deploys = items(probes, 'probe_deployments_kubesystem')

        # Map deployment name -> annotations on pod template
        deploy_template_annotations: Dict[str, Dict[str, str]] = {}
        for d in deploys:
            name = ((d.get('metadata') or {}).get('name')) or ''
            anno = (((d.get('spec') or {}).get('template') or {}).get('metadata') or {}).get('annotations') or {}
            deploy_template_annotations[name] = anno

        pods_by_node: Dict[str, List[Dict[str, Any]]] = {}
        for p in pods:
            n = pod_node(p)
            if n:
                pods_by_node.setdefault(n, []).append(p)

        for node in node_items:
            name = node_name(node)
            # Duration gate (node-age proxy): a node scaled up < 10 min ago is
            # still settling — user pods may not have landed yet, so "empty"
            # is expected. Node age is only a *proxy* for "empty the whole
            # time": it excludes freshly scaled-up nodes but cannot prove the
            # node was idle since creation. Best signal from a single snapshot.
            observed_min = minutes_since((node.get('metadata') or {}).get('creationTimestamp'))
            if observed_min < _DURATION_GATE_MIN:
                continue
            on_node = pods_by_node.get(name, [])
            non_ds_pods = [p for p in on_node if pod_owner_kind(p) != 'DaemonSet']
            # User pods with non-zero requests pin a node open. Zero-request pods
            # are effectively cluster-autoscaler-invisible (the scheduler treats
            # them as needing 0 capacity), so they aren't true blockers — they'll
            # reschedule onto any other node if this one drains.
            def _user_pod_holds_node(p):
                if is_kube_system_ns(pod_namespace(p)):
                    return False
                cpu, mem, _ = pod_total_requests(p)
                return cpu > 0 or mem > 0
            user_pods = [p for p in non_ds_pods if _user_pod_holds_node(p)]
            if user_pods:
                continue
            # No user pods. Are blocking system pods present?
            blocking: List[Dict[str, str]] = []
            for p in non_ds_pods:
                if not is_kube_system_ns(pod_namespace(p)):
                    continue
                labels = ((p.get('metadata') or {}).get('labels') or {})
                annotations = ((p.get('metadata') or {}).get('annotations') or {})
                if annotations.get(SAFE_TO_EVICT_KEY) == 'true':
                    continue
                controller = labels.get('k8s-app') or labels.get('app') or labels.get('app.kubernetes.io/name') or ''
                if controller not in SYSTEM_BLOCKERS:
                    continue
                deploy_anno = deploy_template_annotations.get(controller) or {}
                template_safe = deploy_anno.get(SAFE_TO_EVICT_KEY) == 'true'
                if template_safe:
                    continue
                blocking.append({
                    'pod': f'{pod_namespace(p)}/{pod_name(p)}',
                    'controller': controller,
                    'currentAnnotation': annotations.get(SAFE_TO_EVICT_KEY) or '(not set)',
                })
            if not blocking:
                continue
            # Build per-controller patch commands.
            patches = []
            seen: set = set()
            for b in blocking:
                ctrl = b['controller']
                if ctrl in seen:
                    continue
                seen.add(ctrl)
                patches.append(kubectl_remediation(
                    f'Allow {ctrl} to be evicted by cluster-autoscaler',
                    'kubectl -n kube-system patch deployment %s --patch \'{"spec": {"template": '
                    '{"metadata": {"annotations": {"cluster-autoscaler.kubernetes.io/safe-to-evict": "true"}}}}}\''
                    % ctrl,
                    namespace='kube-system',
                ))
            out.append(Finding(
                id=make_id(self.id, name),
                rule=self.id,
                severity='high',
                category=self.category,
                title=f'Empty node "{name}" cannot scale down — pinned open by system pod',
                summary=(
                    f'Node {name} has no user workloads, only DaemonSets and {len(blocking)} '
                    'kube-system pod(s). cluster-autoscaler won\'t evict these without the '
                    'safe-to-evict annotation, so the node stays paid for.'
                ),
                evidence={
                    'node': name,
                    'blockingPods': blocking,
                    'daemonSetPodCount': len(on_node) - len(non_ds_pods),
                    'observedForMinutes': round(observed_min, 1),
                },
                remediation=patches + [
                    doc_link_remediation(
                        'cluster-autoscaler: safe-to-evict semantics',
                        'https://github.com/kubernetes/autoscaler/blob/master/cluster-autoscaler/FAQ.md#what-types-of-pods-can-prevent-ca-from-removing-a-node',
                    ),
                ],
                confidence='high',
            ))
        return out


class Rule10PdbBlockingDrain(Rule):
    id = 'pdb-blocking-drain'
    category = 'autoscaler'
    severity = 'medium'
    requires_probes = ['probe_pdbs', 'probe_pods', 'probe_nodes']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        for pdb in items(probes, 'probe_pdbs'):
            status = pdb.get('status') or {}
            disruptions_allowed = status.get('disruptionsAllowed')
            if disruptions_allowed is None:
                continue
            if int(disruptions_allowed) > 0:
                continue
            meta = pdb.get('metadata') or {}
            ns = meta.get('namespace') or ''
            name = meta.get('name') or ''
            spec = pdb.get('spec') or {}
            out.append(Finding(
                id=make_id(self.id, f'{ns}/{name}'),
                rule=self.id,
                severity='medium',
                category=self.category,
                title=f'PodDisruptionBudget "{ns}/{name}" blocks all eviction',
                summary=(
                    f'PDB {ns}/{name} reports disruptionsAllowed=0 — any drain or node '
                    'scale-down attempt on a node hosting a matching pod will hang. Likely '
                    'cause: replicas equal minAvailable.'
                ),
                evidence={
                    'pdb': f'{ns}/{name}',
                    'minAvailable': spec.get('minAvailable'),
                    'maxUnavailable': spec.get('maxUnavailable'),
                    'selector': spec.get('selector'),
                    'status': status,
                },
                remediation=[
                    kubectl_remediation(
                        'Inspect',
                        f'kubectl -n {ns} get pdb {name} -o yaml',
                        namespace=ns,
                    ),
                    kubectl_remediation(
                        'Loosen the PDB (allow one disruption)',
                        f'kubectl -n {ns} patch pdb {name} --type merge --patch \'{{"spec": {{"maxUnavailable": 1}}}}\'',
                        namespace=ns,
                    ),
                ],
            ))
        return out


class Rule11ClusterAutoscalerNotInstalled(Rule):
    id = 'cluster-autoscaler-not-installed'
    category = 'autoscaler'
    severity = 'medium'
    requires_probes = ['probe_deployments_kubesystem']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        deploys = items(probes, 'probe_deployments_kubesystem')
        has_ca = any(
            (((d.get('metadata') or {}).get('name') or '').startswith('cluster-autoscaler'))
            for d in deploys
        )
        if has_ca:
            return []
        return [Finding(
            id=make_id(self.id, 'kube-system'),
            rule=self.id,
            severity='medium',
            category='autoscaler',
            title='cluster-autoscaler not detected in kube-system',
            summary=(
                'No cluster-autoscaler Deployment was found in kube-system. Without it, '
                'nodes won\'t scale down automatically and the cost-savings findings in '
                'this report require manual action.'
            ),
            evidence={'deploymentsSeen': [((d.get('metadata') or {}).get('name')) for d in deploys]},
            remediation=[
                doc_link_remediation(
                    'cluster-autoscaler install guide (EKS)',
                    'https://github.com/kubernetes/autoscaler/tree/master/cluster-autoscaler/cloudprovider/aws',
                ),
            ],
        )]


class Rule12ClusterAutoscalerScaleDownDisabled(Rule):
    id = 'cluster-autoscaler-scale-down-disabled'
    category = 'autoscaler'
    severity = 'high'
    requires_probes = ['probe_deployments_kubesystem']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        for d in items(probes, 'probe_deployments_kubesystem'):
            name = ((d.get('metadata') or {}).get('name') or '')
            if not name.startswith('cluster-autoscaler'):
                continue
            containers = (((d.get('spec') or {}).get('template') or {}).get('spec') or {}).get('containers') or []
            for c in containers:
                args = c.get('args') or c.get('command') or []
                joined = ' '.join(args)
                if '--scale-down-enabled=false' in joined:
                    out.append(Finding(
                        id=make_id(self.id, name),
                        rule=self.id,
                        severity='high',
                        category='autoscaler',
                        title='cluster-autoscaler has scale-down disabled',
                        summary=(
                            'The cluster-autoscaler Deployment is started with '
                            '--scale-down-enabled=false. The cluster will grow but never '
                            'shrink, which directly defeats cost optimization.'
                        ),
                        evidence={'deployment': name, 'args': args},
                        remediation=[
                            kubectl_remediation(
                                'Edit the deployment to remove the flag',
                                f'kubectl -n kube-system edit deployment {name}',
                                namespace='kube-system',
                            ),
                        ],
                    ))
        return out


class Rule13ClusterAutoscalerScaleDownGraceTooLong(Rule):
    id = 'cluster-autoscaler-scale-down-grace-too-long'
    category = 'autoscaler'
    severity = 'low'
    requires_probes = ['probe_deployments_kubesystem']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        for d in items(probes, 'probe_deployments_kubesystem'):
            name = ((d.get('metadata') or {}).get('name') or '')
            if not name.startswith('cluster-autoscaler'):
                continue
            containers = (((d.get('spec') or {}).get('template') or {}).get('spec') or {}).get('containers') or []
            for c in containers:
                args = c.get('args') or []
                for a in args:
                    if a.startswith('--scale-down-unneeded-time='):
                        val = a.split('=', 1)[1]
                        minutes = _parse_duration_minutes(val)
                        if minutes > 20:
                            out.append(Finding(
                                id=make_id(self.id, name),
                                rule=self.id,
                                severity='low',
                                category='autoscaler',
                                title=f'cluster-autoscaler --scale-down-unneeded-time={val} is slow',
                                summary=(
                                    f'The autoscaler waits {minutes} minutes before draining an '
                                    'unneeded node. Default is 10m. Longer values delay savings.'
                                ),
                                evidence={'deployment': name, 'arg': a, 'minutes': minutes},
                                remediation=[
                                    kubectl_remediation(
                                        'Lower the grace period',
                                        f'kubectl -n kube-system edit deployment {name}  # change --scale-down-unneeded-time to 10m',
                                        namespace='kube-system',
                                    ),
                                ],
                            ))
        return out


def _parse_duration_minutes(val: str) -> int:
    s = (val or '').strip()
    if not s:
        return 0
    if s.endswith('m'):
        try:
            return int(float(s[:-1]))
        except ValueError:
            return 0
    if s.endswith('s'):
        try:
            return int(float(s[:-1]) / 60)
        except ValueError:
            return 0
    if s.endswith('h'):
        try:
            return int(float(s[:-1]) * 60)
        except ValueError:
            return 0
    try:
        return int(float(s) / 60)
    except ValueError:
        return 0


RULES = [
    Rule09NodeEmptyBlockedBySystemPod(),
    Rule10PdbBlockingDrain(),
    Rule11ClusterAutoscalerNotInstalled(),
    Rule12ClusterAutoscalerScaleDownDisabled(),
    Rule13ClusterAutoscalerScaleDownGraceTooLong(),
]
