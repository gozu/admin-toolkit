"""Resource limits and recovery behavior for explicitly authorized live checks."""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts' / 'agents'))
from run_headless_remaining import Controller, validate_spec, package


def spec():
    return {'vpc': {'id': 'existing'}, 'managedNodeGroups': [{
        'name': 'one-small-node', 'instanceType': 't3.small', 'desiredCapacity': 1,
        'minSize': 0, 'maxSize': 1}]}


@pytest.mark.parametrize('change', [
    lambda s: s['managedNodeGroups'].append(copy.deepcopy(s['managedNodeGroups'][0])),
    lambda s: s['managedNodeGroups'][0].update(instanceType='t3.large'),
    lambda s: s['managedNodeGroups'][0].update(maxSize=2),
    lambda s: s['managedNodeGroups'][0].update(desiredCapacity=0),
    lambda s: s['managedNodeGroups'][0].update(launchTemplate={'id': 'foreign'}),
    lambda s: s.update(nodeGroups=[{}]),
    lambda s: s.update(fargateProfiles=[{}]),
    lambda s: s.update(karpenter={'version': 'x'}),
    lambda s: s.update(autoModeConfig={'enabled': True}),
    lambda s: s.update(vpc={}),
])
def test_resource_limits_reject_extra_capacity(change):
    value = spec()
    validate_spec(value)
    change(value)
    with pytest.raises(ValueError):
        validate_spec(value)


def test_lost_submission_is_journaled_and_never_deleted_or_retried(tmp_path):
    attempts = []
    def submit(**kwargs):
        attempts.append(kwargs)
        raise TimeoutError('reply lost')
    macro = SimpleNamespace(run=submit)
    dss = SimpleNamespace(get_project=lambda p: SimpleNamespace(get_macro=lambda m: macro))
    ctl = Controller(dss, tmp_path, {'plugin': 'owned'})
    with pytest.raises(TimeoutError):
        ctl.call('trial', capability='cluster-start', provider='headless')
    ctl.cleanup()  # Must not call any DSS mutation while the trial is unknown.
    saved = json.loads((tmp_path / 'state.json').read_text())
    assert saved['active'] == 0 and saved['runs'][0]['status'] == 'submitting'
    assert len(attempts) == 1


def test_temporary_package_has_no_historical_destructive_fixture_targets():
    import zipfile
    with zipfile.ZipFile(package('owned', {'cluster': 'one'})) as archive:
        assert json.loads(archive.read('python-lib/fixture.json')) == {'cluster': 'one'}
        assert 'python-lib/cobuild_images_compare.py' not in archive.namelist()
        assert 'python-lib/cobuild_python_compare.py' not in archive.namelist()
        assert b'headless_remaining_fixtures' in archive.read('python-runnables/checks/runnable.py')


def test_image_targets_cannot_expand_to_other_repositories():
    from run_headless_image_pair import validate_targets
    images = [{'repositoryName': 'dku-exec-base-owned', 'imageDigest': 'sha256:a'},
              {'repositoryName': 'dku-spark-base-owned', 'imageDigest': 'sha256:b'}]
    validate_targets(images, 'OWNED')
    images[1]['repositoryName'] = 'dku-spark-base-other'
    with pytest.raises(ValueError):
        validate_targets(images, 'OWNED')


def test_followup_report_requires_both_modes_and_drops_private_payloads(tmp_path):
    from render_headless_remaining import render
    trial = {'status': 'completed', 'verified': True, 'total_seconds': 5,
             'model_calls': [{'seconds': 4}], 'tools': [{'seconds': 1, 'arguments': 'SECRET'}],
             'execution_attempted': True, 'answer': 'SECRET', 'operation_result': {'token': 'SECRET'}}
    (tmp_path / 'trial.json').write_text(json.dumps({'trial_private': trial}))
    (tmp_path / 'state.json').write_text(json.dumps({'manifest': {'credential': 'SECRET'}, 'runs': [
        {'operation': 'trial', 'capability': 'python-run', 'provider': 'existing',
         'status': 'finished', 'result': 'trial.json'}]}))
    report = render(tmp_path)
    row = next(r for r in report['rows'] if r['capability'] == 'python-run')
    assert row['status'] == 'blocked' and row['trials']['legacy']['status'] == 'passed'
    assert 'SECRET' not in json.dumps(report)
    assert not report['cleanup']['cluster_definition_deleted']


def test_cleanup_reconciles_a_definition_that_was_already_removed(tmp_path):
    ctl = Controller.__new__(Controller)
    ctl.dss = SimpleNamespace(list_clusters=lambda: [])
    ctl.output, ctl.manifest = tmp_path, {'cluster': 'owned'}
    ctl.state = {'active': None, 'definition_created': True, 'plugin_installed': False}
    ctl.call = lambda operation: {'cloud_absent': True}
    ctl.cleanup()
    assert ctl.state['cluster_deleted'] is True
