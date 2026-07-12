"""Actuator confirm tokens: the cryptographic handshake between plan and execute.

plan-admin-action mints `HMAC(key, action|host|canonical(target)|expires)`;
execute-admin-action recomputes it. Any drift — different action, host, target,
or an expired timestamp — invalidates the token, so an agent can only ever
execute the EXACT plan a human saw. The key derives from the configured
red-actions password: rotating the password instantly voids all outstanding
tokens (same self-invalidation property as the backend's own red tokens).
"""

import base64
import hashlib
import hmac
import json
import time

TOKEN_TTL_SECONDS = 15 * 60
_SIGNING_CONTEXT = b'atk-agents-confirm-v1'


class ConfirmTokenError(Exception):
    pass


def _signing_key(red_password):
    if not red_password:
        raise ConfirmTokenError('No Advanced Actions password configured — cannot mint confirm tokens.')
    return hashlib.sha256(red_password.encode('utf-8') + _SIGNING_CONTEXT).digest()


def _strip_nulls(node):
    """Drop dict keys whose value is None (recursively; list order preserved).

    The DSS agent-tool transport serializer drops null-valued keys from tool
    I/O, so a canonical carrying None values (e.g. a create's scenarioId=None,
    or an expectedCurrent email that is genuinely unset) serializes with those
    keys at mint time but WITHOUT them on the echoed-back execute side —
    breaking the token. Normalizing here, symmetrically in mint and verify,
    makes signing match that reality. It is behaviour-preserving: executors
    read target.get(k), so a key set to None and an absent key already act
    identically, and any action that survives transport unchanged has no None
    keys to drop (so this is a no-op for it)."""
    if isinstance(node, dict):
        return {k: _strip_nulls(v) for k, v in node.items() if v is not None}
    if isinstance(node, list):
        return [_strip_nulls(v) for v in node]
    return node


def canonical_target(target):
    """Deterministic serialization of the target (dict/str) for signing."""
    if isinstance(target, dict):
        return json.dumps(_strip_nulls(target), sort_keys=True, separators=(',', ':'))
    return str(target)


def _b64(data):
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def _b64d(text):
    return base64.urlsafe_b64decode(text + ('=' * (-len(text) % 4)))


def mint(red_password, action, host, target, now=None):
    exp = int(now or time.time()) + TOKEN_TTL_SECONDS
    payload = json.dumps({'a': action, 'h': host, 't': canonical_target(target), 'e': exp},
                         sort_keys=True, separators=(',', ':')).encode('utf-8')
    sig = hmac.new(_signing_key(red_password), payload, hashlib.sha256).digest()
    return _b64(payload) + '.' + _b64(sig), exp


def verify(red_password, token, action, host, target, now=None):
    """Raise ConfirmTokenError unless `token` matches this exact action/host/
    target and is unexpired."""
    try:
        payload_b64, sig_b64 = (token or '').split('.', 1)
        payload = _b64d(payload_b64)
        expected = hmac.new(_signing_key(red_password), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(_b64d(sig_b64), expected):
            raise ConfirmTokenError('Confirm token signature is invalid (password rotated, or token forged).')
        data = json.loads(payload.decode('utf-8'))
    except ConfirmTokenError:
        raise
    except Exception:
        raise ConfirmTokenError('Malformed confirm token — re-run plan-admin-action to get a fresh one.')
    if int(data.get('e', 0)) < int(now or time.time()):
        raise ConfirmTokenError('Confirm token expired (15 min TTL) — re-run plan-admin-action.')
    if data.get('a') != action or data.get('h') != host or data.get('t') != canonical_target(target):
        raise ConfirmTokenError('Confirm token does not match this action/host/target — the plan '
                                'and the execution drifted. Re-run plan-admin-action.')
    return True


def token_hash(token):
    """Non-reversible reference for audit rows."""
    return hashlib.sha256((token or '').encode('utf-8')).hexdigest()[:16]
