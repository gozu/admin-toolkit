"""DSS general-settings path policy: security/auth/license paths are BLOCKED,
everything else may be mutated through the gated settings-set action.

Path syntax: dot-separated keys with optional list indices —
``containerSettings.executionConfigs[2].kubernetesRuntimeConfig``.
The executor re-checks the blacklist at execute time (never trusts the plan).
"""

import re

# A path is blocked when its FIRST segment (lowercased) contains any of these
# substrings — the auth/security/licensing families of general settings.
BLOCKED_FIRST_SEGMENT_SUBSTRINGS = (
    'security',           # *security*, globalApiKeysSecurity, personalApiKeysSecurity
    'ldap',
    'sso',
    'saml',
    'openid',
    'azuread',
    'licens',             # licensing, license
    'audittrail',
    'authentication',
)

# Any segment anywhere in the path matching this is blocked — secret material
# must never flow through (or be readable via) the settings mutator. The same
# regex drives _redact_secrets on the read side, so a credential key name that
# escapes here is BOTH readable and writable via connection-update / settings-set.
# `<word>key` catches the cloud-credential families that carry no `secret`/
# `token` substring (Azure `accountKey`/`storageAccountKey`, S3 `accessKey`,
# `sharedKey`, `signingKey`); `keyfile`/`keyjson` catch the leading-`key`
# credential-file variants. Bare `key` (DSS {key,value} property lists) and
# `keyspace`/`keyColumns` deliberately do NOT match.
BLOCKED_SEGMENT_RE = re.compile(
    r'(password|passphrase|secret|credential|privatekey|apikey|token|keytab'
    r'|keyfile|keyjson|[a-z0-9]+key)', re.IGNORECASE)

_SEGMENT_RE = re.compile(r'^([A-Za-z0-9_$@-]+)((\[\d+\])*)$')


class SettingsPathError(ValueError):
    pass


def parse_path(path):
    """``a.b[2].c`` → ['a', 'b', 2, 'c']. Raises SettingsPathError on garbage."""
    if not isinstance(path, str) or not path.strip():
        raise SettingsPathError('path must be a non-empty string')
    segments = []
    for part in path.strip().split('.'):
        m = _SEGMENT_RE.match(part)
        if not m:
            raise SettingsPathError('invalid path segment %r' % part)
        segments.append(m.group(1))
        for idx in re.findall(r'\[(\d+)\]', m.group(2) or ''):
            segments.append(int(idx))
    return segments


def check_path(path, extra_blocked=()):
    """Blacklist check. Returns (ok, reason). ``extra_blocked`` is a list of
    admin-configured path prefixes (case-insensitive, from
    ``settings_set_blocked_extra``)."""
    try:
        segments = parse_path(path)
    except SettingsPathError as exc:
        return False, str(exc)
    first = str(segments[0]).lower()
    for sub in BLOCKED_FIRST_SEGMENT_SUBSTRINGS:
        if sub in first:
            return False, ('path %r is blocked: top-level %r is a security/auth/licensing '
                           'setting — never mutable by agents' % (path, segments[0]))
    for seg in segments:
        if isinstance(seg, str) and BLOCKED_SEGMENT_RE.search(seg):
            return False, ('path %r is blocked: segment %r matches the secret-material '
                           'blacklist' % (path, seg))
    normalized = path.strip().lower()
    for prefix in extra_blocked:
        p = str(prefix or '').strip().lower()
        if p and normalized.startswith(p):
            return False, ('path %r is blocked by the admin-configured blacklist entry %r '
                           '(plugin setting settings_set_blocked_extra)' % (path, prefix))
    return True, 'ok'


def redact_secrets(node):
    """Structure-preserving secret redaction for settings reads. A scalar is
    masked when its own key matches BLOCKED_SEGMENT_RE, when its {key|name,
    value} property-row's key/name matches, or when the row carries DSS's own
    secret:true marker. Dicts/lists always recurse — a matching parent key
    never hides a whole subtree, so non-secret flags under keys like
    globalApiKeysSecurity stay readable. check_path uses the same regex on the
    write side, so anything masked here is also immutable via settings-set."""
    if isinstance(node, dict):
        prop = node.get('key') if isinstance(node.get('key'), str) else None
        if prop is None and isinstance(node.get('name'), str):
            prop = node['name']
        prop_secret = bool(node.get('secret')) or bool(
            prop and BLOCKED_SEGMENT_RE.search(prop))
        out = {}
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                out[k] = redact_secrets(v)
            elif k == 'value' and prop_secret and v not in (None, '', False, True):
                out[k] = '<redacted>'
            elif (isinstance(k, str) and BLOCKED_SEGMENT_RE.search(k)
                    and v not in (None, '', False, True)):
                out[k] = '<redacted>'
            else:
                out[k] = v
        return out
    if isinstance(node, list):
        return [redact_secrets(v) for v in node]
    return node


def get_at(obj, path):
    """Value at `path` in nested dict/list `obj`; None when any hop is missing."""
    current = obj
    for seg in parse_path(path):
        if isinstance(seg, int):
            if not isinstance(current, list) or seg >= len(current):
                return None
            current = current[seg]
        else:
            if not isinstance(current, dict) or seg not in current:
                return None
            current = current[seg]
    return current


def set_at(obj, path, value):
    """Set `path` to `value` in-place. Every intermediate container must
    already exist (a new FINAL dict key is allowed); list indices must be in
    range. Raises SettingsPathError otherwise — settings-set never fabricates
    whole subtrees."""
    segments = parse_path(path)
    current = obj
    for i, seg in enumerate(segments[:-1]):
        if isinstance(seg, int):
            if not isinstance(current, list) or seg >= len(current):
                raise SettingsPathError('index [%d] out of range at %r'
                                        % (seg, '.'.join(map(str, segments[:i + 1]))))
            current = current[seg]
        else:
            if not isinstance(current, dict) or seg not in current:
                raise SettingsPathError('intermediate key %r does not exist — settings-set '
                                        'never creates new subtrees' % seg)
            current = current[seg]
    last = segments[-1]
    if isinstance(last, int):
        if not isinstance(current, list) or last >= len(current):
            raise SettingsPathError('final index [%d] out of range' % last)
        current[last] = value
    else:
        if not isinstance(current, dict):
            raise SettingsPathError('parent of %r is not an object' % last)
        current[last] = value
    return obj
