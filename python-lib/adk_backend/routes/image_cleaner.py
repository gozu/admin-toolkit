"""Image Cleaner routes — multi-cloud container-registry scan/delete (ECR, ACR, GAR)."""
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import TimeoutError as FuturesTimeoutError, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import dataiku
from flask import Blueprint, g, jsonify, request

from adk_backend.caching import _cache_get
from adk_backend.clients import ThreadPoolExecutor, _safe_request_host_id
from adk_backend.macros import _host_metrics_macro, _image_cleaner_macro
from adk_backend.settings import _BACKEND_SETTINGS
from adk_backend.sysinfo import _dip_home, _safe_read_json
from adk_backend.utils import _sse_response, advanced

bp = Blueprint('image_cleaner', __name__)
_LOGGER = logging.getLogger(__name__)


# ── Image Cleaner (multi-cloud: ECR, ACR, GAR) ─────────────────────────

_IMAGE_CLEANER_CLIENTS: Dict[Tuple[str, str], Any] = {}
_IMAGE_CLEANER_CLIENTS_LOCK = threading.Lock()


def _ensure_pkg(import_name: str, pip_name: Optional[str] = None, log_tag: str = 'image-cleaner'):
    """Import a package, auto-installing if necessary. 5-attempt strategy (same as legacy _ensure_boto3)."""
    pip_name = pip_name or import_name
    try:
        return __import__(import_name)
    except ImportError:
        pass

    safe_tag = import_name.replace('.', '_')
    _tmp_target = os.path.join(tempfile.gettempdir(), 'dku_%s' % safe_tag)
    _datadir_target = os.path.join(os.environ.get('DIP_HOME', '/tmp'), 'lib', 'python', safe_tag)
    install_attempts = [
        ('pip install (default)', [sys.executable, '-m', 'pip', 'install', pip_name, '--quiet']),
        ('pip install --user', [sys.executable, '-m', 'pip', 'install', pip_name, '--quiet', '--user']),
        ('pip install --break-system-packages', [sys.executable, '-m', 'pip', 'install', pip_name, '--quiet', '--break-system-packages']),
        ('pip install --target %s' % _tmp_target, [sys.executable, '-m', 'pip', 'install', pip_name, '--quiet', '--target', _tmp_target]),
        ('pip install --target %s' % _datadir_target, [sys.executable, '-m', 'pip', 'install', pip_name, '--quiet', '--target', _datadir_target]),
    ]
    for label, cmd in install_attempts:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                continue
            for tgt in (_tmp_target, _datadir_target):
                if tgt not in sys.path and os.path.isdir(tgt):
                    sys.path.insert(0, tgt)
            try:
                mod = __import__(import_name)
                _LOGGER.info("[%s] %s installed via %s", log_tag, import_name, label)
                return mod
            except ImportError:
                pass
        except Exception:
            pass

    raise ImportError("%s is not installed and auto-install failed. Install %s in the DSS Python environment."
                      % (import_name, pip_name))


def _ensure_boto3():
    return _ensure_pkg('boto3', 'boto3', 'image-cleaner')


def _parse_version_tuple(v):
    """Parse '14.5.1' or '14.5.1-beta1' into (major, minor, patch); return None if unparseable."""
    m = re.match(r'^(\d+)\.(\d+)\.(\d+)', v)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _pick_closest_dss_version(requested, available):
    """Pick the best download-page entry for `requested` from `available` (list of (version, date)).

    Order of preference:
      1. Exact match.
      2. Base version with any pre-release suffix stripped (e.g. 14.5.1-beta1 → 14.5.1).
      3. Highest stable (non-prerelease) version with (major, minor, patch) <= requested.
    Returns (version, date) or None.
    """
    for v, d in available:
        if v == requested:
            return v, d
    base = re.split(r'[-+]', requested, 1)[0]
    if base != requested:
        for v, d in available:
            if v == base:
                return v, d
    req_key = _parse_version_tuple(requested)
    if req_key is None:
        return None
    best = None
    for v, d in available:
        if re.search(r'[-+]', v):
            continue
        vk = _parse_version_tuple(v)
        if vk is None or vk > req_key:
            continue
        if best is None or vk > best[0]:
            best = (vk, v, d)
    return (best[1], best[2]) if best else None


def _image_cleaner_release_info():
    """Get DSS version and its release date from downloads.dataiku.com.

    Falls back to the closest stable version if the exact (e.g. beta) version is not published.
    """
    from datetime import timedelta
    import urllib.request

    t0 = time.time()
    if _safe_request_host_id() != 'local':
        metrics = _cache_get('host_metrics', _BACKEND_SETTINGS['cache_ttl_overview'], lambda: _host_metrics_macro(g.client))
        version_info = metrics.get('version') if isinstance(metrics, dict) else {}
    else:
        dip_home = _dip_home()
        version_info = _safe_read_json(os.path.join(dip_home, 'dss-version.json')) or {}
    version = version_info.get('product_version') or version_info.get('version') or version_info.get('dssVersion')
    if not version:
        raise ValueError("Cannot determine DSS version from dss-version.json")
    t1 = time.time()
    _LOGGER.info("[perf:image-cleaner] version_read=%.0fms version=%s", (t1 - t0) * 1000, version)

    url = 'https://downloads.dataiku.com/public/dss/'
    req = urllib.request.Request(url, headers={'User-Agent': 'AdminToolkit/1.0'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        html = resp.read().decode('utf-8', errors='replace')
    t2 = time.time()
    _LOGGER.info("[perf:image-cleaner] http_fetch=%.0fms url=%s bytes=%d", (t2 - t1) * 1000, url, len(html))

    available = re.findall(
        r'<a href="([^"/]+)/">\1/</a></td>\s*<td[^>]*>\s*(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}',
        html,
    )
    picked = _pick_closest_dss_version(version, available)
    t3 = time.time()
    _LOGGER.info(
        "[perf:image-cleaner] regex_parse=%.0fms versions_available=%d picked=%s",
        (t3 - t2) * 1000, len(available), picked[0] if picked else None,
    )
    if not picked:
        raise ValueError(
            "DSS version %s not found on downloads.dataiku.com and no fallback version available" % version
        )

    matched_version, matched_date = picked
    fallback_used = matched_version != version
    if fallback_used:
        _LOGGER.warning(
            "[image-cleaner] DSS version %s not published; falling back to closest available: %s (%s)",
            version, matched_version, matched_date,
        )

    release_date = datetime.strptime(matched_date, '%Y-%m-%d').date()
    max_cutoff = release_date - timedelta(days=2)

    return {
        'version': version,
        'matchedVersion': matched_version,
        'releaseDate': matched_date,
        'maxCutoffDate': max_cutoff.isoformat(),
        'fallbackUsed': fallback_used,
    }


def _image_cleaner_validate_cutoff(cutoff_str):
    """Validate cutoff and enforce server-side max. Returns (cutoff_date, release_info)."""
    try:
        cutoff = datetime.strptime(cutoff_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        raise ValueError("Invalid cutoff date format, expected YYYY-MM-DD")
    info = _image_cleaner_release_info()
    max_cutoff = datetime.strptime(info['maxCutoffDate'], '%Y-%m-%d').date()
    if cutoff > max_cutoff:
        raise ValueError("Cutoff %s exceeds maximum allowed %s" % (cutoff_str, info['maxCutoffDate']))
    return cutoff, info


def _matches_dataiku(name: str) -> bool:
    n = (name or '').lower()
    return 'dataiku' in n or 'dku' in n


# ── RegistryAdapter interface ──

class RegistryAdapter:
    """Base. Subclasses implement list_repositories / list_images / head_image / delete_images.

    list_images returns [{digest, tags, pushedAt (isoformat)}]
    head_image returns {pushedAt: date} or None if missing
    delete_images returns (deleted, failed) — lists of {repo, digest[, reason]}
    """
    provider = ''

    def list_repositories(self) -> List[str]:
        raise NotImplementedError

    def list_images(self, repo: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def head_image(self, repo: str, digest: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def delete_images(self, repo: str, digests: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        raise NotImplementedError


# ── EcrAdapter ──

class EcrAdapter(RegistryAdapter):
    provider = 'ecr'

    def __init__(self, region: str):
        if not region:
            raise ValueError("Cannot detect AWS region. Set AWS_DEFAULT_REGION environment variable "
                             "or configure a region in ~/.aws/config on the DSS server.")
        boto3 = _ensure_boto3()
        self._client = boto3.client('ecr', region_name=region)
        self._region = region
        _LOGGER.info("[image-cleaner] ecr client created region=%s", region)

    def list_repositories(self) -> List[str]:
        out: List[str] = []
        pag = self._client.get_paginator('describe_repositories')
        for page in pag.paginate():
            for r in page.get('repositories', []):
                name = r['repositoryName']
                if _matches_dataiku(name):
                    out.append(name)
        out.sort()
        return out

    def list_images(self, repo: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        pag = self._client.get_paginator('describe_images')
        for page in pag.paginate(repositoryName=repo):
            for img in page.get('imageDetails', []):
                pushed = img.get('imagePushedAt')
                if pushed is None:
                    continue
                out.append({
                    'digest': img.get('imageDigest', ''),
                    'tags': img.get('imageTags', []),
                    'pushedAt': pushed.isoformat() if hasattr(pushed, 'isoformat') else str(pushed),
                })
        return out

    def head_image(self, repo: str, digest: str) -> Optional[Dict[str, Any]]:
        try:
            resp = self._client.describe_images(repositoryName=repo, imageIds=[{'imageDigest': digest}])
        except Exception:
            return None
        details = resp.get('imageDetails', [])
        if not details:
            return None
        pushed = details[0].get('imagePushedAt')
        if pushed is None:
            return None
        pushed_date = pushed.date() if hasattr(pushed, 'date') else datetime.fromisoformat(str(pushed)).date()
        return {'pushedAt': pushed_date}

    def delete_images(self, repo: str, digests: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        deleted: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        try:
            resp = self._client.batch_delete_image(
                repositoryName=repo,
                imageIds=[{'imageDigest': d} for d in digests],
            )
            for d in resp.get('imageIds', []):
                deleted.append({'repo': repo, 'digest': d.get('imageDigest', '')})
            for f in resp.get('failures', []):
                failed.append({
                    'repo': repo,
                    'digest': f.get('imageId', {}).get('imageDigest', ''),
                    'reason': f.get('failureReason', ''),
                })
        except Exception as e:
            for d in digests:
                failed.append({'repo': repo, 'digest': d, 'reason': str(e)})
        return deleted, failed


# ── AcrAdapter (raw REST) ──
# NOTE: Response field names (lastUpdateTime, manifests[]) taken from Azure docs;
# needs one live run against a real ACR to confirm — see verification block in plan.

class AcrAdapter(RegistryAdapter):
    provider = 'acr'

    def __init__(self, registry_host: str):
        if not registry_host:
            raise ValueError("Cannot detect ACR registry host from DSS containerSettings. "
                             "Configure an executionConfig with a *.azurecr.io repositoryURL.")
        _ensure_pkg('azure.identity', 'azure-identity', 'image-cleaner')
        from azure.identity import DefaultAzureCredential
        cred = DefaultAzureCredential()
        self._host = registry_host.rstrip('/')
        self._registry_url = 'https://' + self._host
        aad = cred.get_token('https://management.azure.com/.default')
        self._aad_token = aad.token
        _LOGGER.info("[image-cleaner] acr adapter created host=%s", self._host)

    def _get_access_token(self, scope: str) -> str:
        import urllib.request, urllib.parse
        data = urllib.parse.urlencode({
            'grant_type': 'access_token',
            'service': self._host,
            'access_token': self._aad_token,
        }).encode()
        req = urllib.request.Request(
            self._registry_url + '/oauth2/exchange',
            data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            refresh_token = json.loads(resp.read().decode())['refresh_token']

        data2 = urllib.parse.urlencode({
            'grant_type': 'refresh_token',
            'service': self._host,
            'scope': scope,
            'refresh_token': refresh_token,
        }).encode()
        req2 = urllib.request.Request(
            self._registry_url + '/oauth2/token',
            data=data2,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            method='POST',
        )
        with urllib.request.urlopen(req2, timeout=10) as resp2:
            return json.loads(resp2.read().decode())['access_token']

    def _paginated_get(self, path: str, scope: str) -> List[Dict[str, Any]]:
        import urllib.request
        tok = self._get_access_token(scope)
        bodies: List[Dict[str, Any]] = []
        while True:
            req = urllib.request.Request(
                self._registry_url + path,
                headers={'Authorization': 'Bearer ' + tok},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode())
                link_hdr = resp.headers.get('Link', '')
            bodies.append(body)
            if not link_hdr:
                break
            m = re.search(r'<([^>]+)>;\s*rel="next"', link_hdr)
            if not m:
                break
            path = m.group(1)
        return bodies

    def list_repositories(self) -> List[str]:
        out: List[str] = []
        for body in self._paginated_get('/acr/v1/_catalog', 'registry:catalog:*'):
            for name in body.get('repositories', []):
                if _matches_dataiku(name):
                    out.append(name)
        out.sort()
        return out

    def list_images(self, repo: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for body in self._paginated_get('/acr/v1/%s/_manifests' % repo, 'repository:%s:metadata_read' % repo):
            for m in body.get('manifests', []):
                pushed = m.get('lastUpdateTime') or m.get('createdTime')
                if not pushed:
                    continue
                out.append({
                    'digest': m.get('digest', ''),
                    'tags': list(m.get('tags', []) or []),
                    'pushedAt': str(pushed),
                })
        return out

    def head_image(self, repo: str, digest: str) -> Optional[Dict[str, Any]]:
        import urllib.request
        try:
            tok = self._get_access_token('repository:%s:metadata_read' % repo)
            req = urllib.request.Request(
                self._registry_url + '/acr/v1/%s/_manifests/%s' % (repo, digest),
                headers={'Authorization': 'Bearer ' + tok},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read().decode())
        except Exception:
            return None
        pushed = body.get('lastUpdateTime') or body.get('createdTime')
        if not pushed:
            return None
        try:
            pushed_date = datetime.fromisoformat(str(pushed).replace('Z', '+00:00')).date()
        except Exception:
            return None
        return {'pushedAt': pushed_date}

    def delete_images(self, repo: str, digests: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        import urllib.request
        tok = self._get_access_token('repository:%s:delete' % repo)
        deleted: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        for d in digests:
            try:
                req = urllib.request.Request(
                    self._registry_url + '/v2/%s/manifests/%s' % (repo, d),
                    headers={'Authorization': 'Bearer ' + tok},
                    method='DELETE',
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if 200 <= resp.status < 300:
                        deleted.append({'repo': repo, 'digest': d})
                    else:
                        failed.append({'repo': repo, 'digest': d, 'reason': 'HTTP %s' % resp.status})
            except Exception as e:
                failed.append({'repo': repo, 'digest': d, 'reason': str(e)})
        return deleted, failed


# ── GarAdapter ──
# NOTE: DeleteVersion path construction from docs; needs one live run to confirm.

class GarAdapter(RegistryAdapter):
    provider = 'gar'

    def __init__(self, project: Optional[str], location: Optional[str]):
        if not project or not location:
            raise ValueError("Cannot detect GCP project/location for Artifact Registry. "
                             "Set GOOGLE_APPLICATION_CREDENTIALS or run on GCE, or configure "
                             "containerSettings with a *-docker.pkg.dev repositoryURL.")
        _ensure_pkg('google.auth', 'google-auth', 'image-cleaner')
        _ensure_pkg('google.cloud.artifactregistry_v1', 'google-cloud-artifact-registry', 'image-cleaner')
        from google.cloud import artifactregistry_v1
        self._client = artifactregistry_v1.ArtifactRegistryClient()
        self._project = project
        self._location = location
        _LOGGER.info("[image-cleaner] gar client created project=%s location=%s", project, location)

    def _parent(self) -> str:
        return 'projects/%s/locations/%s' % (self._project, self._location)

    def list_repositories(self) -> List[str]:
        from google.cloud import artifactregistry_v1
        out: List[str] = []
        req = artifactregistry_v1.ListRepositoriesRequest(parent=self._parent())
        for repo in self._client.list_repositories(request=req):
            fmt = getattr(repo, 'format_', None)
            if fmt is not None and getattr(fmt, 'name', '') != 'DOCKER':
                continue
            short = repo.name.split('/')[-1]
            if _matches_dataiku(short):
                out.append(repo.name)  # full resource name — consumed by list_images
        out.sort()
        return out

    def list_images(self, repo: str) -> List[Dict[str, Any]]:
        from google.cloud import artifactregistry_v1
        out: List[Dict[str, Any]] = []
        req = artifactregistry_v1.ListDockerImagesRequest(parent=repo)
        for img in self._client.list_docker_images(request=req):
            pushed = img.upload_time
            digest = img.name.split('@')[-1] if '@' in img.name else img.name.split('/')[-1]
            out.append({
                'digest': digest,
                'tags': list(img.tags) if img.tags else [],
                'pushedAt': pushed.isoformat() if pushed else '',
            })
        return out

    def head_image(self, repo: str, digest: str) -> Optional[Dict[str, Any]]:
        from google.cloud import artifactregistry_v1
        try:
            req = artifactregistry_v1.ListDockerImagesRequest(parent=repo)
            for img in self._client.list_docker_images(request=req):
                if digest in img.name:
                    pushed = img.upload_time
                    if not pushed:
                        return None
                    return {'pushedAt': pushed.date()}
        except Exception:
            return None
        return None

    def delete_images(self, repo: str, digests: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        from google.cloud import artifactregistry_v1
        deleted: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        name_by_digest: Dict[str, str] = {}
        try:
            req = artifactregistry_v1.ListDockerImagesRequest(parent=repo)
            for img in self._client.list_docker_images(request=req):
                for d in digests:
                    if d in img.name:
                        name_by_digest[d] = img.name
        except Exception as e:
            for d in digests:
                failed.append({'repo': repo, 'digest': d, 'reason': 'list failed: %s' % e})
            return deleted, failed

        for d in digests:
            full_name = name_by_digest.get(d)
            if not full_name:
                failed.append({'repo': repo, 'digest': d, 'reason': 'image not found'})
                continue
            try:
                pkg_part, _, dg = full_name.partition('@')
                pkg_name = pkg_part.replace('/dockerImages/', '/packages/')
                version_name = '%s/versions/%s' % (pkg_name, dg)
                del_req = artifactregistry_v1.DeleteVersionRequest(name=version_name, force=True)
                op = self._client.delete_version(request=del_req)
                op.result(timeout=60)
                deleted.append({'repo': repo, 'digest': d})
            except Exception as e:
                failed.append({'repo': repo, 'digest': d, 'reason': str(e)})
        return deleted, failed


# ── Detection ──

def _image_cleaner_walk_container_settings() -> Optional[Dict[str, str]]:
    """Walk containerSettings.executionConfigs[] looking for a recognizable registry URL.
    Returns {provider, registryUrl} or None. Never raises."""
    try:
        try:
            client = g.client
        except RuntimeError:
            client = dataiku.api_client()
        settings = client.get_general_settings().get_raw()
    except Exception:
        return None
    cs = settings.get('containerSettings') if isinstance(settings, dict) else None
    if not isinstance(cs, dict):
        return None

    configs = cs.get('executionConfigs') or []
    default_name = cs.get('defaultExecutionConfig')
    ordered: List[Dict[str, Any]] = []
    for c in configs:
        if isinstance(c, dict) and c.get('name') == default_name:
            ordered.insert(0, c)
        elif isinstance(c, dict):
            ordered.append(c)
    generic = cs.get('executionConfigsGenericOverrides')
    if isinstance(generic, dict):
        ordered.append(generic)

    ecr_re = re.compile(r'^(?:https?://)?\d+\.dkr\.ecr\.([a-z0-9-]+)\.amazonaws\.com', re.I)
    acr_re = re.compile(r'^(?:https?://)?([a-zA-Z0-9]+\.azurecr\.io)', re.I)
    gar_re = re.compile(r'^(?:https?://)?([a-z0-9-]+-docker\.pkg\.dev|(?:[a-z0-9-]+\.)?gcr\.io)', re.I)

    for c in ordered:
        url = (c.get('repositoryURL') or '').strip()
        if not url:
            continue
        if ecr_re.match(url):
            return {'provider': 'ecr', 'registryUrl': url}
        if acr_re.match(url):
            return {'provider': 'acr', 'registryUrl': url}
        if gar_re.match(url):
            return {'provider': 'gar', 'registryUrl': url}
    return None


def _imds_probe_aws(timeout: float = 2.0) -> Optional[str]:
    import urllib.request
    try:
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


def _imds_probe_azure(timeout: float = 2.0) -> Optional[str]:
    import urllib.request
    try:
        req = urllib.request.Request(
            'http://169.254.169.254/metadata/instance?api-version=2021-02-01',
            headers={'Metadata': 'true'},
        )
        body = urllib.request.urlopen(req, timeout=timeout).read().decode()
        data = json.loads(body)
        return (data.get('compute') or {}).get('location') or None
    except Exception:
        return None


def _imds_probe_gcp(timeout: float = 2.0) -> Optional[str]:
    import urllib.request
    try:
        req = urllib.request.Request(
            'http://metadata.google.internal/computeMetadata/v1/project/project-id',
            headers={'Metadata-Flavor': 'Google'},
        )
        return urllib.request.urlopen(req, timeout=timeout).read().decode().strip() or None
    except Exception:
        return None


def _imds_probe_gcp_zone(timeout: float = 2.0) -> Optional[str]:
    import urllib.request
    try:
        req = urllib.request.Request(
            'http://metadata.google.internal/computeMetadata/v1/instance/zone',
            headers={'Metadata-Flavor': 'Google'},
        )
        z = urllib.request.urlopen(req, timeout=timeout).read().decode().strip()
        return z.split('/')[-1] if z else None
    except Exception:
        return None


def _imds_probe_parallel() -> Optional[Dict[str, str]]:
    """Race AWS/Azure/GCP IMDS probes; return {provider, hint} of first hit."""
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {
            ex.submit(_imds_probe_aws): 'ecr',
            ex.submit(_imds_probe_azure): 'acr',
            ex.submit(_imds_probe_gcp): 'gar',
        }
        try:
            for fut in as_completed(list(futures), timeout=3):
                try:
                    result = fut.result()
                except Exception:
                    continue
                if result:
                    return {'provider': futures[fut], 'hint': result}
        except FuturesTimeoutError:
            pass
    return None


def _ipnet_probe() -> Optional[str]:
    """Option C: look up outbound IP → whereismyinstance.com → cloud."""
    import urllib.request
    ip: Optional[str] = None
    for url in ('https://checkip.amazonaws.com', 'https://api.ipify.org'):
        try:
            ip = urllib.request.urlopen(url, timeout=3).read().decode().strip()
            if ip:
                break
        except Exception:
            continue
    if not ip:
        return None
    try:
        with urllib.request.urlopen('https://whereismyinstance.com/api/%s' % ip, timeout=5) as resp:
            body = json.loads(resp.read().decode())
    except Exception:
        return None
    cloud = (body.get('cloud') or '').lower()
    if 'amazon' in cloud:
        return 'ecr'
    if 'microsoft' in cloud or 'azure' in cloud:
        return 'acr'
    if 'google' in cloud:
        return 'gar'
    return None


def _ecr_detect_region() -> Optional[str]:
    for var in ('AWS_DEFAULT_REGION', 'AWS_REGION'):
        val = os.environ.get(var, '').strip()
        if val:
            return val
    r = _imds_probe_aws()
    if r:
        return r
    try:
        result = subprocess.run(['aws', 'configure', 'get', 'region'],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    # Also try containerSettings (repositoryURL gives us the region)
    info = _image_cleaner_walk_container_settings()
    if info and info.get('provider') == 'ecr':
        m = re.match(r'.*\.dkr\.ecr\.([a-z0-9-]+)\.amazonaws\.com', info['registryUrl'], re.I)
        if m:
            return m.group(1)
    return None


def _acr_detect_registry() -> Optional[str]:
    info = _image_cleaner_walk_container_settings()
    if info and info.get('provider') == 'acr':
        return info['registryUrl'].replace('https://', '').replace('http://', '').rstrip('/')
    return None


def _gar_detect_project_location() -> Tuple[Optional[str], Optional[str]]:
    info = _image_cleaner_walk_container_settings()
    if info and info.get('provider') == 'gar':
        url = info['registryUrl'].replace('https://', '').replace('http://', '')
        m = re.match(r'^([a-z0-9-]+)-docker\.pkg\.dev/([^/]+)', url, re.I)
        if m:
            return m.group(2), m.group(1)
        m2 = re.match(r'^(?:([a-z0-9-]+)\.)?gcr\.io/([^/]+)', url, re.I)
        if m2:
            legacy = (m2.group(1) or 'us').lower()
            loc = {'us': 'us', 'eu': 'europe', 'asia': 'asia'}.get(legacy, 'us')
            return m2.group(2), loc
    try:
        _ensure_pkg('google.auth', 'google-auth', 'image-cleaner')
        import google.auth
        _creds, project = google.auth.default()
        zone = _imds_probe_gcp_zone()
        location = zone.rsplit('-', 1)[0] if zone else 'us'
        return project, location
    except Exception:
        return None, None


def _image_cleaner_adapter(provider: str) -> RegistryAdapter:
    """Return a cached adapter for the given provider. Raises on misconfiguration."""
    provider = (provider or '').lower().strip()
    if provider == 'ecr':
        scope = _ecr_detect_region() or ''
    elif provider == 'acr':
        scope = _acr_detect_registry() or ''
    elif provider == 'gar':
        proj, loc = _gar_detect_project_location()
        scope = '%s/%s' % (proj or '', loc or '')
    else:
        raise ValueError("Unknown provider %r (expected ecr|acr|gar)" % provider)
    key = (provider, scope)
    with _IMAGE_CLEANER_CLIENTS_LOCK:
        if key not in _IMAGE_CLEANER_CLIENTS:
            if provider == 'ecr':
                _IMAGE_CLEANER_CLIENTS[key] = EcrAdapter(region=scope)
            elif provider == 'acr':
                _IMAGE_CLEANER_CLIENTS[key] = AcrAdapter(registry_host=scope)
            else:  # gar
                proj, loc = _gar_detect_project_location()
                _IMAGE_CLEANER_CLIENTS[key] = GarAdapter(project=proj, location=loc)
        return _IMAGE_CLEANER_CLIENTS[key]


def _image_cleaner_error_hint(provider: str) -> str:
    if provider == 'ecr':
        return "Ensure the DSS host has an AWS IAM role or access keys with ECR read/delete permissions."
    if provider == 'acr':
        return "Run `az login` on the DSS host, or assign a managed identity with AcrDelete role."
    if provider == 'gar':
        return "Set GOOGLE_APPLICATION_CREDENTIALS or attach a GCP service account with artifactregistry.repositories.deletePackages."
    return ""


# ── Endpoints ──

@bp.route('/api/tools/image-cleaner/detect-provider')
def api_image_cleaner_detect_provider():
    """A (containerSettings) → B (IMDS race) → C (whereismyinstance). Never throws."""
    t0 = time.time()
    a = _image_cleaner_walk_container_settings()
    if a:
        _LOGGER.info("[image-cleaner] detect via dss-config in %.0fms", (time.time()-t0)*1000)
        return jsonify({'provider': a['provider'], 'registryUrl': a['registryUrl'], 'source': 'dss-config'})
    if _safe_request_host_id() != 'local':
        try:
            result = _image_cleaner_macro(g.client, 'detect-provider')
        except Exception as e:
            _LOGGER.error("[image-cleaner] remote detect macro failed: %s", e)
            return jsonify({
                'provider': None,
                'registryUrl': None,
                'source': 'target-macro',
                'error': str(e),
            }), 502
        _LOGGER.info("[image-cleaner] remote detect via macro in %.0fms", (time.time()-t0)*1000)
        return jsonify({
            'provider': result.get('provider'),
            'registryUrl': result.get('registryUrl'),
            'source': result.get('source') or 'target-macro',
            'error': result.get('error'),
        })
    b = _imds_probe_parallel()
    if b:
        _LOGGER.info("[image-cleaner] detect via imds in %.0fms", (time.time()-t0)*1000)
        return jsonify({'provider': b['provider'], 'registryUrl': None, 'source': 'imds'})
    c = _ipnet_probe()
    if c:
        _LOGGER.info("[image-cleaner] detect via ipnet in %.0fms", (time.time()-t0)*1000)
        return jsonify({'provider': c, 'registryUrl': None, 'source': 'ipnet'})
    _LOGGER.info("[image-cleaner] detect MISS in %.0fms", (time.time()-t0)*1000)
    return jsonify({'provider': None, 'registryUrl': None, 'source': 'none'})


@bp.route('/api/tools/image-cleaner/release-date')
def api_image_cleaner_release_date():
    t0 = time.time()
    provider = (request.args.get('provider') or 'ecr').strip().lower()
    try:
        info = _image_cleaner_release_info()
        if _safe_request_host_id() == 'local':
            try:
                t3 = time.time()
                _image_cleaner_adapter(provider)
                _LOGGER.info("[perf:image-cleaner] adapter_prewarm=%.0fms provider=%s", (time.time()-t3)*1000, provider)
            except Exception as e:
                _LOGGER.info("[perf:image-cleaner] adapter_prewarm FAILED provider=%s: %s", provider, e)
        _LOGGER.info("[perf:image-cleaner] release-date total=%.0fms provider=%s", (time.time()-t0)*1000, provider)
        return jsonify(info)
    except Exception as e:
        _LOGGER.error("[image-cleaner] release-date error (%.0fms): %s", (time.time()-t0)*1000, e)
        return jsonify({'error': str(e)}), 500


@bp.route('/api/tools/image-cleaner/scan')
def api_image_cleaner_scan():
    provider = (request.args.get('provider') or 'ecr').strip().lower()
    cutoff_str = request.args.get('cutoff', '').strip()
    if not cutoff_str:
        return jsonify({'error': 'Missing cutoff parameter'}), 400
    try:
        cutoff, info = _image_cleaner_validate_cutoff(cutoff_str)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    if _safe_request_host_id() != 'local':
        def generate_remote():
            t0 = time.time()
            try:
                result = _image_cleaner_macro(g.client, 'scan', provider=provider, cutoff=cutoff_str)
            except Exception as e:
                _LOGGER.error("[image-cleaner] remote scan macro failed: %s", e)
                yield "event: error\ndata: %s\n\n" % json.dumps({
                    'error': str(e),
                    'provider': provider,
                    'hint': _image_cleaner_error_hint(provider),
                })
                return
            if not result.get('ok'):
                yield "event: error\ndata: %s\n\n" % json.dumps({
                    'error': result.get('error') or 'Remote image-cleaner macro failed',
                    'provider': provider,
                    'hint': _image_cleaner_error_hint(provider),
                })
                return
            repos = result.get('repos') or []
            yield "event: init\ndata: %s\n\n" % json.dumps({
                "total": len(repos),
                "cutoff": cutoff_str,
                "maxCutoffDate": info['maxCutoffDate'],
                "provider": provider,
                "source": "target-macro",
            })
            for repo in repos:
                yield "event: repo\ndata: %s\n\n" % json.dumps(repo)
            yield "event: done\ndata: %s\n\n" % json.dumps({"total_ms": int((time.time()-t0)*1000)})

        return _sse_response(generate_remote)

    def generate():
        t0 = time.time()
        try:
            adapter = _image_cleaner_adapter(provider)
            repos = adapter.list_repositories()
        except Exception as e:
            yield "event: error\ndata: %s\n\n" % json.dumps({
                'error': str(e),
                'provider': provider,
                'hint': _image_cleaner_error_hint(provider),
            })
            return

        yield "event: init\ndata: %s\n\n" % json.dumps({
            "total": len(repos),
            "cutoff": cutoff_str,
            "maxCutoffDate": info['maxCutoffDate'],
            "provider": provider,
        })

        for repo in repos:
            try:
                raw = adapter.list_images(repo)
                images: List[Dict[str, Any]] = []
                for img in raw:
                    pushed_iso = img.get('pushedAt', '')
                    if not pushed_iso:
                        continue
                    try:
                        pushed_date = datetime.fromisoformat(str(pushed_iso).replace('Z', '+00:00')).date()
                    except Exception:
                        continue
                    images.append({
                        'digest': img.get('digest', ''),
                        'tags': img.get('tags', []) or [],
                        'pushedAt': pushed_iso,
                        'deletable': pushed_date < cutoff,
                    })
                images.sort(key=lambda x: x['pushedAt'])
                repo_display = repo.split('/')[-1] if provider == 'gar' else repo
                yield "event: repo\ndata: %s\n\n" % json.dumps({'name': repo_display, 'images': images})
            except Exception as e:
                yield "event: repo\ndata: %s\n\n" % json.dumps({'name': repo, 'images': [], 'error': str(e)})

        yield "event: done\ndata: %s\n\n" % json.dumps({"total_ms": int((time.time()-t0)*1000)})

    return _sse_response(generate)


@bp.route('/api/tools/image-cleaner/delete', methods=['POST'])
@advanced
def api_image_cleaner_delete():
    body = request.get_json(force=True, silent=True) or {}
    provider = (body.get('provider') or 'ecr').strip().lower()
    cutoff_str = (body.get('cutoff') or '').strip()
    images = body.get('images', [])
    dry_run = bool(body.get('dryRun', False))
    if not cutoff_str:
        return jsonify({'error': 'Missing cutoff'}), 400
    if not images:
        return jsonify({'error': 'No images specified'}), 400
    try:
        cutoff, _info = _image_cleaner_validate_cutoff(cutoff_str)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if _safe_request_host_id() != 'local':
        try:
            result = _image_cleaner_macro(
                g.client,
                'delete',
                provider=provider,
                cutoff=cutoff_str,
                images_json=json.dumps(images),
                dryRun=dry_run,
            )
        except Exception as e:
            _LOGGER.error("[image-cleaner] remote delete macro failed: %s", e)
            return jsonify({
                'ok': False,
                'error': str(e),
                'provider': provider,
                'hint': _image_cleaner_error_hint(provider),
            }), 502
        status = 200 if result.get('ok') else 400
        return jsonify(result), status
    try:
        adapter = _image_cleaner_adapter(provider)
    except Exception as e:
        return jsonify({
            'error': str(e), 'provider': provider,
            'hint': _image_cleaner_error_hint(provider),
        }), 502

    preflight_errors = []
    for img in images:
        repo = img.get('repositoryName', '')
        digest = img.get('imageDigest', '')
        if not repo or not digest:
            preflight_errors.append({'repo': repo, 'digest': digest, 'reason': 'missing repo or digest'})
            continue
        head = adapter.head_image(repo, digest)
        if head is None:
            preflight_errors.append({'repo': repo, 'digest': digest, 'reason': 'image not found'})
            continue
        pushed_date = head['pushedAt']
        if pushed_date >= cutoff:
            preflight_errors.append({
                'repo': repo, 'digest': digest,
                'reason': 'pushed %s is not before cutoff %s' % (pushed_date.isoformat(), cutoff_str),
            })

    if preflight_errors:
        return jsonify({'error': 'Preflight failed — no images were deleted',
                        'preflight_errors': preflight_errors}), 400

    by_repo: Dict[str, List[str]] = {}
    for img in images:
        by_repo.setdefault(img['repositoryName'], []).append(img['imageDigest'])

    deleted: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for repo, digests in by_repo.items():
        if dry_run:
            d = [{'repo': repo, 'digest': digest, 'dryRun': True} for digest in digests]
            f = []
        else:
            d, f = adapter.delete_images(repo, digests)
        deleted.extend(d)
        failed.extend(f)

    _LOGGER.info("[image-cleaner] provider=%s dryRun=%s deleted=%d failed=%d", provider, dry_run, len(deleted), len(failed))
    return jsonify({'dryRun': dry_run, 'deleted': deleted, 'failed': failed})
