"""Lifecycle / hygiene rules (29–32). Pod status patterns."""
import datetime
from typing import Any, Dict, List

from finding import Finding  # type: ignore
from .base import (
    Rule, ProbeBundle, items, pod_namespace, pod_name, pod_node, pod_phase,
    pod_status_terminated_reason, node_name, node_conditions, node_ready,
    kubectl_remediation, doc_link_remediation, make_id,
)


def _parse_k8s_time(ts: str) -> datetime.datetime:
    try:
        return datetime.datetime.strptime(ts, '%Y-%m-%dT%H:%M:%SZ')
    except (ValueError, TypeError):
        return datetime.datetime.utcnow()


class Rule29PodImagePullFailure(Rule):
    id = 'pod-imagepull-failure'
    category = 'lifecycle'
    severity = 'high'
    requires_probes = ['probe_pods']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        for pod in items(probes, 'probe_pods'):
            for cs in (((pod.get('status') or {}).get('containerStatuses') or [])
                       + ((pod.get('status') or {}).get('initContainerStatuses') or [])):
                waiting = (cs.get('state') or {}).get('waiting') or {}
                reason = waiting.get('reason') or ''
                if reason not in ('ImagePullBackOff', 'ErrImagePull', 'InvalidImageName'):
                    continue
                image = cs.get('image') or ''
                out.append(Finding(
                    id=make_id(self.id, f'{pod_namespace(pod)}/{pod_name(pod)}/{cs.get("name")}'),
                    rule=self.id,
                    severity='high',
                    category=self.category,
                    title=f'Container "{cs.get("name")}" cannot pull image',
                    summary=(
                        f'Pod {pod_namespace(pod)}/{pod_name(pod)} container {cs.get("name")} '
                        f'failed image pull ({reason}). Image: {image}.'
                    ),
                    evidence={
                        'pod': f'{pod_namespace(pod)}/{pod_name(pod)}',
                        'container': cs.get('name'),
                        'image': image,
                        'reason': reason,
                        'message': waiting.get('message'),
                    },
                    remediation=[
                        kubectl_remediation(
                            'Inspect events',
                            f'kubectl -n {pod_namespace(pod)} describe pod {pod_name(pod)}',
                            namespace=pod_namespace(pod),
                        ),
                        doc_link_remediation(
                            'Common causes: ECR auth token expired, image tag missing, registry typo',
                            'https://docs.aws.amazon.com/AmazonECR/latest/userguide/registry_auth.html',
                        ),
                    ],
                ))
        return out


class Rule30PodOomKilledRecent(Rule):
    id = 'pod-oomkilled-recent'
    category = 'lifecycle'
    severity = 'high'
    requires_probes = ['probe_pods']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        now = datetime.datetime.utcnow()
        for pod in items(probes, 'probe_pods'):
            for cs in (((pod.get('status') or {}).get('containerStatuses') or []) +
                       ((pod.get('status') or {}).get('initContainerStatuses') or [])):
                last = (cs.get('lastState') or {}).get('terminated') or {}
                if last.get('reason') != 'OOMKilled':
                    continue
                fin_at = last.get('finishedAt')
                age_h = float('inf')
                if fin_at:
                    age_h = (now - _parse_k8s_time(fin_at)).total_seconds() / 3600.0
                if age_h > 24:
                    continue
                # Pull current memory request
                containers = ((pod.get('spec') or {}).get('containers') or [])
                target = next((c for c in containers if c.get('name') == cs.get('name')), {})
                req = (((target or {}).get('resources') or {}).get('requests') or {}).get('memory')
                lim = (((target or {}).get('resources') or {}).get('limits') or {}).get('memory')
                out.append(Finding(
                    id=make_id(self.id, f'{pod_namespace(pod)}/{pod_name(pod)}/{cs.get("name")}'),
                    rule=self.id,
                    severity='high',
                    category=self.category,
                    title=f'Container "{cs.get("name")}" was OOMKilled in the last 24h',
                    summary=(
                        f'{pod_namespace(pod)}/{pod_name(pod)} container {cs.get("name")} '
                        f'killed for OOM ~{age_h:.1f}h ago. memRequest={req}, memLimit={lim}.'
                    ),
                    evidence={
                        'pod': f'{pod_namespace(pod)}/{pod_name(pod)}',
                        'container': cs.get('name'),
                        'memRequest': req,
                        'memLimit': lim,
                        'finishedAt': fin_at,
                        'ageHours': round(age_h, 2),
                        'restartCount': cs.get('restartCount'),
                    },
                    remediation=[
                        kubectl_remediation(
                            'Inspect previous container log',
                            f'kubectl -n {pod_namespace(pod)} logs {pod_name(pod)} -c {cs.get("name")} --previous',
                            namespace=pod_namespace(pod),
                        ),
                        doc_link_remediation(
                            'Raise memLimitMB on the matching DSS execution config',
                            'https://doc.dataiku.com/dss/latest/containers/setup.html#execution-configurations',
                        ),
                    ],
                ))
        return out


class Rule31PodStuckTerminating(Rule):
    id = 'pod-stuck-terminating'
    category = 'lifecycle'
    severity = 'medium'
    requires_probes = ['probe_pods']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        now = datetime.datetime.utcnow()
        for pod in items(probes, 'probe_pods'):
            deletion_ts = ((pod.get('metadata') or {}).get('deletionTimestamp'))
            if not deletion_ts:
                continue
            age_min = (now - _parse_k8s_time(deletion_ts)).total_seconds() / 60.0
            if age_min < 5:
                continue
            out.append(Finding(
                id=make_id(self.id, f'{pod_namespace(pod)}/{pod_name(pod)}'),
                rule=self.id,
                severity='medium',
                category=self.category,
                title=f'Pod "{pod_name(pod)}" stuck Terminating for {age_min:.0f}min',
                summary=(
                    'Pod is past its grace period but still present. Common causes: '
                    'finalizer stuck, kernel-level unkillable process, or the node went '
                    'NotReady mid-shutdown.'
                ),
                evidence={
                    'pod': f'{pod_namespace(pod)}/{pod_name(pod)}',
                    'deletionTimestamp': deletion_ts,
                    'ageMinutes': round(age_min, 1),
                    'finalizers': ((pod.get('metadata') or {}).get('finalizers') or []),
                },
                remediation=[
                    kubectl_remediation(
                        'Force delete',
                        f'kubectl -n {pod_namespace(pod)} delete pod {pod_name(pod)} --grace-period=0 --force',
                        namespace=pod_namespace(pod),
                    ),
                ],
            ))
        return out


class Rule32NodeNotReady(Rule):
    id = 'node-not-ready'
    category = 'lifecycle'
    severity = 'critical'
    requires_probes = ['probe_nodes', 'probe_pods']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        now = datetime.datetime.utcnow()
        pods_by_node: Dict[str, List[str]] = {}
        for p in items(probes, 'probe_pods'):
            n = pod_node(p)
            if n:
                pods_by_node.setdefault(n, []).append(f'{pod_namespace(p)}/{pod_name(p)}')
        for node in items(probes, 'probe_nodes'):
            cond = node_conditions(node)
            if cond.get('Ready') == 'True':
                continue
            # When was Ready last True? Use lastTransitionTime
            last_change = None
            for c in ((node.get('status') or {}).get('conditions') or []):
                if c.get('type') == 'Ready':
                    last_change = c.get('lastTransitionTime')
                    break
            age_min = float('inf')
            if last_change:
                age_min = (now - _parse_k8s_time(last_change)).total_seconds() / 60.0
            if age_min < 5:
                continue
            name = node_name(node)
            out.append(Finding(
                id=make_id(self.id, name),
                rule=self.id,
                severity='critical',
                category=self.category,
                title=f'Node "{name}" is NotReady for {age_min:.0f}min',
                summary=(
                    f'Node {name} has been NotReady since {last_change}. Workloads hosted '
                    f'there: {len(pods_by_node.get(name, []))} pod(s).'
                ),
                evidence={
                    'node': name,
                    'conditions': cond,
                    'sinceMinutes': round(age_min, 1),
                    'hostedPods': pods_by_node.get(name, [])[:20],
                },
                remediation=[
                    kubectl_remediation('Describe node', f'kubectl describe node {name}'),
                    kubectl_remediation('Inspect kubelet status', 'ssh into node && systemctl status kubelet'),
                ],
            ))
        return out


RULES = [
    Rule29PodImagePullFailure(),
    Rule30PodOomKilledRecent(),
    Rule31PodStuckTerminating(),
    Rule32NodeNotReady(),
]
