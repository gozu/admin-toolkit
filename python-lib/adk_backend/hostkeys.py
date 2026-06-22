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

# Deterministic-salt derivation tag. The leading space is significant and MUST
# match hash.html's `HOST_SALT_TAG = ' adk-hostkey-salt-v1'` byte-for-byte, or a
# blob made in the browser tool and one made server-side would get different
# salts → different keys → cross-decrypt failures.
HOST_SALT_TAG = b' adk-hostkey-salt-v1'


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


def host_salt(password: str) -> bytes:
    """Deterministic 16-byte salt = PBKDF2(pw, HOST_SALT_TAG, 600k)[:16].

    Mirrors hash.html's hostSalt(): the salt is derived through the full KDF (not
    a bare hash) so the stored salt can't serve as a fast offline password
    verifier. Same password → same salt → one derived key opens every blob, with
    nothing for the admin to manage. Used only as the first-key fallback (path A):
    once any encrypted preset exists, its salt is reused via salt_from_blob."""
    derived = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                                  HOST_SALT_TAG, _PBKDF2_ITERATIONS, dklen=_PBKDF2_DKLEN)
    return derived[:16]


def encrypt_blob(plaintext: str, fernet_key: bytes, salt: bytes) -> str:
    """Inverse of decrypt_blob → 'adkfk1$<b64url-salt>$<fernet-token>'.

    cryptography's Fernet.encrypt() produces the same 0x80│ts│iv│ct│hmac frame
    the hand-rolled JS Fernet in hash.html emits, so blobs round-trip across both
    implementations. Salt is stored unpadded, token padded — matching hash.html."""
    from cryptography.fernet import Fernet
    token = Fernet(fernet_key).encrypt(plaintext.encode('utf-8')).decode('ascii')
    salt_b64 = base64.urlsafe_b64encode(salt).decode('ascii').rstrip('=')
    return f'{BLOB_PREFIX}{salt_b64}${token}'


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
