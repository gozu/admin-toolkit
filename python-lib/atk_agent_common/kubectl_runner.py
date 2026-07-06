"""Shared kubectl runner — the DSS cluster API adapter used by both the
k8s-insights (read-only) and k8s-apply (gated mutation) macros.

DSS-attached EKS clusters never write a kubeconfig to disk; the only reliable
channel is `dataiku.api_client().get_cluster(id).run_kubectl(args)`. Note it
takes an ARG STRING (everything after the word `kubectl`) and has NO stdin —
manifest applies must go through a tempfile on the same host.
"""


def make_kubectl_runner(cluster_id):
    """Build `run(args: str) -> (rc, stdout, stderr)` for one DSS cluster."""
    import dataiku
    api = dataiku.api_client()
    cluster = api.get_cluster(cluster_id)

    def run(args):
        try:
            res = cluster.run_kubectl(args) or {}
        except Exception as exc:
            return -1, '', '%s: %s' % (type(exc).__name__, str(exc)[:300])
        rc = int(res.get('returnValue', -1))
        return rc, str(res.get('output') or ''), str(res.get('error') or '')

    return run
