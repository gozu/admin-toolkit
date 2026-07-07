"""Unit tests for the Agent Tuning prompt-version store (pure logic)."""

from adk_backend import agent_prompt_store as store
from atk_agent_common import prompts


def _row(saved_at, **cells):
    row = {col: '' for col in store.ALL_COLUMNS}
    row['saved_at'] = saved_at
    row.update(cells)
    return row


# ---- normalize_rows ----

def test_normalize_rows_sorts_and_fills_missing_columns():
    rows = store.normalize_rows([
        {'saved_at': '2026-07-02T10:00:00', 'triage_system_prompt': 'v2'},
        {'saved_at': '2026-07-01T10:00:00', 'note': 'first'},
        'garbage',
        None,
    ])
    assert [r['saved_at'] for r in rows] == ['2026-07-01T10:00:00', '2026-07-02T10:00:00']
    for row in rows:
        assert set(row) == set(store.ALL_COLUMNS)
    assert rows[1]['triage_system_prompt'] == 'v2'
    assert rows[0]['note'] == 'first'


def test_normalize_rows_stringifies_non_string_cells():
    rows = store.normalize_rows([{'saved_at': '2026-01-01', 'note': 42,
                                  'severity_rubric': None}])
    assert rows[0]['note'] == '42'
    assert rows[0]['severity_rubric'] == ''


def test_normalize_rows_fills_llm_override_into_pre_upgrade_rows():
    """Rows written before the LLM picker existed have no llm_override column;
    normalize_rows must fill it with '' (no override) — zero migration."""
    rows = store.normalize_rows([{'saved_at': '2026-07-01', 'note': 'old row'}])
    assert rows[0]['llm_override'] == ''
    assert store.latest_settings(rows) == {'llm_override': ''}


# ---- latest_overrides ----

def test_latest_overrides_takes_newest_row_non_empty_cells_only():
    rows = store.normalize_rows([
        _row('2026-07-01', triage_system_prompt='old triage', severity_rubric='old rubric'),
        _row('2026-07-02', triage_system_prompt='new triage'),
    ])
    overrides = store.latest_overrides(rows)
    # Newest row wins wholesale: its empty severity_rubric means "default".
    assert overrides == {'triage_system_prompt': 'new triage'}


def test_latest_overrides_empty_store():
    assert store.latest_overrides([]) == {}


# ---- latest_settings ----

def test_latest_settings_newest_row_wins():
    rows = store.normalize_rows([
        _row('2026-07-01', llm_override='custom:old'),
        _row('2026-07-02', llm_override='  custom:new  '),
    ])
    assert store.latest_settings(rows) == {'llm_override': 'custom:new'}
    # A newest row with an empty cell clears the override.
    rows = store.normalize_rows(rows + [_row('2026-07-03')])
    assert store.latest_settings(rows) == {'llm_override': ''}


def test_latest_settings_empty_store():
    assert store.latest_settings([]) == {'llm_override': ''}


# ---- versions_payload ----

def test_versions_payload_newest_first_with_customized_keys():
    rows = store.normalize_rows([
        _row('2026-07-01', author='a', note='first', scoping_system_prompt='S',
             llm_override='custom:gpt'),
        _row('2026-07-02', author='b', note='second'),
    ])
    versions = store.versions_payload(rows)
    assert [v['savedAt'] for v in versions] == ['2026-07-02', '2026-07-01']
    assert versions[0]['customized'] == []
    assert versions[0]['llmOverride'] == ''
    assert versions[1]['customized'] == ['scoping_system_prompt']
    assert versions[1]['values']['scoping_system_prompt'] == 'S'
    # llm_override rides along per version so a restore round-trips the model.
    assert versions[1]['llmOverride'] == 'custom:gpt'


def test_versions_payload_respects_limit():
    rows = store.normalize_rows([_row('2026-07-%02d' % d) for d in range(1, 30)])
    versions = store.versions_payload(rows, limit=5)
    assert len(versions) == 5
    assert versions[0]['savedAt'] == '2026-07-29'


# ---- validate_values ----

def test_validate_values_accepts_full_and_partial_dicts():
    values = store.validate_values({'triage_system_prompt': 'x'})
    assert values is not None
    assert values['triage_system_prompt'] == 'x'
    assert all(values[c] == '' for c in store.PROMPT_COLUMNS if c != 'triage_system_prompt')


def test_validate_values_rejects_non_dict_and_non_string_and_oversized():
    assert store.validate_values(None) is None
    assert store.validate_values([]) is None
    assert store.validate_values({'triage_system_prompt': 42}) is None
    assert store.validate_values({'triage_system_prompt': 'x' * (store.MAX_PROMPT_CHARS + 1)}) is None


def test_validate_values_ignores_unknown_keys():
    values = store.validate_values({'not_a_prompt': 'x'})
    assert values is not None
    assert 'not_a_prompt' not in values


# ---- validate_settings ----

def test_validate_settings_absent_means_no_overrides():
    """Pre-picker clients POST no settings key — that must stay a valid save."""
    assert store.validate_settings(None) == {'llm_override': ''}


def test_validate_settings_accepts_strings_and_strips():
    assert store.validate_settings({'llm_override': '  custom:x  '}) == {'llm_override': 'custom:x'}
    assert store.validate_settings({}) == {'llm_override': ''}
    assert store.validate_settings({'llm_override': None}) == {'llm_override': ''}


def test_validate_settings_rejects_malformed():
    assert store.validate_settings([]) is None
    assert store.validate_settings('custom:x') is None
    assert store.validate_settings({'llm_override': 42}) is None
    assert store.validate_settings({'llm_override': 'x' * (store.MAX_SETTING_CHARS + 1)}) is None


def test_validate_settings_ignores_unknown_keys():
    settings = store.validate_settings({'not_a_setting': 'x'})
    assert settings is not None
    assert 'not_a_setting' not in settings


# ---- build_row ----

def test_build_row_stores_default_valued_cells_as_empty():
    values = {c: '' for c in store.PROMPT_COLUMNS}
    values['triage_system_prompt'] = prompts.TRIAGE_SYSTEM_PROMPT  # verbatim default
    values['scoping_system_prompt'] = 'custom scoping'
    row = store.build_row(values, {'llm_override': 'custom:claude'},
                          author='alex', note='n', saved_at='2026-07-06T00:00:00')
    assert row['triage_system_prompt'] == ''      # default → honest empty cell
    assert row['scoping_system_prompt'] == 'custom scoping'
    assert row['llm_override'] == 'custom:claude'
    assert row['author'] == 'alex'
    assert row['saved_at'] == '2026-07-06T00:00:00'


def test_build_row_clips_note_and_author():
    row = store.build_row({}, {}, author='a' * 500, note='n' * 500, saved_at='t')
    assert len(row['author']) == 120
    assert len(row['note']) == store.MAX_NOTE_CHARS
    assert row['llm_override'] == ''


def test_settings_round_trip_through_rows():
    """Save → normalize → latest_settings/versions_payload round-trips the
    override, and an old-code save (no settings) clears it — the documented
    downgrade caveat."""
    row1 = store.build_row({}, {'llm_override': 'custom:claude'},
                           author='a', note='set', saved_at='2026-07-01')
    row2 = store.build_row({}, store.validate_settings(None),
                           author='a', note='old client', saved_at='2026-07-02')
    rows = store.normalize_rows([row1, row2])
    assert store.versions_payload(rows)[1]['llmOverride'] == 'custom:claude'
    assert store.latest_settings(rows) == {'llm_override': ''}


# ---- registry sanity ----

def test_prompt_type_registry_matches_columns():
    registry = prompts.prompt_type_registry()
    assert [e['key'] for e in registry] == list(store.PROMPT_COLUMNS)
    for entry in registry:
        assert entry['default'].strip()
        for placeholder in entry['placeholders']:
            assert placeholder in entry['default']
