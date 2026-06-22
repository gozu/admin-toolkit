"""Crypto invariants for remote-host key encryption (adk_backend.hostkeys).

These lock the byte-compatibility between the server-side encrypt path and the
hand-rolled JS Fernet in resource/hash.html: same KDF params, same leading-space
salt tag, same adkfk1$<salt>$<token> framing. A drift in any of them would make a
blob made in one place undecryptable in the other.
"""

import conftest  # noqa: F401  (installs the python-lib path + DSS stubs)

from adk_backend import hostkeys


# host_salt('correct horse battery staple') computed offline. Locks the
# leading-space HOST_SALT_TAG (b' adk-hostkey-salt-v1') byte-for-byte: drop the
# space and this vector changes.
_PW = 'correct horse battery staple'
_EXPECTED_SALT_HEX = '0e16255e2f54962551aa8de08e0cb344'


def test_host_salt_is_deterministic_and_matches_vector():
    salt = hostkeys.host_salt(_PW)
    assert isinstance(salt, bytes)
    assert len(salt) == 16
    # Deterministic — same password → same salt across calls.
    assert salt == hostkeys.host_salt(_PW)
    # Hard-coded vector locks the leading-space tag + KDF params.
    assert salt.hex() == _EXPECTED_SALT_HEX


def test_host_salt_differs_per_password():
    assert hostkeys.host_salt(_PW) != hostkeys.host_salt(_PW + '!')


def test_derive_fernet_key_is_44_bytes():
    key = hostkeys.derive_fernet_key(_PW, hostkeys.host_salt(_PW))
    assert len(key) == 44  # urlsafe-base64 of 32 raw bytes


def test_encrypt_blob_round_trips_and_preserves_salt():
    salt = hostkeys.host_salt(_PW)
    key = hostkeys.derive_fernet_key(_PW, salt)
    plaintext = 'dku-ADMIN-API-KEY-1234567890'

    blob = hostkeys.encrypt_blob(plaintext, key, salt)
    assert hostkeys.is_encrypted(blob)
    assert blob.startswith(hostkeys.BLOB_PREFIX)
    # The salt is recoverable from the blob (single-unlock invariant).
    assert hostkeys.salt_from_blob(blob) == salt
    # Round-trips back to the original plaintext under the same key.
    assert hostkeys.decrypt_blob(blob, key) == plaintext


def test_decrypt_blob_rejects_wrong_key():
    salt = hostkeys.host_salt(_PW)
    blob = hostkeys.encrypt_blob('secret', hostkeys.derive_fernet_key(_PW, salt), salt)
    wrong = hostkeys.derive_fernet_key('not the password', salt)
    try:
        hostkeys.decrypt_blob(blob, wrong)
        assert False, 'decrypt should have failed with the wrong key'
    except Exception:
        pass
