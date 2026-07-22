"""K8S Insights routes — clusters, cluster health, pod-describe, audit stream."""
import json
import logging
import queue
import threading
from typing import Any, Dict, List, Optional

from flask import Blueprint, Response, g, jsonify, request

from adk_backend.caching import _cache_get
from adk_backend.clients import MacroProjectMissing
from adk_backend.context import _THREAD_LOCAL
from adk_backend.macros import _k8s_insights_macro
from adk_backend.settings import _BACKEND_SETTINGS
from adk_backend.utils import _sse_response

bp = Blueprint('k8s_insights', __name__)
_LOGGER = logging.getLogger(__name__)


@bp.route('/api/k8s-insights/cluster-count')
def api_k8s_insights_cluster_count():
    """Cheap K8s presence signal for module availability gating.

    Pure DSS API (no macro, no host filesystem probing): counts clusters
    registered in DSS plus containerized-execution configs that declare a
    custom kubeConfigPath — those are auditable via `kubectl --kubeconfig`
    even when no cluster is registered. `count: null` means "unknown" — the
    frontend treats unknown as available and never hides a module on it.
    """
    client = g.client

    def _count():
        try:
            count = len(client.list_clusters() or [])
        except Exception:
            return {'count': None}
        try:
            settings = client.get_general_settings().get_raw()
            for cfg in ((settings.get('containerSettings') or {}).get('executionConfigs') or []):
                krc = cfg.get('kubernetesRuntimeConfig') if isinstance(cfg, dict) else None
                if isinstance(krc, dict) and krc.get('kubeConfigPath'):
                    count += 1
        except Exception:
            pass
        return {'count': count}

    return jsonify(_cache_get('k8s_cluster_count', _BACKEND_SETTINGS['cache_ttl_overview'], _count))


# ---------- K8S Insights ---------- #

_K8S_INSIGHTS_PROBE_NAMES = [
    'probe_pods', 'probe_nodes', 'probe_daemonsets', 'probe_replicasets',
    'probe_deployments_all', 'probe_deployments_kubesystem', 'probe_pdbs',
    'probe_events', 'probe_top_pods', 'probe_top_nodes',
    'probe_kubectl_version', 'probe_dss_general_settings',
    'probe_managed_cluster_dir', 'probe_eks_plugin_gpu_driver',
]


@bp.route('/api/k8s-insights/clusters')
def api_k8s_insights_clusters():
    """List clusters available for audit on the active host.

    "Available" means: registered in DSS (so orphan filesystem dirs from
    deleted clusters are dropped) AND currently has a kubeconfig file on the
    host (DSS writes that file when a cluster is "started" and removes it
    when stopped, so kubeconfig presence ≈ "turned on"). Additionally,
    kubeconfig files discovered on the host (containerized-exec configs with
    a custom kubeConfigPath, ~/.kube layouts) are appended as audit targets
    under synthetic `kubeconfig:<path>` ids.
    """
    client = g.client
    try:
        data = _k8s_insights_macro(client, operation='list-clusters')
    except MacroProjectMissing:
        raise
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:200]}'}), 502

    # Cross-reference with DSS's cluster registry to drop orphan FS dirs and
    # enrich with state/type/architecture for the UI.
    dss_by_id: Dict[str, Dict[str, Any]] = {}
    dss_error: Optional[str] = None
    try:
        for c in (client.list_clusters() or []):
            cid = c.get('id') if isinstance(c, dict) else None
            if cid:
                dss_by_id[cid] = c
    except Exception as exc:
        dss_error = f'{type(exc).__name__}: {str(exc)[:200]}'

    fs_clusters = data.get('clusters') or []
    fs_by_id = {fc.get('id'): fc for fc in fs_clusters if fc.get('id')}
    available: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []
    # Iterate by DSS-registry membership when possible (so we surface DSS-known
    # clusters that don't have a FS dir yet). Fall back to FS-only listing.
    candidate_ids = list(dss_by_id.keys()) if dss_by_id else list(fs_by_id.keys())
    for cid in candidate_ids:
        fc = fs_by_id.get(cid) or {'id': cid, 'hasKubeconfig': False}
        dss_meta = dss_by_id.get(cid) or {}
        state = dss_meta.get('state')
        is_available = bool(fc.get('hasKubeconfig')) or state == 'RUNNING'
        entry = {
            **fc,
            'id': cid,
            'state': state,
            'type': dss_meta.get('type'),
            'architecture': dss_meta.get('architecture'),
            'name': dss_meta.get('name') or cid,
        }
        if is_available:
            available.append(entry)
        else:
            diagnostics.append({
                'id': cid,
                'state': state,
                'type': dss_meta.get('type'),
                'hasKubeconfig': bool(fc.get('hasKubeconfig')),
                'baseDir': fc.get('baseDir'),
                'dirFiles': fc.get('dirFiles') or [],
            })

    # Kubeconfig-file targets: containerized-exec configs with a custom
    # kubeConfigPath plus ~/.kube discoveries, already deduped host-side by
    # the macro against DSS-managed cluster kubeconfigs. Existing files
    # become first-class picker entries under their synthetic id; declared
    # -but-missing paths stay in kubeconfigCandidates for the empty state.
    candidates = data.get('kubeconfigCandidates') or []
    for cand in candidates:
        if not cand.get('exists') or not cand.get('id'):
            continue
        available.append({
            'id': cand['id'],
            'name': cand.get('name') or cand.get('displayPath') or cand['id'],
            'type': 'KUBECONFIG',
            'state': None,
            'architecture': None,
            'hasKubeconfig': True,
            'kubeconfig': cand.get('path'),
            'source': cand.get('source'),
            'execConfig': cand.get('execConfig'),
            'displayPath': cand.get('displayPath'),
            'kubeCtlContext': cand.get('context'),
            'currentContext': cand.get('currentContext'),
            'server': cand.get('server'),
        })

    return jsonify({
        **data,
        'clusters': available,
        'unavailable': diagnostics,
        'totalDiscovered': len(fs_clusters),
        'dssRegistryError': dss_error,
    })


@bp.route('/api/k8s-insights/clusters/health')
def api_k8s_insights_clusters_health():
    """Parallel `kubectl version` probe across every DSS-known cluster.

    Used by the picker to render per-cluster health dots without forcing the
    user to run a full audit just to discover that an attachment is stale.
    """
    client = g.client
    try:
        data = _k8s_insights_macro(client, operation='cluster-health')
    except MacroProjectMissing:
        raise
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:200]}', 'clusters': []}), 502
    return jsonify(data)


@bp.route('/api/k8s-insights/pod-describe')
def api_k8s_insights_pod_describe():
    """`kubectl describe pod <name> -n <ns>` for one pod on the audited cluster.

    Returns the raw describe output as text/plain so the UI renders it verbatim
    in a <pre> via `fetchText`; failures surface as a non-2xx whose body carries
    the reason. The host-bound kubectl call runs inside the K8S Insights macro.
    """
    cluster_id = (request.args.get('clusterId') or '').strip()
    namespace = (request.args.get('ns') or '').strip()
    pod_name = (request.args.get('name') or '').strip()
    if not cluster_id or not namespace or not pod_name:
        return jsonify({'ok': False, 'error': 'clusterId, ns and name are required'}), 400
    client = g.client
    try:
        data = _k8s_insights_macro(
            client,
            operation='describe-pod',
            cluster_id=cluster_id,
            namespace=namespace,
            pod_name=pod_name,
        )
    except MacroProjectMissing:
        raise
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:200]}'}), 502
    if not data.get('ok'):
        return jsonify({'ok': False, 'error': data.get('error') or 'describe failed'}), 502
    return Response(data.get('text') or '', mimetype='text/plain; charset=utf-8')


@bp.route('/api/k8s-insights/stream')
def api_k8s_insights_stream():
    """SSE wrapper around the K8S Insights macro.

    The macro itself is synchronous (probes are run server-side in parallel,
    then rules evaluate), but we surface progress events as best we can:
      init  -> {clusterId, totalProbes}
      probe -> {name, ok, durationMs} (synthesized from result.probes)
      done  -> full payload
    """
    cluster_id = (request.args.get('clusterId') or '').strip()
    rules_filter = (request.args.get('rulesFilter') or '').strip()
    request_client = g.client
    request_host_id = getattr(g, 'host_id', 'local')

    def sse(event_name: str, payload: Dict[str, Any]) -> str:
        return "event: %s\ndata: %s\n\n" % (event_name, json.dumps(payload))

    def generate():
        events_q: "queue.Queue[Dict[str, Any]]" = queue.Queue()

        def worker() -> None:
            previous_host_id = getattr(_THREAD_LOCAL, 'host_id', None)
            _THREAD_LOCAL.host_id = request_host_id
            try:
                result = _k8s_insights_macro(
                    request_client,
                    operation='audit',
                    cluster_id=cluster_id,
                    rules_filter=rules_filter,
                )
                # Synthesize probe-progress events from the result for the UI.
                probes_summary = (result.get('probes') or {}) if isinstance(result, dict) else {}
                for name in _K8S_INSIGHTS_PROBE_NAMES:
                    p = probes_summary.get(name) or {}
                    events_q.put({'event': 'probe', 'payload': {
                        'name': name,
                        'ok': bool(p.get('ok')),
                        'error': p.get('error'),
                        'durationMs': int(p.get('durationMs') or 0),
                    }})
                events_q.put({'event': 'done', 'payload': result})
            except MacroProjectMissing:
                events_q.put({'event': 'error', 'payload': {'error': 'macro-project-missing'}})
            except Exception as exc:
                events_q.put({'event': 'error', 'payload': {'error': f'{type(exc).__name__}: {str(exc)[:500]}'}})
            finally:
                if previous_host_id is None:
                    try:
                        delattr(_THREAD_LOCAL, 'host_id')
                    except AttributeError:
                        pass
                else:
                    _THREAD_LOCAL.host_id = previous_host_id

        yield sse('init', {'clusterId': cluster_id, 'totalProbes': len(_K8S_INSIGHTS_PROBE_NAMES)})
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        while True:
            item = events_q.get()
            event_name = str(item.get('event') or 'progress')
            payload = item.get('payload') or {}
            yield sse(event_name, payload if isinstance(payload, dict) else {'payload': payload})
            if event_name in ('done', 'error'):
                break

    return _sse_response(generate)


