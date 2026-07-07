"""Master-password resolution + legacy migration (adk_backend.routes.auth).

One `master_password` plugin setting covers the browser red unlock, host-key
encryption, and the headless agents. These tests lock the upgrade contract:
legacy `red_actions_password` / `host_keys_password` plaintexts still resolve
(and get migrated into `master_password` exactly once), and a hash-only
`red_actions_secret` install keeps verifying through the PBKDF2 path.
"""

import base64
import hashlib

import conftest  # noqa: F401  (installs the python-lib path + DSS stubs)
import pytest

from adk_backend.routes import auth
from adk_backend import hostkeys


class _FakeSettings:
    def __init__(self, config):
        self._raw = {'config': config}
        self.saved = 0

    def get_raw(self):
        return self._raw

    def save(self):
        self.saved += 1


def _install(monkeypatch, config):
    """Point auth.py's settings reads at an in-memory config dict."""
    settings = _FakeSettings(config)

    class _Plugin:
        def get_settings(self):
            return settings

    class _Client:
        def get_plugin(self, plugin_id):
            assert plugin_id == 'admin-toolkit'
            return _Plugin()

    monkeypatch.setattr(auth.dataiku, 'api_client', lambda: _Client(), raising=False)
    monkeypatch.setattr(auth, '_migrated_master_password', False)
    return settings


def _legacy_hash(password):
    salt = b'0123456789abcdef'
    derived = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 1000)
    return 'pbkdf2_sha256$1000$%s$%s' % (
        base64.b64encode(salt).decode(), base64.b64encode(derived).decode())


def test_master_password_wins_over_legacy(monkeypatch):
    _install(monkeypatch, {'master_password': 'new-pw',
                           'red_actions_password': 'old-pw',
                           'red_actions_secret': _legacy_hash('older-pw')})
    assert auth._red_secret_source() == ('plain', 'new-pw')
    assert auth._verify_red_password('new-pw', 'plain', 'new-pw')
    assert not auth._verify_red_password('old-pw', 'plain', 'new-pw')


def test_legacy_plaintext_falls_back_and_migrates_once(monkeypatch):
    settings = _install(monkeypatch, {'master_password': '',
                                      'red_actions_password': 'huzzah'})
    assert auth._red_secret_source() == ('plain', 'huzzah')
    # Migrated into master_password so the value survives DSS pruning
    # undeclared config keys on the next settings save.
    assert settings.get_raw()['config']['master_password'] == 'huzzah'
    assert settings.saved == 1
    auth._red_secret_source()
    assert settings.saved == 1  # once per process


def test_legacy_host_keys_password_also_counts(monkeypatch):
    _install(monkeypatch, {'host_keys_password': 'keys-pw'})
    assert auth._master_password() == 'keys-pw'


def test_hash_only_install_verifies_through_pbkdf2(monkeypatch):
    settings = _install(monkeypatch, {'red_actions_secret': _legacy_hash('s3cret')})
    kind, stored = auth._red_secret_source()
    assert kind == 'hash'
    assert auth._verify_red_password('s3cret', kind, stored)
    assert not auth._verify_red_password('wrong', kind, stored)
    # Nothing to migrate — the plaintext is unknown.
    assert settings.saved == 0


def test_nothing_configured_is_locked(monkeypatch):
    _install(monkeypatch, {})
    assert auth._red_secret_source() == (None, '')
    assert auth._red_token_exp_ms('anything.anything') == 0


def test_signing_key_plain_is_strengthened_and_stable(monkeypatch):
    k1 = auth._red_signing_key('plain', 'pw-1')
    assert k1 == auth._red_signing_key('plain', 'pw-1')  # cached + deterministic
    assert k1 != auth._red_signing_key('plain', 'pw-2')
    # Legacy hash strings keep the historical derivation → old cookies survive.
    legacy = _legacy_hash('pw-1')
    expected = hashlib.sha256(legacy.encode() + auth._RED_SIGNING_CONTEXT).digest()
    assert auth._red_signing_key('hash', legacy) == expected


def test_auto_unlock_host_keys_from_master(monkeypatch):
    password = 'fleet-pw'
    salt = hostkeys.host_salt(password)
    key = hostkeys.derive_fernet_key(password, salt)
    blob = hostkeys.encrypt_blob('dku-REMOTE-KEY', key, salt)

    _install(monkeypatch, {'master_password': password})
    hostkeys.clear_active_key()
    try:
        fernet_key = auth.auto_unlock_host_keys(blob)
        assert fernet_key is not None
        assert hostkeys.decrypt_blob(blob, fernet_key) == 'dku-REMOTE-KEY'
        assert hostkeys.get_active_key() == fernet_key  # cached for the process

        # Wrong master → locked, nothing cached.
        hostkeys.clear_active_key()
        _install(monkeypatch, {'master_password': 'different-pw'})
        assert auth.auto_unlock_host_keys(blob) is None
        assert hostkeys.get_active_key() is None
    finally:
        hostkeys.clear_active_key()


def test_token_round_trip_with_plain_master(monkeypatch):
    _install(monkeypatch, {'master_password': 'pw'})
    import time
    exp = int(time.time()) + 60
    token = auth._make_red_token('plain', 'pw', exp)
    assert auth._red_token_exp_ms(token) == exp * 1000
    assert auth._verify_red_token(token)
    # Rotating the password invalidates outstanding tokens.
    _install(monkeypatch, {'master_password': 'rotated'})
    assert auth._red_token_exp_ms(token) == 0
