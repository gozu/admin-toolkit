"""Shared building blocks for the actuator action domains.

Two patterns recur across the catalog:
  * backend action — the executor POSTs the backend's @advanced dispatch
    (`/api/tools/admin-actions/<action>`) on `g.client`, so the action is
    fleet-routable for free (B-api in the catalog docs).
  * drift-guarded setting — the planner reads the current value, refuses
    secret-material path segments, and binds `expectedCurrent` into the
    canonical target so the HMAC confirm token dies on any drift.
"""

import json

from ..errors import ToolkitError
from ..policies import settings_paths


def backup_folder(client, host):
    """First managed folder of the toolkit support project on `host` —
    deletes always back up first, so a backup folder is required."""
    folders = (client.get('/api/managed-folders', host=host) or {}).get('folders') or []
    if not folders:
        raise ToolkitError(
            'No managed folder exists in the toolkit support project on host %r — deletes '
            'always back up first, so a backup folder is required.' % host,
            remediation='Create a managed folder in the ADMINTOOLKIT project (any filesystem '
                        'connection) and re-plan.')
    return folders[0]


def post_backend_action(client, host, action, payload):
    """Run one backend admin-action impl and normalize refusals to
    ToolkitError (the backend returns {'ok': False, 'error': ...} on 409)."""
    result = client.post('/api/tools/admin-actions/%s' % action, host=host, red=True,
                         json=payload)
    if isinstance(result, dict) and result.get('ok') is False:
        raise ToolkitError('%s refused/failed: %s' % (action, result.get('error') or result))
    return result


def require_str(target, key, action):
    value = str((target or {}).get(key) or '').strip()
    if not value:
        raise ToolkitError('%s target needs a non-empty %r.' % (action, key))
    return value


def check_secret_path(path):
    """Refuse dot/index paths whose segments touch secret material. Returns
    the parsed segments (raises ToolkitError on garbage paths too)."""
    try:
        segments = settings_paths.parse_path(path)
    except settings_paths.SettingsPathError as exc:
        raise ToolkitError('invalid path: %s' % exc)
    for seg in segments:
        if isinstance(seg, str) and settings_paths.BLOCKED_SEGMENT_RE.search(seg):
            raise ToolkitError(
                'path %r is blocked: segment %r matches the secret-material blacklist '
                '(passwords/tokens/keys are never agent-mutable or agent-readable).'
                % (path, seg),
                remediation='Relay this refusal to the user verbatim.')
    return segments


def drift_note():
    return ('The current value is bound into the confirm token — if it changes between '
            'plan and execute, execution refuses. The change lands in the restorable '
            'settings history (agents.settings_changes).')


def spec(action, shape, risk, planner, executor, batchable=False, local_only=False,
         settings_hook=None):
    """One registry row. `shape` is the target-shape prose fragment that the
    generated TARGET_SHAPES string (and every tool description) is built from."""
    return {'action': action, 'shape': shape, 'risk': risk, 'planner': planner,
            'executor': executor, 'batchable': batchable, 'local_only': local_only,
            'settings_hook': settings_hook}


def value_drifted(current, expected):
    return json.dumps(current, sort_keys=True, default=str) != \
        json.dumps(expected, sort_keys=True, default=str)
