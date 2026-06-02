"""
Shared compare registry and diff helpers for the exhaustive trends comparison.

Used by both TrackingDB (SQLite) and SQLTrackingDB (SQL) to implement
compare_runs_full() and get_compare_dataset_detail().
"""

import json
from typing import Any, Dict, List, Optional, Tuple

# ── Support modes ──
SUPPORT_FULL = 'full'
SUPPORT_LIFECYCLE = 'lifecycle'
SUPPORT_CURRENT_ONLY = 'current_only'

# ── Rendering kinds ──
KIND_SCALAR = 'scalar'
KIND_JSON = 'json'
KIND_TEXT = 'text'
KIND_KEYED_TABLE = 'keyed_table'
KIND_INTERVAL_EVENTS = 'interval_events'
KIND_METADATA = 'metadata'

# ── Categories (display order) ──
CAT_RUN_SUMMARY = 'run_summary'
CAT_HEALTH_METRICS = 'health_metrics'
CAT_SNAPSHOT_ENTITIES = 'snapshot_entities'
CAT_LIFECYCLE = 'lifecycle'
CAT_METADATA = 'metadata'

CATEGORY_ORDER = [
    CAT_RUN_SUMMARY,
    CAT_HEALTH_METRICS,
    CAT_SNAPSHOT_ENTITIES,
    CAT_LIFECYCLE,
    CAT_METADATA,
]

CATEGORY_LABELS = {
    CAT_RUN_SUMMARY: 'Run Summary',
    CAT_HEALTH_METRICS: 'Health Metrics',
    CAT_SNAPSHOT_ENTITIES: 'Snapshot Entities',
    CAT_LIFECYCLE: 'Lifecycle / Interval Data',
    CAT_METADATA: 'Metadata / Admin',
}

# ── JSON blob columns in run_health_metrics ──
JSON_COLUMNS = frozenset({
    'plugins_json', 'connections_json', 'filesystem_mounts_json',
    'user_profile_stats_json', 'general_settings_json',
    'code_envs_json', 'log_errors_json', 'project_footprint_json',
    'db_health_json',
})

# ── Text blob columns ──
TEXT_COLUMNS = frozenset({'java_memory_raw'})


# =============================================================================
# DATASET REGISTRY — Steps 4-16
# =============================================================================

DATASET_REGISTRY: List[Dict[str, Any]] = [

    # ── Step 4: Scalar run-level datasets ──

    {
        'dataset_id': 'runs',
        'label': 'Run Summary',
        'category': CAT_RUN_SUMMARY,
        'kind': KIND_SCALAR,
        'support': SUPPORT_FULL,
        'table': 'runs',
        'key_fields': ['run_id'],
        'min_schema_version': 1,
        'columns': [
            'run_id', 'run_at', 'instance_id', 'dss_version', 'python_version',
            'health_score', 'health_status', 'user_count', 'enabled_user_count',
            'project_count', 'code_env_count', 'plugin_count', 'connection_count',
            'cluster_count', 'coverage_status', 'notes',
        ],
    },
    {
        'dataset_id': 'run_health_metrics',
        'label': 'Health Metrics',
        'category': CAT_HEALTH_METRICS,
        'kind': KIND_SCALAR,
        'support': SUPPORT_FULL,
        'table': 'run_health_metrics',
        'key_fields': ['run_id'],
        'min_schema_version': 1,
        'columns': [
            'cpu_cores', 'memory_total_mb', 'memory_used_mb', 'memory_available_mb',
            'swap_total_mb', 'swap_used_mb', 'max_filesystem_pct', 'max_filesystem_mount',
            'backend_heap_mb', 'jek_heap_mb', 'fek_heap_mb', 'open_files_limit',
            'version_currency_score', 'system_capacity_score', 'configuration_score',
            'security_isolation_score',
            'license_named_users_pct', 'license_concurrent_users_pct',
            'license_projects_pct', 'license_connections_pct', 'license_expiry_date',
            # V5 JSON blobs
            'plugins_json', 'connections_json', 'filesystem_mounts_json',
            'user_profile_stats_json',
            # V5 scalars
            'os_info', 'spark_version',
            # V8 additions
            'general_settings_json', 'java_memory_raw',
            'code_envs_json', 'log_errors_json', 'project_footprint_json',
            # V9 additions
            'db_health_json',
        ],
    },

    # ── Step 5: Keyed snapshot tables (V1) ──

    {
        'dataset_id': 'run_sections',
        'label': 'Run Sections',
        'category': CAT_SNAPSHOT_ENTITIES,
        'kind': KIND_KEYED_TABLE,
        'support': SUPPORT_FULL,
        'table': 'run_sections',
        'key_fields': ['section_key'],
        'min_schema_version': 1,
        'columns': ['section_key', 'status', 'is_complete', 'error_message'],
    },
    {
        'dataset_id': 'run_campaign_summaries',
        'label': 'Campaign Summaries',
        'category': CAT_SNAPSHOT_ENTITIES,
        'kind': KIND_KEYED_TABLE,
        'support': SUPPORT_FULL,
        'table': 'run_campaign_summaries',
        'key_fields': ['campaign_id'],
        'min_schema_version': 1,
        'columns': ['campaign_id', 'finding_count', 'recipient_count'],
    },

    # ── Step 6: V6 snapshot tables ──

    {
        'dataset_id': 'user_snapshots',
        'label': 'Users',
        'category': CAT_SNAPSHOT_ENTITIES,
        'kind': KIND_KEYED_TABLE,
        'support': SUPPORT_FULL,
        'table': 'user_snapshots',
        'key_fields': ['login'],
        'min_schema_version': 6,
        'columns': ['login', 'display_name', 'email', 'user_profile', 'enabled'],
    },
    {
        'dataset_id': 'project_snapshots',
        'label': 'Projects',
        'category': CAT_SNAPSHOT_ENTITIES,
        'kind': KIND_KEYED_TABLE,
        'support': SUPPORT_FULL,
        'table': 'project_snapshots',
        'key_fields': ['project_key'],
        'min_schema_version': 6,
        'columns': ['project_key', 'name', 'owner_login'],
    },

    # ── Step 7: V6 snapshot tables (cont.) ──

    {
        'dataset_id': 'run_plugins',
        'label': 'Plugins',
        'category': CAT_SNAPSHOT_ENTITIES,
        'kind': KIND_KEYED_TABLE,
        'support': SUPPORT_FULL,
        'table': 'run_plugins',
        'key_fields': ['plugin_id'],
        'min_schema_version': 6,
        'columns': ['plugin_id', 'label', 'version', 'is_dev'],
    },
    {
        'dataset_id': 'run_connections',
        'label': 'Connections',
        'category': CAT_SNAPSHOT_ENTITIES,
        'kind': KIND_KEYED_TABLE,
        'support': SUPPORT_FULL,
        'table': 'run_connections',
        'key_fields': ['connection_name'],
        'min_schema_version': 6,
        'columns': ['connection_name', 'connection_type'],
    },

    # ── Step 8: V7 snapshot tables ──

    {
        'dataset_id': 'run_datasets',
        'label': 'Datasets',
        'category': CAT_SNAPSHOT_ENTITIES,
        'kind': KIND_KEYED_TABLE,
        'support': SUPPORT_FULL,
        'table': 'run_datasets',
        'key_fields': ['project_key', 'dataset_name'],
        'min_schema_version': 7,
        'columns': ['project_key', 'dataset_name', 'dataset_type', 'connection_name'],
    },
    {
        'dataset_id': 'run_recipes',
        'label': 'Recipes',
        'category': CAT_SNAPSHOT_ENTITIES,
        'kind': KIND_KEYED_TABLE,
        'support': SUPPORT_FULL,
        'table': 'run_recipes',
        'key_fields': ['project_key', 'recipe_name'],
        'min_schema_version': 7,
        'columns': ['project_key', 'recipe_name', 'recipe_type'],
    },

    # ── Step 9: V7 GenAI snapshot tables ──

    {
        'dataset_id': 'run_llms',
        'label': 'LLMs',
        'category': CAT_SNAPSHOT_ENTITIES,
        'kind': KIND_KEYED_TABLE,
        'support': SUPPORT_FULL,
        'table': 'run_llms',
        'key_fields': ['llm_id'],
        'min_schema_version': 7,
        'columns': ['llm_id', 'llm_type', 'friendly_name'],
    },
    {
        'dataset_id': 'run_agents',
        'label': 'Agents',
        'category': CAT_SNAPSHOT_ENTITIES,
        'kind': KIND_KEYED_TABLE,
        'support': SUPPORT_FULL,
        'table': 'run_agents',
        'key_fields': ['project_key', 'agent_id'],
        'min_schema_version': 7,
        'columns': ['project_key', 'agent_id', 'agent_name'],
    },
    {
        'dataset_id': 'run_agent_tools',
        'label': 'Agent Tools',
        'category': CAT_SNAPSHOT_ENTITIES,
        'kind': KIND_KEYED_TABLE,
        'support': SUPPORT_FULL,
        'table': 'run_agent_tools',
        'key_fields': ['project_key', 'tool_id'],
        'min_schema_version': 7,
        'columns': ['project_key', 'tool_id', 'tool_type'],
    },
    {
        'dataset_id': 'run_knowledge_banks',
        'label': 'Knowledge Banks',
        'category': CAT_SNAPSHOT_ENTITIES,
        'kind': KIND_KEYED_TABLE,
        'support': SUPPORT_FULL,
        'table': 'run_knowledge_banks',
        'key_fields': ['project_key', 'kb_id'],
        'min_schema_version': 7,
        'columns': ['project_key', 'kb_id', 'kb_name'],
    },

    # ── Step 10: V7 git commits ──

    {
        'dataset_id': 'run_git_commits',
        'label': 'Git Commits',
        'category': CAT_SNAPSHOT_ENTITIES,
        'kind': KIND_KEYED_TABLE,
        'support': SUPPORT_FULL,
        'table': 'run_git_commits',
        'key_fields': ['project_key', 'commit_hash'],
        'min_schema_version': 7,
        'columns': ['project_key', 'commit_hash', 'author', 'committed_at'],
    },

    # ── Step 10b: V9 connection health + DB health ──

    {
        'dataset_id': 'run_connection_health',
        'label': 'Connection Health',
        'category': CAT_SNAPSHOT_ENTITIES,
        'kind': KIND_KEYED_TABLE,
        'support': SUPPORT_FULL,
        'table': 'run_connection_health',
        'key_fields': ['connection_name'],
        'min_schema_version': 9,
        'columns': ['connection_name', 'connection_type', 'status', 'error_category', 'error_message'],
    },
    {
        'dataset_id': 'run_db_health',
        'label': 'DB Health Tables',
        'category': CAT_SNAPSHOT_ENTITIES,
        'kind': KIND_KEYED_TABLE,
        'support': SUPPORT_FULL,
        'table': 'run_db_health',
        'key_fields': ['table_name'],
        'min_schema_version': 9,
        'columns': ['table_name', 'schema_name', 'table_size', 'row_count', 'dead_tuples',
                     'bloat_pct', 'last_vacuum', 'last_autovacuum', 'last_analyze'],
    },
    {
        'dataset_id': 'run_container_execs',
        'label': 'Container Execs',
        'category': CAT_SNAPSHOT_ENTITIES,
        'kind': KIND_KEYED_TABLE,
        'support': SUPPORT_FULL,
        'table': 'run_container_execs',
        'key_fields': ['project_key', 'object_type', 'object_id'],
        'min_schema_version': 10,
        'columns': [
            'project_key', 'object_type', 'object_id', 'container_conf',
            'effective_container_conf', 'override_level',
        ],
    },

    # ── Step 11: Findings ──

    {
        'dataset_id': 'findings',
        'label': 'Findings',
        'category': CAT_SNAPSHOT_ENTITIES,
        'kind': KIND_KEYED_TABLE,
        'support': SUPPORT_FULL,
        'table': 'findings',
        'key_fields': ['campaign_id', 'entity_type', 'entity_key'],
        'min_schema_version': 1,
        'columns': [
            'campaign_id', 'entity_type', 'entity_key', 'entity_name',
            'owner_login', 'owner_email', 'metrics_json',
        ],
    },

    # ── Steps 12-13: Lifecycle outreach tables ──

    {
        'dataset_id': 'issues',
        'label': 'Issues',
        'category': CAT_LIFECYCLE,
        'kind': KIND_INTERVAL_EVENTS,
        'support': SUPPORT_LIFECYCLE,
        'table': 'issues',
        'key_fields': ['campaign_id', 'entity_type', 'entity_key'],
        'min_schema_version': 1,
        'columns': [
            'issue_id', 'instance_id', 'campaign_id', 'entity_type', 'entity_key',
            'entity_name', 'owner_login', 'owner_email', 'status',
            'first_detected_run', 'first_detected_at',
            'last_detected_run', 'last_detected_at',
            'resolved_run', 'resolved_at', 'resolution_reason',
            'times_regressed', 'times_emailed', 'last_emailed_at',
            'metrics_json',
        ],
    },
    {
        'dataset_id': 'outreach_emails',
        'label': 'Outreach Emails',
        'category': CAT_LIFECYCLE,
        'kind': KIND_INTERVAL_EVENTS,
        'support': SUPPORT_LIFECYCLE,
        'table': 'outreach_emails',
        'key_fields': ['email_id'],
        'min_schema_version': 1,
        'columns': [
            'email_id', 'run_id', 'campaign_id', 'recipient_login',
            'recipient_email', 'sent_at', 'status', 'error_message',
            'subject', 'channel_id', 'sent_by',
        ],
    },

    # ── Step 14: Known entities (lifecycle, as-of) ──

    {
        'dataset_id': 'known_users',
        'label': 'Known Users',
        'category': CAT_LIFECYCLE,
        'kind': KIND_INTERVAL_EVENTS,
        'support': SUPPORT_LIFECYCLE,
        'table': 'known_users',
        'key_fields': ['login'],
        'min_schema_version': 1,
        'columns': [
            'login', 'email', 'display_name', 'user_profile', 'enabled',
            'first_seen_run', 'last_seen_run',
        ],
    },
    {
        'dataset_id': 'known_projects',
        'label': 'Known Projects',
        'category': CAT_LIFECYCLE,
        'kind': KIND_INTERVAL_EVENTS,
        'support': SUPPORT_LIFECYCLE,
        'table': 'known_projects',
        'key_fields': ['project_key'],
        'min_schema_version': 1,
        'columns': [
            'project_key', 'name', 'owner_login',
            'first_seen_run', 'last_seen_run',
        ],
    },

    # ── Step 16: Current-only metadata ──

    {
        'dataset_id': 'instances',
        'label': 'Instance Registry',
        'category': CAT_METADATA,
        'kind': KIND_METADATA,
        'support': SUPPORT_CURRENT_ONLY,
        'table': 'instances',
        'key_fields': ['instance_id'],
        'min_schema_version': 1,
        'columns': [
            'instance_id', 'instance_url', 'install_id', 'node_id',
            'company_name', 'first_seen_at', 'last_seen_at',
        ],
    },
    {
        'dataset_id': 'schema_version',
        'label': 'Schema Version',
        'category': CAT_METADATA,
        'kind': KIND_METADATA,
        'support': SUPPORT_CURRENT_ONLY,
        'table': 'schema_version',
        'key_fields': ['version'],
        'min_schema_version': 1,
        'columns': ['version', 'applied_at'],
    },
    {
        'dataset_id': 'campaign_settings',
        'label': 'Campaign Settings',
        'category': CAT_METADATA,
        'kind': KIND_METADATA,
        'support': SUPPORT_CURRENT_ONLY,
        'table': 'campaign_settings',
        'key_fields': ['campaign_id'],
        'min_schema_version': 3,
        'columns': ['campaign_id', 'enabled', 'updated_at'],
    },
    {
        'dataset_id': 'campaign_exemptions',
        'label': 'Campaign Exemptions',
        'category': CAT_METADATA,
        'kind': KIND_METADATA,
        'support': SUPPORT_CURRENT_ONLY,
        'table': 'campaign_exemptions',
        'key_fields': ['exemption_id'],
        'min_schema_version': 4,
        'columns': [
            'exemption_id', 'campaign_id', 'entity_type', 'entity_key',
            'reason', 'created_at',
        ],
    },
]

# Quick lookup by dataset_id
REGISTRY_BY_ID: Dict[str, Dict[str, Any]] = {d['dataset_id']: d for d in DATASET_REGISTRY}

# Lifecycle filter mapping: generic filter → actual _lifecycle values
LIFECYCLE_FILTER_MAP: Dict[str, Dict[str, list]] = {
}


def map_filter_to_lifecycle(dataset_id: str, change_type: str):
    """Map generic filter to lifecycle _lifecycle values. Returns None if no mapping (use generic)."""
    ds_map = LIFECYCLE_FILTER_MAP.get(dataset_id)
    if ds_map is None:
        return None
    return ds_map.get(change_type, [])


# =============================================================================
# AVAILABILITY — Step 33
# =============================================================================

def check_run_has_v6_data(run_row_counts: Dict[str, int]) -> bool:
    """Check if a run has V6 snapshot data by looking at row counts."""
    for t in ('user_snapshots', 'project_snapshots', 'run_plugins', 'run_connections'):
        if run_row_counts.get(t, 0) > 0:
            return True
    return False


def check_run_has_v7_data(run_row_counts: Dict[str, int]) -> bool:
    """Check if a run has V7 snapshot data by looking at row counts."""
    for t in ('run_datasets', 'run_recipes', 'run_llms', 'run_agents',
              'run_agent_tools', 'run_knowledge_banks', 'run_git_commits'):
        if run_row_counts.get(t, 0) > 0:
            return True
    return False


def dataset_available_for_run(
    entry: Dict[str, Any],
    run: Dict[str, Any],
    run_row_counts: Dict[str, int],
    has_v6: bool,
    has_v7: bool,
    first_v6_run_id: int = None,
    first_v7_run_id: int = None,
) -> bool:
    """Determine whether a dataset is available for a given run."""
    sv = entry['min_schema_version']
    kind = entry['kind']

    # Current-only metadata is always "available" (it's not per-run)
    if entry['support'] == SUPPORT_CURRENT_ONLY:
        return True

    # V1 tables (runs, run_health_metrics, run_sections, etc.) always available
    if sv <= 1:
        return True

    # V3/V4 tables (campaign_settings, campaign_exemptions) are global, not per-run
    if sv <= 4:
        return True

    # V6 snapshot tables
    if sv == 6:
        if has_v6:
            return True
        if first_v6_run_id is not None:
            return run.get('run_id', 0) >= first_v6_run_id
        return False

    # V7 snapshot tables
    if sv == 7:
        if has_v7:
            return True
        if first_v7_run_id is not None:
            return run.get('run_id', 0) >= first_v7_run_id
        return False

    # V8 columns on run_health_metrics: always available (just may be NULL)
    if sv == 8:
        return True

    return True


# =============================================================================
# DIFF HELPERS — Steps 17-22
# =============================================================================

# ── Step 17: Scalar field-by-field comparison ──

def _safe_json_parse(val: Any) -> Any:
    """Parse JSON string or return as-is."""
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return val


def compare_scalar(
    row1: Optional[Dict[str, Any]],
    row2: Optional[Dict[str, Any]],
    columns: List[str],
) -> Tuple[List[Dict[str, Any]], int, int]:
    """Compare two scalar rows field by field.

    Returns (fields, changed_count, unchanged_count).
    """
    fields = []
    changed = 0
    unchanged = 0

    for col in columns:
        v1 = row1.get(col) if row1 else None
        v2 = row2.get(col) if row2 else None

        field: Dict[str, Any] = {'field': col, 'run1Value': v1, 'run2Value': v2}

        if col in JSON_COLUMNS:
            field['kind'] = 'json'
            p1 = _safe_json_parse(v1)
            p2 = _safe_json_parse(v2)
            field['run1Value'] = p1
            field['run2Value'] = p2
            same = json.dumps(p1, sort_keys=True, default=str) == json.dumps(p2, sort_keys=True, default=str)
            field['status'] = 'same' if same else 'changed'
        elif col in TEXT_COLUMNS:
            field['kind'] = 'text'
            field['status'] = 'same' if v1 == v2 else 'changed'
        elif _is_numeric(v1) or _is_numeric(v2):
            field['kind'] = 'numeric'
            n1 = _to_num(v1)
            n2 = _to_num(v2)
            if n1 is not None and n2 is not None:
                field['delta'] = round(n1 - n2, 4)
                if n2 != 0:
                    field['pctDelta'] = round((n1 - n2) / n2 * 100, 2)
            field['status'] = 'same' if v1 == v2 else 'changed'
        else:
            field['kind'] = 'string'
            field['status'] = 'same' if v1 == v2 else 'changed'

        if field['status'] == 'changed':
            changed += 1
        else:
            unchanged += 1
        fields.append(field)

    return fields, changed, unchanged


def _is_numeric(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def _to_num(val: Any) -> Optional[float]:
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    return None


# ── Step 18: Keyed table diffing ──

def _row_key(row: Dict[str, Any], key_fields: List[str]) -> tuple:
    """Extract natural key tuple from a row dict."""
    return tuple(str(row.get(k, '')) for k in key_fields)


def diff_keyed_tables(
    rows1: List[Dict[str, Any]],
    rows2: List[Dict[str, Any]],
    key_fields: List[str],
    columns: List[str],
) -> Dict[str, int]:
    """Diff two sets of rows by natural keys. Returns summary counts."""
    map1 = {_row_key(r, key_fields): r for r in rows1}
    map2 = {_row_key(r, key_fields): r for r in rows2}
    keys1 = set(map1.keys())
    keys2 = set(map2.keys())

    changed = 0
    unchanged_count = 0
    value_cols = [c for c in columns if c not in key_fields]

    for k in keys1 & keys2:
        r1, r2 = map1[k], map2[k]
        if any(r1.get(c) != r2.get(c) for c in value_cols):
            changed += 1
        else:
            unchanged_count += 1

    return {
        'added': len(keys1 - keys2),
        'removed': len(keys2 - keys1),
        'changed': changed,
        'unchanged': unchanged_count,
        'run1Count': len(rows1),
        'run2Count': len(rows2),
    }


def diff_keyed_table_detail(
    rows1: List[Dict[str, Any]],
    rows2: List[Dict[str, Any]],
    key_fields: List[str],
    columns: List[str],
    change_type: str = 'all',
    search: Optional[str] = None,
    sort: Optional[str] = None,
    page: int = 1,
    page_size: int = 100,
) -> Dict[str, Any]:
    """Full detail diff with pagination, filtering, search, sort."""
    map1 = {_row_key(r, key_fields): r for r in rows1}
    map2 = {_row_key(r, key_fields): r for r in rows2}
    keys1 = set(map1.keys())
    keys2 = set(map2.keys())
    value_cols = [c for c in columns if c not in key_fields]

    all_rows: List[Dict[str, Any]] = []

    # Added (in run1 but not run2)
    for k in sorted(keys1 - keys2):
        all_rows.append({
            'status': 'added',
            'key': dict(zip(key_fields, k)),
            'run1': map1[k],
            'run2': None,
            'changes': list(columns),
        })

    # Removed (in run2 but not run1)
    for k in sorted(keys2 - keys1):
        all_rows.append({
            'status': 'removed',
            'key': dict(zip(key_fields, k)),
            'run1': None,
            'run2': map2[k],
            'changes': list(columns),
        })

    # Common keys
    for k in sorted(keys1 & keys2):
        r1, r2 = map1[k], map2[k]
        changes = [c for c in value_cols if r1.get(c) != r2.get(c)]
        all_rows.append({
            'status': 'changed' if changes else 'unchanged',
            'key': dict(zip(key_fields, k)),
            'run1': r1,
            'run2': r2,
            'changes': changes,
        })

    # Filter by change_type
    if change_type and change_type != 'all':
        all_rows = [r for r in all_rows if r['status'] == change_type]

    # Search
    if search:
        sl = search.lower()

        def _matches(row: Dict[str, Any]) -> bool:
            data = row.get('run1') or row.get('run2') or {}
            return any(sl in str(v).lower() for v in data.values())

        all_rows = [r for r in all_rows if _matches(r)]

    # Sort
    if sort:
        parts = sort.split(':') if ':' in sort else [sort, 'asc']
        col, direction = parts[0], parts[1] if len(parts) > 1 else 'asc'
        reverse = direction == 'desc'

        def _sort_key(row: Dict[str, Any]) -> tuple:
            data = row.get('run1') or row.get('run2') or {}
            v = data.get(col, '')
            return (v is None, str(v) if v is not None else '')

        all_rows.sort(key=_sort_key, reverse=reverse)

    # Paginate
    total = len(all_rows)
    start = (page - 1) * page_size
    end = start + page_size

    return {
        'rows': all_rows[start:end],
        'page': page,
        'pageSize': page_size,
        'totalRows': total,
    }


# ── Step 19: Lifecycle / interval classification ──

def classify_issues(
    issues: List[Dict[str, Any]],
    run_id_1: int,
    run_id_2: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Classify issues with lifecycle semantics.

    run_id_1 = newer run, run_id_2 = older run.
    """
    counts: Dict[str, int] = {
        'opened_between_runs': 0,
        'resolved_between_runs': 0,
        'regressed_between_runs': 0,
        'existed_in_both': 0,
        'visible_only_in_run1': 0,
        'visible_only_in_run2': 0,
    }
    classified = []

    for issue in issues:
        first = issue.get('first_detected_run')
        last = issue.get('last_detected_run')
        resolved = issue.get('resolved_run')
        status = issue.get('status')

        # Opened between runs
        if first is not None and first > run_id_2 and first <= run_id_1:
            lifecycle = 'opened_between_runs'
        # Resolved between runs
        elif resolved is not None and resolved > run_id_2 and resolved <= run_id_1:
            lifecycle = 'resolved_between_runs'
        # Regressed (re-opened) between runs
        elif status == 'regressed' and last is not None and last > run_id_2 and last <= run_id_1:
            lifecycle = 'regressed_between_runs'
        else:
            # Was visible at run2?
            in_run2 = (
                first is not None and first <= run_id_2
                and (last is None or last >= run_id_2)
                and (resolved is None or resolved > run_id_2)
            )
            # Was visible at run1?
            in_run1 = (
                first is not None and first <= run_id_1
                and (last is None or last >= run_id_1)
                and (resolved is None or resolved > run_id_1 or status == 'regressed')
            )

            if in_run1 and in_run2:
                lifecycle = 'existed_in_both'
            elif in_run1:
                lifecycle = 'visible_only_in_run1'
            elif in_run2:
                lifecycle = 'visible_only_in_run2'
            else:
                lifecycle = 'existed_in_both'  # fallback

        counts[lifecycle] = counts.get(lifecycle, 0) + 1
        classified.append({**issue, '_lifecycle': lifecycle})

    return classified, counts


def classify_interval_events(
    rows: List[Dict[str, Any]],
    run_id_1: int,
    run_id_2: int,
    run_field: str = 'run_id',
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Classify events (emails, notes, etc.) by when they occurred relative to runs."""
    counts: Dict[str, int] = {
        'event_between_runs': 0,
        'before_run2': 0,
        'after_run1': 0,
    }
    classified = []

    for row in rows:
        rid = row.get(run_field)
        if rid is not None and run_id_2 < rid <= run_id_1:
            lifecycle = 'event_between_runs'
        elif rid is not None and rid <= run_id_2:
            lifecycle = 'before_run2'
        else:
            lifecycle = 'after_run1'
        counts[lifecycle] = counts.get(lifecycle, 0) + 1
        classified.append({**row, '_lifecycle': lifecycle})

    return classified, counts


def classify_issue_notes(
    notes: List[Dict[str, Any]],
    run1_at: str,
    run2_at: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Classify issue notes relative to run timestamps."""
    counts: Dict[str, int] = {
        'created_between_runs': 0,
        'visible_at_run1': 0,
        'visible_at_run2': 0,
    }
    classified = []

    for note in notes:
        created = note.get('created_at', '')
        if created <= run2_at:
            lifecycle = 'visible_at_run2'
            counts['visible_at_run2'] += 1
            counts['visible_at_run1'] += 1
        elif created <= run1_at:
            lifecycle = 'created_between_runs'
            counts['created_between_runs'] += 1
            counts['visible_at_run1'] += 1
        else:
            lifecycle = 'after_run1'
        classified.append({**note, '_lifecycle': lifecycle})

    return classified, counts


# ── Step 20: As-of-run reconstruction ──

def reconstruct_as_of(
    rows: List[Dict[str, Any]],
    run_id: int,
    first_field: str = 'first_seen_run',
    last_field: str = 'last_seen_run',
) -> List[Dict[str, Any]]:
    """Return rows that were visible as of a given run_id."""
    return [
        r for r in rows
        if r.get(first_field) is not None
        and r[first_field] <= run_id
        and r.get(last_field) is not None
        and r[last_field] >= run_id
    ]


def classify_known_entities(
    rows: List[Dict[str, Any]],
    run_id_1: int,
    run_id_2: int,
    key_fields: List[str],
    first_field: str = 'first_seen_run',
    last_field: str = 'last_seen_run',
) -> Dict[str, int]:
    """Classify known_users/known_projects with as-of semantics."""
    as_of_1 = {_row_key(r, key_fields): r for r in reconstruct_as_of(rows, run_id_1, first_field, last_field)}
    as_of_2 = {_row_key(r, key_fields): r for r in reconstruct_as_of(rows, run_id_2, first_field, last_field)}

    keys1 = set(as_of_1.keys())
    keys2 = set(as_of_2.keys())

    return {
        'added': len(keys1 - keys2),
        'removed': len(keys2 - keys1),
        'existed_in_both': len(keys1 & keys2),
        'run1Count': len(keys1),
        'run2Count': len(keys2),
    }


# ── Step 21: Text diff preparation ──

def prepare_text_diff(text1: Optional[str], text2: Optional[str]) -> Dict[str, Any]:
    """Prepare a text diff result for display."""
    t1 = text1 or ''
    t2 = text2 or ''
    return {
        'run1Value': t1,
        'run2Value': t2,
        'status': 'same' if t1 == t2 else 'changed',
        'run1Lines': t1.count('\n') + (1 if t1 else 0),
        'run2Lines': t2.count('\n') + (1 if t2 else 0),
    }


# ── Step 22: JSON normalization and raw fallback ──

def normalize_json_blob(val: Any) -> Dict[str, Any]:
    """Normalize a JSON blob to a structured summary + raw fallback."""
    parsed = _safe_json_parse(val)
    if parsed is None:
        return {'summary': None, 'raw': None, 'type': 'null'}
    if isinstance(parsed, list):
        return {
            'summary': {'count': len(parsed)},
            'raw': parsed,
            'type': 'array',
        }
    if isinstance(parsed, dict):
        return {
            'summary': {'keys': sorted(parsed.keys()), 'keyCount': len(parsed)},
            'raw': parsed,
            'type': 'object',
        }
    return {'summary': None, 'raw': parsed, 'type': type(parsed).__name__}


def compare_json_blobs(val1: Any, val2: Any) -> Dict[str, Any]:
    """Compare two JSON blobs with normalized summaries."""
    n1 = normalize_json_blob(val1)
    n2 = normalize_json_blob(val2)
    same = json.dumps(n1['raw'], sort_keys=True, default=str) == json.dumps(n2['raw'], sort_keys=True, default=str)
    return {
        'run1': n1,
        'run2': n2,
        'status': 'same' if same else 'changed',
    }


# =============================================================================
# SUMMARY BUILDER — Step 32
# =============================================================================

def build_summary_stats(
    run1: Dict[str, Any],
    run2: Dict[str, Any],
) -> Dict[str, Any]:
    """Build top-level summary stats for the header band."""

    def _delta(key: str) -> Dict[str, Any]:
        v1 = run1.get(key)
        v2 = run2.get(key)
        result: Dict[str, Any] = {'run1': v1, 'run2': v2, 'delta': None, 'pctDelta': None}
        if _is_numeric(v1) and _is_numeric(v2):
            result['delta'] = round(v1 - v2, 4)
            if v2 != 0:
                result['pctDelta'] = round((v1 - v2) / v2 * 100, 2)
        return result

    return {
        'healthScore': _delta('health_score'),
        'userCount': _delta('user_count'),
        'enabledUserCount': _delta('enabled_user_count'),
        'projectCount': _delta('project_count'),
        'pluginCount': _delta('plugin_count'),
        'connectionCount': _delta('connection_count'),
        'codeEnvCount': _delta('code_env_count'),
        'clusterCount': _delta('cluster_count'),
        'coverageStatus': {
            'run1': run1.get('coverage_status'),
            'run2': run2.get('coverage_status'),
        },
    }


# =============================================================================
# COVERAGE WARNINGS — Step 34
# =============================================================================

def build_coverage_warnings(
    run1: Dict[str, Any],
    run2: Dict[str, Any],
    sections1: List[Dict[str, Any]],
    sections2: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build coverage warnings from coverage_status and run_sections."""
    warnings = []

    for label, run, sections in [('Run 1', run1, sections1), ('Run 2', run2, sections2)]:
        cs = run.get('coverage_status', 'complete')
        if cs and cs != 'complete':
            warnings.append({
                'run': label,
                'runId': run.get('run_id'),
                'type': 'coverage_status',
                'message': f'{label} has coverage_status={cs}',
            })
        incomplete = [s for s in sections if not s.get('is_complete', True)]
        for s in incomplete:
            warnings.append({
                'run': label,
                'runId': run.get('run_id'),
                'type': 'incomplete_section',
                'section': s.get('section_key'),
                'message': f'{label} section "{s.get("section_key")}" is incomplete: {s.get("error_message", "")}',
            })

    return warnings
