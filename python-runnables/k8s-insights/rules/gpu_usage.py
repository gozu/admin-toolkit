"""GPU-usage rule: flag a GPU-requesting pod whose DSS code uses no GPU library
*and* whose live GPU utilization is zero.

Two-tier, driven by `probe_gpu_pod_code`:
  Tier 1 (code scan) selects candidates — resolved DSS objects whose code
          imports no GPU library.
  Tier 2 (live nvidia-smi) overrides — any live GPU use suppresses the finding;
          zero use emits it with a "re-check in a few minutes" advisory because
          utilization is instantaneous/bursty.
Pods whose code *does* import a GPU library are never flagged (the critical
false-positive guard). Objects whose code can't be read — e.g. a pod owned by
another DSS instance on a shared cluster — are flagged only when a live nvidia-smi
read independently shows the GPU idle (no static signal, so live evidence is
required and the finding is lower-confidence).
"""
from typing import Any, Dict, List

from finding import Finding  # type: ignore
from .base import (
    Rule, ProbeBundle, items, probe_data, pod_namespace, pod_name, pod_node,
    pod_total_requests, node_name, node_instance_type, node_is_gpu,
    gui_remediation, doc_link_remediation, make_id,
)
from .cost import _price_map


class RuleGpuPodNotUsingGpu(Rule):
    id = 'gpu-pod-not-using-gpu'
    category = 'cost'  # co-locate with rule 20 (GpuNodeIdle) in cost ranking
    severity = 'high'
    requires_probes = ['probe_gpu_pod_code', 'probe_pods', 'probe_nodes']

    def evaluate(self, probes: ProbeBundle) -> List[Finding]:
        out: List[Finding] = []
        gpu_code: Dict[str, Any] = probe_data(probes, 'probe_gpu_pod_code') or {}
        gpu_nodes_by_name = {node_name(n): n for n in items(probes, 'probe_nodes') if node_is_gpu(n)}

        for pod in items(probes, 'probe_pods'):
            _, _, gpu_req = pod_total_requests(pod)
            if gpu_req <= 0:
                continue
            node = pod_node(pod)
            # Only GPU pods actually on a GPU node (rule 05 owns the misplaced case).
            if not node or node not in gpu_nodes_by_name:
                continue
            key = f'{pod_namespace(pod)}/{pod_name(pod)}'
            entry = gpu_code.get(key)
            if not entry:
                continue

            resolved = entry.get('resolved') is True
            keywords_found = entry.get('gpuKeywordsFound') is True
            live_ok = entry.get('nvidiaSmiOk') is True

            # FP guard: a *resolved* object whose code imports a GPU library is
            # legitimately using the GPU — never flag it.
            if resolved and keywords_found:
                continue
            # Live override: any real GPU use beats both signals → suppress.
            if entry.get('gpuBusy') is True:
                continue
            # Unresolved code (e.g. the pod belongs to another DSS instance on this
            # shared cluster, so its project isn't local) gives no static signal —
            # flag it only on a *successful* live nvidia-smi read showing it idle,
            # never on a foreign pod we couldn't probe at all.
            if not resolved and not live_ok:
                continue

            project_key = entry.get('projectKey')
            object_type = entry.get('objectType') or 'object'
            object_id = entry.get('objectId')
            submitter = entry.get('submitter')
            util = entry.get('gpuUtilPct')

            evidence: Dict[str, Any] = {
                'pod': key,
                'node': node,
                'projectKey': project_key,
                'objectType': object_type,
                'objectId': object_id,
                'submitter': submitter,
                'requestedGpu': gpu_req,
                'gpuUtilPct': util,
                'gpuComputeProcCount': entry.get('gpuComputeProcCount'),
                'gpuComputeMemMib': entry.get('gpuComputeMemMib'),
                'matchedKeywords': [],
                'execType': entry.get('execType'),
                'sourceChars': entry.get('sourceChars'),
            }

            cpu_doc = doc_link_remediation(
                'Move to a CPU execution config (re-check live GPU usage first)',
                'https://doc.dataiku.com/dss/latest/containers/setup.html#execution-configurations',
            )
            resolved_gui = gui_remediation(
                f'Open the DSS {object_type} and confirm it needs a GPU',
                f'Project {project_key} → {object_type} "{object_id}"'
                + (f' (submitted by {submitter})' if submitter else ''),
                target=project_key or '',
            )

            if not resolved:
                # Foreign / unreadable code object — flagged purely on live evidence.
                evidence['codeUninspectable'] = True
                evidence['recheckAdvised'] = True
                evidence['resolveError'] = entry.get('error')
                summary = (
                    f'{object_type} "{object_id}" in project {project_key} requests {gpu_req} GPU, '
                    f'but a live nvidia-smi read shows {util}% utilization with no attached compute '
                    f'processes. GPU utilization is instantaneous and bursty — re-run this audit in a '
                    f'few minutes before acting; only treat this as waste if it persists.'
                )
                confidence = 'low'  # no static signal — live read only
                remediation = [
                    gui_remediation(
                        'Investigate on the owning DSS instance',
                        f'This GPU pod was launched by another DSS instance sharing this cluster '
                        f'(project {project_key}, {object_type} "{object_id}"'
                        + (f', submitted by {submitter}' if submitter else '') + '); it is not on '
                        f'this instance, so review it there or contact the submitter.',
                    ),
                    cpu_doc,
                ]
            elif live_ok:
                # Tier 2 confirmed zero usage — but utilization is instantaneous.
                evidence['recheckAdvised'] = True
                summary = (
                    f'{object_type} "{object_id}" in project {project_key} requests {gpu_req} GPU, '
                    f'but its code imports no GPU library and a live nvidia-smi read shows '
                    f'{util}% utilization with no attached compute processes. GPU utilization is '
                    f'instantaneous and bursty — re-run this audit in a few minutes before acting; '
                    f'only treat this as waste if it persists.'
                )
                confidence = 'medium'
                remediation = [resolved_gui, cpu_doc]
            else:
                # Tier 2 unavailable — code-scan-only, flagged as such.
                evidence['liveCheckUnavailable'] = True
                evidence['nvidiaSmiError'] = entry.get('nvidiaSmiError')
                summary = (
                    f'{object_type} "{object_id}" in project {project_key} requests {gpu_req} GPU, '
                    f'but its code imports no GPU library. Could not verify live GPU usage '
                    f'(RBAC/exec or nvidia-smi missing: {entry.get("nvidiaSmiError")}) — this finding '
                    f'is code-scan-only.'
                )
                confidence = 'medium'
                remediation = [resolved_gui, cpu_doc]

            # Freeing this idle GPU pod releases its GPU node; value the recovered
            # capacity at 0.9 × the node's monthly cost (a GPU instance is ~10× a
            # CPU one). Opportunistic — None when pricing is unavailable; the
            # finding still fires either way.
            hourly = _price_map(probes).get(node_instance_type(gpu_nodes_by_name[node]))
            cost = round(0.9 * hourly * 730.0, 2) if hourly is not None else None
            if cost is not None:
                evidence['gpuInstanceType'] = node_instance_type(gpu_nodes_by_name[node])
                evidence['gpuHourly'] = round(hourly, 4)
                evidence['savingsHourly'] = round(0.9 * hourly, 4)
                evidence['savingsMonthly'] = cost

            out.append(Finding(
                id=make_id(self.id, key),
                rule=self.id,
                severity='high',
                category=self.category,
                title=f'GPU pod "{pod_name(pod)}" may not be using its GPU',
                summary=summary,
                evidence=evidence,
                confidence=confidence,
                cost_impact_per_month=cost,
                remediation=remediation,
            ))
        return out


RULES = [
    RuleGpuPodNotUsingGpu(),
]
