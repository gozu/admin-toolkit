"""Cluster-domain actuator actions (B-api: backend red routes on g.client).

cluster-detach removes the DSS attachment ONLY (`DSSCluster.delete()`) — the
cloud-side cluster is untouched. Its natural prey is the stale attachment
whose endpoint no longer resolves (k8s_health reachability errorClass 'dns').
The definition JSON is backed up first, so a wrongly-detached cluster can be
re-attached from the backup.
"""

from ..errors import ToolkitError
from . import _base


def _cluster_row(client, host, cluster_id):
    data = client.get('/api/k8s-insights/clusters', host=host)
    rows = data.get('clusters') or []
    row = next((c for c in rows if c.get('id') == cluster_id), None)
    if row is None:
        ids = sorted(str(c.get('id')) for c in rows)
        raise ToolkitError(
            'Cluster %r not found on host %r. Clusters: %s'
            % (cluster_id, host, ', '.join(ids[:20]) or '(none)'),
            remediation="Check the id with k8s_health or config_inspect domain='clusters'.")
    return row


def _reachability(client, host, cluster_id):
    """Best-effort reachability probe row for one cluster (None = unknown)."""
    try:
        health = client.get('/api/k8s-insights/clusters/health', host=host, heavy=True)
    except ToolkitError:
        return None
    return next((p for p in health.get('clusters') or [] if p.get('id') == cluster_id), None)


def _plan_cluster_detach(client, host, target, params):
    cluster_id = _base.require_str(target, 'clusterId', 'cluster-detach')
    row = _cluster_row(client, host, cluster_id)
    folder = _base.backup_folder(client, host)
    probe = _reachability(client, host, cluster_id)
    warnings = []
    if str(row.get('state') or '').upper() == 'RUNNING':
        warnings.append('Cluster %s is in state RUNNING — detaching removes it from DSS '
                        'while workloads may still target it.' % cluster_id)
    if probe is not None and probe.get('ok'):
        warnings.append('Cluster %s is REACHABLE — this does not look like a stale '
                        'attachment. Detach only if the admin confirms it is unused.'
                        % cluster_id)
    return {'clusterId': cluster_id}, {
        'summary': 'Back up the definition of cluster %s (%s) to %r, then DETACH it from DSS.'
                   % (cluster_id, row.get('name') or '?', folder['name']),
        'clusterName': row.get('name'),
        'state': row.get('state'),
        'architecture': row.get('architecture'),
        'reachability': ({'ok': probe.get('ok'), 'errorClass': probe.get('errorClass'),
                          'errorSummary': probe.get('errorSummary')} if probe else 'unknown'),
        'backupFolder': folder,
        'warnings': warnings or None,
        'note': 'Detach removes the DSS attachment only — the cloud-side cluster keeps '
                'running (and costing) until removed in the cloud console. Re-attach from '
                'the backup JSON if this was wrong.',
    }


def _exec_cluster_detach(client, host, target):
    folder = _base.backup_folder(client, host)
    return _base.post_backend_action(client, host, 'cluster-detach',
                                     {'clusterId': target['clusterId'],
                                      'folderId': folder['id']})


SPECS = [
    _base.spec('cluster-detach',
               'cluster-detach {clusterId}', 'red',
               _plan_cluster_detach, _exec_cluster_detach),
]
