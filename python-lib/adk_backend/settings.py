"""Configurable backend settings (updated via /api/settings).

_BACKEND_SETTINGS is mutated in place by routes in backend.py — it must remain
ONE shared dict object, so always import the name and mutate, never rebind.
"""

import threading
from typing import Any, Dict

# ── Configurable backend settings (updated via /api/settings) ──
_BACKEND_SETTINGS: Dict[str, Any] = {
    # Concurrency
    'parallel_workers_default': 8,
    'parallel_workers_max': 32,
    'code_env_detail_workers': 8,
    # Timeouts
    'code_env_timeout_ms': 600000,
    'project_footprint_timeout_ms': 600000,
    'container_exec_timeout_ms': 600000,
    # Cache TTLs (seconds)
    'cache_ttl_overview': 600,
    'cache_ttl_connections': 600,
    'cache_ttl_users': 600,
    'cache_ttl_license': 600,
    'cache_ttl_projects': 600,
    'cache_ttl_code_envs': 600,
    'cache_ttl_usage_full': 600,
    'cache_ttl_outreach': 600,
    'cache_ttl_inactive': 600,
    'cache_ttl_plugins': 600,
    'cache_ttl_log_errors': 600,
    'cache_ttl_dir_tree': 600,
    'cache_ttl_llm_audit': 7200,
    'cache_ttl_llm_pricing': 21600,
    # LLM audit
    'llm_audit_timeout_ms': 1200000,
    'llm_audit_pricing_timeout_sec': 30,
    # Frontend API timeouts (served to frontend for sync)
    'fe_timeout_code_envs': 620000,
    'fe_timeout_project_footprint': 620000,
    'fe_timeout_container_execs': 620000,
    'fe_timeout_projects': 45000,
    'fe_timeout_logs': 30000,
    'fe_timeout_llm_analysis': 120000,
    'fe_timeout_llm_audit': 1200000,
    'sqlite_connect_timeout': 30,
    # Codenvclean
    'codenvclean_thread_max': 20,
}
_BACKEND_SETTINGS_LOCK = threading.Lock()

# Load plugin.json performance defaults and merge into _BACKEND_SETTINGS
try:
    from db_adapter import load_plugin_performance_settings as _load_perf
    _plugin_perf = _load_perf()
    if _plugin_perf:
        _BACKEND_SETTINGS.update(_plugin_perf)
except Exception:
    pass
# Snapshot after plugin merge — used as reset target
_BACKEND_SETTINGS_DEFAULTS: Dict[str, Any] = dict(_BACKEND_SETTINGS)

# Load outreach detection thresholds from plugin params
try:
    from db_adapter import load_plugin_outreach_thresholds as _load_outreach_thresh
    _outreach_thresholds: Dict[str, Any] = _load_outreach_thresh()
except Exception:
    _outreach_thresholds = {}
