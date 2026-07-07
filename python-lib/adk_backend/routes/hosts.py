"""Multi-instance host routes: list presets, probe a host, bootstrap ADMINTOOLKIT.

These endpoints are exempted from backend.py's _check_host_ready gate — they
exist precisely to diagnose / fix a broken host config (including the one-click
install-toolkit flow that turns a plugin-less remote green).
"""

import io
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

from adk_backend import hostkeys
from adk_backend.clients import (
    ADMIN_TOOLKIT_GIT_BRANCH,
    ADMIN_TOOLKIT_GIT_REPO_URL,
    MACRO_PROJECT_DEFAULT_NAME,
    MACRO_PROJECT_KEY,
    _build_remote_client,
    _list_remote_hosts,
    _local_thread_client,
    _remote_host_config,
    _resolve_client,
)
from adk_backend.routes.auth import _master_password, _red_secret_source, _verify_red_password, set_hostkey_cookie
from adk_backend.utils import _sse_response, advanced, local_only

bp = Blueprint('hosts', __name__)

# DSS prefixes parameter-set preset types as `parameter-set-<plugin-id>-<name>`.
# Reads elsewhere match on the `remote-dss-host` suffix; writes use this exact
# canonical type so a webapp-created preset is indistinguishable from one made
# in the DSS plugin-settings UI.
_PLUGIN_ID = 'admin-toolkit'
_PRESET_TYPE = 'parameter-set-admin-toolkit-remote-dss-host'

# Upload-fallback plugin .zip ceiling. The real plugin bundle is ~2 MB; cap well
# under Flask's 16 MB default so an oversized/wrong file is rejected cleanly
# rather than silently truncated. Raise both this and MAX_CONTENT_LENGTH
# (backend.py) together if the bundle ever approaches the limit.
_PLUGIN_ZIP_MAX_BYTES = 16 * 1024 * 1024
# How many trailing build-log lines to forward per SSE tick (live install feed).
_INSTALL_LOG_TAIL = 12


def _future_log_tail(state: Dict[str, Any], n: int = _INSTALL_LOG_TAIL) -> Tuple[List[str], Optional[int]]:
    """Pull a compact tail of build-log lines from a DSSFuture peek_state.

    DSS long-running futures carry a rolling `log` object
    ({totalLines, lines[]}) whose `lines` are the most-recent build output
    (git clone / pip install). We forward only the last `n` lines over SSE for
    a live feed — pure DSS API, no macro, no extra endpoint. The future's
    `log.lines` are clean build output (unlike admin get_log(), whose header
    embeds an access-token line), so they're safe to stream to the public
    webapp. Returns ([], None) when the future has not logged anything yet.
    """
    lg = (state or {}).get('log') or {}
    lines = lg.get('lines') or []
    tail = [str(x) for x in lines[-n:] if str(x).strip()]
    return tail, lg.get('totalLines')


def _install_error_message(exc: Exception) -> str:
    """Map known git-install failures to actionable guidance; otherwise return
    the raw exception text.

    The DSS `installFromGit` NPE *"Cannot read field credentials because
    currentUser is null"* means the host preset's API key has no associated DSS
    user (a global API key). DSS resolves git credentials from the current user,
    so the clone fails regardless of repo/URL/visibility. The fix is a personal
    API key (which carries a user), or the upload-.zip path (which needs none).
    """
    msg = str(exc)
    if 'currentUser' in msg and 'null' in msg:
        return ("This host's API key has no associated DSS user (it looks like a "
                "global API key from Settings → Security → Global API keys), "
                "so DSS can't resolve git credentials for the clone. The key must "
                "belong to a user: have an admin create one from their own profile "
                "→ API keys → New API key, then paste that into this host's "
                "preset. Or switch to Upload .zip (which needs no user).")
    return f'{type(exc).__name__}: {str(exc)[:300]}'


@bp.route('/api/hosts')
def api_hosts():
    """List local + remote-preset hosts. API keys are never returned."""
    hosts = [{'id': 'local', 'label': 'Local DSS', 'url': ''}]
    hosts.extend(_list_remote_hosts())
    return jsonify(hosts)


@bp.route('/api/hosts/check', methods=['POST'])
def api_hosts_check():
    """Probe a host: reachable? plugin installed? ADMINTOOLKIT exists?"""
    payload = request.get_json(silent=True) or {}
    host_id = (payload.get('hostId') or 'local').strip()
    try:
        client = _resolve_client(host_id)
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:200]}'})
    result: Dict[str, Any] = {
        'ok': True,
        'pluginInstalled': False,
        'pluginVersion': None,
        'adminToolkitProjectExists': False,
    }
    try:
        plugins = client.list_plugins() or []
        for plug in plugins:
            if isinstance(plug, dict) and plug.get('id') == 'admin-toolkit':
                result['pluginInstalled'] = True
                result['pluginVersion'] = plug.get('version')
                break
    except Exception as exc:
        result['ok'] = False
        result['error'] = f'list_plugins failed: {str(exc)[:200]}'
        return jsonify(result)
    try:
        project = client.get_project(MACRO_PROJECT_KEY)
        project.get_summary()
        result['adminToolkitProjectExists'] = True
    except Exception:
        result['adminToolkitProjectExists'] = False
    return jsonify(result)


@bp.route('/api/hosts/macro-project', methods=['POST'])
def api_hosts_macro_project():
    """Create the ADMINTOOLKIT project on the active host."""
    payload = request.get_json(silent=True) or {}
    host_id = (payload.get('hostId') or 'local').strip()
    name = (payload.get('name') or MACRO_PROJECT_DEFAULT_NAME).strip() or MACRO_PROJECT_DEFAULT_NAME
    try:
        client = _resolve_client(host_id)
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:200]}'}), 400
    try:
        client.create_project(MACRO_PROJECT_KEY, name, owner='admin')
        return jsonify({'ok': True, 'projectKey': MACRO_PROJECT_KEY, 'name': name})
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:300]}'}), 500


@bp.route('/api/hosts/install-toolkit', methods=['POST'])
def api_hosts_install_toolkit():
    """One-click turnkey install of the Admin Toolkit onto a reachable remote.

    Produces a normal *installed* (updatable) plugin on the remote via one of two
    admin-chosen sources — both end in a plugin the customer can later update:

      • git (default)  — the remote pulls the plugin straight from a hardcoded
        repo URL/branch (prefilled in the dialog, editable per run) via
        install_plugin_from_git / update_from_git. No bytes leave the controller.
      • upload (.zip)   — the admin uploads the plugin archive in the dialog and
        the backend streams it to install_plugin_from_archive / update_from_zip.
        Used when the remote can't reach the repo (private / air-gapped).

    Three steps total — only step 1 branches on the source; steps 2 & 3 are
    source-agnostic:

      1. install  — install (or update) the plugin on the remote,
      2. codeenv  — build + select the plugin's managed code env on the remote,
      3. project  — create the ADMINTOOLKIT support project if absent.

    Streams one SSE `step` event per phase: {step, status, msg?, error?} where
    status ∈ active|done|error, then a terminal {step:'complete', status:'done'}.
    All ops are pure DSS-API calls → they stay on the client (no macro needed).
    """
    # Parse by content type so the legacy {hostId} JSON body still works.
    # Upload mode arrives as multipart (admin-supplied .zip in the `plugin`
    # field); everything else is JSON with an optional mode (default 'git') and
    # an optional repoUrl/branch override of the hardcoded git source.
    zip_bytes: Optional[bytes] = None
    repo_url = ADMIN_TOOLKIT_GIT_REPO_URL
    branch = ADMIN_TOOLKIT_GIT_BRANCH
    if (request.content_type or '').startswith('multipart/'):
        mode = 'upload'
        host_id = (request.form.get('hostId') or '').strip()
    else:
        payload = request.get_json(silent=True) or {}
        host_id = (payload.get('hostId') or '').strip()
        mode = (payload.get('mode') or 'git').strip() or 'git'
        repo_url = (payload.get('repoUrl') or '').strip() or ADMIN_TOOLKIT_GIT_REPO_URL
        branch = (payload.get('branch') or '').strip() or ADMIN_TOOLKIT_GIT_BRANCH
    if not host_id or host_id == 'local':
        return jsonify({'error': 'hostId must reference a remote-dss-host preset'}), 400

    if mode == 'upload':
        # Buffer + validate the upload BEFORE the generator so a bad file fails
        # with a clean 400 instead of mid-stream (mirrors routes/feedback.py).
        upload = request.files.get('plugin')
        if upload is None or not upload.filename:
            return jsonify({'error': 'No plugin .zip uploaded'}), 400
        if os.path.splitext(upload.filename)[1].lower() != '.zip':
            return jsonify({'error': 'Plugin upload must be a .zip file'}), 400
        zip_bytes = upload.read()
        if not zip_bytes:
            return jsonify({'error': 'Uploaded plugin .zip is empty'}), 400
        if len(zip_bytes) > _PLUGIN_ZIP_MAX_BYTES:
            return jsonify({
                'error': f'Plugin .zip too large (max {_PLUGIN_ZIP_MAX_BYTES // (1024 * 1024)} MB)',
            }), 400

    # _remote_host_config raises RemoteKeysLocked (→ 409 via @errorhandler, pops
    # the unlock modal) when the preset key is encrypted and we hold no key.
    cfg = _remote_host_config(host_id)
    if cfg is None:
        return jsonify({'error': 'invalid-host-id', 'hostId': host_id}), 400
    remote_client = _build_remote_client(cfg)

    def sse(step: str, status: str, msg: str = None, error: str = None,
            log: List[str] = None, total: int = None) -> str:
        evt: Dict[str, Any] = {'step': step, 'status': status}
        if msg is not None:
            evt['msg'] = msg
        if error is not None:
            evt['error'] = error
        if log:
            evt['log'] = log
        if total is not None:
            evt['total'] = total
        return "event: step\ndata: %s\n\n" % json.dumps(evt)

    def generate():
        # ── Step 1: install (or update) the plugin on the remote ──
        yield sse('install', 'active',
                  'Installing plugin from git…' if mode == 'git'
                  else 'Uploading plugin to remote…')
        try:
            already_installed = False
            for plug in (remote_client.list_plugins() or []):
                if isinstance(plug, dict) and plug.get('id') == 'admin-toolkit':
                    already_installed = True
                    break
            if mode == 'upload':
                # Synchronous archive install/update from the buffered upload.
                stream = io.BytesIO(zip_bytes)
                if already_installed:
                    remote_client.get_plugin('admin-toolkit').update_from_zip(stream)
                else:
                    remote_client.install_plugin_from_archive(stream)
            else:
                # git: install_plugin_from_git / update_from_git both return a
                # DSSFuture → poll with the same pattern as the codeenv step.
                if already_installed:
                    future = remote_client.get_plugin('admin-toolkit').update_from_git(
                        repo_url, checkout=branch)
                else:
                    future = remote_client.install_plugin_from_git(
                        repo_url, checkout=branch)
                if future.job_id:
                    polls = 0
                    while True:
                        state = future.peek_state() or {}
                        if state.get('hasResult') or not state.get('alive', True):
                            break
                        polls += 1
                        if polls > 240:  # ~20 min cap at 5s/poll
                            raise Exception('git install timed out after ~20 min')
                        tail, total = _future_log_tail(state)
                        yield sse('install', 'active',
                                  msg=state.get('jobDisplayName') or 'Cloning from git…',
                                  log=tail, total=total)
                        time.sleep(5)
                    future.get_result()
                else:
                    future.wait_for_result()
            yield sse('install', 'done',
                      'Plugin updated' if already_installed else 'Plugin installed')
        except Exception as exc:
            yield sse('install', 'error', error=_install_error_message(exc))
            return

        # ── Step 2: build + select the plugin's managed code env on the remote ──
        yield sse('codeenv', 'active', 'Checking code env…')
        try:
            plugin = remote_client.get_plugin('admin-toolkit')
            settings = plugin.get_settings()
            if (settings.get_raw() or {}).get('codeEnvName'):
                yield sse('codeenv', 'done', 'Code env already built')
            else:
                # plugin default interpreter; future result carries envName.
                future = plugin.create_code_env()
                if future.job_id:
                    polls = 0
                    while True:
                        state = future.peek_state() or {}
                        if state.get('hasResult') or not state.get('alive', True):
                            break
                        polls += 1
                        if polls > 240:  # ~20 min cap at 5s/poll
                            raise Exception('code env build timed out after ~20 min')
                        tail, total = _future_log_tail(state)
                        yield sse('codeenv', 'active',
                                  msg=state.get('jobDisplayName') or 'Building code env…',
                                  log=tail, total=total)
                        time.sleep(5)
                    result = future.get_result() or {}
                else:
                    result = future.wait_for_result() or {}
                env_name = (result or {}).get('envName')
                if not env_name:
                    raise Exception('code env build returned no envName')
                settings.set_code_env(env_name)
                settings.save()
                yield sse('codeenv', 'done', 'Code env built: %s' % env_name)
        except Exception as exc:
            yield sse('codeenv', 'error', error=f'{type(exc).__name__}: {str(exc)[:300]}')
            return

        # ── Step 3: create the ADMINTOOLKIT support project if absent ──
        yield sse('project', 'active', 'Creating support project…')
        try:
            exists = False
            try:
                remote_client.get_project(MACRO_PROJECT_KEY).get_summary()
                exists = True
            except Exception:
                exists = False
            if exists:
                yield sse('project', 'done', 'Support project already exists')
            else:
                remote_client.create_project(
                    MACRO_PROJECT_KEY, MACRO_PROJECT_DEFAULT_NAME, owner='admin')
                yield sse('project', 'done', 'Support project created')
        except Exception as exc:
            yield sse('project', 'error', error=f'{type(exc).__name__}: {str(exc)[:300]}')
            return

        yield sse('complete', 'done', 'Admin Toolkit installed')

    return _sse_response(generate)


# ─────────────────────────────────────────────────────────────────────────
# Remote-host preset CRUD (managed from the webapp's Settings → Remote Hosts)
#
# The webapp owns the whole remote-dss-host preset. The plaintext API key is
# sent here over HTTPS and encrypted SERVER-SIDE into an adkfk1$ blob before it
# touches saved settings (never stored in plaintext). Key/salt provenance (see
# _encrypt_api_key): a typed password wins (verified against the master
# password), else the settings-stored master password (zero user action), else
# an already-unlocked Fernet key, else prompt.
#
# All routes are @advanced (gated on the red unlock cookie — managing hosts is
# an advanced action) + @local_only (plugin settings are local-only; never
# 502'd when the active *remote* host is unreachable). Request bodies carry
# plaintext keys / the master password and are NEVER logged.
# ─────────────────────────────────────────────────────────────────────────


def _is_remote_host_preset(preset: Any) -> bool:
    return isinstance(preset, dict) and (preset.get('type') or '').endswith('remote-dss-host')


def _local_plugin_settings():
    """Plugin settings object on the LOCAL instance (where presets live)."""
    return _local_thread_client().get_plugin(_PLUGIN_ID).get_settings()


def _slugify(label: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', (label or '').lower()).strip('-')
    return slug or 'host'


def _unique_preset_name(label: str, presets: List[Any]) -> str:
    """Generate an immutable preset id (routing key) from the label, deduped."""
    base = _slugify(label)
    taken = {p.get('name') for p in presets if isinstance(p, dict)}
    if base not in taken:
        return base
    i = 2
    while f'{base}-{i}' in taken:
        i += 1
    return f'{base}-{i}'


def _existing_salt_source() -> Optional[bytes]:
    """The salt shared by all encrypted presets (from the first encrypted blob),
    or None when no encrypted preset exists yet. Reusing it guarantees a single
    unlock opens every blob regardless of salt-derivation history."""
    try:
        raw = _local_plugin_settings().get_raw()
    except Exception:
        return None
    presets = raw.get('presets') if isinstance(raw, dict) else None
    if not isinstance(presets, list):
        return None
    for preset in presets:
        if not _is_remote_host_preset(preset):
            continue
        key = (preset.get('config') or {}).get('apiKey') or ''
        if hostkeys.is_encrypted(key):
            try:
                return hostkeys.salt_from_blob(key)
            except Exception:
                continue
    return None


def _encrypt_api_key(
    plaintext: str, password: Optional[str]
) -> Tuple[Optional[str], Optional[bytes], Optional[Tuple[Dict[str, Any], int]]]:
    """Encrypt a plaintext API key into an adkfk1$ blob via the B→A failover.

    Returns (blob, fernet_key, error). On success error is None; fernet_key is
    non-None only when a password was in play (so the caller auto-unlocks via
    set_active_key + cookie). On failure blob/fernet_key are None and error is
    a (payload, status) tuple to return verbatim."""
    salt = _existing_salt_source()
    password = (password or '').strip()

    if password:
        # ── Typed password: verify against the master password (or the
        #    legacy hash on a pre-master install) before trusting it ──
        kind, stored = _red_secret_source()
        if not stored:
            return None, None, ({
                'ok': False,
                'error': 'advanced-not-configured',
                'message': 'No master password is configured yet. Set one in the plugin '
                           'settings first, then add the host.',
            }, 400)
        if not _verify_red_password(password, kind, stored):
            return None, None, ({'ok': False, 'error': 'invalid-password',
                                 'message': 'Incorrect password.'}, 401)
    else:
        # ── No password typed: the settings-stored master password covers it ──
        password = _master_password()
        if not password:
            # Legacy hash-only install: fall back to the already-unlocked key.
            active = hostkeys.get_active_key()
            if active is None or salt is None:
                return None, None, ({'ok': False, 'needPassword': True}, 200)
            try:
                return hostkeys.encrypt_blob(plaintext, active, salt), None, None
            except Exception:
                return None, None, ({'ok': False, 'needPassword': True}, 200)

    if salt is None:
        salt = hostkeys.host_salt(password)  # first key ever
    fernet_key = hostkeys.derive_fernet_key(password, salt)
    try:
        blob = hostkeys.encrypt_blob(plaintext, fernet_key, salt)
    except Exception as exc:
        return None, None, ({'ok': False, 'error': 'encrypt-failed',
                             'message': f'{type(exc).__name__}: {str(exc)[:200]}'}, 500)
    return blob, fernet_key, None


def _save_preset(name: str, cfg: Dict[str, Any]) -> None:
    """Upsert a remote-dss-host preset by name into local plugin settings."""
    settings = _local_plugin_settings()
    raw = settings.get_raw()
    if not isinstance(raw.get('presets'), list):
        raw['presets'] = []
    presets = raw['presets']
    for preset in presets:
        if _is_remote_host_preset(preset) and preset.get('name') == name:
            preset['type'] = _PRESET_TYPE
            preset['config'] = cfg
            break
    else:
        presets.append({'name': name, 'type': _PRESET_TYPE, 'config': cfg})
    settings.save()


def _delete_preset(name: str) -> None:
    """Drop the matching remote-dss-host preset from local plugin settings."""
    settings = _local_plugin_settings()
    raw = settings.get_raw()
    presets = raw.get('presets')
    if not isinstance(presets, list):
        return
    raw['presets'] = [
        p for p in presets
        if not (_is_remote_host_preset(p) and p.get('name') == name)
    ]
    settings.save()


@bp.route('/api/hosts/presets', methods=['GET'])
@advanced
@local_only
def api_hosts_presets_list():
    """Full editable host list. keyStatus ∈ encrypted|plaintext|none. Never
    returns the key itself."""
    try:
        raw = _local_plugin_settings().get_raw()
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:200]}'}), 500
    presets = raw.get('presets') if isinstance(raw, dict) else None
    out: List[Dict[str, Any]] = []
    if isinstance(presets, list):
        for preset in presets:
            if not _is_remote_host_preset(preset):
                continue
            cfg = preset.get('config') or {}
            key = cfg.get('apiKey') or ''
            key_status = 'encrypted' if hostkeys.is_encrypted(key) else ('plaintext' if key else 'none')
            out.append({
                'name': preset.get('name'),
                'label': cfg.get('label') or preset.get('name'),
                'url': (cfg.get('url') or '').rstrip('/'),
                'verifyTls': bool(cfg.get('verifyTls', True)),
                'backupProjectKey': (cfg.get('backupProjectKey') or '').strip(),
                'keyStatus': key_status,
            })
    return jsonify({'ok': True, 'hosts': out})


@bp.route('/api/hosts/presets', methods=['POST'])
@advanced
@local_only
def api_hosts_presets_save():
    """Create or update a remote-dss-host preset (encrypting the key server-side)."""
    payload = request.get_json(silent=True) or {}
    label = (payload.get('label') or '').strip()
    url = (payload.get('url') or '').strip().rstrip('/')
    name = (payload.get('name') or '').strip()
    api_key = payload.get('apiKey')
    password = payload.get('password')
    verify_tls = bool(payload.get('verifyTls', True))
    backup_project_key = (payload.get('backupProjectKey') or '').strip()

    if not label:
        return jsonify({'ok': False, 'error': 'invalid-label', 'message': 'Label is required.'}), 400
    if not url or not re.match(r'^https?://', url, re.IGNORECASE):
        return jsonify({'ok': False, 'error': 'invalid-url',
                        'message': 'URL must start with http:// or https://.'}), 400

    try:
        raw = _local_plugin_settings().get_raw()
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:200]}'}), 500
    presets = raw.get('presets') if isinstance(raw.get('presets'), list) else []

    existing: Optional[Dict[str, Any]] = None
    if name:
        for preset in presets:
            if _is_remote_host_preset(preset) and preset.get('name') == name:
                existing = preset
                break
    # name is the routing key: generated on create, immutable on edit.
    if existing is None:
        name = _unique_preset_name(label, presets)

    fernet_key: Optional[bytes] = None  # set on path-A success → auto-unlock
    raw_key = (api_key or '').strip()
    if raw_key:
        if hostkeys.is_encrypted(raw_key):
            stored_key = raw_key  # power-user paste of a ready blob — store as-is
        else:
            blob, fernet_key, err = _encrypt_api_key(raw_key, password)
            if err is not None:
                body, status = err
                return jsonify(body), status
            stored_key = blob
    elif existing is not None:
        stored_key = (existing.get('config') or {}).get('apiKey') or ''  # keep current key
    else:
        return jsonify({'ok': False, 'error': 'missing-key',
                        'message': 'An API key is required for a new host.'}), 400

    cfg = {
        'label': label,
        'url': url,
        'apiKey': stored_key,
        'verifyTls': verify_tls,
        'backupProjectKey': backup_project_key,
    }
    try:
        _save_preset(name, cfg)
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:200]}'}), 500

    resp = jsonify({'ok': True, 'name': name})
    if fernet_key is not None:
        hostkeys.set_active_key(fernet_key)
        set_hostkey_cookie(resp, fernet_key)
    return resp


@bp.route('/api/hosts/presets/<name>', methods=['DELETE'])
@advanced
@local_only
def api_hosts_presets_delete(name: str):
    """Delete a remote-dss-host preset by name."""
    try:
        _delete_preset((name or '').strip())
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:200]}'}), 500
    return jsonify({'ok': True})
