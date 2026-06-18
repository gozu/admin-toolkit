"""Reversible, password-based encryption for remote-host API keys at rest.

Pure crypto + a process-global key cache. NO Flask/dataiku imports, so this is
safe to import from both `clients.py` (preset resolution) and the auth route
(unlock) without a circular import.

Scheme
------
- KDF: PBKDF2-HMAC-SHA256, 600 000 iterations, 32-byte output (same params as
  hash.html). Salt = 16 random bytes, generated in the browser tool.
- Cipher: Fernet (AES-128-CBC + HMAC-SHA256, authenticated). The Fernet key is
  `base64.urlsafe_b64encode(PBKDF2(password, salt))` — that 44-byte token IS the
  "derived key" the unlock cookie carries.
- Stored blob (pasted into a preset's API Key field):
    `adkfk1$<b64url-salt>$<fernet-token>`
  Absent prefix → legacy plaintext key (backward compatible).

All hosts share ONE password and ONE salt, so one derived key opens every blob.
A blob whose salt differs from the active key's salt simply fails to decrypt.

The derived key rides an HttpOnly cookie; each request repopulates the process
cache below so background threads/loaders (which carry no cookie) can decrypt,
and so the key survives until the next worker restart.
"""

import base64
import hashlib
import threading
from typing import Optional

BLOB_PREFIX = 'adkfk1$'
KEY_COOKIE_NAME = 'admin_toolkit_hostkey'

# Same KDF params as hash.html / the red-secret verifier.
_PBKDF2_ITERATIONS = 600000
_PBKDF2_DKLEN = 32


class RemoteKeysLocked(Exception):
    """Raised when an encrypted preset key is encountered but no decryption key
    is available (no unlock cookie / no cached key, or the key can't open it).
    Turned into a 409 `remote-keys-locked` so the frontend pops the unlock modal.
    The first arg, if present, is the offending host id."""
    pass


def is_encrypted(value: str) -> bool:
    """True if `value` is an adkfk1$ encrypted blob (vs a legacy plaintext key)."""
    return isinstance(value, str) and value.startswith(BLOB_PREFIX)


def _b64url_decode(text: str) -> bytes:
    """Decode unpadded-or-padded urlsafe base64."""
    return base64.urlsafe_b64decode(text + ('=' * (-len(text) % 4)))


def derive_fernet_key(password: str, salt: bytes) -> bytes:
    """PBKDF2(password, salt) → urlsafe-base64 (the 44-byte Fernet key)."""
    derived = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt,
                                  _PBKDF2_ITERATIONS, dklen=_PBKDF2_DKLEN)
    return base64.urlsafe_b64encode(derived)


def salt_from_blob(blob: str) -> bytes:
    """Extract the 16-byte salt from an adkfk1$<salt>$<token> blob."""
    parts = blob.split('$', 2)
    if len(parts) != 3 or parts[0] != 'adkfk1':
        raise ValueError('not an adkfk1 blob')
    return _b64url_decode(parts[1])


def decrypt_blob(blob: str, fernet_key: Optional[bytes]) -> str:
    """Decrypt an adkfk1$ blob with `fernet_key`. Raises on a missing key or an
    InvalidToken (wrong password / mismatched salt / corrupt blob)."""
    if not fernet_key:
        raise RemoteKeysLocked()
    parts = blob.split('$', 2)
    if len(parts) != 3 or parts[0] != 'adkfk1':
        raise ValueError('not an adkfk1 blob')
    token = parts[2]
    # Lazy import: only decryption needs `cryptography`; module import stays safe
    # even before the code-env rebuild that pins it lands.
    from cryptography.fernet import Fernet
    return Fernet(fernet_key).decrypt(token.encode('ascii')).decode('utf-8')


# ── Process-global active key cache ──────────────────────────────────────────
# Populated from the unlock cookie on each request (see _attach_client) so that
# background threads/loaders with no request cookie can still decrypt, and so
# the key persists across requests until the worker restarts.
_active_key: Optional[bytes] = None
_active_key_lock = threading.Lock()


def set_active_key(fernet_key: bytes) -> None:
    global _active_key
    with _active_key_lock:
        _active_key = fernet_key


def get_active_key() -> Optional[bytes]:
    with _active_key_lock:
        return _active_key


def clear_active_key() -> None:
    global _active_key
    with _active_key_lock:
        _active_key = None
