"""User/security actuator actions (B-api: backend red routes on g.client).

user-disable is the reversible half of account hygiene (user-enable is its
inverse; nothing is deleted). Both the planner and the backend impl refuse
the toolkit's own identity — an agent must never lock out the account the
backend authenticates with. api-key-delete is IRREVERSIBLE (the secret cannot
be regenerated) and refuses personal keys of the executing identity for the
same self-lockout reason; global keys carry no owner, so the plan says that
verification is impossible and the human must check.
"""

from ..errors import ToolkitError
from . import _base


def _inventory(client, host, domain):
    return client.get('/api/tools/admin-actions/inventory', host=host,
                      params={'domain': domain})


def _user_row(client, host, login):
    rows = _inventory(client, host, 'users').get('users') or []
    row = next((u for u in rows if u.get('login') == login), None)
    if row is None:
        raise ToolkitError('User %r not found on host %r.' % (login, host),
                           remediation="Check the login with config_inspect domain='users'.")
    return row


def _backend_identity(client, host):
    """The identity the backend's admin client runs as (best-effort)."""
    try:
        return (_inventory(client, host, 'users').get('callerIdentity') or '').strip()
    except ToolkitError:
        return ''


def _plan_user_toggle(client, host, target, action, new_enabled):
    login = _base.require_str(target, 'login', action)
    row = _user_row(client, host, login)
    caller = _backend_identity(client, host)
    if not new_enabled and caller and login == caller:
        raise ToolkitError(
            'Refusing to plan user-disable for %r — that is the identity the toolkit '
            'itself runs as (self-lockout).' % login,
            remediation='If this account really must go, a human does it in '
                        'Administration → Security.')
    current = bool(row.get('enabled', True))
    warnings = []
    if current == new_enabled:
        warnings.append('User %s is already %s — executing is a no-op.'
                        % (login, 'enabled' if new_enabled else 'disabled'))
    if not new_enabled and 'administrators' in (row.get('groups') or []):
        warnings.append('User %s is in the administrators group — make sure another '
                        'admin remains enabled.' % login)
    canonical = {'login': login, 'enabled': new_enabled, 'expectedCurrent': current}
    return canonical, {
        'summary': '%s user account %s (%s).'
                   % ('Enable' if new_enabled else 'Disable', login,
                      row.get('displayName') or '?'),
        'displayName': row.get('displayName'),
        'groups': row.get('groups'),
        'currentValue': current,
        'proposedValue': new_enabled,
        'warnings': warnings or None,
        'note': _base.drift_note() + (' Revert = user-%s.'
                                      % ('disable' if new_enabled else 'enable')),
    }


def _plan_user_disable(client, host, target, params):
    return _plan_user_toggle(client, host, target, 'user-disable', False)


def _plan_user_enable(client, host, target, params):
    return _plan_user_toggle(client, host, target, 'user-enable', True)


def _exec_user_toggle(client, host, target):
    return _base.post_backend_action(client, host, 'user-set-enabled', {
        'login': target['login'], 'enabled': bool(target.get('enabled')),
        'expectedCurrent': target.get('expectedCurrent')})


def _changes_user_toggle(target, result):
    return [{'itemKey': 'user:%s:enabled' % result.get('login'),
             'before': result.get('before'), 'after': result.get('after')}]


def _plan_api_key_delete(client, host, target, params):
    key_type = _base.require_str(target, 'keyType', 'api-key-delete').lower()
    key_id = _base.require_str(target, 'keyId', 'api-key-delete')
    if key_type not in ('personal', 'global'):
        raise ToolkitError("api-key-delete keyType must be 'personal' or 'global'.")
    inv = _inventory(client, host, 'api-keys')
    rows = inv.get(key_type) or []
    row = next((k for k in rows if k.get('id') == key_id), None)
    if row is None:
        raise ToolkitError(
            '%s API key %r not found on host %r.' % (key_type.title(), key_id, host),
            remediation="List keys with config_inspect domain='api-keys'.")
    caller = (inv.get('callerIdentity') or '').strip()
    if key_type == 'personal' and caller and row.get('user') == caller:
        raise ToolkitError(
            'Refusing to plan the deletion of personal key %r — it belongs to %r, the '
            'identity the toolkit itself runs as (self-lockout).' % (key_id, caller),
            remediation='A human deletes it in the user profile → API keys.')
    warnings = ['This deletion is IRREVERSIBLE — the key secret cannot be restored or '
                'regenerated; anything still using it breaks immediately.']
    if key_type == 'global':
        warnings.append('Global keys carry no owner, so the toolkit CANNOT verify this '
                        'is not the key it authenticates with — confirm with the admin '
                        'before approving.')
    return {'keyType': key_type, 'keyId': key_id}, {
        'summary': 'DELETE %s API key %s (%s) — irreversible.'
                   % (key_type, key_id, row.get('label') or 'no label'),
        'keyLabel': row.get('label'),
        'keyUser': row.get('user'),
        'createdOn': row.get('createdOn'),
        'irreversible': True,
        'warnings': warnings,
        'note': 'Restore is NOT possible for API keys — say so when presenting this plan.',
    }


def _exec_api_key_delete(client, host, target):
    return _base.post_backend_action(client, host, 'api-key-delete', {
        'keyType': target['keyType'], 'keyId': target['keyId']})


SPECS = [
    _base.spec('user-disable',
               'user-disable {login}', 'red',
               _plan_user_disable, _exec_user_toggle, batchable=True,
               settings_hook=_changes_user_toggle),
    _base.spec('user-enable',
               'user-enable {login}', 'amber',
               _plan_user_enable, _exec_user_toggle,
               settings_hook=_changes_user_toggle),
    _base.spec('api-key-delete',
               'api-key-delete {keyType: personal|global, keyId} (IRREVERSIBLE)', 'red',
               _plan_api_key_delete, _exec_api_key_delete),
]
