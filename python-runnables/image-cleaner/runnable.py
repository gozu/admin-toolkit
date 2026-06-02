"""Target-host Image Cleaner macro.

Runs on the selected DSS host so registry credentials, IAM roles, and cloud
metadata are evaluated on the target host rather than the webapp host.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from importlib import import_module

import dataiku
from dataiku.runnables import Runnable


def _matches_dataiku(name):
    value = (name or '').lower()
    return 'dataiku' in value or 'dku' in value


def _parse_date(value):
    return datetime.strptime(str(value or ''), '%Y-%m-%d').date()


def _ensure_pkg(import_name, pip_name=None, required_attr=None):
    pip_name = pip_name or import_name

    def _valid_import():
        module = import_module(import_name)
        if required_attr and not hasattr(module, required_attr):
            raise AttributeError(
                "The import name '%s' does not expose %s; check for a shadowing module/package."
                % (import_name, required_attr)
            )
        return module

    try:
        return _valid_import()
    except ImportError:
        pass
    except AttributeError:
        # A broken/shadowed import may be earlier in sys.path. Installing into a
        # target dir placed first gives the real package a chance to win.
        sys.modules.pop(import_name, None)

    safe_tag = import_name.replace('.', '_')
    tmp_target = os.path.join(tempfile.gettempdir(), 'dku_%s' % safe_tag)
    dip_target = os.path.join(os.environ.get('DIP_HOME', '/tmp'), 'lib', 'python', safe_tag)
    attempts = [
        [sys.executable, '-m', 'pip', 'install', pip_name, '--quiet'],
        [sys.executable, '-m', 'pip', 'install', pip_name, '--quiet', '--user'],
        [sys.executable, '-m', 'pip', 'install', pip_name, '--quiet', '--break-system-packages'],
        [sys.executable, '-m', 'pip', 'install', pip_name, '--quiet', '--target', tmp_target],
        [sys.executable, '-m', 'pip', 'install', pip_name, '--quiet', '--target', dip_target],
    ]
    last_error = None
    for cmd in attempts:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                last_error = (result.stderr or result.stdout or '').strip()
                continue
            for target in (tmp_target, dip_target):
                if os.path.isdir(target) and target not in sys.path:
                    sys.path.insert(0, target)
            sys.modules.pop(import_name, None)
            try:
                return _valid_import()
            except Exception as exc:
                last_error = str(exc)
        except Exception as exc:
            last_error = str(exc)

    if last_error:
        raise RuntimeError(
            "%s is not installed or could not be loaded on the target DSS host: %s" % (import_name, last_error)
        )
    raise RuntimeError(
        "%s is not installed and auto-install failed. Install %s in the DSS Python environment."
        % (import_name, pip_name)
    )


def _import_boto3():
    boto3 = _ensure_pkg('boto3', 'boto3', 'client')
    if not hasattr(boto3, 'client'):
        raise RuntimeError(
            "The import name 'boto3' does not expose boto3.client on the target DSS host; check for a shadowing module/package."
        )
    return boto3


def _settings_registry_hint():
    try:
        settings = dataiku.api_client().get_general_settings().get_raw()
    except Exception:
        return None
    cs = settings.get('containerSettings') if isinstance(settings, dict) else None
    if not isinstance(cs, dict):
        return None
    configs = cs.get('executionConfigs') or []
    default_name = cs.get('defaultExecutionConfig')
    ordered = []
    for config in configs:
        if isinstance(config, dict) and config.get('name') == default_name:
            ordered.insert(0, config)
        elif isinstance(config, dict):
            ordered.append(config)
    generic = cs.get('executionConfigsGenericOverrides')
    if isinstance(generic, dict):
        ordered.append(generic)
    for config in ordered:
        url = str(config.get('repositoryURL') or '').strip()
        if not url:
            continue
        if re.match(r'^(?:https?://)?\d+\.dkr\.ecr\.([a-z0-9-]+)\.amazonaws\.com', url, re.I):
            return {'provider': 'ecr', 'registryUrl': url, 'source': 'dss-config'}
        if re.match(r'^(?:https?://)?([a-zA-Z0-9]+\.azurecr\.io)', url, re.I):
            return {'provider': 'acr', 'registryUrl': url, 'source': 'dss-config'}
        if re.match(r'^(?:https?://)?([a-z0-9-]+-docker\.pkg\.dev|(?:[a-z0-9-]+\.)?gcr\.io)', url, re.I):
            return {'provider': 'gar', 'registryUrl': url, 'source': 'dss-config'}
    return None


def _aws_region_from_imds(timeout=2.0):
    try:
        import urllib.request
        token_req = urllib.request.Request(
            'http://169.254.169.254/latest/api/token',
            headers={'X-aws-ec2-metadata-token-ttl-seconds': '30'},
            method='PUT',
        )
        token = urllib.request.urlopen(token_req, timeout=timeout).read().decode().strip()
        region_req = urllib.request.Request(
            'http://169.254.169.254/latest/meta-data/placement/region',
            headers={'X-aws-ec2-metadata-token': token},
        )
        return urllib.request.urlopen(region_req, timeout=timeout).read().decode().strip() or None
    except Exception:
        return None


def _ecr_region():
    for key in ('AWS_DEFAULT_REGION', 'AWS_REGION'):
        value = os.environ.get(key, '').strip()
        if value:
            return value
    hint = _settings_registry_hint()
    if hint and hint.get('provider') == 'ecr':
        match = re.match(r'.*\.dkr\.ecr\.([a-z0-9-]+)\.amazonaws\.com', hint.get('registryUrl') or '', re.I)
        if match:
            return match.group(1)
    return _aws_region_from_imds()


def _detect_provider():
    hint = _settings_registry_hint()
    if hint:
        return {'ok': True, **hint}
    if _ecr_region():
        return {'ok': True, 'provider': 'ecr', 'registryUrl': None, 'source': 'imds'}
    return {'ok': True, 'provider': None, 'registryUrl': None, 'source': 'none'}


def _ecr_client():
    region = _ecr_region()
    if not region:
        raise RuntimeError(
            "Cannot detect AWS region on the target DSS host. Set AWS_DEFAULT_REGION or configure an ECR repository URL."
        )
    return _import_boto3().client('ecr', region_name=region)


def _scan_ecr(cutoff):
    client = _ecr_client()
    repos = []
    paginator = client.get_paginator('describe_repositories')
    for page in paginator.paginate():
        for repo in page.get('repositories', []):
            name = repo.get('repositoryName') or ''
            if _matches_dataiku(name):
                repos.append(name)
    repos.sort()

    repo_rows = []
    for repo in repos:
        images = []
        try:
            image_paginator = client.get_paginator('describe_images')
            for page in image_paginator.paginate(repositoryName=repo):
                for img in page.get('imageDetails', []):
                    pushed = img.get('imagePushedAt')
                    if pushed is None:
                        continue
                    pushed_date = pushed.date() if hasattr(pushed, 'date') else datetime.fromisoformat(str(pushed)).date()
                    images.append({
                        'digest': img.get('imageDigest', ''),
                        'tags': img.get('imageTags') or [],
                        'pushedAt': pushed.isoformat() if hasattr(pushed, 'isoformat') else str(pushed),
                        'deletable': pushed_date < cutoff,
                    })
        except Exception as exc:
            repo_rows.append({'name': repo, 'images': [], 'error': str(exc)})
            continue
        images.sort(key=lambda item: item.get('pushedAt') or '')
        repo_rows.append({'name': repo, 'images': images})
    return {'ok': True, 'repos': repo_rows}


def _delete_ecr(cutoff, images, dry_run):
    client = _ecr_client()
    preflight_errors = []
    by_repo = {}
    for img in images:
        repo = img.get('repositoryName') or img.get('repo') or ''
        digest = img.get('imageDigest') or img.get('digest') or ''
        if not repo or not digest:
            preflight_errors.append({'repo': repo, 'digest': digest, 'reason': 'missing repo or digest'})
            continue
        try:
            resp = client.describe_images(repositoryName=repo, imageIds=[{'imageDigest': digest}])
            details = resp.get('imageDetails') or []
            if not details:
                preflight_errors.append({'repo': repo, 'digest': digest, 'reason': 'image not found'})
                continue
            pushed = details[0].get('imagePushedAt')
            pushed_date = pushed.date() if hasattr(pushed, 'date') else datetime.fromisoformat(str(pushed)).date()
            if pushed_date >= cutoff:
                preflight_errors.append({
                    'repo': repo,
                    'digest': digest,
                    'reason': 'pushed %s is not before cutoff %s' % (pushed_date.isoformat(), cutoff.isoformat()),
                })
                continue
        except Exception as exc:
            preflight_errors.append({'repo': repo, 'digest': digest, 'reason': str(exc)})
            continue
        by_repo.setdefault(repo, []).append(digest)

    if preflight_errors:
        return {'ok': False, 'error': 'Preflight failed - no images were deleted', 'preflight_errors': preflight_errors}

    deleted = []
    failed = []
    for repo, digests in by_repo.items():
        if dry_run:
            deleted.extend({'repo': repo, 'digest': digest, 'dryRun': True} for digest in digests)
            continue
        try:
            resp = client.batch_delete_image(repositoryName=repo, imageIds=[{'imageDigest': digest} for digest in digests])
            for item in resp.get('imageIds') or []:
                deleted.append({'repo': repo, 'digest': item.get('imageDigest', '')})
            for item in resp.get('failures') or []:
                failed.append({
                    'repo': repo,
                    'digest': (item.get('imageId') or {}).get('imageDigest', ''),
                    'reason': item.get('failureReason', ''),
                })
        except Exception as exc:
            failed.extend({'repo': repo, 'digest': digest, 'reason': str(exc)} for digest in digests)
    return {'ok': True, 'dryRun': bool(dry_run), 'deleted': deleted, 'failed': failed}


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        operation = str(self.config.get('operation') or '').strip().lower()
        provider = str(self.config.get('provider') or 'ecr').strip().lower()
        try:
            if operation == 'detect-provider':
                return json.dumps(_detect_provider())
            if provider != 'ecr':
                return json.dumps({
                    'ok': False,
                    'error': 'Remote image-cleaner macro currently supports ECR only for scan/delete.',
                    'provider': provider,
                })
            if operation == 'scan':
                cutoff = _parse_date(self.config.get('cutoff'))
                return json.dumps(_scan_ecr(cutoff))
            if operation == 'delete':
                cutoff = _parse_date(self.config.get('cutoff'))
                images = json.loads(self.config.get('images_json') or '[]')
                dry_run = self.config.get('dryRun')
                if isinstance(dry_run, str):
                    dry_run = dry_run.strip().lower() in ('true', '1', 'yes')
                return json.dumps(_delete_ecr(cutoff, images, bool(dry_run)))
            return json.dumps({'ok': False, 'error': 'Unknown operation: %s' % operation})
        except Exception as exc:
            return json.dumps({'ok': False, 'error': '%s: %s' % (type(exc).__name__, str(exc))})
