"""Unit tests for the DSS settings-path blacklist and path helpers."""

import pytest

from atk_agent_common.policies import settings_paths as sp


# ---- blacklist ----

@pytest.mark.parametrize('path', [
    'security.someFlag',
    'globalApiKeysSecurity.mode',
    'personalApiKeysSecurity.mode',
    'ldapSettings.url',
    'ssoSettings.protocol',
    'samlSPParams.x',
    'openIDConnectSettings.clientId',
    'azureADSettings.tenant',
    'licensing.gracePeriod',
    'auditTrail.targets[0].type',
    'authenticationRealms[0].type',
])
def test_blocked_first_segments(path):
    ok, reason = sp.check_path(path)
    assert not ok, path
    assert 'blocked' in reason


@pytest.mark.parametrize('path', [
    'containerSettings.executionConfigs[0].repositoryPassword',
    'smtpSettings.password',
    'anything.apiKeys[0]',
    'x.privateKeyPath',
    'y.clientSecret',
    'z.credentialStore.a',
    'kerberos.keytabPath',
    'mesh.authToken',
    # cloud-credential key families with no secret/token substring (regression:
    # these escaped the blacklist AND redaction before the <word>key broadening)
    'params.accountKey',
    'params.storageAccountKey',
    'params.accessKey',
    'params.sharedKey',
    'params.signingKey',
    'params.passphrase',
    'params.keyFile',
    'params.keyJson',
])
def test_blocked_secret_segments(path):
    ok, reason = sp.check_path(path)
    assert not ok, path


@pytest.mark.parametrize('path', [
    'containerSettings.executionConfigs[2].kubernetesRuntimeConfig.kubernetesResources.memLimitMB',
    'maxRunningActivities',
    'javaMemory.backendXmx',
    'limits.attachmentBytes',
    'notebooks.autoUnloadEnabled',
    # non-secret keys the <word>key broadening must NOT catch: bare {key,value}
    # property leaves and keyspace/keyColumns-style config names
    'dkuProperties[0].key',
    'params.keyspace',
    'params.keyColumns',
])
def test_allowed_paths(path):
    ok, reason = sp.check_path(path)
    assert ok, reason


def test_extra_blocked_prefixes():
    ok, reason = sp.check_path('containerSettings.executionConfigs[0].name',
                               extra_blocked=['containerSettings'])
    assert not ok and 'settings_set_blocked_extra' in reason
    ok, _ = sp.check_path('maxRunningActivities', extra_blocked=['containerSettings'])
    assert ok


def test_garbage_path_refused():
    for bad in ('', '..', 'a..b', 'a.[0]', 'a b.c', 'a;drop', None):
        ok, _ = sp.check_path(bad)
        assert not ok, bad


# ---- parse/get/set round-trip ----

def test_parse_path():
    assert sp.parse_path('a.b[2].c') == ['a', 'b', 2, 'c']
    assert sp.parse_path('a[0][1]') == ['a', 0, 1]


def test_get_at():
    obj = {'containerSettings': {'executionConfigs': [{'name': 'a'}, {'name': 'b'}]}}
    assert sp.get_at(obj, 'containerSettings.executionConfigs[1].name') == 'b'
    assert sp.get_at(obj, 'containerSettings.executionConfigs[9].name') is None
    assert sp.get_at(obj, 'missing.path') is None


def test_set_at_round_trip():
    obj = {'limits': {'attachmentBytes': 1000}, 'configs': [{'v': 1}]}
    sp.set_at(obj, 'limits.attachmentBytes', 5000)
    assert obj['limits']['attachmentBytes'] == 5000
    sp.set_at(obj, 'configs[0].v', 2)
    assert obj['configs'][0]['v'] == 2
    # new FINAL key on an existing dict is allowed
    sp.set_at(obj, 'limits.newFlag', True)
    assert obj['limits']['newFlag'] is True


def test_set_at_never_creates_subtrees():
    obj = {'a': {}}
    with pytest.raises(sp.SettingsPathError):
        sp.set_at(obj, 'a.b.c', 1)
    with pytest.raises(sp.SettingsPathError):
        sp.set_at(obj, 'nope[0]', 1)
    with pytest.raises(sp.SettingsPathError):
        sp.set_at({'xs': [1]}, 'xs[5]', 1)


def test_redact_secrets_masks_secret_scalars():
    node = {
        'internalDatabase': {'connection': {'params': {'password': 'hunter2',
                                                       'port': 5432}}},
        'deployerClientSettings': {'apiKey': 'dkuaps-abc', 'url': 'https://x'},
    }
    r = sp.redact_secrets(node)
    assert r['internalDatabase']['connection']['params']['password'] == '<redacted>'
    assert r['internalDatabase']['connection']['params']['port'] == 5432
    assert r['deployerClientSettings']['apiKey'] == '<redacted>'
    assert r['deployerClientSettings']['url'] == 'https://x'


def test_redact_secrets_property_rows():
    rows = [
        {'key': 'somePassword', 'value': 'x', 'secret': False},
        {'key': 'memory.max', 'value': '38g', 'secret': False},
        {'name': 'harmless', 'value': 'y', 'secret': True},
    ]
    r = sp.redact_secrets(rows)
    assert r[0]['value'] == '<redacted>'          # key name matches the regex
    assert r[1]['value'] == '38g'                 # plain config row survives
    assert r[2]['value'] == '<redacted>'          # DSS's own secret:true marker


def test_redact_secrets_preserves_structure_and_flags():
    node = {'globalApiKeysSecurity': {'enabled': True, 'clearanceLevel': 3},
            'allowPerUserSSHKey': True, 'emptyToken': ''}
    r = sp.redact_secrets(node)
    # a matching parent key never hides its subtree; booleans/empties untouched
    assert r['globalApiKeysSecurity'] == {'enabled': True, 'clearanceLevel': 3}
    assert r['allowPerUserSSHKey'] is True
    assert r['emptyToken'] == ''
