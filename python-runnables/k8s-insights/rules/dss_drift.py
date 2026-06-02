"""DSS-drift rules (22–28). The unique value of this module.

These cross-reference DSS general-settings.json against live cluster state to
catch the exact patterns surfaced in your 3-hour troubleshooting session.
"""
from typing import Any, Dict, List, Tuple

from finding import Finding  # type: ignore
from binpack import parse_cpu_milli, parse_mem_mib  # type: ignore
from .base import (
    Rule, ProbeBundle, items, pod_namespace, pod_name, pod_phase,
    pod_containers, pod_total_requests, parse_dss_namespace,
    is_kube_system_ns, kubectl_remediation, file_edit_remediation,
    gui_remediation, doc_link_remediation, make_id,
)


NVIDIA_DS_AFFINITY_PYTHON_PATCH = '''# In gpu_driver.py, after constructing the device-plugin DaemonSet manifest, add:
ds["spec"]["template"]["spec"]["affinity"] = {
    "nodeAffinity": {
        "requiredDuringSchedulingIgnoredDuringExecution": {
            "nodeSelectorTerms": [
                {"matchExpressions": [{"key": "feature.node.kubernetes.io/pci-10de.present",
                                       "operator": "In", "values": ["true"]}]},
                {"matchExpressions": [{"key": "nvidia.com/gpu.present",
                                       "operator": "In", "values": ["true"]}]},
            ]
        }
    }
}
'''


def _exec_config_index(probes: ProbeBundle) -> Dict[str, Dict[str, Any]]:
    data = (probes.get('probe_dss_general_settings') or {}).get('data') or {}
    out: Dict[str, Dict[str, Any]] = {}
    for cfg in (data.get('executionConfigs') or []):
        name = cfg.get('name')
        if name:
            out[name] = cfg
    return out


class Rule22DeploymentRequestVsConfigDrift(Rule):
    id = 'deployment-request-vs-config-drift'
    category = 'dss-drift'
    severity = 'high'
    requires_probes = ['probe_deployments_all', 'probe_dss_general_settings']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        configs = _exec_config_index(probes)
        if not configs:
            return out
        for deploy in items(probes, 'probe_deployments_all'):
            meta = deploy.get('metadata') or {}
            ns = meta.get('namespace') or ''
            parsed = parse_dss_namespace(ns)
            if not parsed:
                continue
            config_name = parsed['config']
            cfg = configs.get(config_name)
            if not cfg:
                continue
            cfg_mem = cfg.get('memRequestMB')
            try:
                cfg_mem_mib = int(cfg_mem) if cfg_mem is not None else None
            except (TypeError, ValueError):
                cfg_mem_mib = None
            if cfg_mem_mib is None:
                continue
            containers = (((deploy.get('spec') or {}).get('template') or {}).get('spec') or {}).get('containers') or []
            template_mem_mib = 0
            for c in containers:
                req = ((c or {}).get('resources') or {}).get('requests') or {}
                template_mem_mib += parse_mem_mib(req.get('memory'))
            if template_mem_mib == 0:
                continue
            ratio = abs(template_mem_mib - cfg_mem_mib) / max(cfg_mem_mib, 1)
            if ratio < 0.10:
                continue
            out.append(Finding(
                id=make_id(self.id, f'{ns}/{meta.get("name")}'),
                rule=self.id,
                severity='high',
                category=self.category,
                title=(
                    f'Deployment "{meta.get("name")}" memory request drifted from DSS config "{config_name}"'
                ),
                summary=(
                    f'Pod-template memory request is {template_mem_mib}MiB but the matching '
                    f'execution config "{config_name}" has memRequestMB={cfg_mem_mib}. The '
                    'Deployment needs a restart to pick up the newer config value.'
                ),
                evidence={
                    'deployment': f'{ns}/{meta.get("name")}',
                    'configName': config_name,
                    'templateMemMib': template_mem_mib,
                    'configMemMib': cfg_mem_mib,
                    'deltaPct': round(ratio * 100, 1),
                },
                remediation=[
                    kubectl_remediation(
                        'Force a rollout to pull current config values',
                        f'kubectl -n {ns} rollout restart deployment {meta.get("name")}',
                        namespace=ns,
                    ),
                    doc_link_remediation(
                        'DSS: how execution configs propagate to pod templates',
                        'https://doc.dataiku.com/dss/latest/containers/setup.html',
                    ),
                ],
            ))
        return out


class Rule23NvidiaDevicePluginMissingAffinity(Rule):
    id = 'nvidia-device-plugin-missing-affinity'
    category = 'dss-drift'
    severity = 'critical'
    requires_probes = ['probe_managed_cluster_dir']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        data = (probes.get('probe_managed_cluster_dir') or {}).get('data') or {}
        nvidia = data.get('nvidiaYaml') or {}
        text = nvidia.get('text') or ''
        if not text:
            return []
        if 'affinity:' in text and 'nodeAffinity' in text:
            return []
        return [Finding(
            id=make_id(self.id, nvidia.get('path') or 'unknown'),
            rule=self.id,
            severity='critical',
            category='dss-drift',
            title='DSS-generated nvidia-device-plugin.yml has no affinity block',
            summary=(
                'The DSS-managed cluster ships a device-plugin DaemonSet without nodeAffinity, '
                'so it lands on every node (including CPU-only). This is the root of the '
                'cluster-wide DS spread you saw earlier.'
            ),
            evidence={'path': nvidia.get('path'), 'sizeBytes': len(text)},
            remediation=[
                file_edit_remediation(
                    'Patch gpu_driver.py to inject affinity when materializing the DS',
                    path='$DIP_HOME/plugins/installed/eks-clusters/python-lib/dku_kube/gpu_driver.py',
                    snippet=NVIDIA_DS_AFFINITY_PYTHON_PATCH,
                ),
                kubectl_remediation(
                    'For an immediate fix on the running cluster',
                    'kubectl -n kube-system patch ds nvidia-device-plugin-daemonset --type strategic --patch-file affinity.yaml',
                    namespace='kube-system',
                ),
            ],
        )]


class Rule25ExecutionConfigNoLimit(Rule):
    id = 'execution-config-no-limit'
    category = 'dss-drift'
    severity = 'medium'
    requires_probes = ['probe_dss_general_settings']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        for cfg in ((probes.get('probe_dss_general_settings') or {}).get('data') or {}).get('executionConfigs') or []:
            req = cfg.get('memRequestMB')
            lim = cfg.get('memLimitMB')
            # An *unset* limit is the opposite of the "tight limit" problem: no
            # limit means memory is unbounded, so the pod can burst freely — it
            # will never OOM for lack of headroom. Only a real, set limit that
            # is ≤ the request is the bug this rule flags.
            if lim is None:           # unset → no limit → not a problem
                continue
            try:
                req_n = int(req or 0)
                lim_n = int(lim or 0)
            except (TypeError, ValueError):
                continue
            if lim_n <= 0:            # 0 → no limit (DSS treats 0/unset alike)
                continue
            if req_n <= 0:
                continue
            if lim_n > req_n:         # has headroom → fine
                continue
            # fires only for a real, set limit with no headroom: 0 < lim <= req
            out.append(Finding(
                id=make_id(self.id, cfg.get('name') or 'unknown'),
                rule=self.id,
                severity='medium',
                category='dss-drift',
                title=f'Execution config "{cfg.get("name")}" has no memory headroom over request',
                summary=(
                    f'memRequestMB={req_n}, memLimitMB={lim_n}. Pods cannot burst above request, '
                    'so any temporary spike becomes OOMKilled. Set memLimitMB ≥ 1.5× request.'
                ),
                evidence={'configName': cfg.get('name'), 'memRequestMB': req_n, 'memLimitMB': lim_n},
                remediation=[
                    gui_remediation(
                        'DSS → Administration → Settings → Containerized execution',
                        f'Raise memLimitMB for "{cfg.get("name")}" to at least {int(req_n * 1.5)}',
                        target=cfg.get('name') or '',
                    ),
                ],
            ))
        return out


class Rule26ExecutionConfigOrphanNamespace(Rule):
    id = 'execution-config-orphan-namespace'
    category = 'dss-drift'
    severity = 'high'
    requires_probes = ['probe_pods', 'probe_dss_general_settings']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        configs = _exec_config_index(probes)
        out: List[Finding] = []
        ns_pod_counts: Dict[str, int] = {}
        for pod in items(probes, 'probe_pods'):
            ns = pod_namespace(pod)
            ns_pod_counts[ns] = ns_pod_counts.get(ns, 0) + 1
        # Group orphans by config name -> namespaces
        by_config: Dict[str, List[str]] = {}
        for ns in ns_pod_counts.keys():
            parsed = parse_dss_namespace(ns)
            if not parsed:
                continue
            config = parsed['config']
            if config in configs:
                continue
            by_config.setdefault(config, []).append(ns)
        for config, ns_list in by_config.items():
            pod_total = sum(ns_pod_counts.get(n, 0) for n in ns_list)
            out.append(Finding(
                id=make_id(self.id, config),
                rule=self.id,
                severity='high',
                category='dss-drift',
                title=f'Orphan DSS namespace(s) for config "{config}" — another DSS instance shares this cluster',
                summary=(
                    f'Found {len(ns_list)} namespace(s) matching dss-ns-{config}-* with '
                    f'{pod_total} pod(s), but this DSS has no execution config named "{config}". '
                    'Another DSS instance (e.g., sandbox vs designer) is sharing this EKS cluster.'
                ),
                evidence={
                    'orphanConfigName': config,
                    'namespaces': ns_list,
                    'podCount': pod_total,
                    'knownConfigs': list(configs.keys()),
                },
                remediation=[
                    doc_link_remediation(
                        'Investigate: identify the other DSS instance that owns these pods',
                        'https://doc.dataiku.com/dss/latest/containers/eks/clusters.html',
                    ),
                ],
            ))
        return out


class Rule27ExecutionConfigVsActualUsageBadFit(Rule):
    id = 'execution-config-vs-actual-usage-bad-fit'
    category = 'dss-drift'
    severity = 'medium'
    requires_probes = ['probe_pods', 'probe_top_pods', 'probe_dss_general_settings']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        configs = _exec_config_index(probes)
        if not configs:
            return []
        top_rows = (probes.get('probe_top_pods') or {}).get('data') or []
        usage_by_pod: Dict[str, int] = {}
        for r in top_rows:
            key = f"{r.get('namespace')}/{r.get('pod')}"
            usage_by_pod[key] = usage_by_pod.get(key, 0) + int(r.get('memMib') or 0)
        # Bucket usage by config (derived from namespace).
        usage_by_config: Dict[str, List[int]] = {}
        for pod in items(probes, 'probe_pods'):
            parsed = parse_dss_namespace(pod_namespace(pod))
            if not parsed:
                continue
            key = f'{pod_namespace(pod)}/{pod_name(pod)}'
            usage = usage_by_pod.get(key)
            if usage is None:
                continue
            usage_by_config.setdefault(parsed['config'], []).append(usage)
        out: List[Finding] = []
        for cfg_name, samples in usage_by_config.items():
            cfg = configs.get(cfg_name)
            if not cfg:
                continue
            req_mb = cfg.get('memRequestMB')
            try:
                req_n = int(req_mb or 0)
            except (TypeError, ValueError):
                continue
            if req_n <= 0 or len(samples) < 3:
                continue
            samples_sorted = sorted(samples)
            p95 = samples_sorted[max(0, int(len(samples_sorted) * 0.95) - 1)]
            if p95 == 0:
                continue
            ratio = req_n / p95
            if ratio > 5.0:
                out.append(Finding(
                    id=make_id(self.id, f'overshoot::{cfg_name}'),
                    rule=self.id,
                    severity='low',
                    category='dss-drift',
                    title=f'Config "{cfg_name}": memRequestMB is {ratio:.1f}× p95 real usage',
                    summary=(
                        f'memRequestMB={req_n} vs p95 actual={p95}MiB across {len(samples)} pods. '
                        f'Lower the request to ~{int(p95 * 1.5)}MiB to free up bin-pack capacity.'
                    ),
                    evidence={
                        'configName': cfg_name,
                        'memRequestMB': req_n,
                        'p95UsageMib': p95,
                        'samples': len(samples),
                        'recommendedMemRequestMB': int(p95 * 1.5),
                    },
                ))
            elif ratio < 0.8:
                out.append(Finding(
                    id=make_id(self.id, f'undershoot::{cfg_name}'),
                    rule=self.id,
                    severity='high',
                    category='dss-drift',
                    title=f'Config "{cfg_name}": memRequestMB below p95 real usage',
                    summary=(
                        f'memRequestMB={req_n} vs p95 actual={p95}MiB across {len(samples)} pods. '
                        'Workloads regularly burst above request, risking node OOM.'
                    ),
                    evidence={
                        'configName': cfg_name,
                        'memRequestMB': req_n,
                        'p95UsageMib': p95,
                        'samples': len(samples),
                        'recommendedMemRequestMB': int(p95 * 1.3),
                    },
                ))
        return out


RULES = [
    Rule22DeploymentRequestVsConfigDrift(),
    Rule23NvidiaDevicePluginMissingAffinity(),
    Rule25ExecutionConfigNoLimit(),
    Rule26ExecutionConfigOrphanNamespace(),
    Rule27ExecutionConfigVsActualUsageBadFit(),
]
