"""Story host resolution — the same remote-dss-host presets the toolkit uses,
readable without flask so the story-collect macro can enumerate hosts.

The hub is always host 'local'; every `remote-dss-host` preset on the hub
becomes one remote host. Encrypted API keys (adkfk1$ blobs) are decrypted with
the process-cached key when available; a locked key raises loudly — a host we
cannot reach must fail the collection (and trigger the failure email), never
silently vanish from the fleet.
"""
from typing import Any, Dict, List, Optional

from adk_backend import hostkeys


class StoryHostKeyLocked(Exception):
    """A remote preset's API key is encrypted and no decryption key is cached."""

    def __init__(self, host_id: str):
        self.host_id = host_id
        super().__init__(
            "Remote host '%s' has an encrypted API key and no unlock key is active. "
            "Unlock remote hosts from the toolkit UI, or store a plaintext key for "
            "scheduled Story collection." % host_id)


def _decrypt_api_key(host_id: str, raw_key: str) -> str:
    if not hostkeys.is_encrypted(raw_key):
        return raw_key
    active = hostkeys.get_active_key()
    if active is None:
        raise StoryHostKeyLocked(host_id)
    try:
        return hostkeys.decrypt_blob(raw_key, active)
    except Exception:
        raise StoryHostKeyLocked(host_id)


def _remote_presets(local_client: Any) -> List[Dict[str, Any]]:
    raw = local_client.get_plugin('admin-toolkit').get_settings().get_raw()
    presets = raw.get('presets') if isinstance(raw, dict) else None
    if not isinstance(presets, list):
        return []
    out: List[Dict[str, Any]] = []
    for preset in presets:
        if not isinstance(preset, dict):
            continue
        # DSS prefixes preset types as `parameter-set-<plugin-id>-<name>`;
        # match on suffix so both raw and prefixed shapes work.
        if not (preset.get('type') or '').endswith('remote-dss-host'):
            continue
        cfg = preset.get('config') or {}
        name = preset.get('name')
        if not name or not cfg.get('url'):
            continue
        out.append({
            'id': name,
            'label': cfg.get('label') or name,
            'url': (cfg.get('url') or '').rstrip('/'),
            'apiKey': cfg.get('apiKey') or '',
            'verifyTls': bool(cfg.get('verifyTls', True)),
        })
    return out


def _build_remote_client(cfg: Dict[str, Any]) -> Any:
    import dataikuapi
    client = dataikuapi.DSSClient(cfg['url'], _decrypt_api_key(cfg['id'], cfg['apiKey']))
    if not cfg.get('verifyTls', True) and hasattr(client, '_session'):
        client._session.verify = False
    return client


def list_story_hosts(local_client: Optional[Any] = None,
                     only_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """All hosts Story collects from: [{'id','label','client','isLocal'}].

    only_ids restricts the fleet (the collect macro's `hosts` csv param);
    asking for an unknown id raises — a typo must not silently shrink coverage.
    """
    if local_client is None:
        import dataiku
        local_client = dataiku.api_client()
    descriptors: List[Dict[str, Any]] = [
        {'id': 'local', 'label': 'Local (hub)', 'preset': None},
    ]
    descriptors.extend(
        {'id': p['id'], 'label': p['label'], 'preset': p} for p in _remote_presets(local_client)
    )
    if only_ids:
        wanted = [h.strip() for h in only_ids if h and h.strip()]
        by_id = {d['id']: d for d in descriptors}
        missing = [h for h in wanted if h not in by_id]
        if missing:
            raise ValueError('Unknown Story host id(s): %s (known: %s)'
                             % (', '.join(missing), ', '.join(by_id)))
        descriptors = [by_id[h] for h in wanted]
    # Clients are built only for the hosts actually collected, so a locked key
    # on an excluded host cannot block a targeted run.
    hosts: List[Dict[str, Any]] = []
    for desc in descriptors:
        if desc['preset'] is None:
            hosts.append({'id': 'local', 'label': desc['label'],
                          'client': local_client, 'isLocal': True})
        else:
            hosts.append({'id': desc['id'], 'label': desc['label'],
                          'client': _build_remote_client(desc['preset']), 'isLocal': False})
    return hosts
