"""
Plugin configuration loaders for the Admin Toolkit backend.

Reads plugin settings into the backend's performance settings, the frontend
threshold defaults, outreach detection thresholds, and DB Health config.
"""

import logging
from dataclasses import dataclass
from typing import Optional

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DbHealthConfig:
    """Plugin-level configuration for DB Health tool."""
    connection_name: Optional[str] = None
    password: Optional[str] = None


def load_dbhealth_config() -> DbHealthConfig:
    """Read DB Health config from plugin settings."""
    try:
        import dataiku
        client = dataiku.api_client()
        raw = client.get_plugin('admin-toolkit').get_settings().get_raw()
        config = raw.get('config', {}) if isinstance(raw, dict) else {}
        conn = (config.get('dbhealth_connection') or '').strip() or None
        pw = (config.get('dbhealth_password') or '').strip() or None
        return DbHealthConfig(connection_name=conn, password=pw)
    except Exception as exc:
        _log.debug("Could not load dbhealth config: %s", exc)
        return DbHealthConfig()


_PERF_MAP = {
    'perf_parallel_workers_default': ('parallel_workers_default', int),
    'perf_parallel_workers_max': ('parallel_workers_max', int),
    'perf_code_env_detail_workers': ('code_env_detail_workers', int),
    'perf_code_env_timeout_ms': ('code_env_timeout_ms', int),
    'perf_project_footprint_timeout_ms': ('project_footprint_timeout_ms', int),
    'perf_cache_ttl_overview': ('cache_ttl_overview', int),
    'perf_cache_ttl_projects': ('cache_ttl_projects', int),
    'perf_cache_ttl_code_envs': ('cache_ttl_code_envs', int),
    'perf_codenvclean_thread_max': ('codenvclean_thread_max', int),
    'perf_cache_ttl_connections': ('cache_ttl_connections', int),
    'perf_cache_ttl_users': ('cache_ttl_users', int),
    'perf_cache_ttl_license': ('cache_ttl_license', int),
    'perf_cache_ttl_usage_full': ('cache_ttl_usage_full', int),
    'perf_cache_ttl_outreach': ('cache_ttl_outreach', int),
    'perf_cache_ttl_inactive': ('cache_ttl_inactive', int),
    'perf_cache_ttl_plugins': ('cache_ttl_plugins', int),
    'perf_cache_ttl_log_errors': ('cache_ttl_log_errors', int),
    'perf_cache_ttl_dir_tree': ('cache_ttl_dir_tree', int),
    'perf_fe_timeout_code_envs': ('fe_timeout_code_envs', int),
    'perf_fe_timeout_project_footprint': ('fe_timeout_project_footprint', int),
    'perf_fe_timeout_projects': ('fe_timeout_projects', int),
    'perf_fe_timeout_logs': ('fe_timeout_logs', int),
    'perf_fe_timeout_llm_analysis': ('fe_timeout_llm_analysis', int),
    'perf_sqlite_connect_timeout': ('sqlite_connect_timeout', int),
}

_THRESH_MAP = {
    'thresh_inactive_project_days': ('inactiveProjectDays', int),
    'thresh_filesystem_warning_pct': ('filesystemWarningPct', int),
    'thresh_filesystem_critical_pct': ('filesystemCriticalPct', int),
    'thresh_code_env_count_unhealthy': ('codeEnvCountUnhealthy', int),
    'thresh_empty_project_kb': ('emptyProjectBytes', lambda v: int(v) * 1024),
    'thresh_health_warning_below': ('healthWarningBelow', int),
    'thresh_health_critical_below': ('healthCriticalBelow', int),
    'thresh_deprecated_python_prefixes': ('deprecatedPythonPrefixes', str),
    'thresh_code_studio_count_unhealthy': ('codeStudioCountUnhealthy', int),
    'thresh_orphan_notebook_min': ('orphanNotebookMin', int),
    'thresh_large_flow_objects': ('largeFlowObjects', int),
    'thresh_high_freq_scenario_minutes': ('highFreqScenarioMinutes', int),
    'thresh_overshared_project_permissions': ('oversharedProjectPermissions', int),
    'thresh_disabled_features_cutoff': ('disabledFeaturesSeverityCutoff', int),
    'thresh_open_files_minimum': ('openFilesMinimum', int),
    'thresh_java_heap_minimum_mb': ('javaHeapMinimumMB', int),
    'thresh_python_critical_below': ('pythonCriticalBelow', str),
    'thresh_python_warning_below': ('pythonWarningBelow', str),
    'thresh_spark_version_minimum': ('sparkVersionMinimum', int),
    'thresh_project_count_warning': ('projectCountWarning', int),
    'weight_code_envs': ('weightCodeEnvs', float),
    'weight_project_footprint': ('weightProjectFootprint', float),
    'weight_system_capacity': ('weightSystemCapacity', float),
    'weight_security_isolation': ('weightSecurityIsolation', float),
    'weight_version_currency': ('weightVersionCurrency', float),
    'weight_runtime_config': ('weightRuntimeConfig', float),
    'log_lines_before': ('logLinesBefore', int),
    'log_lines_after': ('logLinesAfter', int),
    'log_grouping_window_sec': ('logTimeThresholdSec', int),
    'log_max_errors': ('logMaxErrors', int),
    'log_ai_system_prompt': ('aiLogAnalysisPrompt', str),
    'scan_large_file_threshold_gb': ('largeFileThresholdGB', int),
    'scan_dir_tree_default_depth': ('dirTreeDefaultDepth', int),
    'scan_file_viewer_max_lines': ('fileViewerMaxLines', int),
    'scan_syntax_highlight_max_kb': ('syntaxHighlightMaxKB', int),
}

_OUTREACH_THRESH_MAP = {
    'thresh_inactive_project_days': ('inactive_project_days', int),
    'thresh_empty_project_kb': ('empty_project_bytes', lambda v: int(v) * 1024),
    'thresh_code_env_count_unhealthy': ('code_env_count_unhealthy', int),
    'thresh_code_studio_count_unhealthy': ('code_studio_count_unhealthy', int),
    'thresh_orphan_notebook_min': ('orphan_notebook_min', int),
    'thresh_large_flow_objects': ('large_flow_objects', int),
    'thresh_high_freq_scenario_minutes': ('high_freq_scenario_minutes', int),
    'thresh_overshared_project_permissions': ('overshared_project_permissions', int),
}


def _get_plugin_config() -> dict:
    """Read raw plugin config dict. Returns {} on any error."""
    try:
        import dataiku
        client = dataiku.api_client()
        raw = client.get_plugin('admin-toolkit').get_settings().get_raw()
        return raw.get('config', {}) if isinstance(raw, dict) else {}
    except Exception as exc:
        _log.debug("Could not read plugin config: %s", exc)
        return {}


def load_plugin_performance_settings() -> dict:
    """Read perf_* plugin params and return a dict of _BACKEND_SETTINGS keys."""
    config = _get_plugin_config()
    result = {}
    for param_key, (setting_key, cast) in _PERF_MAP.items():
        val = config.get(param_key)
        if val is not None and val != '':
            try:
                result[setting_key] = cast(val)
            except (ValueError, TypeError):
                pass
    return result


def load_plugin_threshold_defaults() -> dict:
    """Read thresh_* plugin params and return a dict of frontend threshold keys."""
    config = _get_plugin_config()
    result = {}
    for param_key, (thresh_key, cast) in _THRESH_MAP.items():
        val = config.get(param_key)
        if val is not None and val != '':
            try:
                result[thresh_key] = cast(val)
            except (ValueError, TypeError):
                pass
    return result


def load_plugin_outreach_thresholds() -> dict:
    """Read outreach detection thresholds from plugin params for backend use."""
    config = _get_plugin_config()
    result = {}
    for param_key, (key, cast) in _OUTREACH_THRESH_MAP.items():
        val = config.get(param_key)
        if val is not None and val != '':
            try:
                result[key] = cast(val)
            except (ValueError, TypeError):
                pass
    return result
