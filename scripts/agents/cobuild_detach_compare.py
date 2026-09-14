"""Detach empty DSS cluster definitions; no cloud resource is created."""
import json
import secrets
from urllib.parse import urlsplit


def fixtures(run):
    c, tk = run.dss, run.tk
    name = 'atk-ab-' + secrets.token_hex(4)
    cluster = None
    backups = set()
    parts = urlsplit(tk.base_url).path.strip('/').split('/')
    folder = c.get_project(parts[parts.index('web-apps-backends') + 1]).get_managed_folder(
        tk.get('/api/managed-folders')['folders'][0]['id'])

    def exists():
        return cluster and any(row['id'] == cluster.cluster_id for row in c.list_clusters())

    def reset():
        nonlocal cluster
        if exists():
            cluster.delete()
        cluster = c.create_cluster(name, cluster_type='manual', cluster_architecture='KUBERNETES')
        tk.post('/api/cache/clear')

    def verify(result):
        assert not exists()
        filename = result['result']['backupFile']
        backups.add(filename)
        with folder.get_file(filename) as response:
            definition = response.json()
            assert definition['type'] == 'manual'
        return {'dss_attachment_deleted': True, 'readable_definition_backup': True,
                'cloud_resources_created': 0}

    try:
        with run.gates(['cluster-detach']):
            run.action('cluster-detach', lambda: {'clusterId': cluster.cluster_id}, reset, verify,
                       'Empty manual cluster attachment; verify absence and readable backup; no cloud resources')
    finally:
        errors = []
        try:
            if exists():
                cluster.delete()
            for path in backups:
                folder.delete_file(path)
        except Exception as exc:
            errors.append(type(exc).__name__)
        run.save({'capability': '_cluster_attachment_fixture',
                  'status': 'cleanup_failed' if errors else 'deleted', 'errors': errors})
