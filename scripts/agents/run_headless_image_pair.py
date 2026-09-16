#!/usr/bin/env python3
"""Delete two specifically approved old akaos base images, one per mode.

Requires a fresh operator-approved target file and the owned read-only observer
macro. Never creates fake ages, changes eligibility rules, or retries a write.
"""
import argparse
import json
import pathlib
import time

from cobuild_compare import ROOT
from headless_model_compare import HeadlessComparison
from run_cobuild_model_suite import private_write


def validate_targets(images, install_id):
    expected = {'dku-exec-base-' + install_id.lower(), 'dku-spark-base-' + install_id.lower()}
    if (len(images) != 2 or {i.get('repositoryName') for i in images} != expected
            or any(set(i) != {'repositoryName', 'imageDigest'} for i in images)
            or any(not isinstance(i.get('imageDigest'), str) or not i['imageDigest'].startswith('sha256:') for i in images)):
        raise ValueError('Approval must identify exactly the two akaos base-image digests')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--approved-targets', required=True, type=pathlib.Path)
    parser.add_argument('--observer-state', required=True, type=pathlib.Path)
    parser.add_argument('--output', required=True, type=pathlib.Path)
    args = parser.parse_args()
    if not args.execute:
        parser.error('--execute requires fresh approval of the exact two pre-existing digests')
    targets = json.loads(args.approved_targets.read_text())['images']
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    run = HeadlessComparison(ROOT / '.dss-url', ROOT / '.dss-api-key', args.output / 'results.json')
    from urllib.parse import urlsplit
    if urlsplit(run.dss.host).hostname != 'akaos.fe-aws.dkucloud-dev.com':
        raise ValueError('This fixture is akaos-only')
    validate_targets(targets, run.dss.get_instance_info().raw['installId'])
    observer = json.loads(args.observer_state.read_text())
    plugin = observer['plugin']
    if not plugin.startswith('atk-hl-checks-') or not plugin.endswith('-observe'):
        raise ValueError('Expected the owned read-only observer plugin')
    macro = run.project.get_macro('pyrunnable_' + plugin + '_checks')
    private_write(args.output / 'authorized-targets-private.json', {'images': targets})
    cutoff = run.tk.get('/api/tools/image-cleaner/release-date')['maxCutoffDate']
    state = {'index': -1, 'before': None}

    def inventory(repo):
        rid = macro.run(params={'operation': 'images'}, wait=False)
        deadline = time.monotonic() + 180
        while macro.get_status(rid).get('running'):
            if time.monotonic() > deadline:
                raise TimeoutError('Read-only registry observation timed out')
            time.sleep(3)
        result = macro.get_result(rid, as_type='json')
        rows = next(x['result']['imageDetails'] for x in result['images'] if x['repo'] == repo)
        return {i['imageDigest']: i for i in rows}

    def reset():
        state['index'] += 1
        target = targets[state['index']]
        before = inventory(target['repositoryName'])
        image = before[target['imageDigest']]
        if image.get('imageTags') != ['dss-15.0.0'] or str(image['imagePushedAt']) >= cutoff:
            raise ValueError('Approved image identity or eligibility changed')
        if not any(i.get('imageTags') == ['dss-15.0.1'] for i in before.values()):
            raise ValueError('Current base image is missing')
        state['before'] = before
        private_write(args.output / ('before-' + str(state['index']) + '-private.json'), before)

    def target():
        return {'provider': 'ecr', 'cutoff': cutoff, 'images': [targets[state['index']]]}

    def verify(_):
        selected = targets[state['index']]
        after = inventory(selected['repositoryName'])
        if selected['imageDigest'] in after or not set(state['before']) - {selected['imageDigest']} <= set(after):
            raise AssertionError('Selected deletion or preservation of other images was not verified')
        return {'selected_image_deleted': True, 'all_other_images_preserved': True}

    with run.gates(['image-delete']):
        run.action('image-delete', target, reset, verify,
                   'Two specifically authorized obsolete DSS 15.0.0 akaos base images; preserve 15.0.1 and every other digest')


if __name__ == '__main__':
    main()
