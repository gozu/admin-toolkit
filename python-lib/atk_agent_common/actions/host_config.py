"""Host-level configuration actuator action (install.ini / systemd / ulimits).

Everything host-bound runs through the host-config macro
(python-runnables/host-config/), which re-validates the policy whitelist and
does the actual file edits with timestamped backups. install.ini is writable
by the dataiku user; systemd drop-ins and limits.d usually are NOT — when the
macro hits a permission wall the executor surfaces a ready-to-run sudo script
in the error remediation (docker-prune daemon.json precedent: display-only,
never executed by the toolkit).
"""

from ..errors import ToolkitError
from ..policies import host_config as policy
from . import _base


def _current_value(state, file, section, key):
    if file == 'install-ini':
        return ((state.get('installIni') or {}).get(section) or {}).get(key)
    if file == 'systemd-override':
        return (state.get('systemdOverride') or {}).get(key)
    return (state.get('ulimits') or {}).get(key)


def _plan_host_config_set(client, host, target, params):
    file = str((target or {}).get('file') or '').strip()
    section = str((target or {}).get('section') or '').strip() or None
    key = str((target or {}).get('key') or '').strip()
    value = str((target or {}).get('value') or '').strip()
    if not key or not value:
        raise ToolkitError('host-config-set target needs {"file": ..., "key": ..., "value": ...}.')
    ok, reason = policy.validate(file, section, key, value)
    if not ok:
        raise ToolkitError('host-config-set refused: %s' % reason,
                           remediation='Relay this refusal to the user verbatim.')
    state = client.get('/api/tools/host-config/read', host=host)
    if not state.get('ok'):
        raise ToolkitError('host-config read failed: %s' % (state.get('error') or state))
    current = _current_value(state, file, section, key)
    warnings = []
    if file == 'install-ini':
        warnings.append('install.ini changes take effect at the NEXT DSS restart — the '
                        'toolkit never restarts DSS itself.')
    else:
        warnings.append('%s changes normally require root; if the DSS user cannot write '
                        'the file, execute returns a ready-to-run sudo script for a human '
                        'admin instead of applying anything.'
                        % ('systemd drop-in' if file == 'systemd-override' else 'limits.d'))
        if file == 'systemd-override':
            warnings.append('The drop-in only applies after `systemctl daemon-reload` and a '
                            'service restart.')
    canonical = {'file': file, 'section': section, 'key': key, 'value': value,
                 'expectedCurrent': current}
    return canonical, {
        'summary': 'Set host config %s%s %s: %s → %s.' % (
            file, ':%s' % section if section else '', key, current, value),
        'currentValue': current,
        'proposedValue': value,
        'effectiveLimits': state.get('effectiveLimits'),
        'targetPath': (state.get('paths') or {}).get(file),
        'warnings': warnings,
        'note': _base.drift_note() + ' The macro re-validates the whitelist and backs the '
                'file up before writing.',
    }


def _exec_host_config_set(client, host, target):
    result = client.post('/api/tools/host-config/apply', host=host, red=True,
                         json={'file': target['file'], 'section': target.get('section'),
                               'key': target['key'], 'value': target['value'],
                               'expectedCurrent': target.get('expectedCurrent')})
    if not result.get('ok'):
        script = result.get('manualScript')
        raise ToolkitError(
            'host-config apply refused/failed: %s' % (result.get('error') or result),
            remediation=('The DSS user cannot write this file. A human admin can apply it '
                         'with:\n%s' % script) if script else None)
    return result


def _changes_host_config_set(target, result):
    return [{'itemKey': 'hostConfig:%s:%s%s' % (
                 target.get('file'),
                 '%s.' % target.get('section') if target.get('section') else '',
                 target.get('key')),
             'before': result.get('before'), 'after': result.get('after')}]


SPECS = [
    _base.spec('host-config-set',
               'host-config-set {file: install-ini|systemd-override|ulimits, key, value, '
               'section?} (section required for install-ini; systemd keys limited to '
               'Limit*/Timeout*/RestartSec; ulimit items nofile/nproc/memlock/core/'
               'fsize/stack)', 'red',
               _plan_host_config_set, _exec_host_config_set,
               settings_hook=_changes_host_config_set),
]
