"""Cluster-domain actuator actions (B-api: backend red routes on g.client).

cluster-detach removes the DSS attachment ONLY (`DSSCluster.delete()`) — the
cloud-side cluster is untouched. Its natural prey is the stale attachment
whose endpoint no longer resolves (k8s_health reachability errorClass 'dns').
The definition JSON is backed up first, so a wrongly-detached cluster can be
re-attached from the backup. Stale attachments accumulate in fleets (a whole
k8s_health list of DNS-dead 'no such host' clusters), so cluster-detach is
batchable — one backup-first plan/token covers every target, like
connection-delete/project-delete.
"""

from ..errors import ToolkitError
from . import _base


def _cluster_row(client, host, cluster_id):
    data = client.get('/api/k8s-insights/clusters', host=host)
    # `unavailable` (no kubeconfig, not RUNNING) is where the stale attachments
    # live — exactly the detach candidates. Search both lists.
    rows = (data.get('clusters') or []) + (data.get('unavailable') or [])
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


def _plan_cluster_stop(client, host, target, params):
    cluster_id = _base.require_str(target, 'clusterId', 'cluster-stop')
    terminate = bool((target or {}).get('terminate'))
    row = _cluster_row(client, host, cluster_id)
    if str(row.get('type') or '') == 'manual':
        raise ToolkitError('Cluster %s is a manual attachment — DSS cannot stop it. '
                           'Use cluster-detach to remove the attachment instead.' % cluster_id)
    warnings = []
    if terminate:
        warnings.append('terminate=true DESTROYS the cloud-side cluster — this is '
                        'IRREVERSIBLE (state cannot be restored from any backup).')
    canonical = {'clusterId': cluster_id, 'terminate': terminate}
    return canonical, {
        'summary': 'STOP managed cluster %s (%s)%s.' % (
            cluster_id, row.get('name') or '?',
            ' and TERMINATE the cloud-side resources' if terminate else ''),
        'clusterName': row.get('name'),
        'state': row.get('state'),
        'irreversible': terminate or None,
        'warnings': warnings or None,
        'note': ('Without terminate, the cluster can be started again with cluster-start.'
                 if not terminate else
                 'Restore is NOT possible — the cloud resources are destroyed.'),
    }


def _exec_cluster_stop(client, host, target):
    return _base.post_backend_action(client, host, 'cluster-stop',
                                     {'clusterId': target['clusterId'],
                                      'terminate': bool(target.get('terminate'))})


def _plan_cluster_start(client, host, target, params):
    cluster_id = _base.require_str(target, 'clusterId', 'cluster-start')
    row = _cluster_row(client, host, cluster_id)
    if str(row.get('type') or '') == 'manual':
        raise ToolkitError('Cluster %s is a manual attachment — DSS cannot start it.'
                           % cluster_id)
    return {'clusterId': cluster_id}, {
        'summary': 'START managed cluster %s (%s).' % (cluster_id, row.get('name') or '?'),
        'clusterName': row.get('name'),
        'state': row.get('state'),
        'note': 'Starting a managed cluster provisions cloud resources — cost resumes.',
    }


def _exec_cluster_start(client, host, target):
    return _base.post_backend_action(client, host, 'cluster-start',
                                     {'clusterId': target['clusterId']})


def _plan_cluster_pods_cleanup(client, host, target, params):
    cluster_id = _base.require_str(target, 'clusterId', 'cluster-pods-cleanup')
    row = _cluster_row(client, host, cluster_id)
    probe = _reachability(client, host, cluster_id)
    warnings = []
    if probe is not None and not probe.get('ok'):
        warnings.append('Cluster %s is currently UNREACHABLE (%s) — the cleanup will '
                        'likely fail until connectivity is restored.'
                        % (cluster_id, probe.get('errorClass')))
    return {'clusterId': cluster_id}, {
        'summary': 'Delete FINISHED pods and jobs on cluster %s (%s) — running workloads '
                   'are untouched.' % (cluster_id, row.get('name') or '?'),
        'clusterName': row.get('name'),
        'state': row.get('state'),
        'warnings': warnings or None,
        'note': 'Only completed/failed pod and job objects are removed '
                '(DSSCluster.delete_finished_pods/delete_finished_jobs).',
    }


def _exec_cluster_pods_cleanup(client, host, target):
    return _base.post_backend_action(client, host, 'cluster-pods-cleanup',
                                     {'clusterId': target['clusterId']})


SPECS = [
    _base.spec('cluster-detach',
               'cluster-detach {clusterId}', 'red',
               _plan_cluster_detach, _exec_cluster_detach, batchable=True),
    _base.spec('cluster-stop',
               'cluster-stop {clusterId, terminate?} (managed clusters only; '
               'terminate=true destroys cloud resources — irreversible)', 'red',
               _plan_cluster_stop, _exec_cluster_stop),
    _base.spec('cluster-start',
               'cluster-start {clusterId} (managed clusters only)', 'amber',
               _plan_cluster_start, _exec_cluster_start),
    _base.spec('cluster-pods-cleanup',
               'cluster-pods-cleanup {clusterId} (finished pods/jobs only)', 'green',
               _plan_cluster_pods_cleanup, _exec_cluster_pods_cleanup),
]
