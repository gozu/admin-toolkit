"""Host Config macro — read + apply whitelisted host-level configuration.

Surfaces:
  install.ini      — DIP_HOME/install.ini (writable by the DSS user);
                     changes need a DSS restart to take effect.
  systemd-override — /etc/systemd/system/<unit>.d/99-admin-toolkit.conf;
                     usually root-owned, so a PermissionError returns a
                     ready-to-run sudo script instead of a change.
  ulimits          — /etc/security/limits.d/99-dataiku-admin-toolkit.conf;
                     same root caveat + script fallback.

The macro NEVER trusts the caller: atk_agent_common.policies.host_config is
re-applied to every apply request, the current value is drift-checked against
expected_current, and every successful write leaves a timestamped .bak next
to the file. Policy refusals return {'ok': False, ...}; they never raise.
"""
import configparser
import getpass
import glob
import json
import os
import shutil
import time

from dataiku.runnables import Runnable

from atk_agent_common.policies import host_config as policy

_ULIMITS_PATH = '/etc/security/limits.d/99-dataiku-admin-toolkit.conf'
_OVERRIDE_NAME = '99-admin-toolkit.conf'


def _dip_home():
    return os.environ.get('DIP_HOME') or os.environ.get('DKU_DIP_HOME') or ''


def _install_ini_path():
    return os.path.join(_dip_home(), 'install.ini')


def _read_install_ini():
    """{section: {key: value}} with secret-material keys redacted."""
    parser = configparser.ConfigParser()
    parser.optionxform = str  # install.ini keys are case-sensitive
    path = _install_ini_path()
    if not os.path.isfile(path):
        return {}
    parser.read(path)
    out = {}
    for section in parser.sections():
        row = {}
        for key, value in parser.items(section):
            row[key] = '__redacted__' if policy._SECRET_RE.search(key) else value
        out[section] = row
    return out


def _dss_unit():
    """Best-effort systemd unit name for this DSS (dataiku*.service)."""
    for pattern in ('/etc/systemd/system/dataiku*.service',
                    '/usr/lib/systemd/system/dataiku*.service',
                    '/lib/systemd/system/dataiku*.service'):
        hits = sorted(glob.glob(pattern))
        if hits:
            return os.path.basename(hits[0])
    return 'dataiku.service'


def _override_path():
    return '/etc/systemd/system/%s.d/%s' % (_dss_unit(), _OVERRIDE_NAME)


def _read_kv_file(path):
    """{key: value} from 'Key=Value' lines (systemd drop-in, [Service] ignored)."""
    out = {}
    if not os.path.isfile(path):
        return out
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if '=' in line and not line.startswith(('#', '[')):
                    key, _, value = line.partition('=')
                    out[key.strip()] = value.strip()
    except OSError:
        pass
    return out


def _read_ulimits_file():
    """{item: value} from this macro's own limits.d file (both soft+hard
    are always written identically, so one value per item)."""
    out = {}
    if not os.path.isfile(_ULIMITS_PATH):
        return out
    try:
        with open(_ULIMITS_PATH) as fh:
            for line in fh:
                parts = line.split()
                if len(parts) == 4 and not line.lstrip().startswith('#'):
                    out[parts[2]] = parts[3]
    except OSError:
        pass
    return out


def _effective_limits():
    """Selected rows of /proc/self/limits — the limits DSS actually runs with."""
    out = {}
    try:
        with open('/proc/self/limits') as fh:
            for line in fh:
                for label, item in (('Max open files', 'nofile'),
                                    ('Max processes', 'nproc'),
                                    ('Max locked memory', 'memlock'),
                                    ('Max core file size', 'core'),
                                    ('Max stack size', 'stack'),
                                    ('Max file size', 'fsize')):
                    if line.startswith(label):
                        parts = line[len(label):].split()
                        out[item] = {'soft': parts[0], 'hard': parts[1]}
    except OSError:
        pass
    return out


def _backup(path):
    if os.path.isfile(path):
        backup = '%s.bak-%d' % (path, int(time.time()))
        shutil.copy2(path, backup)
        return backup
    return None


def _drifted(current, expected_raw):
    if expected_raw in (None, ''):
        return False, None
    try:
        expected = json.loads(expected_raw)
    except ValueError:
        expected = expected_raw
    if json.dumps(current, sort_keys=True, default=str) != \
            json.dumps(expected, sort_keys=True, default=str):
        return True, expected
    return False, None


def _apply_install_ini(section, key, value, expected_raw):
    path = _install_ini_path()
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(path)
    current = parser.get(section, key, fallback=None)
    drifted, expected = _drifted(current, expected_raw)
    if drifted:
        return {'ok': False, 'error': 'install.ini %s.%s drifted (expected %r, found %r) '
                                      '— re-plan.' % (section, key, expected, current)}
    if not parser.has_section(section):
        parser.add_section(section)
    parser.set(section, key, value)
    backup = _backup(path)
    with open(path, 'w') as fh:
        parser.write(fh)
    return {'ok': True, 'path': path, 'backup': backup, 'before': current, 'after': value,
            'note': 'install.ini changes take effect at the next DSS restart.'}


def _sudo_script(path, content, extra=''):
    return ('sudo mkdir -p %s\n'
            "sudo tee %s > /dev/null <<'EOF'\n%s\nEOF\n%s"
            % (os.path.dirname(path), path, content, extra)).strip()


def _apply_systemd(key, value, expected_raw):
    path = _override_path()
    current_map = _read_kv_file(path)
    current = current_map.get(key)
    drifted, expected = _drifted(current, expected_raw)
    if drifted:
        return {'ok': False, 'error': 'systemd override %s drifted (expected %r, found %r) '
                                      '— re-plan.' % (key, expected, current)}
    merged = dict(current_map)
    merged[key] = value
    content = '[Service]\n' + '\n'.join('%s=%s' % kv for kv in sorted(merged.items()))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        backup = _backup(path)
        with open(path, 'w') as fh:
            fh.write(content + '\n')
    except PermissionError:
        return {'ok': False, 'error': 'permission-denied',
                'manualScript': _sudo_script(
                    path, content,
                    'sudo systemctl daemon-reload  # then restart DSS in a window')}
    return {'ok': True, 'path': path, 'backup': backup, 'before': current, 'after': value,
            'note': 'Run `systemctl daemon-reload` and restart the DSS service for the '
                    'drop-in to apply — the toolkit never restarts DSS itself.'}


def _apply_ulimits(item, value, expected_raw):
    current_map = _read_ulimits_file()
    current = current_map.get(item)
    drifted, expected = _drifted(current, expected_raw)
    if drifted:
        return {'ok': False, 'error': 'ulimit %s drifted (expected %r, found %r) — re-plan.'
                                      % (item, expected, current)}
    user = getpass.getuser()
    merged = dict(current_map)
    merged[item] = value
    lines = ['# managed by the Admin Toolkit host-config macro'] + \
            ['%s - %s %s' % (user, k, v) for k, v in sorted(merged.items())]
    content = '\n'.join(lines)
    try:
        backup = _backup(_ULIMITS_PATH)
        with open(_ULIMITS_PATH, 'w') as fh:
            fh.write(content + '\n')
    except PermissionError:
        return {'ok': False, 'error': 'permission-denied',
                'manualScript': _sudo_script(_ULIMITS_PATH, content)}
    return {'ok': True, 'path': _ULIMITS_PATH, 'backup': backup,
            'before': current, 'after': value,
            'note': 'limits.d applies to NEW sessions of user %r — DSS picks it up at its '
                    'next full restart.' % user}


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        operation = str(self.config.get('operation') or 'read').strip()
        try:
            if operation == 'read':
                result = {
                    'ok': True,
                    'installIni': _read_install_ini(),
                    'systemdOverride': _read_kv_file(_override_path()),
                    'ulimits': _read_ulimits_file(),
                    'effectiveLimits': _effective_limits(),
                    'paths': {'install-ini': _install_ini_path(),
                              'systemd-override': _override_path(),
                              'ulimits': _ULIMITS_PATH},
                    'systemdUnit': _dss_unit(),
                }
            elif operation == 'apply':
                file = str(self.config.get('file') or '').strip()
                section = str(self.config.get('section') or '').strip() or None
                key = str(self.config.get('key') or '').strip()
                value = str(self.config.get('value') or '').strip()
                expected_raw = self.config.get('expected_current')
                ok, reason = policy.validate(file, section, key, value)
                if not ok:
                    result = {'ok': False, 'error': 'policy refused: %s' % reason}
                elif file == 'install-ini':
                    result = _apply_install_ini(section, key, value, expected_raw)
                elif file == 'systemd-override':
                    result = _apply_systemd(key, value, expected_raw)
                else:
                    result = _apply_ulimits(key, value, expected_raw)
            else:
                result = {'ok': False, 'error': 'unknown operation %r' % operation}
        except Exception as exc:
            result = {'ok': False,
                      'error': '%s: %s' % (type(exc).__name__, str(exc)[:300])}
        return json.dumps(result)
