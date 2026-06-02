"""
Typed registry for trends snapshot tables.

New trends tables should be added here first. Snapshot endpoints and contract
tests consume this registry so table additions are not hand-wired in multiple
places.
"""

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class TrendSnapshotTable:
    key: str
    table: str
    min_schema_version: int
    compare_dataset_id: str
    columns: Tuple[str, ...]


TREND_SNAPSHOT_TABLES: Tuple[TrendSnapshotTable, ...] = (
    TrendSnapshotTable('users', 'user_snapshots', 6, 'user_snapshots', ('login', 'display_name', 'email', 'user_profile', 'enabled')),
    TrendSnapshotTable('projects', 'project_snapshots', 6, 'project_snapshots', ('project_key', 'name', 'owner_login')),
    TrendSnapshotTable('plugins', 'run_plugins', 6, 'run_plugins', ('plugin_id', 'label', 'version', 'is_dev')),
    TrendSnapshotTable('connections_inventory', 'run_connections', 6, 'run_connections', ('connection_name', 'connection_type')),
    TrendSnapshotTable('datasets', 'run_datasets', 7, 'run_datasets', ('project_key', 'dataset_name', 'dataset_type', 'connection_name')),
    TrendSnapshotTable('recipes', 'run_recipes', 7, 'run_recipes', ('project_key', 'recipe_name', 'recipe_type')),
    TrendSnapshotTable('llms', 'run_llms', 7, 'run_llms', ('llm_id', 'llm_type', 'friendly_name')),
    TrendSnapshotTable('agents', 'run_agents', 7, 'run_agents', ('project_key', 'agent_id', 'agent_name')),
    TrendSnapshotTable('agent_tools', 'run_agent_tools', 7, 'run_agent_tools', ('project_key', 'tool_id', 'tool_type')),
    TrendSnapshotTable('knowledge_banks', 'run_knowledge_banks', 7, 'run_knowledge_banks', ('project_key', 'kb_id', 'kb_name')),
    TrendSnapshotTable('git_commits', 'run_git_commits', 7, 'run_git_commits', ('project_key', 'commit_hash', 'author', 'committed_at')),
    TrendSnapshotTable('connection_health', 'run_connection_health', 9, 'run_connection_health', ('connection_name', 'connection_type', 'status', 'error_category', 'error_message')),
    TrendSnapshotTable('db_health', 'run_db_health', 9, 'run_db_health', ('table_name', 'schema_name', 'table_size', 'row_count', 'dead_tuples', 'bloat_pct', 'last_vacuum', 'last_autovacuum', 'last_analyze')),
    TrendSnapshotTable('container_execs', 'run_container_execs', 10, 'run_container_execs', ('project_key', 'object_type', 'object_id', 'container_conf', 'effective_container_conf', 'override_level')),
)


TREND_SNAPSHOT_TABLE_BY_NAME = {spec.table: spec for spec in TREND_SNAPSHOT_TABLES}
