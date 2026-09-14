"""Prune one owned dangling image; refuse any other dangling image.

Host macro only. Imports a tiny tar without running a container or building
layers. Builder-cache pruning is outside this fixture's coverage.
"""
import io
import secrets
import subprocess
import tarfile


def fixtures(run):
    tk = run.tk
    image_id = None
    owned_ids = set()

    def docker(args, data=None):
        p = subprocess.run(['docker'] + args, input=data, capture_output=True, timeout=60)
        if p.returncode:
            raise RuntimeError('Docker fixture command failed: ' + p.stderr.decode(errors='replace')[:150])
        return p.stdout.decode().strip()

    def dangling():
        return set(docker(['images', '--filter', 'dangling=true', '--no-trunc', '--quiet']).splitlines())

    def images():
        return set(docker(['images', '--all', '--no-trunc', '--quiet']).splitlines())

    assert not dangling(), 'Existing dangling images require separate review'
    original_images = images()
    original_post = tk.post

    def guarded_post(path, **kwargs):
        if path == '/api/tools/docker/prune':
            assert kwargs.get('json', {}).get('mode') == 'image'
            assert dangling() == {image_id}, 'Refusing prune with non-fixture candidates'
        return original_post(path, **kwargs)

    def reset():
        nonlocal image_id
        if image_id and image_id in images():
            docker(['image', 'rm', image_id])
        assert not dangling(), 'Refusing concurrent dangling images'
        marker = secrets.token_hex(12).encode()
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            info = tarfile.TarInfo('adtk-comparison.txt')
            info.size = len(marker)
            archive.addfile(info, io.BytesIO(marker))
        image_id = docker(['import', '-'], stream.getvalue())
        assert image_id.startswith('sha256:')
        owned_ids.add(image_id)
        assert dangling() == {image_id}

    def verify(_):
        assert image_id not in images()
        assert images() == original_images, 'Pre-existing image inventory changed'
        return {'owned_dangling_image_removed': True, 'other_images_preserved': True,
                'builder_cache_pruned': False}

    tk.post = guarded_post  # Safety check only; every request still reaches the real backend.
    try:
        with run.gates(['docker-prune']):
            run.action('docker-prune', {'mode': 'image'}, reset, verify,
                       'One tiny imported dangling image; refuse other candidates; compare all pre-existing image IDs; builder mode untested')
    finally:
        tk.post = original_post
        errors = []
        for owned in owned_ids:
            try:
                if owned in images():
                    docker(['image', 'rm', owned])
            except Exception as exc:
                errors.append(type(exc).__name__)
        run.save({'capability': '_docker_fixture', 'status': 'cleanup_failed' if errors else 'deleted',
                  'errors': errors})
