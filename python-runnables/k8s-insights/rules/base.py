"""Base class + shared helpers for all rules.

Rules are pure: they consume the probe bundle and produce Findings. No I/O.
"""
from __future__ import annotations

import datetime
import re
from typing import Any, Dict, List, Optional, Tuple

from finding import Finding, Remediation  # type: ignore
from binpack import parse_cpu_milli, parse_mem_mib  # type: ignore


ProbeBundle = Dict[str, Dict[str, Any]]


# ---------- duration gate ---------- #

# A single audit is a point-in-time snapshot, so rules that flag a transient
# state (a pod Pending during autoscale, a node freshly scaled up) fire on
# normal cluster churn. Rules that care about persistence gate on this: only
# flag a condition once its anchoring timestamp is at least this old.
_DURATION_GATE_MIN = 10


def minutes_since(ts: Optional[str]) -> float:
    """Minutes since an RFC3339 K8s timestamp; +inf if unparseable/missing.

    Returning +inf on a missing/odd timestamp means the gate degrades *safe*:
    it will never hide a real finding just because the timestamp is absent or
    in an unexpected format. Uses the same parse format as
    `lifecycle._parse_k8s_time`.
    """
    if not ts:
        return float('inf')
    try:
        dt = datetime.datetime.strptime(ts, '%Y-%m-%dT%H:%M:%SZ')
    except (ValueError, TypeError):
        return float('inf')
    return max(0.0, (datetime.datetime.utcnow() - dt).total_seconds() / 60.0)


class Rule:
    id: str = ''
    category: str = ''
    severity: str = 'medium'
    requires_probes: List[str] = []

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        raise NotImplementedError


# ---------- probe data accessors ---------- #


def items(probes: ProbeBundle, probe_name: str) -> List[Dict[str, Any]]:
    p = probes.get(probe_name) or {}
    if not p.get('ok'):
        return []
    data = p.get('data') or {}
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get('items') or []
    return []


def probe_data(probes: ProbeBundle, probe_name: str) -> Any:
    p = probes.get(probe_name) or {}
    return p.get('data') if p.get('ok') else None


# ---------- pod/container helpers ---------- #


def pod_namespace(pod: Dict[str, Any]) -> str:
    return ((pod or {}).get('metadata') or {}).get('namespace') or ''


def pod_name(pod: Dict[str, Any]) -> str:
    return ((pod or {}).get('metadata') or {}).get('name') or ''


def pod_node(pod: Dict[str, Any]) -> str:
    return ((pod or {}).get('spec') or {}).get('nodeName') or ''


def pod_phase(pod: Dict[str, Any]) -> str:
    return ((pod or {}).get('status') or {}).get('phase') or ''


def pod_containers(pod: Dict[str, Any]) -> List[Dict[str, Any]]:
    return ((pod or {}).get('spec') or {}).get('containers') or []


def pod_owner_kind(pod: Dict[str, Any]) -> str:
    refs = ((pod or {}).get('metadata') or {}).get('ownerReferences') or []
    if refs:
        return refs[0].get('kind') or ''
    return ''


def pod_total_requests(pod: Dict[str, Any]) -> Tuple[int, int, int]:
    """Sum CPU (milli), memory (MiB), GPU across containers."""
    cpu = 0
    mem = 0
    gpu = 0
    for c in pod_containers(pod):
        req = ((c or {}).get('resources') or {}).get('requests') or {}
        cpu += parse_cpu_milli(req.get('cpu'))
        mem += parse_mem_mib(req.get('memory'))
        try:
            gpu += int(req.get('nvidia.com/gpu') or 0)
        except (TypeError, ValueError):
            pass
    return cpu, mem, gpu


def pod_total_limits(pod: Dict[str, Any]) -> Tuple[int, int]:
    cpu = 0
    mem = 0
    for c in pod_containers(pod):
        lim = ((c or {}).get('resources') or {}).get('limits') or {}
        cpu += parse_cpu_milli(lim.get('cpu'))
        mem += parse_mem_mib(lim.get('memory'))
    return cpu, mem


def pod_status_terminated_reason(pod: Dict[str, Any]) -> Optional[str]:
    for cs in ((pod or {}).get('status') or {}).get('containerStatuses') or []:
        last = (cs or {}).get('lastState') or {}
        term = last.get('terminated') or {}
        if term.get('reason'):
            return term['reason']
    return None


# ---------- node helpers ---------- #


def node_name(node: Dict[str, Any]) -> str:
    return ((node or {}).get('metadata') or {}).get('name') or ''


def node_labels(node: Dict[str, Any]) -> Dict[str, str]:
    return ((node or {}).get('metadata') or {}).get('labels') or {}


def node_instance_type(node: Dict[str, Any]) -> str:
    labels = node_labels(node)
    return labels.get('node.kubernetes.io/instance-type') or labels.get('beta.kubernetes.io/instance-type') or ''


def node_allocatable(node: Dict[str, Any]) -> Tuple[int, int, int]:
    """Return allocatable (cpu_milli, mem_mib, gpu) for a node."""
    alloc = ((node or {}).get('status') or {}).get('allocatable') or {}
    cpu = parse_cpu_milli(alloc.get('cpu'))
    mem = parse_mem_mib(alloc.get('memory'))
    try:
        gpu = int(alloc.get('nvidia.com/gpu') or 0)
    except (TypeError, ValueError):
        gpu = 0
    return cpu, mem, gpu


def node_taints(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    return ((node or {}).get('spec') or {}).get('taints') or []


def node_conditions(node: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for c in ((node or {}).get('status') or {}).get('conditions') or []:
        if c.get('type'):
            out[c['type']] = c.get('status') or ''
    return out


def node_ready(node: Dict[str, Any]) -> bool:
    return node_conditions(node).get('Ready') == 'True'


def node_is_gpu(node: Dict[str, Any]) -> bool:
    _, _, gpu = node_allocatable(node)
    return gpu > 0


# ---------- DSS-specific helpers ---------- #


# DSS namespace canonical form: `dss-ns-{config}-{projkey}-{counter}` where
# `projkey` is the 4-char shortened project key and `counter` is a 4+ digit
# numeric id appended by DSS. Config names can contain dashes themselves
# (e.g. `code-studio`), so we anchor on the projkey + counter at the *end*.
_DSS_NAMESPACE_RE = re.compile(r'^dss-ns-(?P<config>.+)-(?P<projkey>[a-z0-9]{4})-(?P<id>\d{4,})$')


def parse_dss_namespace(ns: str) -> Optional[Dict[str, str]]:
    """`dss-ns-mycfg-PKEY-NNNNNN` -> {'config': 'mycfg', 'projkey': 'PKEY', 'id': 'NNNNNN'}."""
    if not ns:
        return None
    m = _DSS_NAMESPACE_RE.match(ns)
    if not m:
        return None
    return {'config': m.group('config'), 'projkey': m.group('projkey'), 'id': m.group('id')}


def is_kube_system_ns(ns: str) -> bool:
    return ns in ('kube-system', 'kube-public', 'kube-node-lease')


# ---------- finding builders ---------- #


def kubectl_remediation(title: str, command: str, namespace: Optional[str] = None) -> Remediation:
    return Remediation(kind='kubectl', title=title, body=command, target=namespace)


def file_edit_remediation(title: str, path: str, snippet: str = '') -> Remediation:
    return Remediation(kind='file-edit', title=title, body=snippet, target=path)


def gui_remediation(title: str, body: str, target: str = '') -> Remediation:
    return Remediation(kind='gui-step', title=title, body=body, target=target)


def doc_link_remediation(title: str, url: str) -> Remediation:
    return Remediation(kind='doc-link', title=title, body=url, target=url)


def make_id(rule_id: str, ref: str) -> str:
    return f'{rule_id}::{ref}'
