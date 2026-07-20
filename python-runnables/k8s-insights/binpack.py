"""First-fit-decreasing bin packing across instance types.

Used by rule 21 (cluster-floor-projection) to estimate the minimum number of
nodes required to host the *current* pod set at the *current* requests
(zero-request pods are sized by live usage, or held at zero size, upstream).

Inputs are normalized into milli-cpu and MiB of memory. Pods that don't fit on
any node group at all (oversized) are returned in `unplaceable`, so the rule
can surface them honestly rather than under-reporting the floor.
"""
from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass
class PodReq:
    name: str
    namespace: str
    cpu_milli: int  # request, milli-cores
    mem_mib: int   # request, MiB
    gpu: int = 0
    node_selector: Dict[str, str] = None  # type: ignore
    tolerations: List[dict] = None  # type: ignore


@dataclass
class NodeGroup:
    name: str
    instance_type: str
    cpu_alloc_milli: int
    mem_alloc_mib: int
    gpu_alloc: int
    labels: Dict[str, str]
    taints: List[dict]


@dataclass
class FloorResult:
    by_group: Dict[str, int]  # node_group_name -> count
    unplaceable: List[str]


def _pod_fits_group(pod: PodReq, group: NodeGroup) -> bool:
    if pod.gpu > 0 and group.gpu_alloc <= 0:
        return False
    if pod.gpu > group.gpu_alloc:
        return False
    if pod.cpu_milli > group.cpu_alloc_milli:
        return False
    if pod.mem_mib > group.mem_alloc_mib:
        return False
    if pod.node_selector:
        for k, v in pod.node_selector.items():
            if group.labels.get(k) != v:
                return False
    # Taint tolerance check (NoSchedule / NoExecute)
    pod_tols = pod.tolerations or []
    for taint in group.taints or []:
        effect = taint.get('effect') or ''
        if effect not in ('NoSchedule', 'NoExecute'):
            continue
        if not _tolerates(pod_tols, taint):
            return False
    return True


def _tolerates(tols: List[dict], taint: dict) -> bool:
    for t in tols:
        if t.get('operator') == 'Exists':
            if not t.get('key') or t.get('key') == taint.get('key'):
                eff = t.get('effect') or ''
                if not eff or eff == taint.get('effect'):
                    return True
        else:
            if t.get('key') == taint.get('key') and t.get('value') == taint.get('value'):
                eff = t.get('effect') or ''
                if not eff or eff == taint.get('effect'):
                    return True
    return False


def compute_floor(pods: List[PodReq], node_groups: List[NodeGroup], price_by_type: Dict[str, float] = None) -> FloorResult:
    """First-fit-decreasing pack of pods onto fresh nodes of each group.

    Strategy: order pods by (gpu DESC, mem DESC, cpu DESC). For each pod:
    1. Try to fit on an existing open node (cheapest first).
    2. Else open a new node from the cheapest group that *can* host the pod.
    3. Else record as unplaceable.

    `price_by_type` is the resolved on-demand USD/hr map. When unavailable
    (caller passes None or {}), groups fall back to alphabetic order — the
    bin-pack still works, just without cost-optimal group selection.
    """
    if not node_groups:
        return FloorResult(by_group={}, unplaceable=[p.name for p in pods])

    prices = price_by_type or {}
    sorted_groups = sorted(node_groups, key=lambda g: prices.get(g.instance_type, 1e9))

    sorted_pods = sorted(
        pods,
        key=lambda p: (-p.gpu, -p.mem_mib, -p.cpu_milli),
    )

    # Open nodes: list of dicts with remaining capacity + group ref
    open_nodes: List[dict] = []
    counts: Dict[str, int] = {g.name: 0 for g in sorted_groups}
    unplaceable: List[str] = []

    for pod in sorted_pods:
        placed = False
        for node in open_nodes:
            g = node['group']
            if pod.gpu > 0 and g.gpu_alloc <= 0:
                continue
            if not _pod_fits_group(pod, g):
                continue
            if (pod.cpu_milli <= node['cpu_left']
                    and pod.mem_mib <= node['mem_left']
                    and pod.gpu <= node['gpu_left']):
                node['cpu_left'] -= pod.cpu_milli
                node['mem_left'] -= pod.mem_mib
                node['gpu_left'] -= pod.gpu
                node['pods'].append(pod.name)
                placed = True
                break
        if placed:
            continue

        # Open a new node from the cheapest group that fits
        for g in sorted_groups:
            if not _pod_fits_group(pod, g):
                continue
            open_nodes.append({
                'group': g,
                'cpu_left': g.cpu_alloc_milli - pod.cpu_milli,
                'mem_left': g.mem_alloc_mib - pod.mem_mib,
                'gpu_left': g.gpu_alloc - pod.gpu,
                'pods': [pod.name],
            })
            counts[g.name] = counts.get(g.name, 0) + 1
            placed = True
            break
        if not placed:
            unplaceable.append(pod.name)

    return FloorResult(by_group=counts, unplaceable=unplaceable)


def parse_cpu_milli(value) -> int:
    """K8s CPU strings -> millicores. '500m' -> 500, '2' -> 2000, '1.5' -> 1500."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(float(value) * 1000)
    s = str(value).strip()
    if not s:
        return 0
    if s.endswith('m'):
        try:
            return int(float(s[:-1]))
        except ValueError:
            return 0
    if s.endswith('n'):
        try:
            return max(0, int(float(s[:-1]) / 1_000_000))
        except ValueError:
            return 0
    try:
        return int(float(s) * 1000)
    except ValueError:
        return 0


def parse_mem_mib(value) -> int:
    """K8s memory strings -> MiB. Accepts Ki, Mi, Gi, Ti, K, M, G, T, bytes."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(float(value) / (1024 * 1024))
    s = str(value).strip()
    if not s:
        return 0
    suffixes = [
        ('Ki', 1.0 / 1024),
        ('Mi', 1.0),
        ('Gi', 1024.0),
        ('Ti', 1024.0 * 1024),
        ('Pi', 1024.0 * 1024 * 1024),
        ('K', 1000.0 / (1024 * 1024)),
        ('M', 1000.0 * 1000 / (1024 * 1024)),
        ('G', 1000.0 ** 3 / (1024 * 1024)),
        ('T', 1000.0 ** 4 / (1024 * 1024)),
    ]
    for suf, mult in suffixes:
        if s.endswith(suf):
            try:
                return max(0, int(float(s[:-len(suf)]) * mult))
            except ValueError:
                return 0
    try:
        return max(0, int(float(s) / (1024 * 1024)))
    except ValueError:
        return 0
