"""Audit-event classification vocabulary — the single source of truth.

Ported from Pulse's users_activity_vocab.yaml (dss-plugin-dataiku-pulse,
python-lib/data_collection/audit_logs_modules/users_activity_vocab.yaml) as
plain constants: no YAML load at runtime, no second copy anywhere.

Rules (mirroring Pulse's user_login.py):
- developing action: msgType contains any ACTION_WORDS substring AND does NOT
  contain any REMOVE_WORDS substring (case-insensitive).
- viewing action: any retained UI action row (developing is a subset).

VOCAB_VERSION is stamped into every story-audit-aggregate payload; the hub
collector rejects a remote whose vocab version differs, so per-day aggregates
in Postgres are never a mix of two classification rules.
"""
from typing import Any, Dict, Optional, Tuple

VOCAB_VERSION = 1

ACTION_WORDS: Tuple[str, ...] = (
    'save', 'create', 'analysis', 'clear', 'run', 'edit', 'update', 'upload',
    'import', 'export', 'build', 'train', 'deploy', 'publish', 'execute',
    'compute', 'recipe', 'scenario', 'notebook', 'webapp', 'dashboard', 'api',
    'app', 'flow', 'plugin', 'code-env', 'codeenv', 'settings', 'rename',
    'delete', 'move', 'copy', 'duplicate', 'sync', 'git', 'commit', 'schema',
    'partition',
)

REMOVE_WORDS: Tuple[str, ...] = (
    'list', 'dataset-clear-samples', 'dataset-save-schema',
    'project-save-variables', 'get', 'read', 'open', 'preview', 'view',
    'explore', 'sample', 'status', 'summary', 'timeline', 'search', 'recent',
    'details', 'read-meta', 'read-data', 'read-schema',
)

# Query-time taxonomy for the Event Mix view: first matching keyword wins, so
# more specific object types come before generic verbs.
_TAXONOMY: Tuple[Tuple[str, str], ...] = (
    ('dataset', 'Datasets'),
    ('recipe', 'Recipes'),
    ('notebook', 'Notebooks'),
    ('scenario', 'Scenarios'),
    ('webapp', 'Webapps & Dashboards'),
    ('dashboard', 'Webapps & Dashboards'),
    ('insight', 'Webapps & Dashboards'),
    ('saved-model', 'ML & Analysis'),
    ('model', 'ML & Analysis'),
    ('analysis', 'ML & Analysis'),
    ('ml-', 'ML & Analysis'),
    ('flow', 'Flow'),
    ('job', 'Jobs'),
    ('project', 'Projects'),
    ('folder', 'Folders'),
    ('plugin', 'Plugins & Code Envs'),
    ('code-env', 'Plugins & Code Envs'),
    ('codeenv', 'Plugins & Code Envs'),
    ('user', 'Admin & Security'),
    ('group', 'Admin & Security'),
    ('connection', 'Admin & Security'),
    ('settings', 'Admin & Security'),
    ('login', 'Admin & Security'),
    ('git', 'Git'),
    ('wiki', 'Wiki & Discussions'),
    ('discussion', 'Wiki & Discussions'),
)


def classify_msg_type(msg_type: str) -> str:
    """Return 'developing' or 'viewing' for a retained UI msgType."""
    low = (msg_type or '').lower()
    if any(word in low for word in ACTION_WORDS) and not any(word in low for word in REMOVE_WORDS):
        return 'developing'
    return 'viewing'


def taxonomy_for(msg_type: str) -> str:
    """Coarse Event-Mix bucket for a msgType. Applied at query time only —
    never persisted, so the taxonomy can evolve without a backfill."""
    low = (msg_type or '').lower()
    for keyword, bucket in _TAXONOMY:
        if keyword in low:
            return bucket
    return 'Other'


def is_ui_user_event(obj: Dict[str, Any]) -> Tuple[bool, Optional[str], str, str]:
    """Decide whether an audit-log line is a human UI action.

    Returns (keep, login, project_key, msg_type). keep is True only for
    topic=generic events with authSource USER_FROM_UI, not attributable to a
    scenario or job, and carrying a resolvable login + msgType.
    """
    if not isinstance(obj, dict) or obj.get('topic') != 'generic':
        return (False, None, '', '')
    msg = obj.get('message')
    if not isinstance(msg, dict):
        return (False, None, '', '')
    if msg.get('authSource') != 'USER_FROM_UI':
        return (False, None, '', '')
    if msg.get('scenarioId') or msg.get('jobId'):
        return (False, None, '', '')
    msg_type = str(msg.get('msgType') or '').strip()
    if not msg_type:
        return (False, None, '', '')
    mdc = obj.get('mdc')
    login = (
        msg.get('login') or msg.get('user') or msg.get('authUser')
        or (mdc.get('user') if isinstance(mdc, dict) else None)
    )
    login = str(login).strip() if login else ''
    if not login:
        return (False, None, '', '')
    project_key = str(msg.get('projectKey') or '').strip()
    return (True, login, project_key, msg_type)
