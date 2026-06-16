"""Plugins routes — catalog, usages scan, cross-host compare/deploy."""
import logging
import os
from concurrent.futures import as_completed
from typing import Any, Dict

from flask import Blueprint, g, jsonify, request

from adk_backend.caching import _cache_get
from adk_backend.clients import (
    ThreadPoolExecutor, _build_remote_client, _remote_host_config,
    _safe_request_host_id, _sdk_fetch,
)
from adk_backend.macros import _host_metrics_macro
from adk_backend.settings import _BACKEND_SETTINGS
from adk_backend.sysinfo import _dip_home, _safe_read_json
from adk_backend.utils import advanced

bp = Blueprint('plugins', __name__)
_LOGGER = logging.getLogger(__name__)


@bp.route('/api/tools/plugins/compare', methods=['POST'])
def api_tools_plugins_compare():
    """Compare local (Design) plugins with a remote host configured as a preset."""
    payload = request.get_json(silent=True) or {}
    target_host_id = (payload.get('targetHostId') or '').strip()
    if not target_host_id or target_host_id == 'local':
        return jsonify({"error": "targetHostId is required and must reference a remote-dss-host preset"}), 400

    cfg = _remote_host_config(target_host_id)
    if cfg is None:
        return jsonify({"error": "invalid-host-id", "hostId": target_host_id}), 400

    try:
        local_client = g.client
        local_plugins_raw = local_client.list_plugins()
    except Exception as e:
        return jsonify({"error": "Failed to fetch local plugins: %s" % str(e)}), 500

    try:
        remote_client = _build_remote_client(cfg)
        remote_plugins_raw = remote_client.list_plugins()
    except Exception as e:
        return jsonify({"error": "Failed to fetch remote plugins: %s" % str(e)}), 500

    def _parse_plugins(raw_list):
        out = {}
        for p in raw_list:
            if isinstance(p, dict):
                meta = p.get('meta') or {}
                pid = p.get('id') or p.get('name') or meta.get('label')
                if not pid:
                    continue
                out[pid] = {
                    'label': meta.get('label') or pid,
                    'version': p.get('version'),
                    'isDev': bool(p.get('isDev', False)),
                }
            else:
                pid = str(p)
                if pid:
                    out[pid] = {'label': pid, 'version': None, 'isDev': False}
        return out

    local_map = _parse_plugins(local_plugins_raw)
    remote_map = _parse_plugins(remote_plugins_raw)
    all_ids = sorted(set(list(local_map.keys()) + list(remote_map.keys())))

    rows = []
    for pid in all_ids:
        local = local_map.get(pid)
        remote = remote_map.get(pid)
        rows.append({
            'id': pid,
            'label': (local or remote or {}).get('label', pid),
            'localVersion': local['version'] if local else None,
            'remoteVersion': remote['version'] if remote else None,
            'isDev': (local or {}).get('isDev', False),
        })

    return jsonify({"rows": rows})


@bp.route('/api/tools/plugins/deploy-one', methods=['POST'])
@advanced
def api_tools_plugins_deploy_one():
    body = request.get_json(force=True) or {}
    target_host_id = (body.get('targetHostId') or '').strip()
    plugin_id = (body.get('pluginId') or '').strip()

    if not target_host_id or target_host_id == 'local' or not plugin_id:
        return jsonify({"error": "targetHostId (remote preset) and pluginId are required"}), 400

    cfg = _remote_host_config(target_host_id)
    if cfg is None:
        return jsonify({"error": "invalid-host-id", "hostId": target_host_id}), 400

    local_client = g.client
    remote_client = _build_remote_client(cfg)

    # Strategy 1: dev plugin → download stream and upload archive
    try:
        stream = local_client.download_plugin_stream(plugin_id)
        remote_client.install_plugin_from_archive(stream)
        return jsonify({"ok": True, "method": "archive"})
    except Exception as e:
        dev_error = str(e)

    # Strategy 2: non-dev (store) plugin → install from store on remote
    try:
        remote_client.install_plugin_from_store(plugin_id)
        return jsonify({"ok": True, "method": "store"})
    except Exception as e:
        store_error = str(e)

    return jsonify({
        "error": "Failed to deploy plugin '%s'. Archive: %s | Store: %s" % (plugin_id, dev_error, store_error)
    }), 500


def _scan_plugin_usages(client: Any, plugin_id: str) -> Dict[str, Any]:
    """Fetch + summarize usages for one plugin. Returns the fields to merge
    into the pluginDetails row (projectsUsingCount/projectsUsing/missingTypes
    or usagesError on failure)."""
    raw = client.get_plugin(plugin_id).list_usages().get_raw() or {}
    usages = raw.get('usages') or []
    missing_raw = raw.get('missingTypes') or []

    per_project: Dict[str, Dict[str, Any]] = {}
    for u in usages:
        if not isinstance(u, dict):
            continue
        pk = u.get('projectKey') or ''
        if not pk:
            continue
        kind = u.get('elementKind') or ''
        bucket = per_project.setdefault(pk, {
            'projectKey': pk,
            'elementKinds': {},
            'objects': [],
        })
        if kind:
            bucket['elementKinds'][kind] = bucket['elementKinds'].get(kind, 0) + 1
        bucket['objects'].append({
            'elementKind': kind,
            'elementType': u.get('elementType') or '',
            'objectType': u.get('objectType') or '',
            'objectId': u.get('objectId') or '',
        })

    grouped = list(per_project.values())
    grouped.sort(key=lambda g_: (-len(g_['objects']), g_['projectKey']))
    grouped = grouped[:50]

    missing_types = []
    for m in missing_raw:
        if not isinstance(m, dict):
            continue
        missing_types.append({
            'missingType': m.get('missingType') or '',
            'objectType': m.get('objectType') or '',
            'projectKey': m.get('projectKey') or '',
            'objectId': m.get('objectId') or '',
        })

    return {
        'projectsUsingCount': len(per_project),
        'projectsUsing': grouped,
        'missingTypes': missing_types,
    }


def _latest_store_plugin_versions(client: Any) -> Dict[str, str]:
    """Map of plugin id -> latest store version, for the plugin-currency column.

    Mirrors the public snippet: fetch the store catalog for the active host's DSS
    major version from update.dataiku.com and key each item's storeVersion by id.
    Best-effort: on any failure (network, parse, unknown version) returns {} so the
    plugins endpoint still loads, just without a Latest column. The DSS major is
    read the same way as _image_cleaner_release_info, falling back to "14"."""
    import re
    import requests

    out: Dict[str, str] = {}
    try:
        # "Latest" is keyed off the newest AVAILABLE DSS line (not this
        # instance's version): highest stable release on the public download
        # listing, then iterate catalog URLs down from there.
        listing = requests.get('https://downloads.dataiku.com/public/dss/', timeout=10).text
        version = max(re.findall(r'href="(\d+\.\d+\.\d+)/"', listing),
                      key=lambda v: [int(n) for n in v.split('.')])
        parts = str(version or '').split('.')
        major = parts[0] if parts and parts[0].isdigit() else '14'
        # Prefer the major.minor catalog (e.g. "14.6"): the bare-major path
        # ("14") serves a snapshot frozen at the .0 release, so plugins shipped
        # in later minors show stale storeVersions. The update server only
        # publishes some minors (e.g. 14.2/14.4/14.6), so when the instance's
        # own minor is absent, step DOWN through lower minors to the nearest
        # published catalog before finally falling back to the bare major.
        candidates = []
        if len(parts) >= 2 and parts[1].isdigit():
            for minor in range(int(parts[1]), -1, -1):
                candidates.append(f'{major}.{minor}')
        candidates.append(major)

        resp = None
        for seg in candidates:
            url = f'https://update.dataiku.com/dss/{seg}/plugins/list.json'
            try:
                resp = requests.get(
                    url,
                    headers={'Content-Type': 'application/json'},
                    verify=True,
                    timeout=(3, 10),
                )
                resp.raise_for_status()
                break
            except Exception:
                resp = None
        if resp is None:
            return out
        for item in (resp.json().get('items') or []):
            if isinstance(item, dict):
                pid = item.get('id')
                store_version = item.get('storeVersion')
                if pid and store_version:
                    out[str(pid)] = str(store_version)
    except Exception as exc:
        _LOGGER.warning("[plugins] latest store-version fetch failed: %s", exc)
    return out


@bp.route('/api/plugins')
def api_plugins():
    client = g.client

    def loader():
        plugins = []
        plugin_details = []
        _all_plugins = _sdk_fetch(
            'list_plugins',
            _BACKEND_SETTINGS['cache_ttl_overview'],
            lambda: list(client.list_plugins()),
        )
        for p in _all_plugins:
            if isinstance(p, dict):
                meta = p.get('meta') or {}
                pid = p.get('id') or p.get('name') or meta.get('label')
                if not pid:
                    continue
                plugins.append(pid)
                plugin_details.append({
                    'id': pid,
                    'label': meta.get('label') or pid,
                    'installedVersion': p.get('version'),
                    'isDev': bool(p.get('isDev', False)),
                })
            else:
                pid = str(p)
                if pid:
                    plugins.append(pid)
                    plugin_details.append({'id': pid, 'label': pid})
        plugins.sort()
        plugin_details.sort(key=lambda d: d.get('id', ''))

        # Plugin currency: latest store version per plugin id (best-effort, cached
        # separately so a short plugins-cache TTL doesn't refetch the store catalog).
        latest_versions = _cache_get(
            'plugin_store_versions',
            _BACKEND_SETTINGS['cache_ttl_overview'],
            lambda: _latest_store_plugin_versions(client),
        )
        for row in plugin_details:
            latest = latest_versions.get(row.get('id') or '')
            if latest:
                row['latestVersion'] = latest

        return {'plugins': plugins, 'pluginDetails': plugin_details, 'pluginsCount': len(plugins)}

    data = _cache_get('plugins', _BACKEND_SETTINGS['cache_ttl_plugins'], loader)
    return jsonify(data)


@bp.route('/api/plugins/usages')
def api_plugins_usages():
    """Per-plugin usage scan, split out of /api/plugins so the cheap plugin list
    (names/versions) loads fast and these expensive get_plugin().list_usages()
    fan-outs (2 chained DSS calls x N plugins) fill in asynchronously. Returns a
    map keyed by plugin id; the frontend merges it into the pluginDetails rows."""
    client = g.client

    def loader():
        _all_plugins = _sdk_fetch(
            'list_plugins',
            _BACKEND_SETTINGS['cache_ttl_overview'],
            lambda: list(client.list_plugins()),
        )
        pids = []
        for p in _all_plugins:
            if isinstance(p, dict):
                meta = p.get('meta') or {}
                pid = p.get('id') or p.get('name') or meta.get('label')
            else:
                pid = str(p)
            if pid:
                pids.append(pid)

        # Fan out per-plugin usage scans in parallel. Per-plugin SDK call is
        # cached so subsequent loads within the cache window are free.
        usage_ttl = int(_BACKEND_SETTINGS.get('cache_ttl_plugins', 600))
        workers = max(1, int(_BACKEND_SETTINGS.get('parallel_workers_default', 8) or 8))
        usage_by_pid: Dict[str, Dict[str, Any]] = {}
        if pids:
            def _fetch_one(pid: str) -> Dict[str, Any]:
                return _sdk_fetch(
                    'plugin_usages:' + pid,
                    usage_ttl,
                    lambda: _scan_plugin_usages(client, pid),
                )
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {ex.submit(_fetch_one, pid): pid for pid in pids}
                for fut in as_completed(futures):
                    pid = futures[fut]
                    try:
                        usage_by_pid[pid] = fut.result()
                    except Exception as exc:
                        usage_by_pid[pid] = {
                            'projectsUsingCount': None,
                            'projectsUsing': [],
                            'missingTypes': [],
                            'usagesError': str(exc),
                        }

        return {'usagesByPlugin': usage_by_pid}

    data = _cache_get('plugin_usages_all', _BACKEND_SETTINGS['cache_ttl_plugins'], loader)
    return jsonify(data)
