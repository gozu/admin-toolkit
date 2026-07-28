"""Build orphan-project fixtures on akaos so the Filesystem delete affordance
has something real to act on.

How an orphan actually arises: DSSProject.delete() defaults to
clear_managed_datasets=False / clear_output_managed_folders=False, so a project
deleted the ordinary way leaves its managed data on disk. Pass
clear_job_and_scenario_logs=False too and the job/scenario logs stay as well.
The project row is gone, the directories are not — which is exactly what DSS
reports as `orphanProjects`.

Each fixture project gets real bytes in several footprint areas so the tree
shows more than one location per orphan key:
    uploads/<KEY>            uploaded dataset files
    managed_folders/<KEY>    managed folder contents
    managed_datasets/<KEY>   the built output of a sync recipe
    jobs/<KEY>               the build's job log
    scenarios/<KEY>          a scenario run log

Usage:
    python make_orphan_fixtures.py            # create + orphan the fixtures
    python make_orphan_fixtures.py --purge    # delete any leftover live fixtures
"""
import io
import sys
import time

import dataikuapi
import urllib3

urllib3.disable_warnings()

# Sized so the three orphans are visibly different in the treemap.
FIXTURES = [
    {'key': 'ORPHAN_ALPHA', 'name': 'Orphan fixture Alpha', 'rows': 40000, 'folder_files': 4},
    {'key': 'ORPHAN_BETA', 'name': 'Orphan fixture Beta', 'rows': 12000, 'folder_files': 2},
    {'key': 'ORPHAN_GAMMA', 'name': 'Orphan fixture Gamma', 'rows': 2000, 'folder_files': 1},
]


def client():
    url = open('.dss-url').read().strip().rstrip('/')
    key = open('.dss-api-key').read().strip()
    c = dataikuapi.DSSClient(url, key)
    c._session.verify = False
    return c


def csv_bytes(rows):
    buf = io.StringIO()
    buf.write('event_id,customer,region,amount,ts\n')
    regions = ['emea', 'amer', 'apac', 'latam']
    for i in range(rows):
        buf.write('%d,customer_%05d,%s,%0.2f,2026-07-%02dT%02d:00:00\n'
                  % (i, i % 9973, regions[i % 4], (i % 997) / 3.0, (i % 28) + 1, i % 24))
    return buf.getvalue().encode('utf-8')


def drop(c, key):
    try:
        c.get_project(key).delete(clear_managed_datasets=True,
                                  clear_output_managed_folders=True,
                                  clear_job_and_scenario_logs=True)
        print('  purged live project %s' % key)
    except Exception as exc:
        if '404' not in str(exc) and 'not found' not in str(exc).lower():
            print('  purge %s: %s' % (key, str(exc)[:120]))


def build_fixture(c, spec):
    key, rows = spec['key'], spec['rows']
    print('\n== %s' % key)
    drop(c, key)
    project = c.create_project(key, spec['name'], 'admin',
                               description='Disposable fixture for orphan-project deletion.')

    # 1. uploaded dataset -> uploads/<KEY>
    upload = project.create_upload_dataset('raw_events')
    upload.uploaded_add_file(io.BytesIO(csv_bytes(rows)), 'events.csv')
    schema = upload.autodetect_settings()
    schema.save()
    print('  uploads/        %d rows' % rows)

    # 2. managed folder -> managed_folders/<KEY>
    folder = project.create_managed_folder('artifacts_%s' % key.lower())
    for i in range(spec['folder_files']):
        folder.put_file('report_%02d.csv' % i, io.BytesIO(csv_bytes(rows // 4)))
    print('  managed_folders/ %d files' % spec['folder_files'])

    # 3. sync recipe -> managed_datasets/<KEY>, and its build -> jobs/<KEY>
    builder = project.new_recipe('sync', 'compute_events_prepared')
    builder.with_input('raw_events')
    builder.with_new_output('events_prepared', 'filesystem_managed')
    builder.create()
    job = project.get_dataset('events_prepared').build(wait=True, no_fail=True)
    print('  managed_datasets/ build state: %s' % getattr(job, 'outcome', job))

    # 4. scenario run -> scenarios/<KEY>
    scenario = project.create_scenario('rebuild_events', 'step_based', definition={
        'params': {'steps': [{
            'id': 'build', 'type': 'build_flowitem', 'name': 'Build events_prepared',
            'params': {'builds': [{'type': 'DATASET', 'itemId': 'events_prepared'}]},
        }]},
        'triggers': [],
    })
    try:
        scenario.run_and_wait()
        print('  scenarios/       ran')
    except Exception as exc:
        print('  scenarios/       run failed (log still written): %s' % str(exc)[:100])
    return project


def orphan(project, key):
    """The whole point: delete the project but keep its data on disk."""
    project.delete(clear_managed_datasets=False,
                   clear_output_managed_folders=False,
                   clear_job_and_scenario_logs=False)
    print('  deleted %s, data left on disk -> orphan' % key)


def main():
    c = client()
    if '--purge' in sys.argv:
        for spec in FIXTURES:
            drop(c, spec['key'])
        return
    for spec in FIXTURES:
        project = build_fixture(c, spec)
        time.sleep(2)
        orphan(project, spec['key'])
    print('\ndone — %d orphan keys created' % len(FIXTURES))


if __name__ == '__main__':
    main()
