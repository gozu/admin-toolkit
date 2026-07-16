"""Plugin-config resolution for tools/agents/runnables.

Primary source is `plugin_config` handed to each component by DSS (PASSWORD
params arrive decrypted there). Env-var overrides (ATK_AGENTS_*) exist so the
whole stack is testable as pure Python against a live backend without DSS.

backend_url resolution order:
  1. explicit `backend_url` plugin param / ATK_AGENTS_BACKEND_URL
  2. discovery: scan local DSS projects for an admin-toolkit webapp and build
     <studioExternalUrl>/web-apps-backends/<projectKey>/<webappId>
"""

import os


def _env(name, default=None):
    val = os.environ.get('ATK_AGENTS_' + name)
    return val if val not in (None, '') else default


def resolve(plugin_config=None):
    """Merge plugin_config + env overrides into one settings dict."""
    cfg = dict(plugin_config or {})

    def pick(key, default=''):
        return _env(key.upper(), cfg.get(key) or default)

    # Unified data-store connections (win over the per-feature keys below, which
    # stay for existing installs). new-key-first mirrors master_password.
    _toolkit_db = pick('toolkit_db_connection')
    _toolkit_storage = pick('toolkit_storage_connection')
    settings = {
        'backend_url': (pick('backend_url') or '').rstrip('/'),
        'toolkit_db_connection': _toolkit_db,
        'toolkit_storage_connection': _toolkit_storage,
        # One password unlocks red endpoints AND opens encrypted host keys.
        # Legacy keys cover a pre-0.4.659 config DSS hasn't pruned yet.
        'master_password': (pick('master_password') or cfg.get('red_actions_password')
                            or cfg.get('host_keys_password') or ''),
        'host_allowlist': [h.strip() for h in (pick('host_allowlist') or '').split(',') if h.strip()],
        'verify_tls': str(pick('verify_tls', cfg.get('verify_tls', True))).lower() not in ('false', '0', 'no'),
        'http_timeout_s': int(pick('http_timeout_s', cfg.get('http_timeout_s') or 30)),
        'heavy_timeout_s': int(pick('heavy_timeout_s', cfg.get('heavy_timeout_s') or 420)),
        'default_llm_id': pick('default_llm_id'),
        'enable_red_actions': str(pick('enable_red_actions', cfg.get('enable_red_actions', False))).lower() in ('true', '1', 'yes'),
        'triage_connection': _toolkit_db or pick('triage_connection'),
        # Audit-DB fallback chain inputs (audit.resolve_connection): the unified
        # toolkit DB wins, then the dedicated param and the legacy Story key —
        # all must survive this whitelist or kernels can never resolve them.
        'agents_audit_postgres_connection': _toolkit_db or pick('agents_audit_postgres_connection'),
        'story_postgres_connection': pick('story_postgres_connection'),
        'triage_score_threshold': int(pick('triage_score_threshold', cfg.get('triage_score_threshold') or 75)),
        'triage_mail_channel': pick('triage_mail_channel'),
        'triage_recipient': pick('triage_recipient'),
        # Auto-remediation tier: per-action opt-in CSV + cumulative caps.
        'auto_remediate_actions': [a.strip() for a in (pick('auto_remediate_actions') or '').split(',')
                                   if a.strip()],
        'auto_remediate_max_gb': int(pick('auto_remediate_max_gb', cfg.get('auto_remediate_max_gb') or 20)),
        'auto_remediate_max_objects': int(pick('auto_remediate_max_objects',
                                               cfg.get('auto_remediate_max_objects') or 25)),
        'log_cleanup_min_age_days': int(pick('log_cleanup_min_age_days',
                                             cfg.get('log_cleanup_min_age_days') or 3)),
        'python_run_timeout_seconds': int(pick('python_run_timeout_seconds',
                                               cfg.get('python_run_timeout_seconds') or 120)),
        'settings_set_blocked_extra': pick('settings_set_blocked_extra'),
        # Per-action enablement map (Agent Settings page). JSON {name: bool};
        # kernel-start snapshot only — action_gates.py fetches the live map
        # through the backend with this as the offline fallback.
        'agent_action_gates': _parse_gates(pick('agent_action_gates')),
    }
    if not settings['backend_url']:
        settings['backend_url'] = _discover_backend_url() or ''
    return settings


def _parse_gates(raw):
    if isinstance(raw, dict):
        return {str(k): bool(v) for k, v in raw.items()}
    try:
        import json
        parsed = json.loads(raw or '{}')
        return {str(k): bool(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _discover_backend_url():
    """Find the admin-toolkit webapp on the local DSS and build its backend base.

    Convenience fallback only — the explicit param wins because discovery costs
    a project sweep and assumes the webapp lives on this DSS.
    """
    try:
        import dataiku
        client = dataiku.api_client()
        external = ''
        try:
            external = (client.get_general_settings().get_raw()
                        .get('studioExternalUrl') or '').rstrip('/')
        except Exception:
            pass
        for proj in client.list_projects():
            key = proj.get('projectKey')
            try:
                webapps = client.get_project(key).list_webapps()
            except Exception:
                continue
            for wa in webapps:
                if 'admin-toolkit' in (wa.get('type') or ''):
                    base = external or 'http://127.0.0.1:%s' % os.environ.get('DKU_BASE_PORT', '10000')
                    return '%s/web-apps-backends/%s/%s' % (base, key, wa.get('id'))
    except Exception:
        pass
    return None
