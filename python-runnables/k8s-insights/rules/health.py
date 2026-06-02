"""Cluster health rules (33–35). Node pressures + pods without resources."""
from typing import Any, Dict, List

from finding import Finding  # type: ignore
from .base import (
    Rule, ProbeBundle, items, pod_namespace, pod_name, pod_containers,
    pod_owner_kind, node_name, node_conditions, is_kube_system_ns,
    kubectl_remediation, doc_link_remediation, make_id,
)


class Rule33NodeMemoryPressure(Rule):
    id = 'node-memory-pressure'
    category = 'health'
    severity = 'critical'
    requires_probes = ['probe_nodes']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        for node in items(probes, 'probe_nodes'):
            cond = node_conditions(node)
            if cond.get('MemoryPressure') != 'True':
                continue
            out.append(Finding(
                id=make_id(self.id, node_name(node)),
                rule=self.id,
                severity='critical',
                category='health',
                title=f'Node "{node_name(node)}" has MemoryPressure=True',
                summary=(
                    'kubelet reports memory pressure. New pods will be rejected and existing '
                    'ones may be evicted. Drain immediately and investigate.'
                ),
                evidence={'node': node_name(node), 'conditions': cond},
                remediation=[
                    kubectl_remediation('Drain', f'kubectl drain {node_name(node)} --ignore-daemonsets --delete-emptydir-data'),
                ],
            ))
        return out


class Rule34NodeDiskPressure(Rule):
    id = 'node-disk-pressure'
    category = 'health'
    severity = 'critical'
    requires_probes = ['probe_nodes']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        for node in items(probes, 'probe_nodes'):
            cond = node_conditions(node)
            if cond.get('DiskPressure') != 'True':
                continue
            out.append(Finding(
                id=make_id(self.id, node_name(node)),
                rule=self.id,
                severity='critical',
                category='health',
                title=f'Node "{node_name(node)}" has DiskPressure=True',
                summary=(
                    'kubelet is reclaiming disk under pressure. Image GC is happening; pods '
                    'may be evicted. Increase ephemeral-storage on the nodepool or clear '
                    'unused images.'
                ),
                evidence={'node': node_name(node), 'conditions': cond},
                remediation=[
                    kubectl_remediation('Drain', f'kubectl drain {node_name(node)} --ignore-daemonsets'),
                    doc_link_remediation(
                        'admin-toolkit Image Cleaner can clean stale ECR images',
                        'https://doc.dataiku.com/dss/latest/containers/eks/clusters.html',
                    ),
                ],
            ))
        return out


class Rule35PodWithoutResources(Rule):
    id = 'pod-without-resources'
    category = 'health'
    severity = 'medium'
    requires_probes = ['probe_pods']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        controllers: Dict[str, int] = {}
        for pod in items(probes, 'probe_pods'):
            if is_kube_system_ns(pod_namespace(pod)):
                continue
            if pod_owner_kind(pod) == 'DaemonSet':
                continue
            offending = False
            for c in pod_containers(pod):
                req = ((c or {}).get('resources') or {}).get('requests') or {}
                lim = ((c or {}).get('resources') or {}).get('limits') or {}
                if not req and not lim:
                    offending = True
                    break
            if not offending:
                continue
            # Identify the controller name (first ownerRef's name)
            refs = ((pod.get('metadata') or {}).get('ownerReferences') or [])
            ctrl_name = refs[0].get('name') if refs else 'unmanaged'
            key = f'{pod_namespace(pod)}/{ctrl_name}'
            controllers[key] = controllers.get(key, 0) + 1
        if not controllers:
            return []
        return [Finding(
            id=make_id('pod-without-resources', 'cluster'),
            rule='pod-without-resources',
            severity='medium',
            category='health',
            title=f'{len(controllers)} controller(s) launch pods without resource requests',
            summary=(
                'Pods without requests are treated as zero by cluster-autoscaler, so they '
                'can land anywhere and cause noisy-neighbor OOMs.'
            ),
            evidence={'controllers': controllers, 'controllerCount': len(controllers)},
            remediation=[
                doc_link_remediation(
                    'Pod resource requests & limits',
                    'https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/',
                ),
            ],
        )]


RULES = [
    Rule33NodeMemoryPressure(),
    Rule34NodeDiskPressure(),
    Rule35PodWithoutResources(),
]
