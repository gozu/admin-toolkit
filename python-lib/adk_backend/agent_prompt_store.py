"""Pure logic of the Agent Tuning prompt-version store.

The store is a managed Dataiku dataset: one COLUMN per prompt type (plus
saved_at / author / note metadata columns), one ROW per save — a full
snapshot of the overrides at that moment in time. An empty cell means "use
the built-in default". The newest row is the active version; restoring an
old version appends a copy, so history stays immutable.

This module is dataiku-free on purpose: the routes layer feeds it plain row
dicts and it is unit-tested directly (tests/backend/test_agent_prompt_store).
"""

from typing import Any, Dict, List, Optional

from atk_agent_common import prompts

META_COLUMNS = ('saved_at', 'author', 'note')
PROMPT_COLUMNS = tuple(prompts.PROMPT_TYPE_KEYS)
ALL_COLUMNS = META_COLUMNS + PROMPT_COLUMNS

# One prompt cell should comfortably hold the longest built-in template with
# admin additions; anything past this is almost certainly a paste accident.
MAX_PROMPT_CHARS = 60000
MAX_NOTE_CHARS = 300


def normalize_rows(records: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Coerce raw dataset records to full string rows, oldest first."""
    rows = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        row = {col: str(rec.get(col) or '') for col in ALL_COLUMNS}
        rows.append(row)
    rows.sort(key=lambda r: r['saved_at'])
    return rows


def latest_overrides(rows: List[Dict[str, str]]) -> Dict[str, str]:
    """Non-empty prompt cells of the newest row — the active overrides."""
    if not rows:
        return {}
    newest = rows[-1]
    return {col: newest[col] for col in PROMPT_COLUMNS if newest.get(col, '').strip()}


def versions_payload(rows: List[Dict[str, str]], limit: int = 20) -> List[Dict[str, Any]]:
    """Newest-first version list for the UI, including full values so a
    restore can load any listed version straight into the editors."""
    out = []
    for row in reversed(rows[-limit:] if limit else rows):
        values = {col: row.get(col, '') for col in PROMPT_COLUMNS}
        out.append({
            'savedAt': row.get('saved_at', ''),
            'author': row.get('author', ''),
            'note': row.get('note', ''),
            'customized': [col for col in PROMPT_COLUMNS if values[col].strip()],
            'values': values,
        })
    return out


def validate_values(raw: Any) -> Optional[Dict[str, str]]:
    """Clean a POSTed values dict: known keys only, strings only, size-capped.
    Returns None when the payload is not usable at all."""
    if not isinstance(raw, dict):
        return None
    values = {}
    for col in PROMPT_COLUMNS:
        value = raw.get(col, '')
        if value is None:
            value = ''
        if not isinstance(value, str):
            return None
        if len(value) > MAX_PROMPT_CHARS:
            return None
        values[col] = value
    return values


def build_row(values: Dict[str, str], author: str, note: str, saved_at: str) -> Dict[str, str]:
    """One full dataset row for a save. Cells equal to the built-in default
    are stored as '' (meaning "default") so history reads honestly: a cell is
    non-empty exactly when that prompt type is customized."""
    defaults = {entry['key']: entry['default'] for entry in prompts.prompt_type_registry()}
    row = {
        'saved_at': saved_at,
        'author': (author or '')[:120],
        'note': (note or '')[:MAX_NOTE_CHARS],
    }
    for col in PROMPT_COLUMNS:
        value = values.get(col, '')
        row[col] = '' if value.strip() == defaults[col].strip() else value
    return row
