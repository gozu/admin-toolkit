"""Delete exactly two explicitly authorized old images, one through each path.

Runs as a macro on akaos. The caller supplies the selected repo/digest records;
there is no automatic execution retry or expansion to additional images.
"""
import json
import subprocess
import time


def fixtures(run):
    from atk_agent_common import actuator, capability_routing as routing
    targets = run.image_targets
    assert len(targets) == 2
    assert len({(t['repo'], t['digest']) for t in targets}) == 2
    timings = {}
    verified = False
    original_do = run.tk._do

    def observed_request(method, path, **kwargs):
        response = original_do(method, path, **kwargs)
        if response.status_code >= 400:
            run.save({'capability': '_image_http', 'status': 'diagnostic',
                      'method': method, 'path': path, 'http_status': response.status_code,
                      'detail': response.text[:400]})
        return response

    run.tk._do = observed_request

    def inventory(repo):
        proc = subprocess.run(['aws', '--region', 'us-west-2', 'ecr', 'describe-images',
                               '--repository-name', repo, '--output', 'json'],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, 'Registry inventory failed'
        return {x['imageDigest']: x for x in json.loads(proc.stdout)['imageDetails']}

    with run.gates(['image-delete']):
        for provider, image in zip(('existing', 'cobuild'), targets):
            run.save({'capability': '_image_phase', 'status': 'starting', 'provider': provider})
            before = inventory(image['repo'])
            selected = before[image['digest']]
            assert selected.get('imageTags') == ['dss-14.7.0']
            assert image['pushedAt'] < '2026-08-13'
            target = {'provider': 'ecr', 'cutoff': '2026-08-13',
                      'images': [{'repositoryName': image['repo'],
                                  'imageDigest': image['digest']}]}
            started = time.monotonic()
            with routing.override(provider):
                plan = actuator.plan_admin_action(run.tk, action='image-delete', target=target)
                assert plan.get('confirm_token'), 'Image deletion plan refused'
                dry_run = plan['plan']['dryRun']
                assert dry_run.get('dryRun') is True and not dry_run.get('failed')
                assert not dry_run.get('error') and len(dry_run.get('deleted', [])) == 1
                preview = dry_run['deleted'][0]
                assert (preview['repo'], preview['digest']) == (image['repo'], image['digest'])
                result = actuator.execute_admin_action(
                    run.tk, action='image-delete', target=plan['canonicalTarget'],
                    confirm_flag=True, confirm_token=plan['confirm_token'],
                    agent_name='atk-cobuild-comparison')
            timings[provider] = round(time.monotonic() - started, 3)
            assert result.get('status') == 'ok' and not result.get('result', {}).get('error')
            assert not result.get('result', {}).get('failed')
            after = inventory(image['repo'])
            assert image['digest'] not in after
            assert set(before) - {image['digest']} <= set(after)
            run.save({'capability': '_image_deletion', 'provider': provider, 'status': 'verified',
                      'repo': image['repo'], 'digest': image['digest'],
                      'other_images_preserved': True, 'seconds': timings[provider],
                      'execution_result': result,
                      'plan_acknowledged': plan.get('executionRoute', {}).get('resultAcknowledged')})
            if provider == 'cobuild':
                verified = (plan.get('executionRoute', {}).get('resultAcknowledged') is True
                            and result.get('executionRoute', {}).get('resultAcknowledged') is True)
    run.save({'capability': 'image-delete', 'status': 'same' if verified else 'needs_review',
              'scope': 'Two authorized oldest akaos base images tagged DSS 14.7.0; one deletion per path; other repository images preserved',
              'attempt': 1, 'existing_seconds': timings['existing'], 'cobuild_seconds': timings['cobuild'],
              'request_and_result_verified': verified, 'transport': 'deployed HTTP bridge'})
