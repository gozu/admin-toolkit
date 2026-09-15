#!/usr/bin/env python3
"""Package pinned upstream Headless source for DSS without host Git credentials.

Build-time access to Dataiku's private repository is required. The distribution
goes only into the plugin ZIP, not the public source repository. No local .env,
profiles, checkout metadata, tests or credentials enter the package.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile

COMMIT = '9d7f6cc29a9406f347708c8811b5689ab257c6c8'
REPOSITORY = 'https://github.com/dataiku/dataiku-headless.git'
ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    result = subprocess.run(['git', *args], cwd=ROOT, capture_output=True)
    if result.returncode:
        raise RuntimeError('Pinned Headless build failed; verify build-machine repository access.')
    return result.stdout


def artifact():
    cache = ROOT / '.git' / 'headless-build'
    if not cache.exists():
        git('init', '--bare', str(cache))
    git('--git-dir=' + str(cache), 'fetch', '--depth=1', REPOSITORY, COMMIT)
    if git('--git-dir=' + str(cache), 'rev-parse', 'FETCH_HEAD').decode().strip() != COMMIT:
        raise RuntimeError('Headless source revision differs from the reviewed pin.')
    source = io.BytesIO(git('--git-dir=' + str(cache), 'archive', '--format=zip', COMMIT))
    output = io.BytesIO()
    with zipfile.ZipFile(source) as upstream, zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as dest:
        for name in sorted(upstream.namelist()):
            if (name.startswith('dataiku_mcp/') and name.endswith('.py')) or name in {'LICENSE', 'NOTICE'}:
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                dest.writestr(info, upstream.read(name))
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plugin_zip', type=Path)
    args = parser.parse_args()
    value = artifact()
    provenance = {'repository': REPOSITORY, 'commit': COMMIT,
                  'sha256': hashlib.sha256(value).hexdigest(), 'license': 'Apache-2.0'}
    with zipfile.ZipFile(args.plugin_zip, 'a', zipfile.ZIP_DEFLATED) as package:
        if 'python-lib/dataiku_mcp/__init__.py' in package.namelist():
            raise RuntimeError('Headless was already packaged; rebuild a clean plugin ZIP.')
        # DSS ships python-lib to webapp/agent kernels separately from resource.
        # Put upstream modules on that same supported import path. No nested ZIP
        # or filesystem assumption in the deployed inference code is needed.
        with zipfile.ZipFile(io.BytesIO(value)) as upstream:
            for name in upstream.namelist():
                destination = ('python-lib/' + name if name.startswith('dataiku_mcp/')
                               else 'python-lib/dataiku-headless-' + name)
                package.writestr(destination, upstream.read(name))
        package.writestr('python-lib/headless-provenance.json', json.dumps(provenance, indent=2))
    print('Packaged actual Dataiku Headless MCP at %s (%d bytes).' % (COMMIT, len(value)))


if __name__ == '__main__':
    main()
