"""Unit tests for the code-env build-log parsing core (Code Envs -> Broken)."""

import pytest

from adk_backend import code_env_build as ceb


def _banner(title, ts='2026/08/04 13:13:26.021'):
    return (
        '*********************************************************\n'
        '*********************************************************\n'
        '*\n'
        '* %s\n'
        '*\n'
        '* <AC:papi:KEY:admin> at %s\n'
        '*\n'
        '*********************************************************\n'
    ) % (title, ts)


_FAILED_INSTALL = (
    'Collecting numpy==1.99.0\n'
    'ERROR: Could not find a version that satisfies the requirement numpy==1.99.0\n'
    'ERROR: No matching distribution found for numpy==1.99.0\n'
)

_OK_INSTALL = (
    'Collecting numpy==1.26.4\n'
    'Successfully installed numpy-1.26.4\n'
)


# ---- append-only isolation ----

def test_isolate_last_build_ignores_earlier_failure():
    """A fix-and-retry lands minutes after the failure, so isolation must anchor
    on the last install banner — timestamp clustering would merge the two."""
    log = (
        _banner('install packages', '2026/08/04 13:13:26.021') + _FAILED_INSTALL
        + _banner('install packages', '2026/08/04 13:16:41.114') + _OK_INSTALL
        + _banner('list packages', '2026/08/04 13:17:02.900') + 'numpy 1.26.4\n'
    )
    block = ceb.isolate_last_build(log)
    assert 'numpy==1.99.0' not in block
    assert ceb.classify(block) == (None, None)


def test_isolate_last_build_keeps_latest_failure():
    log = (
        _banner('install packages', '2026/08/04 13:13:26.021') + _OK_INSTALL
        + _banner('list packages', '2026/08/04 13:13:44.010') + 'numpy 1.26.4\n'
        + _banner('install packages', '2026/08/04 13:16:41.114') + _FAILED_INSTALL
    )
    block = ceb.isolate_last_build(log)
    assert ceb.classify(block) == (
        'MISSING_PACKAGE', 'Package or version not found on the index')


def test_isolate_last_build_without_banners_returns_whole_text():
    assert ceb.isolate_last_build(_FAILED_INSTALL) == _FAILED_INSTALL


def test_isolate_last_build_falls_back_to_last_section():
    log = _banner('build image') + 'step 1\n' + _banner('push image') + 'step 2\n'
    block = ceb.isolate_last_build(log)
    assert 'step 1' not in block
    assert 'step 2' in block


# ---- false-positive scrub ----

def test_successful_image_push_json_is_not_a_failure():
    """DSS writes {"success": true, "error": null} on a successful image push —
    the IMAGE_BUILD / generic-fail patterns would otherwise flag a healthy env."""
    log = (
        _banner('rebuild image')
        + '{"success": true, "error": null, "digest": "sha256:abc"}\n'
    )
    assert ceb.classify(ceb.isolate_last_build(log)) == (None, None)


def test_failed_image_push_json_is_a_failure():
    log = _banner('rebuild image') + '{"success": false, "error": "denied"}\n'
    assert ceb.classify(ceb.isolate_last_build(log)) == (
        'IMAGE_BUILD', 'Container image build or push failure')


# ---- taxonomy ----

@pytest.mark.parametrize('snippet,expected', [
    ('ERROR: ResolutionImpossible: for help visit ...', 'VERSION_CONFLICT'),
    ('ERROR: No matching distribution found for scikit-learn==9.9', 'MISSING_PACKAGE'),
    ('ERROR: Package foo requires a different Python: 3.6.8 not in ">=3.9"', 'PYTHON_VERSION'),
    ('  error: subprocess-exited-with-error', 'BUILD_FAILURE'),
    ('PackagesNotFoundError: The following packages are not available', 'CONDA_ERROR'),
    ('urllib3 ProxyError: Cannot connect to proxy', 'NETWORK'),
    ('OSError: [Errno 28] No space left on device', 'RESOURCES'),
    ('failed to push image to registry', 'IMAGE_BUILD'),
    ('ERR_CODEENV_UPDATE_FAILED', 'UNCLASSIFIED_FAILURE'),
])
def test_classify_taxonomy(snippet, expected):
    assert ceb.classify(snippet)[0] == expected


def test_classify_is_ordered_most_specific_first():
    both = 'ERROR: ResolutionImpossible\nERROR: No matching distribution found for x'
    assert ceb.classify(both)[0] == 'VERSION_CONFLICT'


def test_classify_clean_log():
    assert ceb.classify(_OK_INSTALL) == (None, None)


# ---- error excerpting ----

def test_extract_error_dedupes_and_drops_noise():
    block = (
        'Collecting numpy==1.99.0\n'
        'Requirement already satisfied: pip\n'
        'ERROR: No matching distribution found for numpy==1.99.0\n'
        'ERROR: No matching distribution found for numpy==1.99.0\n'
    )
    out = ceb.extract_error(block)
    assert out.count('No matching distribution') == 1
    assert 'Requirement already satisfied' not in out
    assert 'Collecting numpy' not in out


def test_extract_error_elides_pip_version_dump():
    versions = ', '.join('0.%d.0' % i for i in range(60))
    block = (
        'ERROR: Could not find a version that satisfies the requirement foo '
        '(from versions: %s)\n' % versions
    )
    out = ceb.extract_error(block)
    assert '(from versions: <long list elided>)' in out
    assert '0.42.0' not in out


def test_extract_error_respects_caps():
    block = '\n'.join('ERROR: failure number %d' % i for i in range(50))
    assert len(ceb.extract_error(block, max_lines=4).splitlines()) == 4
    assert len(ceb.extract_error(block, max_chars=60)) <= 60


def test_extract_error_falls_back_to_tail_when_nothing_matches():
    block = 'step one\nstep two\nRequirement already satisfied: pip\nstep three\n'
    out = ceb.extract_error(block, max_lines=2)
    assert out.splitlines() == ['step two', 'step three']


# ---- log-derived dates ----

def test_derive_dates_uses_create_log_and_newest_entry():
    entries = [
        {'name': 'createPythonEnv.log', 'lastModified': 1000},
        {'name': 'updateEnvAccordingToSpec.log', 'lastModified': 3000},
        {'name': 'rebuildImage.log', 'lastModified': 2000},
    ]
    assert ceb.derive_dates(entries) == (1000, 3000)


def test_derive_dates_without_create_log():
    entries = [{'name': 'updateEnvAccordingToSpec.log', 'lastModified': 3000}]
    assert ceb.derive_dates(entries) == (None, 3000)


def test_derive_dates_empty_listing():
    assert ceb.derive_dates([]) == (None, None)
    assert ceb.derive_dates(None) == (None, None)


def test_derive_dates_ignores_garbage_timestamps():
    entries = [
        {'name': 'createREnv.log', 'lastModified': 0},
        {'name': 'updateREnv.log', 'lastModified': None},
        {'name': 'rebuildImage.log', 'lastModified': '2500'},
    ]
    assert ceb.derive_dates(entries) == (None, 2500)
