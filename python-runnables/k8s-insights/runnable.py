"""K8S Insights — deterministic rule engine for DSS-managed EKS clusters.

Three layers:
  1. probes/  — independent raw-data collectors (kubectl + filesystem)
  2. rules/   — pure-function detectors that consume probe results
  3. output   — assembled JSON: cluster meta + findings + cost snapshot

The macro runs probes in parallel (kubectl calls are independent), then
evaluates all rules synchronously. Total runtime target: ~3–5s on a 5-node
cluster.
"""
import concurrent.futures as cf
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Set

import importlib.util

from dataiku.runnables import Runnable

# DSS stages runnable.py into a tmp folder (e.g. /tmp/tmp_folder_XXXX/) for the
# macro kernel and does NOT copy sibling files alongside it. The actual sibling
# files live in the plugin install dir under $DIP_HOME. Locate that and bootstrap
# every sibling explicitly via importlib so the loaders work regardless of
# whatever sys.path / staging tricks DSS performs.
def _find_plugin_runnable_dir() -> str:
    """Locate <plugin>/python-runnables/k8s-insights/ where sibling .py files actually live."""
    rel = os.path.join('admin-toolkit', 'python-runnables', 'k8s-insights')
    sentinel = 'finding.py'
    # 1) Standard DSS install layouts under DIP_HOME.
    dip_home = os.environ.get('DIP_HOME') or os.environ.get('DKU_DIP_HOME')
    if dip_home:
        for sub in ('plugins/installed', 'plugins/dev'):
            candidate = os.path.join(dip_home, sub, rel)
            if os.path.isfile(os.path.join(candidate, sentinel)):
                return candidate
    # 2) Co-located fallback (works when DSS does NOT stage to /tmp).
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.isfile(os.path.join(here, sentinel)):
        return here
    raise RuntimeError(
        'k8s-insights: cannot locate plugin files; DIP_HOME=%r, __file__=%r' % (dip_home, __file__)
    )


_PLUGIN_DIR = _find_plugin_runnable_dir()
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)


def _bootstrap_local(name: str, is_package: bool = False):
    """Load a sibling .py file (or sub-package) and register it under its bare name."""
    if name in sys.modules:
        return sys.modules[name]
    if is_package:
        init_path = os.path.join(_PLUGIN_DIR, name, '__init__.py')
        spec = importlib.util.spec_from_file_location(
            name, init_path,
            submodule_search_locations=[os.path.join(_PLUGIN_DIR, name)],
        )
    else:
        spec = importlib.util.spec_from_file_location(name, os.path.join(_PLUGIN_DIR, name + '.py'))
    if spec is None or spec.loader is None:
        raise ImportError('cannot load sibling %r from %r' % (name, _PLUGIN_DIR))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Dependency order: finding/pricing/binpack/probes are leaf siblings; the rules
# package imports from finding+binpack+pricing, so it must load last.
_bootstrap_local('finding')
_bootstrap_local('pricing')
_bootstrap_local('binpack')
_bootstrap_local('probes')
_bootstrap_local('rules', is_package=True)

import probes as P  # noqa: E402
from finding import Finding, sort_findings  # noqa: E402
from pricing import is_gpu_instance  # noqa: E402
from pricing_source import (  # noqa: E402
    SOURCE_NAME as PRICING_SOURCE_NAME,
    PricingSourceError,
    get_on_demand_usd_per_hour,
)
import rules as R  # noqa: E402


def _now_ms() -> int:
    return int(time.time() * 1000)


def _resolve_dip_home() -> str:
    return os.environ.get('DIP_HOME') or os.environ.get('DKU_DIP_HOME') or ''


def _pick_cluster_id(dip_home: str, requested: str) -> Optional[Dict[str, Any]]:
    """Resolve which cluster to audit. Returns the cluster meta or None."""
    listing = P.probe_clusters_list(dip_home)
    if not listing['ok']:
        return None
    clusters: List[Dict[str, Any]] = listing.get('data') or []
    if not clusters:
        return None
    if requested:
        for c in clusters:
            if c['id'] == requested:
                return c
        # Tolerate clusters that exist in DSS but have no filesystem dir yet.
        return {'id': requested, 'baseDir': None, 'hasKubeconfig': False}
    if clusters:
        return clusters[0]
    return None


def _make_kubectl_runner(cluster_id: str):
    """Build a `KubectlFn` that routes kubectl calls through the DSS cluster API.

    DSS-attached EKS clusters never write a kubeconfig file to disk; the only
    reliable way to talk to them is `cluster.run_kubectl(args)`. This wrapper
    adapts that to the (rc, stdout, stderr) tuple shape probes expect.
    """
    import dataiku
    api = dataiku.api_client()
    cluster = api.get_cluster(cluster_id)

    def run(args: str):
        try:
            res = cluster.run_kubectl(args) or {}
        except Exception as exc:
            return -1, '', f'{type(exc).__name__}: {str(exc)[:300]}'
        rc = int(res.get('returnValue', -1))
        return rc, str(res.get('output') or ''), str(res.get('error') or '')

    return run


# Synthetic cluster-id namespace for kubeconfig-file targets: audits selected
# from a discovered kubeconfig (containerized-exec config, ~/.kube/...) travel
# through the existing cluster_id plumbing as 'kubeconfig:<path>'.
KUBECONFIG_ID_PREFIX = 'kubeconfig:'


def _make_file_kubectl_runner(kubeconfig_path: str, context: Optional[str] = None):
    """Build a `KubectlFn` that shells out to `kubectl --kubeconfig <path>`.

    Used for clusters DSS does not manage via its cluster API — e.g. a custom
    kubeConfigPath declared on a containerized-execution config. The macro
    kernel runs on the DSS host as the service user, so the file and any
    exec-credential helpers (aws, aws-iam-authenticator) resolve exactly as
    they do for DSS's own containerized execution.
    """
    import shlex
    import shutil
    import subprocess

    env = dict(os.environ)
    env['PATH'] = (env.get('PATH') or '') + os.pathsep + '/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin'
    binary = shutil.which('kubectl', path=env['PATH'])
    if not binary:
        raise RuntimeError('kubectl binary not found on host PATH')
    base = [binary, '--kubeconfig', kubeconfig_path]
    if context:
        base += ['--context', context]

    def run(args: str):
        try:
            proc = subprocess.run(
                base + shlex.split(args),
                capture_output=True, text=True, timeout=120, env=env,
            )
        except subprocess.TimeoutExpired:
            return -1, '', f'kubectl timed out after 120s (args: {args[:120]})'
        except Exception as exc:
            return -1, '', f'{type(exc).__name__}: {str(exc)[:300]}'
        return proc.returncode, proc.stdout or '', proc.stderr or ''

    return run


def _kubeconfig_candidates(dip_home: str) -> List[Dict[str, Any]]:
    """Discovered kubeconfig candidates, deduped against DSS-managed cluster
    kubeconfigs (those are already reachable as first-class clusters) and
    stamped with their synthetic cluster id."""
    listing = P.probe_clusters_list(dip_home)
    managed_real = {
        os.path.realpath(c['kubeconfig'])
        for c in (listing.get('data') or [])
        if c.get('kubeconfig')
    }
    cands = P.probe_kubeconfig_candidates(dip_home).get('data') or []
    out: List[Dict[str, Any]] = []
    for c in cands:
        if c.get('realPath') in managed_real:
            continue
        out.append({**c, 'id': KUBECONFIG_ID_PREFIX + c['path']})
    return out


def _kubeconfig_candidate_by_id(dip_home: str, synthetic_id: str) -> Optional[Dict[str, Any]]:
    for c in _kubeconfig_candidates(dip_home):
        if c['id'] == synthetic_id and c.get('exists'):
            return c
    return None


def _kubectl_for(dip_home: str, cluster_id: str):
    """Resolve a cluster id (DSS-managed or kubeconfig:<path>) to a KubectlFn.

    kubeconfig paths are re-discovered and validated server-side — a requested
    path that isn't among the discovered candidates is refused, so the macro
    never runs kubectl against an arbitrary caller-supplied file."""
    if cluster_id.startswith(KUBECONFIG_ID_PREFIX):
        cand = _kubeconfig_candidate_by_id(dip_home, cluster_id)
        if cand is None:
            raise ValueError(
                'kubeconfig %r is not among the discovered candidates on this host'
                % cluster_id[len(KUBECONFIG_ID_PREFIX):]
            )
        return _make_file_kubectl_runner(cand['path'], cand.get('context'))
    return _make_kubectl_runner(cluster_id)


def _kubeconfig_display_name(cand: Dict[str, Any]) -> str:
    if cand.get('execConfig'):
        return str(cand['execConfig'])
    if cand.get('currentContext'):
        return str(cand['currentContext'])
    return str(cand.get('displayPath') or cand.get('path') or cand.get('id'))


def _run_probes(kubectl, dip_home: str, cluster_id: str) -> Dict[str, Dict[str, Any]]:
    """Run all probes in parallel. Returns {probe_name: result_dict}."""
    kubectl_jobs = {
        'probe_pods': lambda: P.probe_pods(kubectl),
        'probe_nodes': lambda: P.probe_nodes(kubectl),
        'probe_daemonsets': lambda: P.probe_daemonsets(kubectl),
        'probe_replicasets': lambda: P.probe_replicasets(kubectl),
        'probe_deployments_all': lambda: P.probe_deployments_all(kubectl),
        'probe_deployments_kubesystem': lambda: P.probe_deployments_kubesystem(kubectl),
        'probe_pdbs': lambda: P.probe_pdbs(kubectl),
        'probe_events': lambda: P.probe_events(kubectl),
        'probe_top_pods': lambda: P.probe_top_pods(kubectl),
        'probe_top_nodes': lambda: P.probe_top_nodes(kubectl),
        'probe_kubectl_version': lambda: P.probe_kubectl_version(kubectl),
    }
    fs_jobs = {
        'probe_dss_general_settings': lambda: P.probe_dss_general_settings(dip_home),
        'probe_managed_cluster_dir': lambda: P.probe_managed_cluster_dir(dip_home, cluster_id),
        'probe_eks_plugin_gpu_driver': lambda: P.probe_eks_plugin_gpu_driver(dip_home),
    }
    out: Dict[str, Dict[str, Any]] = {}
    with cf.ThreadPoolExecutor(max_workers=8, thread_name_prefix='k8sprobe') as pool:
        futures = {pool.submit(fn): name for name, fn in {**kubectl_jobs, **fs_jobs}.items()}
        for fut in cf.as_completed(futures):
            name = futures[fut]
            try:
                out[name] = fut.result()
            except Exception as exc:
                out[name] = {'ok': False, 'data': None, 'error': f'{type(exc).__name__}: {exc}', 'durationMs': 0}
    return out


def _cluster_meta(probes: Dict[str, Dict[str, Any]], cluster_id: str) -> Dict[str, Any]:
    nodes_probe = probes.get('probe_nodes') or {}
    nodes = (nodes_probe.get('data') or {}).get('items') if nodes_probe.get('ok') else None
    pods_probe = probes.get('probe_pods') or {}
    pods = (pods_probe.get('data') or {}).get('items') if pods_probe.get('ok') else None
    version_probe = probes.get('probe_kubectl_version') or {}
    return {
        'id': cluster_id,
        'nodeCount': len(nodes) if isinstance(nodes, list) else None,
        'podCount': len(pods) if isinstance(pods, list) else None,
        'kubectlVersion': (version_probe.get('data') or {}).get('clientVersion', {}).get('gitVersion')
        if isinstance(version_probe.get('data'), dict) else None,
    }


def _resolve_pricing(probes: Dict[str, Dict[str, Any]], dip_home: str) -> Dict[str, Any]:
    """Resolve on-demand USD/hr for every distinct instance type in probe_nodes.

    Returns an envelope:
        {'ok': bool, 'source': str, 'region': str, 'priceByType': {...},
         'error': str | None, 'fetchedAt': int (epoch ms)}

    Failure cases are non-fatal — the audit continues with `priceByType={}`;
    cost rules are suppressed downstream via the `_pricing` virtual probe.
    `dip_home` is forwarded to the source so the on-disk distilled cache
    lives under DSS HOME rather than $TMPDIR.
    """
    started = _now_ms()
    nodes_probe = probes.get('probe_nodes') or {}
    items = []
    if nodes_probe.get('ok'):
        items = (nodes_probe.get('data') or {}).get('items') or []

    region = _resolve_cluster_region(items)
    instance_types: List[str] = []
    seen = set()
    for n in items:
        labels = (((n or {}).get('metadata') or {}).get('labels') or {})
        it = labels.get('node.kubernetes.io/instance-type') or labels.get('beta.kubernetes.io/instance-type') or ''
        if it and it not in seen:
            seen.add(it)
            instance_types.append(it)

    price_by_type: Dict[str, float] = {}
    if not instance_types:
        return {
            'ok': True, 'source': PRICING_SOURCE_NAME, 'region': region,
            'priceByType': price_by_type, 'error': None, 'fetchedAt': started,
        }
    try:
        for it in instance_types:
            price_by_type[it] = get_on_demand_usd_per_hour(it, region, dip_home=dip_home)
    except PricingSourceError as exc:
        return {
            'ok': False, 'source': exc.source, 'region': region,
            'priceByType': {}, 'error': exc.reason, 'fetchedAt': started,
        }
    # Also price smaller same-family sizes so the floor projection can propose
    # cheaper node shapes (Karpenter-style catalog), not just fewer of the
    # current ones. Best-effort — a coverage gap skips the candidate, never
    # fails the audit.
    from binpack import family_downsize_types  # noqa: E402  # staged sibling module
    for it in instance_types:
        for cand in family_downsize_types(it):
            if cand in price_by_type:
                continue
            try:
                price_by_type[cand] = get_on_demand_usd_per_hour(cand, region, dip_home=dip_home)
            except PricingSourceError:
                continue
    return {
        'ok': True, 'source': PRICING_SOURCE_NAME, 'region': region,
        'priceByType': price_by_type, 'error': None, 'fetchedAt': started,
    }


def _make_dss_source_reader():
    """Build a `read_object_source(project_key, kind, object_id) -> (src, err)`
    closure for `probes.probe_gpu_pod_code`. Keeps `probes.py` dataiku-free by
    injecting the only DSS-touching part here.

    The read patterns are copied verbatim from `find_unused_packages.py`:
      - notebook: get_jupyter_notebook(id).get_content().get_raw()['cells']
      - recipe:   get_recipe(id).get_settings().get_code()
    Every DSS call is wrapped so the closure returns (None, error) instead of
    raising — the probe must never blow up the audit."""
    import dataiku
    api = dataiku.api_client()

    def read_object_source(project_key: str, kind: str, object_id: str):
        try:
            project = api.get_project(project_key)
            if kind == 'notebook':
                content = project.get_jupyter_notebook(object_id).get_content()
                cells = (content.get_raw() or {}).get('cells', []) or []
                parts: List[str] = []
                for cell in cells:
                    if cell.get('cell_type') == 'code':
                        source = cell.get('source', [])
                        if isinstance(source, list):
                            parts.append(''.join(source))
                        else:
                            parts.append(source)
                return ('\n\n'.join(parts), None)
            if kind == 'recipe':
                code = project.get_recipe(object_id).get_settings().get_code()
                return (code or '', None)
            return (None, f'unsupported object kind: {kind}')
        except Exception as exc:
            return (None, f'{type(exc).__name__}: {str(exc)[:200]}')

    return read_object_source


def _resolve_gpu_pod_code(probes_result: Dict[str, Dict[str, Any]], kubectl) -> Dict[str, Any]:
    """Run the GPU-pod code/usage probe after the parallel pool.

    It needs probe_pods output, a DSS api_client (to read code objects) AND the
    kubectl runner (for the live `nvidia-smi` exec), so — like `_resolve_pricing`
    — it cannot run inside the dataiku-free probe pool. The runner is the same
    one the audit already resolved (DSS-API or kubeconfig-file). Failures are
    non-fatal: on any setup error it returns an `ok=False` envelope and the GPU
    rule (which declares `requires_probes=['probe_gpu_pod_code']`) is skipped."""
    started = _now_ms()
    pods_probe = probes_result.get('probe_pods') or {}
    if not pods_probe.get('ok'):
        return {'ok': False, 'data': {}, 'error': 'probe_pods unavailable', 'durationMs': _now_ms() - started}
    pods = (pods_probe.get('data') or {}).get('items') or []
    try:
        reader = _make_dss_source_reader()
    except Exception as exc:
        return {
            'ok': False, 'data': {},
            'error': f'cannot build DSS source reader: {type(exc).__name__}: {str(exc)[:200]}',
            'durationMs': _now_ms() - started,
        }
    return P.probe_gpu_pod_code(pods, reader, kubectl)


def _resolve_cluster_region(node_items: List[Dict[str, Any]]) -> str:
    """Best-effort: read region from any node's `topology.kubernetes.io/region`
    label; fall back to the zone label with its last char stripped; else
    us-east-1 (Pricing API's home region, harmless default)."""
    for n in node_items:
        labels = (((n or {}).get('metadata') or {}).get('labels') or {})
        region = labels.get('topology.kubernetes.io/region') or labels.get('failure-domain.beta.kubernetes.io/region')
        if region:
            return region
        zone = labels.get('topology.kubernetes.io/zone') or labels.get('failure-domain.beta.kubernetes.io/zone')
        if zone and len(zone) > 1 and zone[-1].isalpha():
            return zone[:-1]
    return 'us-east-1'


def _cost_snapshot(probes: Dict[str, Dict[str, Any]], price_by_type: Dict[str, float]) -> Dict[str, Any]:
    """Sum node hourly costs from the resolved price map.

    When the instance type is missing from `price_by_type` (pricing source
    failed, or AWS doesn't list this instance), the row's `hourly` is null
    rather than 0 — the UI suppresses cost figures instead of showing $0.00.
    """
    nodes_probe = probes.get('probe_nodes') or {}
    if not nodes_probe.get('ok'):
        return {'currentHourly': None, 'currentMonthly': None, 'nodes': []}
    items = (nodes_probe.get('data') or {}).get('items') or []
    rows: List[Dict[str, Any]] = []
    current_hourly = 0.0
    any_priced = False
    for n in items:
        labels = (((n or {}).get('metadata') or {}).get('labels') or {})
        instance_type = labels.get('node.kubernetes.io/instance-type') or labels.get('beta.kubernetes.io/instance-type') or ''
        price = price_by_type.get(instance_type)
        if price is not None:
            any_priced = True
            current_hourly += price
        rows.append({
            'name': ((n or {}).get('metadata') or {}).get('name') or '',
            'instanceType': instance_type,
            'hourly': price,
            'isGpu': is_gpu_instance(instance_type),
        })
    if not any_priced:
        return {'currentHourly': None, 'currentMonthly': None, 'nodes': rows}
    return {
        'currentHourly': round(current_hourly, 4),
        'currentMonthly': round(current_hourly * 730.0, 2),
        'nodes': rows,
    }


_NODE_SELECTED_LABEL_KEYS = (
    'eks.amazonaws.com/nodegroup',
    'eks.amazonaws.com/capacityType',
    'eks.amazonaws.com/nodegroup-image',
    'topology.kubernetes.io/zone',
    'topology.kubernetes.io/region',
    'node.kubernetes.io/instance-type',
    'nvidia.com/gpu.product',
    'nvidia.com/gpu.count',
    'nvidia.com/gpu.memory',
)


def _selected_labels(labels: Dict[str, str]) -> Dict[str, str]:
    """Pull the handful of labels that actually carry decision-grade info.

    Full label dicts bloat the payload (EKS adds 30+ housekeeping keys per
    node). The known interesting keys + the wildcard family `karpenter.sh/*`
    cover every real diagnostic case.
    """
    out: Dict[str, str] = {}
    for k in _NODE_SELECTED_LABEL_KEYS:
        if k in labels:
            out[k] = labels[k]
    for k, v in labels.items():
        if k.startswith('karpenter.sh/'):
            out[k] = v
    return out


def _parse_node_creation_ms(timestamp: Optional[str]) -> Optional[int]:
    """Parse RFC3339 like '2026-05-28T15:24:01Z' to epoch ms. None on failure."""
    if not timestamp:
        return None
    import datetime
    for fmt in ('%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S.%fZ'):
        try:
            dt = datetime.datetime.strptime(timestamp, fmt)
            return int(dt.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
        except ValueError:
            continue
    return None


def _pods_by_node(
    pods: List[Dict[str, Any]],
    usage_by_pod_key: Dict[str, Dict[str, int]],
    gpu_code_by_key: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Build the per-node pod sub-table, joining real usage from `kubectl top`.

    `usage_by_pod_key` maps "{ns}/{name}" -> {cpuMilli, memMib}; the indirection
    keeps this function pure (the caller can mock the join table).
    `gpu_code_by_key` maps "{ns}/{name}" -> the `probe_gpu_pod_code` entry,
    joined in for the DSS identity + GPU-keyword fields on GPU pods.
    """
    from binpack import parse_cpu_milli, parse_mem_mib  # type: ignore
    gpu_code_by_key = gpu_code_by_key or {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for p in pods:
        meta = (p or {}).get('metadata') or {}
        spec = (p or {}).get('spec') or {}
        status = (p or {}).get('status') or {}
        node = spec.get('nodeName') or ''
        if not node:
            continue
        ns = meta.get('namespace') or ''
        pname = meta.get('name') or ''
        key = f'{ns}/{pname}'
        # Sum container requests.
        cpu_req = 0
        mem_req = 0
        gpu_req = 0
        for c in (spec.get('containers') or []):
            req = ((c or {}).get('resources') or {}).get('requests') or {}
            cpu_req += parse_cpu_milli(req.get('cpu'))
            mem_req += parse_mem_mib(req.get('memory'))
            try:
                gpu_req += int(req.get('nvidia.com/gpu') or 0)
            except (TypeError, ValueError):
                pass
        # Ready flag + restart count from container statuses.
        cs = status.get('containerStatuses') or []
        ready = bool(cs) and all((c or {}).get('ready') for c in cs)
        restart_count = sum(int((c or {}).get('restartCount') or 0) for c in cs)
        # OOMKilled / CrashLoopBackOff detection.
        oom = False
        crash = False
        for c in cs:
            last = (c or {}).get('lastState') or {}
            term = last.get('terminated') or {}
            if term.get('reason') == 'OOMKilled':
                oom = True
            waiting = ((c or {}).get('state') or {}).get('waiting') or {}
            if waiting.get('reason') == 'CrashLoopBackOff':
                crash = True
        usage = usage_by_pod_key.get(key) or {}
        pod_row: Dict[str, Any] = {
            'name': pname,
            'ns': ns,
            'phase': status.get('phase') or '',
            'restartCount': restart_count,
            'ready': ready,
            'requestedCpuMilli': cpu_req,
            'requestedMemMib': mem_req,
            'requestedGpu': gpu_req,
            'realCpuMilli': usage.get('cpuMilli'),
            'realMemMib': usage.get('memMib'),
            'isSystem': ns in ('kube-system', 'kube-public', 'kube-node-lease'),
            'oomKilled': oom,
            'crashLoopBackOff': crash,
        }
        # DSS identity (GPU pods only — the join table holds GPU-requesting pods).
        gpu_entry = gpu_code_by_key.get(key)
        if gpu_entry:
            pod_row['dssProjectKey'] = gpu_entry.get('projectKey')
            pod_row['dssObjectType'] = gpu_entry.get('objectType')
            pod_row['dssObjectId'] = gpu_entry.get('objectId')
            pod_row['dssSubmitter'] = gpu_entry.get('submitter')
            pod_row['gpuKeywordsFound'] = gpu_entry.get('gpuKeywordsFound')
        out.setdefault(node, []).append(pod_row)
    return out


def _node_breakdown(probes: Dict[str, Dict[str, Any]], price_by_type: Dict[str, float]) -> List[Dict[str, Any]]:
    """Per-node row for the K8sNodeTable UI, including expanded detail fields."""
    nodes_probe = probes.get('probe_nodes') or {}
    pods_probe = probes.get('probe_pods') or {}
    top_nodes_probe = probes.get('probe_top_nodes') or {}
    top_pods_probe = probes.get('probe_top_pods') or {}
    if not nodes_probe.get('ok'):
        return []
    items = (nodes_probe.get('data') or {}).get('items') or []
    pods = (pods_probe.get('data') or {}).get('items') or [] if pods_probe.get('ok') else []
    top_by_node = {row['node']: row for row in ((top_nodes_probe.get('data') or []) if top_nodes_probe.get('ok') else [])}
    usage_by_pod_key: Dict[str, Dict[str, int]] = {}
    if top_pods_probe.get('ok'):
        for row in (top_pods_probe.get('data') or []):
            key = f"{row.get('namespace')}/{row.get('pod')}"
            existing = usage_by_pod_key.get(key) or {'cpuMilli': 0, 'memMib': 0}
            existing['cpuMilli'] = int(existing['cpuMilli']) + int(row.get('cpuMilli') or 0)
            existing['memMib'] = int(existing['memMib']) + int(row.get('memMib') or 0)
            usage_by_pod_key[key] = existing
    gpu_probe = probes.get('probe_gpu_pod_code') or {}
    gpu_code_by_key = (gpu_probe.get('data') or {}) if gpu_probe.get('ok') else {}
    pods_by_node = _pods_by_node(pods, usage_by_pod_key, gpu_code_by_key)

    pod_count_by_node: Dict[str, int] = {}
    user_pod_count_by_node: Dict[str, int] = {}
    for p in pods:
        spec = (p or {}).get('spec') or {}
        ns = ((p or {}).get('metadata') or {}).get('namespace') or ''
        node = spec.get('nodeName') or ''
        if not node:
            continue
        pod_count_by_node[node] = pod_count_by_node.get(node, 0) + 1
        if ns not in ('kube-system', 'kube-public', 'kube-node-lease'):
            user_pod_count_by_node[node] = user_pod_count_by_node.get(node, 0) + 1

    rows: List[Dict[str, Any]] = []
    for n in items:
        meta = (n or {}).get('metadata') or {}
        name = meta.get('name') or ''
        labels = meta.get('labels') or {}
        spec = (n or {}).get('spec') or {}
        status = (n or {}).get('status') or {}
        allocatable = status.get('allocatable') or {}
        capacity = status.get('capacity') or {}
        node_info = status.get('nodeInfo') or {}
        instance_type = labels.get('node.kubernetes.io/instance-type') or labels.get('beta.kubernetes.io/instance-type') or ''
        ready = False
        conditions_full: List[Dict[str, Any]] = []
        for cond in status.get('conditions') or []:
            conditions_full.append({
                'type': cond.get('type') or '',
                'status': cond.get('status') or '',
                'reason': cond.get('reason'),
                'message': cond.get('message'),
                'lastTransitionTime': cond.get('lastTransitionTime'),
            })
            if cond.get('type') == 'Ready':
                ready = cond.get('status') == 'True'
        taints = [
            {'key': t.get('key') or '', 'value': t.get('value'), 'effect': t.get('effect') or ''}
            for t in (spec.get('taints') or [])
        ]
        addresses = [
            {'type': a.get('type') or '', 'address': a.get('address') or ''}
            for a in (status.get('addresses') or [])
        ]
        top = top_by_node.get(name) or {}
        rows.append({
            'name': name,
            'instanceType': instance_type,
            'isGpu': is_gpu_instance(instance_type),
            'ready': ready,
            'podCount': pod_count_by_node.get(name, 0),
            'userPodCount': user_pod_count_by_node.get(name, 0),
            'allocatableCpu': allocatable.get('cpu'),
            'allocatableMemory': allocatable.get('memory'),
            'allocatableGpu': allocatable.get('nvidia.com/gpu'),
            'cpuUsageMilli': top.get('cpuMilli'),
            'cpuPct': top.get('cpuPct'),
            'memUsageMib': top.get('memMib'),
            'memPct': top.get('memPct'),
            'hourly': price_by_type.get(instance_type),
            'labels': labels,
            'taints': taints,
            'unschedulable': bool(spec.get('unschedulable')),
            'conditions': conditions_full,
            'addresses': addresses,
            'nodeInfo': {
                'kubeletVersion': node_info.get('kubeletVersion'),
                'kubeProxyVersion': node_info.get('kubeProxyVersion'),
                'containerRuntimeVersion': node_info.get('containerRuntimeVersion'),
                'kernelVersion': node_info.get('kernelVersion'),
                'osImage': node_info.get('osImage'),
                'operatingSystem': node_info.get('operatingSystem'),
                'architecture': node_info.get('architecture'),
            },
            'capacity': {
                'cpu': capacity.get('cpu'),
                'memory': capacity.get('memory'),
                'ephemeralStorage': capacity.get('ephemeral-storage'),
                'gpu': capacity.get('nvidia.com/gpu'),
            },
            'createdAt': _parse_node_creation_ms(meta.get('creationTimestamp')),
            'selectedLabels': _selected_labels(labels),
            'pods': pods_by_node.get(name) or [],
        })
    return rows


def _pod_summary(probes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    pods_probe = probes.get('probe_pods') or {}
    if not pods_probe.get('ok'):
        return {'total': 0, 'byPhase': {}, 'byNamespace': {}, 'failed': 0}
    items = (pods_probe.get('data') or {}).get('items') or []
    by_phase: Dict[str, int] = {}
    by_ns: Dict[str, int] = {}
    failed = 0
    for p in items:
        ns = ((p or {}).get('metadata') or {}).get('namespace') or ''
        phase = ((p or {}).get('status') or {}).get('phase') or ''
        by_phase[phase] = by_phase.get(phase, 0) + 1
        if ns:
            by_ns[ns] = by_ns.get(ns, 0) + 1
        if phase in ('Failed',):
            failed += 1
    return {'total': len(items), 'byPhase': by_phase, 'byNamespace': by_ns, 'failed': failed}


def _evaluate_rules(probes: Dict[str, Dict[str, Any]], filter_ids: Optional[Set[str]]) -> List[Dict[str, Any]]:
    findings: List[Finding] = []
    for rule in R.ALL_RULES:
        if filter_ids and rule.id not in filter_ids:
            continue
        # Skip when a required probe failed.
        missing = [name for name in rule.requires_probes if not (probes.get(name) or {}).get('ok')]
        if missing:
            continue
        try:
            produced = rule.evaluate(probes) or []
        except Exception as exc:
            # A buggy rule should not kill the whole audit.
            produced = [Finding(
                id=f'{rule.id}::__error__',
                rule=rule.id,
                severity='info',
                category=rule.category,
                title=f'Rule {rule.id} crashed',
                summary=f'{type(exc).__name__}: {str(exc)[:200]}',
                confidence='low',
            )]
        for f in produced:
            findings.append(f)
    findings = sort_findings(findings)
    return [f.to_dict() for f in findings]


def _count_items(data: Any) -> Optional[int]:
    """Item count for a probe payload — kubectl `-o json` puts items under .items,
    `top` probes return a list directly; everything else has no notion of count."""
    if isinstance(data, dict) and isinstance(data.get('items'), list):
        return len(data['items'])
    if isinstance(data, list):
        return len(data)
    return None


def _probe_summary(probes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Strip the large `data` payload but keep diagnostic context for the UI.

    Names starting with `_` are virtual probes (e.g. `_pricing`) — they're
    plumbed through the rule engine but should not appear in the probe-status
    UI; the pricing status surfaces via the dedicated `pricingStatus` envelope key.
    """
    return {
        name: {
            'ok': bool(p.get('ok')),
            'error': p.get('error'),
            'rc': p.get('rc'),
            'stdoutHead': p.get('stdoutHead') or '',
            'stderrFull': p.get('stderrFull') or '',
            'durationMs': int(p.get('durationMs') or 0),
            'itemCount': _count_items(p.get('data')),
        }
        for name, p in probes.items()
        if not name.startswith('_')
    }


def run_audit(cluster_id_req: str, rules_filter: str) -> Dict[str, Any]:
    started = _now_ms()
    dip_home = _resolve_dip_home()
    if not dip_home:
        return {'ok': False, 'error': 'DIP_HOME not set', 'durationMs': _now_ms() - started}

    kubeconfig_name: Optional[str] = None
    if cluster_id_req.startswith(KUBECONFIG_ID_PREFIX):
        cand = _kubeconfig_candidate_by_id(dip_home, cluster_id_req)
        if cand is None:
            return {
                'ok': False,
                'error': f'kubeconfig {cluster_id_req[len(KUBECONFIG_ID_PREFIX):]!r} is not among the discovered candidates on this host',
                'durationMs': _now_ms() - started,
            }
        kubeconfig_name = _kubeconfig_display_name(cand)
        chosen = {
            'id': cluster_id_req,
            'baseDir': None,
            'hasKubeconfig': True,
            'kubeconfig': cand['path'],
        }
        try:
            kubectl = _make_file_kubectl_runner(cand['path'], cand.get('context'))
        except Exception as exc:
            return {
                'ok': False,
                'error': f'Cannot use kubeconfig {cand["displayPath"]!r}: {type(exc).__name__}: {str(exc)[:300]}',
                'cluster': chosen,
                'durationMs': _now_ms() - started,
            }
    else:
        chosen = _pick_cluster_id(dip_home, cluster_id_req)
        if not chosen:
            listing = P.probe_clusters_list(dip_home)
            return {
                'ok': False,
                'error': f'No matching cluster found (requested={cluster_id_req!r})',
                'clusters': listing.get('data') or [],
                'durationMs': _now_ms() - started,
            }
        try:
            kubectl = _make_kubectl_runner(chosen['id'])
        except Exception as exc:
            return {
                'ok': False,
                'error': f'Cannot reach cluster {chosen["id"]!r} via DSS API: {type(exc).__name__}: {str(exc)[:300]}',
                'cluster': chosen,
                'durationMs': _now_ms() - started,
            }
    probes_result = _run_probes(kubectl, dip_home, chosen['id'])

    # Resolve pricing once. The result becomes both a top-level envelope key
    # (so the UI can surface a "Cost analysis unavailable" banner) and a
    # virtual probe `_pricing` — rules that depend on real cost numbers
    # declare `requires_probes=['_pricing']` and the existing rule loop
    # skips them when the source failed.
    pricing_status = _resolve_pricing(probes_result, dip_home)
    price_by_type = pricing_status.get('priceByType') or {}
    probes_result['_pricing'] = {
        'ok': bool(pricing_status.get('ok')),
        'data': {'priceByType': price_by_type, 'region': pricing_status.get('region')},
        'error': pricing_status.get('error'),
        'durationMs': max(0, _now_ms() - int(pricing_status.get('fetchedAt') or _now_ms())),
    }

    # GPU-pod code/usage probe — also a post-pool resolution (needs probe_pods +
    # a DSS reader + the kubectl runner). Injected as a normal probe so the GPU
    # rule sees it and the per-pod identity join (_pods_by_node) can read it.
    probes_result['probe_gpu_pod_code'] = _resolve_gpu_pod_code(probes_result, kubectl)

    filter_ids: Optional[Set[str]] = None
    if rules_filter:
        filter_ids = {tok.strip() for tok in rules_filter.split(',') if tok.strip()}

    findings = _evaluate_rules(probes_result, filter_ids)

    return {
        'ok': True,
        'cluster': {
            **_cluster_meta(probes_result, chosen['id']),
            'baseDir': chosen.get('baseDir'),
            **({'name': kubeconfig_name, 'kubeconfig': chosen.get('kubeconfig')} if kubeconfig_name else {}),
        },
        'probes': _probe_summary(probes_result),
        'findings': findings,
        'findingsCount': len(findings),
        'costSnapshot': _cost_snapshot(probes_result, price_by_type),
        'nodeBreakdown': _node_breakdown(probes_result, price_by_type),
        'podSummary': _pod_summary(probes_result),
        'pricingStatus': {
            'ok': bool(pricing_status.get('ok')),
            'source': pricing_status.get('source'),
            'region': pricing_status.get('region'),
            'error': pricing_status.get('error'),
            'fetchedAt': pricing_status.get('fetchedAt'),
        },
        'metadata': {
            'durationMs': _now_ms() - started,
            'dipHome': dip_home,
            'kubectlVersion': (probes_result.get('probe_kubectl_version') or {}).get('data'),
            'rulesEvaluated': len(R.ALL_RULES) if not filter_ids else len(filter_ids),
            'rulesAvailable': len(R.ALL_RULES),
        },
    }


def cluster_health() -> Dict[str, Any]:
    """Parallel `kubectl version` probe across every DSS-known cluster.

    Returns:
        {
          'ok': bool, 'durationMs': int,
          'clusters': [
            {'id', 'ok', 'errorClass', 'errorSummary', 'errorFull',
             'latencyMs', 'kubectlServerVersion'},
            ...
          ],
        }

    Errors are classified into dns / network / auth / tls / unknown so the
    UI picker can render a tone dot per cluster without re-parsing stderr.
    """
    started = _now_ms()
    dip_home = _resolve_dip_home()
    if not dip_home:
        return {'ok': False, 'error': 'DIP_HOME not set', 'clusters': [], 'durationMs': _now_ms() - started}

    # Combine filesystem-discovered clusters with DSS-registered ones (same
    # source the picker uses) — fs discovery doesn't see clusters that have
    # no $DIP_HOME/clusters/<id>/ dir yet.
    listing = P.probe_clusters_list(dip_home)
    fs_ids = [c['id'] for c in (listing.get('data') or []) if c.get('id')]
    cluster_ids: List[str] = list(fs_ids)
    try:
        import dataiku
        api = dataiku.api_client()
        for c in (api.list_clusters() or []):
            cid = c.get('id') if isinstance(c, dict) else None
            if cid and cid not in cluster_ids:
                cluster_ids.append(cid)
    except Exception:
        pass
    # Kubeconfig-file targets get health dots too — same synthetic ids the
    # picker uses, so the frontend health map joins without special-casing.
    for cand in _kubeconfig_candidates(dip_home):
        if cand.get('exists'):
            cluster_ids.append(cand['id'])

    def probe_one(cid: str) -> Dict[str, Any]:
        t0 = _now_ms()
        try:
            kubectl = _kubectl_for(dip_home, cid)
            rc, out, err = kubectl('version -o json --request-timeout=8s')
        except Exception as exc:
            err_full = f'{type(exc).__name__}: {str(exc)[:600]}'
            cls = P.classify_kubectl_error(err_full)
            return {
                'id': cid, 'ok': False, 'errorClass': cls,
                'errorSummary': err_full.splitlines()[0][:200] if err_full else None,
                'errorFull': err_full, 'latencyMs': _now_ms() - t0,
                'kubectlServerVersion': None,
            }
        elapsed = _now_ms() - t0
        if rc == 0:
            server_version: Optional[str] = None
            try:
                parsed = json.loads(out) if out else {}
                sv = (parsed.get('serverVersion') or {}) if isinstance(parsed, dict) else {}
                server_version = sv.get('gitVersion')
            except (json.JSONDecodeError, AttributeError):
                pass
            return {
                'id': cid, 'ok': True, 'errorClass': None,
                'errorSummary': None, 'errorFull': None,
                'latencyMs': elapsed, 'kubectlServerVersion': server_version,
            }
        err_full = (err or '').strip() or f'kubectl exit {rc}'
        cls = P.classify_kubectl_error(err_full)
        summary = err_full.splitlines()[0][:200]
        return {
            'id': cid, 'ok': False, 'errorClass': cls,
            'errorSummary': summary, 'errorFull': err_full[:4000],
            'latencyMs': elapsed, 'kubectlServerVersion': None,
        }

    results: List[Dict[str, Any]] = []
    if cluster_ids:
        with cf.ThreadPoolExecutor(max_workers=min(8, len(cluster_ids)), thread_name_prefix='k8shealth') as pool:
            futures = {pool.submit(probe_one, cid): cid for cid in cluster_ids}
            try:
                for fut in cf.as_completed(futures, timeout=20):
                    try:
                        results.append(fut.result(timeout=5))
                    except Exception as exc:
                        cid = futures[fut]
                        results.append({
                            'id': cid, 'ok': False, 'errorClass': 'unknown',
                            'errorSummary': f'{type(exc).__name__}: {str(exc)[:200]}',
                            'errorFull': f'{type(exc).__name__}: {exc}',
                            'latencyMs': 0, 'kubectlServerVersion': None,
                        })
            except cf.TimeoutError:
                # A hung probe (dead API server behind a kubeconfig) must not
                # eat the whole health response — report stragglers as network.
                done_ids = {r['id'] for r in results}
                for cid in cluster_ids:
                    if cid not in done_ids:
                        results.append({
                            'id': cid, 'ok': False, 'errorClass': 'network',
                            'errorSummary': 'health probe timed out after 20s',
                            'errorFull': 'health probe timed out after 20s',
                            'latencyMs': 20000, 'kubectlServerVersion': None,
                        })
    results.sort(key=lambda r: r['id'])
    return {'ok': True, 'clusters': results, 'durationMs': _now_ms() - started}


def list_clusters() -> Dict[str, Any]:
    started = _now_ms()
    dip_home = _resolve_dip_home()
    if not dip_home:
        return {'ok': False, 'error': 'DIP_HOME not set', 'durationMs': _now_ms() - started}
    listing = P.probe_clusters_list(dip_home)
    return {
        'ok': bool(listing.get('ok')),
        'error': listing.get('error'),
        'clusters': listing.get('data') or [],
        'kubeconfigCandidates': [
            {**c, 'name': _kubeconfig_display_name(c)}
            for c in _kubeconfig_candidates(dip_home)
        ],
        'durationMs': _now_ms() - started,
    }


def describe_pod(cluster_id: str, namespace: str, pod_name: str) -> Dict[str, Any]:
    """`kubectl describe pod <name> -n <ns>` for one pod, returning raw stdout.

    Args are sanitized (no whitespace / shell metacharacters) before being
    spliced into the kubectl arg string — identity comes from the UI but this
    is host-bound shell-adjacent work, so defense-in-depth applies."""
    started = _now_ms()
    cluster_id = (cluster_id or '').strip()
    namespace = (namespace or '').strip()
    pod_name = (pod_name or '').strip()
    if not cluster_id:
        return {'ok': False, 'error': 'cluster_id is required', 'durationMs': _now_ms() - started}
    if not (P.is_safe_k8s_name(namespace) and P.is_safe_k8s_name(pod_name)):
        return {'ok': False, 'error': 'invalid namespace or pod name', 'durationMs': _now_ms() - started}
    try:
        kubectl = _kubectl_for(_resolve_dip_home(), cluster_id)
    except Exception as exc:
        return {
            'ok': False,
            'error': f'cannot reach cluster {cluster_id!r}: {type(exc).__name__}: {str(exc)[:200]}',
            'durationMs': _now_ms() - started,
        }
    rc, out, err = kubectl(f'describe pod {pod_name} -n {namespace}')
    if rc != 0:
        return {
            'ok': False,
            'error': ((err or '').strip()[:4000] or f'kubectl exit {rc}'),
            'durationMs': _now_ms() - started,
        }
    return {'ok': True, 'text': out or '', 'durationMs': _now_ms() - started}


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        operation = (self.config.get('operation') or 'audit').strip()
        if operation == 'list-clusters':
            return json.dumps(list_clusters())
        if operation == 'cluster-health':
            return json.dumps(cluster_health())
        if operation == 'describe-pod':
            return json.dumps(describe_pod(
                (self.config.get('cluster_id') or '').strip(),
                (self.config.get('namespace') or '').strip(),
                (self.config.get('pod_name') or '').strip(),
            ))
        cluster_id = (self.config.get('cluster_id') or '').strip()
        rules_filter = (self.config.get('rules_filter') or '').strip()
        return json.dumps(run_audit(cluster_id, rules_filter))
