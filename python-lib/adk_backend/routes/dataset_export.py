"""Save Tables as Datasets — persist UI tables as managed datasets in the
toolkit's own project (local-scoped, like DB Health)."""
import re
from typing import Any, List

import dataiku
from flask import Blueprint, jsonify, request

from adk_backend.clients import _local_toolkit_client, _local_toolkit_project
from adk_backend.utils import local_only

bp = Blueprint('dataset_export', __name__)


# ── Save Tables as Datasets ─────────────────────────────────────────────────
# Persist the UI's rendered tables as managed Dataiku datasets, one per table,
# in the toolkit's OWN project on a connection the admin picks in plugin
# settings. Local-scoped (like DB Health): writes go through the in-process
# `dataiku` package on the local DSS, so the host header is intentionally
# ignored here. Empty connection setting ⇒ feature disabled.

_DATASET_NAME_BADCHARS = re.compile(r'[^A-Za-z0-9_]+')


def _sanitize_dss_name(raw: Any, fallback: str) -> str:
    """Coerce an arbitrary UI label into a valid DSS dataset/column name:
    keep [A-Za-z0-9_], collapse other runs to '_', strip leading/trailing '_',
    and prefix with '_' if it starts with a digit or ends up empty."""
    s = _DATASET_NAME_BADCHARS.sub('_', str(raw or '')).strip('_')
    if not s:
        s = fallback
    if s[0].isdigit():
        s = '_' + s
    return s


def _dedupe_dss_names(names: List[str]) -> List[str]:
    """Append _2, _3, … to duplicate names (case-insensitive), order-preserving
    and collision-aware (the suffixed form is itself checked for uniqueness)."""
    used = set()
    out = []
    for name in names:
        candidate = name
        n = 1
        while candidate.lower() in used:
            n += 1
            candidate = '%s_%d' % (name, n)
        used.add(candidate.lower())
        out.append(candidate)
    return out


def _dataset_export_connection() -> str:
    """The configured target connection from LOCAL plugin settings ('' if unset).

    Unified `toolkit_storage_connection` wins; legacy per-feature key is the
    fallback (new-key-first)."""
    raw = _local_toolkit_client().get_plugin('admin-toolkit').get_settings().get_raw()
    config = raw.get('config', {}) if isinstance(raw, dict) else {}
    return (config.get('toolkit_storage_connection')
            or config.get('dataset_export_connection') or '').strip()


@bp.route('/api/tools/dataset-export/config')
@local_only
def api_dataset_export_config():
    """Report whether the feature is enabled (drives toolbar button state)."""
    try:
        return jsonify({
            'configuredConnection': _dataset_export_connection(),
            'project': dataiku.default_project_key(),
        })
    except Exception as exc:
        return jsonify({'error': str(exc)[:200]}), 500


@bp.route('/api/tools/dataset-export/save', methods=['POST'])
@local_only
def api_dataset_export_save():
    """Save each posted UI table as a managed dataset in the toolkit's project.
    Overwrites in place on repeat; every column is string-typed."""
    try:
        connection = _dataset_export_connection()
        if not connection:
            return jsonify({
                'error': 'No connection is configured for Save Tables as Datasets. '
                         'An administrator must select one in the Admin Toolkit plugin settings.',
            }), 400

        body = request.get_json(silent=True) or {}
        tables = body.get('tables') or []
        if not isinstance(tables, list) or not tables:
            return jsonify({'error': 'No tables provided.'}), 400

        import pandas as pd

        project = _local_toolkit_project()
        project_key = dataiku.default_project_key()

        # Backend is the naming authority: sanitize + dedupe across all tables.
        raw_names = [t.get('name') if isinstance(t, dict) else '' for t in tables]
        sane_names = [_sanitize_dss_name(n, 'table_%d' % (i + 1)) for i, n in enumerate(raw_names)]
        dataset_names = _dedupe_dss_names(sane_names)

        try:
            existing = {d.get('name') for d in project.list_datasets() if isinstance(d, dict)}
        except Exception:
            existing = set()

        results = []
        for i, table in enumerate(tables):
            ds_name = dataset_names[i]
            ui_name = raw_names[i] if isinstance(raw_names[i], str) else ''
            try:
                cols_raw = (table.get('columns') if isinstance(table, dict) else None) or []
                rows_raw = (table.get('rows') if isinstance(table, dict) else None) or []
                col_names = _dedupe_dss_names(
                    [_sanitize_dss_name(c, 'col_%d' % (j + 1)) for j, c in enumerate(cols_raw)]
                )
                if not col_names:
                    results.append({'name': ui_name, 'datasetName': ds_name,
                                    'status': 'error', 'rows': 0, 'error': 'Table has no columns'})
                    continue

                # Normalize every row to the column count; all cells are strings.
                width = len(col_names)
                norm_rows = []
                for r in rows_raw:
                    cells = list(r) if isinstance(r, (list, tuple)) else [r]
                    cells = [('' if c is None else str(c)) for c in cells[:width]]
                    cells += [''] * (width - len(cells))
                    norm_rows.append(cells)

                already = ds_name in existing
                if not already:
                    project.new_managed_dataset(ds_name).with_store_into(connection).create()
                    existing.add(ds_name)

                df = pd.DataFrame(norm_rows, columns=col_names, dtype=str)
                dataiku.Dataset(ds_name).write_with_schema(df)

                results.append({'name': ui_name, 'datasetName': ds_name,
                                'status': 'overwritten' if already else 'created',
                                'rows': len(norm_rows)})
            except Exception as exc:
                results.append({'name': ui_name, 'datasetName': ds_name,
                                'status': 'error', 'rows': 0, 'error': str(exc)[:300]})

        return jsonify({'project': project_key, 'connection': connection, 'results': results})
    except Exception as exc:
        return jsonify({'error': str(exc)[:300]}), 500
