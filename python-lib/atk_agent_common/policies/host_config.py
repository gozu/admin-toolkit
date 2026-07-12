"""Policy for host-level configuration changes (host-config-set).

Three writable surfaces, each with its own whitelist, enforced here at
plan/propose time AND re-enforced inside the host-config macro at apply time:

  install-ini      — DATA_DIR/install.ini keys; secret-material keys refused.
  systemd-override — a drop-in override for the DSS unit; only Limit*/Timeout*/
                     Restart* directives (no Environment/ExecStart injection).
  ulimits          — /etc/security/limits.d entries; classic resource items only.
"""

import re

from . import settings_paths

FILES = ('install-ini', 'systemd-override', 'ulimits')

# install.ini: any [section] key is editable EXCEPT secret material — reuse
# the settings-path secret blacklist so 'password'/'key'/'token'-ish keys die.
_SECRET_RE = settings_paths.BLOCKED_SEGMENT_RE

# systemd drop-in: resource/lifecycle directives only. Environment*/Exec*
# would be code/credential injection into the DSS process.
SYSTEMD_KEYS = ('LimitNOFILE', 'LimitNPROC', 'LimitCORE', 'LimitMEMLOCK',
                'LimitSTACK', 'LimitFSIZE', 'TimeoutStartSec', 'TimeoutStopSec',
                'RestartSec')

# /etc/security/limits.d items (soft+hard written together).
ULIMIT_ITEMS = ('nofile', 'nproc', 'memlock', 'core', 'fsize', 'stack')

_INI_TOKEN_RE = re.compile(r'^[A-Za-z0-9_.\-]{1,64}$')
_VALUE_RE = re.compile(r'^[A-Za-z0-9_.:/\-]{0,128}$')


def validate(file, section, key, value):
    """(ok, reason) for one proposed host-config change."""
    if file not in FILES:
        return False, 'file must be one of: %s.' % ', '.join(FILES)
    value = str(value)
    if file == 'install-ini':
        if not section or not _INI_TOKEN_RE.match(str(section)):
            return False, 'install-ini needs a section name ([a-zA-Z0-9_.-], max 64 chars).'
        if not key or not _INI_TOKEN_RE.match(str(key)):
            return False, 'install-ini needs a key name ([a-zA-Z0-9_.-], max 64 chars).'
        if _SECRET_RE.search(str(key)) or _SECRET_RE.search(str(section)):
            return False, ('install.ini entry %r/%r matches the secret-material blacklist — '
                           'credentials are never agent-mutable.' % (section, key))
    elif file == 'systemd-override':
        if key not in SYSTEMD_KEYS:
            return False, ('systemd directive %r refused — only %s may be set.'
                           % (key, ', '.join(SYSTEMD_KEYS)))
        if not re.match(r'^(infinity|\d{1,12}[smhKMG]?)$', value):
            return False, 'systemd value must be a number (optional s/m/h/K/M/G suffix) or "infinity".'
    elif file == 'ulimits':
        if key not in ULIMIT_ITEMS:
            return False, ('ulimit item %r refused — only %s may be set.'
                           % (key, ', '.join(ULIMIT_ITEMS)))
        if not re.match(r'^(unlimited|\d{1,12})$', value):
            return False, 'ulimit value must be a plain number or "unlimited".'
    if not _VALUE_RE.match(value):
        return False, 'value contains characters outside [a-zA-Z0-9_.:/-] or exceeds 128 chars.'
    return True, None
