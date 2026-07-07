"""Agent Tuning — versioned prompt overrides for the plugin agents.

The tuning store is a managed Dataiku dataset in the toolkit's OWN project
(local-scoped, same write path as Save Tables as Datasets): one column per
prompt type, one row per save. The newest row is the active version; agents
read its non-empty cells through GET /api/agents/tuning/prompts
(atk_agent_common.prompt_overrides fetches it at turn start with a short
kernel-side cache, falling back to the built-in templates on any failure).
Saving is a mutation → @advanced, like every other agent-surface mutation.
"""

import logging
import threading
import time
from datetime import datetime, timezone

import dataiku
from flask import Blueprint, jsonify, request

from adk_backend import agent_prompt_store as store
from adk_backend.clients import _local_toolkit_client, _local_toolkit_project
from adk_backend.chat.identity import resolve_chat_user
from adk_backend.utils import advanced, local_only
from atk_agent_common import prompts

bp = Blueprint('agent_tuning', __name__)
_LOGGER = logging.getLogger(__name__)

DATASET_NAME = 'agent_prompt_versions'
_ROWS_TTL_S = 30

_rows_cache = {'ts': 0.0, 'rows': None}
_write_lock = threading.Lock()


def _connection() -> str:
    """Target connection: the Save-Tables-as-Datasets connection when the
    admin configured one, else DSS's built-in filesystem_managed."""
    try:
        raw = _local_toolkit_client().get_plugin('admin-toolkit').get_settings().get_raw()
        config = raw.get('config', {}) if isinstance(raw, dict) else {}
        configured = (config.get('dataset_export_connection') or '').strip()
        return configured or 'filesystem_managed'
    except Exception:
        return 'filesystem_managed'


def _dataset_exists(project) -> bool:
    try:
        return DATASET_NAME in {d.get('name') for d in project.list_datasets()
                                if isinstance(d, dict)}
    except Exception:
        return False


def _read_rows(force: bool = False):
    """All version rows (oldest first), cached briefly — the agents poll
    /prompts once per turn and the page refetches on focus."""
    now = time.time()
    if not force and _rows_cache['rows'] is not None and now - _rows_cache['ts'] < _ROWS_TTL_S:
        return _rows_cache['rows']
    rows = []
    try:
        if _dataset_exists(_local_toolkit_project()):
            import pandas as pd  # noqa: F401 — dataiku returns a DataFrame
            df = dataiku.Dataset(DATASET_NAME).get_dataframe(infer_with_pandas=False)
            df = df.fillna('')
            rows = store.normalize_rows(df.to_dict('records'))
    except Exception as exc:
        _LOGGER.warning('agent tuning: could not read %s: %s', DATASET_NAME, str(exc)[:200])
    _rows_cache['ts'] = now
    _rows_cache['rows'] = rows
    return rows


def _state_payload(rows) -> dict:
    overrides = store.latest_overrides(rows)
    prompt_types = []
    for entry in prompts.prompt_type_registry():
        prompt_types.append({
            'key': entry['key'],
            'label': entry['label'],
            'description': entry['description'],
            'placeholders': entry['placeholders'],
            'default': entry['default'],
            'override': overrides.get(entry['key']) or None,
        })
    return {
        'available': True,
        'datasetName': DATASET_NAME,
        'project': dataiku.default_project_key(),
        'connection': _connection(),
        'promptTypes': prompt_types,
        'versions': store.versions_payload(rows),
    }


@bp.route('/api/agents/tuning')
@local_only
def api_agent_tuning_state():
    """Defaults + active overrides + version history for the tuning page."""
    try:
        return jsonify(_state_payload(_read_rows()))
    except Exception as exc:
        return jsonify({'available': False, 'reason': str(exc)[:300]}), 500


@bp.route('/api/agents/tuning/prompts')
@local_only
def api_agent_tuning_prompts():
    """Active overrides only — the agents' runtime read (prompt_overrides)."""
    try:
        return jsonify({'values': store.latest_overrides(_read_rows())})
    except Exception as exc:
        # The agents fall back to built-in templates on any failure; keep the
        # payload shape so their client never sees a hard error here.
        _LOGGER.warning('agent tuning prompts read failed: %s', str(exc)[:200])
        return jsonify({'values': {}})


@bp.route('/api/agents/tuning/save', methods=['POST'])
@local_only
@advanced
def api_agent_tuning_save():
    """Append one version row (full snapshot). Body: {values: {key: text},
    note?: str}. A cell equal to the built-in default is stored empty."""
    body = request.get_json(silent=True) or {}
    values = store.validate_values(body.get('values'))
    if values is None:
        return jsonify({'error': 'values must map known prompt types to strings '
                                 '(max %d chars each)' % store.MAX_PROMPT_CHARS}), 400
    note = str(body.get('note') or '')
    row = store.build_row(values, author=resolve_chat_user(), note=note,
                          saved_at=datetime.now(timezone.utc).isoformat())
    try:
        import pandas as pd

        with _write_lock:
            project = _local_toolkit_project()
            if not _dataset_exists(project):
                project.new_managed_dataset(DATASET_NAME).with_store_into(_connection()).create()
            rows = _read_rows(force=True) + [row]
            df = pd.DataFrame([[r.get(c, '') for c in store.ALL_COLUMNS] for r in rows],
                              columns=list(store.ALL_COLUMNS), dtype=str)
            dataiku.Dataset(DATASET_NAME).write_with_schema(df)
            _rows_cache['ts'] = 0.0  # next read sees the new version
        return jsonify(_state_payload(_read_rows(force=True)))
    except Exception as exc:
        _LOGGER.warning('agent tuning save failed: %s', exc)
        return jsonify({'error': str(exc)[:300]}), 500
