"""Select an empty manual cluster on an owned project; no cloud provision."""
import secrets


def fixtures(run):
    suffix = secrets.token_hex(5)
    project = cluster = None
    key = 'ATKAB' + suffix.upper()
    try:
        project = run.dss.create_project(key, 'ADTK cluster selection fixture', 'admin')
        cluster = run.dss.create_cluster('atk-ab-' + suffix, cluster_type='manual',
                                         cluster_architecture='KUBERNETES')

        def reset():
            settings = project.get_settings()
            settings.get_raw().setdefault('settings', {}).pop('k8sCluster', None)
            settings.save()
            run.tk.post('/api/cache/clear')

        def verify(_):
            selected = project.get_settings().get_raw()['settings']['k8sCluster']
            assert selected == {'clusterMode': 'EXPLICIT_CLUSTER', 'clusterId': cluster.cluster_id}
            return {'owned_project_selected_owned_definition': True, 'cloud_resources_created': 0}

        with run.gates(['project-set-cluster']):
            run.action('project-set-cluster', {'projectKey': key, 'clusterId': cluster.cluster_id},
                       reset, verify, 'Select an empty manual cluster definition on an owned project; no workload or cloud resources')
    finally:
        errors = []
        if project:
            try:
                project.delete()
            except Exception as exc:
                errors.append('project: ' + type(exc).__name__)
        if cluster:
            try:
                cluster.delete()
            except Exception as exc:
                errors.append('cluster definition: ' + type(exc).__name__)
        run.save({'capability': '_project_cluster_fixture',
                  'status': 'cleanup_failed' if errors else 'deleted', 'errors': errors,
                  'project': key, 'cluster': cluster.cluster_id if cluster else None})
