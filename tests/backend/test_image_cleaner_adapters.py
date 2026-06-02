"""Tests for multi-cloud image cleaner adapters, detection chain, and SSE shape.

Run from project root:
    cd admin-toolkit && python3 -m pytest tests/backend -q

`conftest.py` next to this file stubs `dataiku` so `backend` imports without a DSS."""

from __future__ import annotations

import io
import json
import os
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'tests', 'backend'))  # conftest stubs
sys.path.insert(0, os.path.join(ROOT, 'webapps', 'admin-toolkit'))
sys.path.insert(0, os.path.join(ROOT, 'python-lib'))

import conftest  # noqa: F401  (applies dataiku/dateutil stubs)
import backend


# ──────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────

class MatchesDataikuTest(unittest.TestCase):
    def test_case_insensitive_dataiku(self):
        self.assertTrue(backend._matches_dataiku('dataiku'))
        self.assertTrue(backend._matches_dataiku('Dataiku'))
        self.assertTrue(backend._matches_dataiku('DATAIKU'))
        self.assertTrue(backend._matches_dataiku('my-dataiku-base'))

    def test_case_insensitive_dku(self):
        self.assertTrue(backend._matches_dataiku('dku'))
        self.assertTrue(backend._matches_dataiku('DKU-exec'))
        self.assertTrue(backend._matches_dataiku('env-with-dku-prefix'))

    def test_unrelated(self):
        self.assertFalse(backend._matches_dataiku('nginx'))
        self.assertFalse(backend._matches_dataiku('my-app'))
        self.assertFalse(backend._matches_dataiku(''))


# ──────────────────────────────────────────────────────────────────────
# Detector — containerSettings URL patterns (table-driven)
# ──────────────────────────────────────────────────────────────────────

class WalkContainerSettingsTest(unittest.TestCase):
    CASES = [
        # (repositoryURL, expected_provider)
        ('296821582317.dkr.ecr.us-west-2.amazonaws.com', 'ecr'),
        ('https://296821582317.dkr.ecr.eu-central-1.amazonaws.com/my-image', 'ecr'),
        ('myregistry.azurecr.io', 'acr'),
        ('https://myregistry.azurecr.io/nested', 'acr'),
        ('us-central1-docker.pkg.dev/my-project/my-repo', 'gar'),
        ('europe-west4-docker.pkg.dev/proj/repo', 'gar'),
        ('gcr.io/my-project/image', 'gar'),
        ('us.gcr.io/proj/img', 'gar'),
        ('eu.gcr.io/proj/img', 'gar'),
        # Negatives
        ('harbor.example.com/team/image', None),
        ('', None),
    ]

    def _fake_settings(self, url):
        return {
            'containerSettings': {
                'defaultExecutionConfig': 'only',
                'executionConfigs': [{'name': 'only', 'repositoryURL': url}],
            }
        }

    def test_url_patterns(self):
        for url, expected in self.CASES:
            with self.subTest(url=url):
                fake_client = mock.MagicMock()
                fake_client.get_general_settings.return_value.get_raw.return_value = self._fake_settings(url)
                with mock.patch.object(backend.dataiku, 'api_client', return_value=fake_client):
                    result = backend._image_cleaner_walk_container_settings()
                if expected is None:
                    self.assertIsNone(result, msg='url=%s' % url)
                else:
                    self.assertIsNotNone(result, msg='url=%s' % url)
                    self.assertEqual(result['provider'], expected)
                    self.assertEqual(result['registryUrl'], url)

    def test_default_config_walked_first(self):
        fake_client = mock.MagicMock()
        fake_client.get_general_settings.return_value.get_raw.return_value = {
            'containerSettings': {
                'defaultExecutionConfig': 'prod',
                'executionConfigs': [
                    {'name': 'staging', 'repositoryURL': 'staging.azurecr.io'},
                    {'name': 'prod', 'repositoryURL': '123.dkr.ecr.us-east-1.amazonaws.com'},
                ],
            }
        }
        with mock.patch.object(backend.dataiku, 'api_client', return_value=fake_client):
            result = backend._image_cleaner_walk_container_settings()
        self.assertEqual(result['provider'], 'ecr')

    def test_never_raises_on_bad_settings(self):
        fake_client = mock.MagicMock()
        fake_client.get_general_settings.side_effect = RuntimeError('boom')
        with mock.patch.object(backend.dataiku, 'api_client', return_value=fake_client):
            self.assertIsNone(backend._image_cleaner_walk_container_settings())


# ──────────────────────────────────────────────────────────────────────
# Detector — IMDS probes (mocked urlopen)
# ──────────────────────────────────────────────────────────────────────

class ImdsProbeTest(unittest.TestCase):
    def _fake_urlopen(self, url_to_body):
        """Return a context-manager-compatible urlopen mock that dispatches by URL."""
        def factory(req, timeout=None):
            url = req.full_url if hasattr(req, 'full_url') else req
            for key, body in url_to_body.items():
                if key in url:
                    if isinstance(body, Exception):
                        raise body
                    m = mock.MagicMock()
                    m.read.return_value = body if isinstance(body, bytes) else str(body).encode()
                    m.__enter__ = lambda s: s
                    m.__exit__ = lambda s, *a: False
                    return m
            raise OSError('no mock for ' + url)
        return factory

    def test_aws(self):
        fake = self._fake_urlopen({
            '/latest/api/token': b'TOK',
            '/placement/region': b'us-west-2',
        })
        with mock.patch('urllib.request.urlopen', side_effect=fake):
            self.assertEqual(backend._imds_probe_aws(timeout=1), 'us-west-2')

    def test_azure(self):
        fake = self._fake_urlopen({
            '/metadata/instance': json.dumps({'compute': {'location': 'westeurope'}}),
        })
        with mock.patch('urllib.request.urlopen', side_effect=fake):
            self.assertEqual(backend._imds_probe_azure(timeout=1), 'westeurope')

    def test_gcp(self):
        fake = self._fake_urlopen({
            '/project-id': b'my-gcp-project',
        })
        with mock.patch('urllib.request.urlopen', side_effect=fake):
            self.assertEqual(backend._imds_probe_gcp(timeout=1), 'my-gcp-project')

    def test_all_miss(self):
        fake = self._fake_urlopen({})  # everything raises OSError
        with mock.patch('urllib.request.urlopen', side_effect=fake):
            self.assertIsNone(backend._imds_probe_aws(timeout=0.1))
            self.assertIsNone(backend._imds_probe_azure(timeout=0.1))
            self.assertIsNone(backend._imds_probe_gcp(timeout=0.1))


# ──────────────────────────────────────────────────────────────────────
# Detector — whereismyinstance cloud → provider mapping
# ──────────────────────────────────────────────────────────────────────

class IpnetProbeTest(unittest.TestCase):
    CLOUD_TO_PROVIDER = [
        ('Amazon Web Services', 'ecr'),
        ('Microsoft Azure', 'acr'),
        ('Google Cloud Platform', 'gar'),
        ('Cloudflare', None),
        ('Oracle Cloud', None),
        ('', None),
    ]

    def _mock(self, cloud):
        def factory(url, timeout=None):
            if isinstance(url, str) and 'checkip' in url:
                m = mock.MagicMock()
                m.read.return_value = b'1.2.3.4'
                m.__enter__ = lambda s: s
                m.__exit__ = lambda s, *a: False
                return m
            url_str = url.full_url if hasattr(url, 'full_url') else url
            if 'whereismyinstance' in url_str:
                body = json.dumps({'cloud': cloud} if cloud else {'results': 'No matches found'})
                m = mock.MagicMock()
                m.read.return_value = body.encode()
                m.__enter__ = lambda s: s
                m.__exit__ = lambda s, *a: False
                return m
            raise OSError('unexpected url ' + str(url_str))
        return factory

    def test_cloud_mapping(self):
        for cloud, expected in self.CLOUD_TO_PROVIDER:
            with self.subTest(cloud=cloud):
                with mock.patch('urllib.request.urlopen', side_effect=self._mock(cloud)):
                    self.assertEqual(backend._ipnet_probe(), expected)


# ──────────────────────────────────────────────────────────────────────
# EcrAdapter — mocked boto3
# ──────────────────────────────────────────────────────────────────────

def _make_fake_ecr(repos_by_name, pushed_for_digest=None, fail_batch_delete=False,
                   batch_delete_failures=None):
    """Build a boto3-like fake ECR client."""
    pushed_for_digest = pushed_for_digest or {}
    batch_delete_failures = batch_delete_failures or []

    class _Paginator:
        def __init__(self, pages):
            self._pages = pages

        def paginate(self, **kwargs):
            if 'repositoryName' in kwargs:
                repo = kwargs['repositoryName']
                return iter([{'imageDetails': repos_by_name.get(repo, [])}])
            return iter([{'repositories': [{'repositoryName': n} for n in repos_by_name.keys()]}])

    class _Client:
        def get_paginator(self, name):
            return _Paginator([])

        def describe_images(self, repositoryName, imageIds):
            digest = imageIds[0]['imageDigest']
            if digest in pushed_for_digest:
                return {'imageDetails': [{'imageDigest': digest, 'imagePushedAt': pushed_for_digest[digest]}]}
            return {'imageDetails': []}

        def batch_delete_image(self, repositoryName, imageIds):
            if fail_batch_delete:
                raise RuntimeError('batch delete failed')
            return {
                'imageIds': [d for d in imageIds if d['imageDigest'] not in {f['digest'] for f in batch_delete_failures}],
                'failures': [
                    {'imageId': {'imageDigest': f['digest']}, 'failureReason': f['reason']}
                    for f in batch_delete_failures
                ],
            }

    return _Client()


class EcrAdapterTest(unittest.TestCase):
    def _adapter(self, client):
        with mock.patch.object(backend, '_ensure_boto3', return_value=mock.MagicMock(client=lambda *a, **k: client)):
            return backend.EcrAdapter(region='us-west-2')

    def test_list_repositories_filters_dataiku(self):
        fake = _make_fake_ecr({'dataiku-exec-base': [], 'nginx': [], 'DKU-custom': [], 'app': []})
        adapter = self._adapter(fake)
        self.assertEqual(adapter.list_repositories(), ['DKU-custom', 'dataiku-exec-base'])

    def test_list_images_shape(self):
        when = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        fake = _make_fake_ecr({
            'dataiku-exec-base': [
                {'imageDigest': 'sha256:aaa', 'imageTags': ['v1'], 'imagePushedAt': when},
                {'imageDigest': 'sha256:bbb', 'imageTags': [], 'imagePushedAt': when},
                {'imageDigest': 'sha256:ccc', 'imageTags': ['skip']},  # no pushedAt → filtered out
            ]
        })
        adapter = self._adapter(fake)
        imgs = adapter.list_images('dataiku-exec-base')
        self.assertEqual(len(imgs), 2)
        self.assertEqual(imgs[0]['digest'], 'sha256:aaa')
        self.assertIn('T', imgs[0]['pushedAt'])  # ISO 8601

    def test_head_image_missing(self):
        fake = _make_fake_ecr({'dataiku-exec-base': []})
        adapter = self._adapter(fake)
        self.assertIsNone(adapter.head_image('dataiku-exec-base', 'sha256:missing'))

    def test_head_image_present(self):
        when = datetime(2024, 3, 1, tzinfo=timezone.utc)
        fake = _make_fake_ecr({'r': []}, pushed_for_digest={'sha256:x': when})
        adapter = self._adapter(fake)
        head = adapter.head_image('r', 'sha256:x')
        self.assertEqual(head['pushedAt'], when.date())

    def test_delete_images_partial_failure(self):
        fake = _make_fake_ecr(
            {'r': []},
            batch_delete_failures=[{'digest': 'sha256:bad', 'reason': 'ImageNotFoundException'}],
        )
        adapter = self._adapter(fake)
        deleted, failed = adapter.delete_images('r', ['sha256:ok', 'sha256:bad'])
        self.assertEqual([d['digest'] for d in deleted], ['sha256:ok'])
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]['reason'], 'ImageNotFoundException')

    def test_delete_images_exception_aggregates(self):
        fake = _make_fake_ecr({'r': []}, fail_batch_delete=True)
        adapter = self._adapter(fake)
        deleted, failed = adapter.delete_images('r', ['sha256:a', 'sha256:b'])
        self.assertEqual(deleted, [])
        self.assertEqual(len(failed), 2)


# ──────────────────────────────────────────────────────────────────────
# SSE shape — inject a fake adapter, assert init → repo* → done
# ──────────────────────────────────────────────────────────────────────

class _FakeAdapter(backend.RegistryAdapter):
    provider = 'fake'

    def list_repositories(self):
        return ['dataiku-exec', 'dataiku-cde']

    def list_images(self, repo):
        # cutoff-boundary deterministic dates
        return [
            {'digest': 'sha256:old', 'tags': ['v1'], 'pushedAt': '2020-01-01T00:00:00+00:00'},
            {'digest': 'sha256:new', 'tags': ['v2'], 'pushedAt': '2099-12-31T00:00:00+00:00'},
        ]

    def head_image(self, repo, digest):
        return None

    def delete_images(self, repo, digests):
        return [], []


class SseShapeTest(unittest.TestCase):
    def setUp(self):
        backend.app.config['TESTING'] = True
        self.client = backend.app.test_client()

    def test_scan_emits_init_repo_done(self):
        with mock.patch.object(backend, '_image_cleaner_adapter', return_value=_FakeAdapter()), \
             mock.patch.object(backend, '_image_cleaner_validate_cutoff',
                               return_value=(datetime(2030, 1, 1).date(),
                                             {'maxCutoffDate': '2030-01-01', 'version': 'x', 'releaseDate': '2030-01-03'})):
            resp = self.client.get('/api/tools/image-cleaner/scan?provider=fake&cutoff=2030-01-01')
            self.assertEqual(resp.status_code, 200)
            body = resp.get_data(as_text=True)

        # Extract event names in order
        events = [line[len('event: '):] for line in body.splitlines() if line.startswith('event: ')]
        # Expect: init, repo, repo, done
        self.assertEqual(events[0], 'init')
        self.assertEqual(events[-1], 'done')
        self.assertEqual(events.count('repo'), 2)

        # Spot-check payload: init carries provider and total
        init_blobs = [line[len('data: '):] for line in body.splitlines() if line.startswith('data: ')]
        init_payload = json.loads(init_blobs[0])
        self.assertEqual(init_payload['total'], 2)
        self.assertEqual(init_payload['provider'], 'fake')
        self.assertEqual(init_payload['cutoff'], '2030-01-01')


# ──────────────────────────────────────────────────────────────────────
# detect-provider endpoint — A/B/C chain order
# ──────────────────────────────────────────────────────────────────────

class DetectProviderEndpointTest(unittest.TestCase):
    def setUp(self):
        backend.app.config['TESTING'] = True
        self.client = backend.app.test_client()

    def test_dss_config_first(self):
        with mock.patch.object(backend, '_image_cleaner_walk_container_settings',
                               return_value={'provider': 'ecr', 'registryUrl': 'x.dkr.ecr.us-west-2.amazonaws.com'}), \
             mock.patch.object(backend, '_imds_probe_parallel') as imds, \
             mock.patch.object(backend, '_ipnet_probe') as ipnet:
            r = self.client.get('/api/tools/image-cleaner/detect-provider').get_json()
        self.assertEqual(r['source'], 'dss-config')
        self.assertEqual(r['provider'], 'ecr')
        imds.assert_not_called()
        ipnet.assert_not_called()

    def test_imds_second(self):
        with mock.patch.object(backend, '_image_cleaner_walk_container_settings', return_value=None), \
             mock.patch.object(backend, '_imds_probe_parallel',
                               return_value={'provider': 'gar', 'hint': 'my-project'}), \
             mock.patch.object(backend, '_ipnet_probe') as ipnet:
            r = self.client.get('/api/tools/image-cleaner/detect-provider').get_json()
        self.assertEqual(r['source'], 'imds')
        self.assertEqual(r['provider'], 'gar')
        ipnet.assert_not_called()

    def test_ipnet_third(self):
        with mock.patch.object(backend, '_image_cleaner_walk_container_settings', return_value=None), \
             mock.patch.object(backend, '_imds_probe_parallel', return_value=None), \
             mock.patch.object(backend, '_ipnet_probe', return_value='acr'):
            r = self.client.get('/api/tools/image-cleaner/detect-provider').get_json()
        self.assertEqual(r['source'], 'ipnet')
        self.assertEqual(r['provider'], 'acr')

    def test_total_miss(self):
        with mock.patch.object(backend, '_image_cleaner_walk_container_settings', return_value=None), \
             mock.patch.object(backend, '_imds_probe_parallel', return_value=None), \
             mock.patch.object(backend, '_ipnet_probe', return_value=None):
            r = self.client.get('/api/tools/image-cleaner/detect-provider').get_json()
        self.assertEqual(r['source'], 'none')
        self.assertIsNone(r['provider'])


if __name__ == '__main__':
    unittest.main()
