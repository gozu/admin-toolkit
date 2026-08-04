"""Unit tests for the code-env build-log parsing core (Code Envs -> Broken)."""

import pytest

from adk_backend import code_env_build as ceb
from adk_backend.routes import code_env_broken as broken


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


# ---- per-attempt history parsing ----

def test_split_install_attempts_excludes_jupyter_section():
    """The jupyter-support banner also says "install" but never runs
    `pip install ... -r`, so it must not count as a deployment attempt."""
    log = (
        _banner('install packages', '2026/07/30 09:00:00.000')
        + 'Collecting pandas==2.2.0 (from -r /tmp/req100.txt (line 1))\n'
        + 'Successfully installed pandas-2.2.0\n'
        + _banner('install Jupyter support', '2026/07/30 09:01:12.000')
        + 'Collecting ipykernel\nSuccessfully installed ipykernel-6.29.5\n'
        + _banner('list packages', '2026/07/30 09:02:00.000') + 'pandas 2.2.0\n'
        + _banner('install packages', '2026/08/04 13:13:26.021')
        + 'Collecting numpy==1.99.0 (from -r /tmp/req101.txt (line 1))\n'
        + _FAILED_INSTALL
    )
    attempts = ceb.split_install_attempts(log)
    assert [a['ts'] for a in attempts] == ['2026/07/30 09:00:00', '2026/08/04 13:13:26']
    assert all('ipykernel' not in a['text'] for a in attempts)


def test_reconstruct_requested_spec_merges_forms_in_line_order():
    """Both pip echo forms carry the requirements-file origin; ordering follows
    the (line N) marker, not log order."""
    text = (
        'Requirement already satisfied: requests>=2.31 in /data/lib/site-packages '
        '(from -r /tmp/req5.txt (line 3))\n'
        'Collecting pandas==2.2.0 (from -r /tmp/req5.txt (line 1))\n'
        'Collecting numpy==1.26.4 (from -r /tmp/req5.txt (line 2))\n'
    )
    assert ceb.reconstruct_requested_spec(text) == [
        'pandas==2.2.0', 'numpy==1.26.4', 'requests>=2.31']


def test_reconstruct_requested_spec_excludes_transitive_deps():
    """Transitive deps carry "(from <package>->...)" — only the literal
    "(from -r" marks what the administrator actually requested."""
    text = (
        'Collecting pandas==2.2.0 (from -r /tmp/req5.txt (line 1))\n'
        'Collecting python-dateutil>=2.8.2 (from pandas==2.2.0->-r /tmp/req5.txt (line 1))\n'
        'Collecting six>=1.5 (from python-dateutil>=2.8.2->pandas==2.2.0->-r /tmp/req5.txt (line 1))\n'
        'Collecting pandas==2.2.0 (from -r /tmp/req5.txt (line 1))\n'
    )
    assert ceb.reconstruct_requested_spec(text) == ['pandas==2.2.0']


def test_format_attempt_history_skips_last_and_walks_newest_first():
    log = (
        _banner('install packages', '2026/07/28 10:00:00.000')
        + 'Collecting numpy==1.99.0 (from -r /tmp/req1.txt (line 1))\n'
        + 'ERROR: No matching distribution found for numpy==1.99.0\n'
        + _banner('install packages', '2026/07/29 11:00:00.000')
        + 'Collecting numpy==1.26.4 (from -r /tmp/req2.txt (line 1))\n'
        + 'Successfully installed numpy-1.26.4\n'
        + _banner('install packages', '2026/08/04 13:13:26.021')
        + 'Collecting numpy==2.0.0 (from -r /tmp/req3.txt (line 1))\n'
        + 'ERROR: ResolutionImpossible\n'
    )
    out = broken._format_attempt_history(ceb.split_install_attempts(log))
    # The last attempt is excluded — its error excerpt is already in the prompt.
    assert 'numpy==2.0.0' not in out
    assert out.index('2026/07/29 11:00:00') < out.index('2026/07/28 10:00:00')
    assert 'succeeded' in out and 'numpy==1.26.4' in out
    assert 'No matching distribution found for numpy==1.99.0' in out


def test_format_attempt_history_budgets_and_elides():
    attempts = [
        {'ts': '2026/07/%02d 10:00:00' % (i + 1),
         'text': 'Collecting foo==%d.0 (from -r /tmp/r.txt (line 1))\n' % i}
        for i in range(8)
    ]
    out = broken._format_attempt_history(attempts)
    assert out.count('Attempt at') == 5
    assert '(older attempts elided)' in out
    # Newest prior attempts survive; the oldest fall off; the last is skipped.
    assert '2026/07/07' in out
    assert '2026/07/01' not in out
    assert '2026/07/08' not in out


def test_format_attempt_history_single_attempt_yields_empty():
    attempts = ceb.split_install_attempts(
        _banner('install packages')
        + 'Collecting numpy==1.26.4 (from -r /tmp/r.txt (line 1))\n')
    assert len(attempts) == 1
    assert broken._format_attempt_history(attempts) == ''


# ---- advice prompt spec block ----

def test_format_spec_context_full_definition():
    out = broken._format_spec_context({
        'specPackageList': 'pandas==2.2.0\nnumpy==1.26.4',
        'mandatoryPackageList': 'pyarrow<12\npandas>=1.3',
        'specCondaEnvironment': 'dependencies:\n  - blas',
        'actualPackageList': 'numpy==1.26.4\npandas==2.2.0',
    })
    assert 'Requirements (specPackageList):' in out
    assert 'pandas==2.2.0' in out
    assert 'DSS mandatory base packages' in out and 'pyarrow<12' in out
    assert 'Conda spec:' in out and 'blas' in out
    assert 'Installed packages (pip freeze):' in out


def test_format_spec_context_degrades_on_blank_or_missing_keys():
    """R envs may lack every key — blank spec renders (empty), blank freeze
    renders (unavailable), optional sections disappear entirely."""
    out = broken._format_spec_context({'specPackageList': '  ', 'actualPackageList': None})
    assert '(empty)' in out
    assert '(unavailable)' in out
    assert 'mandatory' not in out.lower()
    assert 'Conda spec' not in out


def test_format_spec_context_truncates_over_cap():
    out = broken._format_spec_context({
        'specPackageList': 'x' * 5000,
        'actualPackageList': 'y' * 20000,
    })
    assert out.count('… (truncated)') == 2
    assert len(out) < 15000


def test_format_spec_context_preserves_spec_comments():
    """requirements.txt comments often explain the pin — the LLM should see them."""
    out = broken._format_spec_context({
        'specPackageList': '# needed for the forecasting project\nprophet==1.1.5',
    })
    assert '# needed for the forecasting project' in out
