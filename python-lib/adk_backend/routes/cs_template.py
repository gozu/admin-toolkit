"""Code Studio template replacement — list projects/templates, migrate a CS
to a new template (file copies go through the cs-template-copy-files macro)."""
import logging
import os
import time
from concurrent.futures import TimeoutError as FuturesTimeoutError, as_completed
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, g, jsonify, request

from adk_backend.clients import ThreadPoolExecutor
from adk_backend.settings import _BACKEND_SETTINGS
from adk_backend.sysinfo import _dip_home
from adk_backend.utils import advanced

bp = Blueprint('cs_template', __name__)
_LOGGER = logging.getLogger(__name__)


# ── Code Studio template replacement ────────────────────────────────────────

def _cs_tmpl_template_index(client: Any) -> Dict[str, Dict[str, str]]:
    """Return {templateId: {id, label, description}} for fast joins."""
    try:
        items = client.list_code_studio_templates(as_type='listitems')
    except Exception:
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for item in items:
        raw = getattr(item, '_data', {}) or {}
        tid = str(raw.get('id') or '')
        if not tid:
            continue
        desc = raw.get('desc') or {}
        out[tid] = {
            'id': tid,
            'label': str(raw.get('label') or desc.get('label') or tid),
            'description': str(desc.get('shortDesc') or ''),
        }
    return out


def _cs_tmpl_list_one_project(client: Any, project_key: str,
                              template_index: Dict[str, Dict[str, str]],
                              include_state: bool) -> List[Dict[str, Any]]:
    """Return code studios for a single project. list_code_studios() returns a slim
    payload (no libName), so we enrich each entry via get_settings()."""
    project = client.get_project(project_key)
    items = project.list_code_studios(as_type='listitems')
    studios: List[Dict[str, Any]] = []
    for item in items:
        raw = getattr(item, '_data', {}) or {}
        tid = str(raw.get('templateId') or '')
        tpl = template_index.get(tid) or {}
        cs_id = str(raw.get('id') or '')
        entry = {
            'id': cs_id,
            'name': str(raw.get('name') or cs_id),
            'owner': str(raw.get('owner') or ''),
            'templateId': tid,
            'templateLabel': tpl.get('label') or (raw.get('desc') or {}).get('label') or tid,
            'libName': '',
            'state': None,
        }
        if cs_id:
            cs_handle = project.get_code_studio(cs_id)
            try:
                settings_raw = cs_handle.get_settings().get_raw()
                entry['libName'] = str(settings_raw.get('libName') or '')
                if not tid:
                    tid = str(settings_raw.get('templateId') or '')
                    entry['templateId'] = tid
                    entry['templateLabel'] = (template_index.get(tid) or {}).get('label') or tid
            except Exception:
                pass
            if include_state:
                try:
                    entry['state'] = cs_handle.get_status().state
                except Exception:
                    entry['state'] = None
        studios.append(entry)
    return studios


@bp.route('/api/cs-template/projects')
def api_cs_template_projects():
    include_state = request.args.get('includeState', '1') != '0'
    client = g.client
    try:
        projects = client.list_projects() or []
    except Exception as exc:
        return jsonify({'error': str(exc)[:300]}), 502
    project_keys = [str(p.get('projectKey') or '') for p in projects if p.get('projectKey')]

    template_index = _cs_tmpl_template_index(client)
    result: List[Dict[str, Any]] = []
    timeout_seconds = max(5, int(_BACKEND_SETTINGS.get('cs_template_list_timeout_ms', 60000) / 1000))

    def load(pk: str) -> Tuple[str, List[Dict[str, Any]]]:
        try:
            return pk, _cs_tmpl_list_one_project(client, pk, template_index, include_state)
        except Exception as exc:
            _LOGGER.info("[cs-tmpl] list pk=%s error=%s", pk, str(exc)[:200])
            return pk, []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(load, pk): pk for pk in project_keys}
        try:
            for fut in as_completed(futures, timeout=timeout_seconds):
                pk, studios = fut.result()
                if studios:
                    result.append({'projectKey': pk, 'codeStudios': studios})
        except FuturesTimeoutError:
            _LOGGER.info("[cs-tmpl] projects scan timed out after %ss", timeout_seconds)

    result.sort(key=lambda r: r['projectKey'])
    return jsonify({'projects': result, 'templates': list(template_index.values())})


@bp.route('/api/cs-template/templates')
def api_cs_template_templates():
    client = g.client
    return jsonify({'templates': list(_cs_tmpl_template_index(client).values())})


def _cs_tmpl_lib_dir(project_key: str, lib_name: str) -> str:
    """Code Studio *resources* zone (libName-keyed, not versioned)."""
    return os.path.join(_dip_home().rstrip('/'), 'lib', 'code_studio', project_key, lib_name)


def _cs_tmpl_versioned_dir(project_key: str, cs_id: str) -> str:
    """Code Studio *versioned* zone (csId-keyed, lives in project config tree)."""
    return os.path.join(_dip_home().rstrip('/'), 'config', 'projects', project_key, 'code_studios', cs_id)


_CS_TMPL_COPY_MACRO_ID = 'pyrunnable_admin-toolkit_cs-template-copy-files'


def _cs_tmpl_macro_files(project: Any, src_dir: str, dst_dir: Optional[str] = None) -> Dict[str, Any]:
    """Delegate per-CS file ops to the plugin macro (runs as `dataiku`), so
    we can read/write `<DIP_HOME>/config/projects/<pk>/code_studios/<csId>/`
    (mode 0700) and `<DIP_HOME>/lib/code_studio/<pk>/<libName>/` (owned by
    `dataiku:dataiku`) regardless of webapp impersonation.

    `dst_dir=None` → walk-only (no writes); else copy with no-overwrite policy.
    Both modes return the same shape: count/totalBytes/copied/skipped/errors/debug
    plus `walked` for walk-only."""
    walk_only = dst_dir is None
    params: Dict[str, Any] = {'src_dir': src_dir}
    if walk_only:
        params['walk_only'] = True
    else:
        params['dst_dir'] = dst_dir
    try:
        macro = project.get_macro(_CS_TMPL_COPY_MACRO_ID)
        run_id = macro.run(params=params, wait=True)
        result = macro.get_result(run_id, as_type='json')
        if not isinstance(result, dict):
            return {
                'count': 0, 'totalBytes': 0, 'walked': [], 'copied': [], 'skipped': [],
                'errors': [{'path': '', 'error': f'macro returned non-dict: {type(result).__name__}'}],
                'debug': {'macroId': _CS_TMPL_COPY_MACRO_ID, 'runId': run_id, 'walkOnly': walk_only},
            }
        result.setdefault('count', 0)
        result.setdefault('totalBytes', 0)
        result.setdefault('walked', [])
        result.setdefault('copied', [])
        result.setdefault('skipped', [])
        result.setdefault('errors', [])
        result.setdefault('debug', {})
        if isinstance(result.get('debug'), dict):
            result['debug']['macroId'] = _CS_TMPL_COPY_MACRO_ID
            result['debug']['runId'] = run_id
            result['debug']['walkOnly'] = walk_only
        return result
    except Exception as exc:
        return {
            'count': 0, 'totalBytes': 0, 'walked': [], 'copied': [], 'skipped': [],
            'errors': [{'path': '', 'error': f'macro run failed: {type(exc).__name__}: {str(exc)[:280]}'}],
            'debug': {'macroId': _CS_TMPL_COPY_MACRO_ID, 'walkOnly': walk_only, 'error': str(exc)[:300]},
        }


def _cs_tmpl_planned_name(old_name: str, new_template_id: str) -> str:
    suffix = '-' + new_template_id
    if old_name.endswith(suffix):
        return old_name + '-2'
    return old_name + suffix


@bp.route('/api/cs-template/migrate', methods=['POST'])
@advanced
def api_cs_template_migrate():
    payload = request.get_json(silent=True) or {}
    project_key = str(payload.get('projectKey') or '').strip()
    code_studio_id = str(payload.get('codeStudioId') or '').strip()
    new_template_id = str(payload.get('newTemplateId') or '').strip()
    dry_run = bool(payload.get('dryRun', True))
    force = bool(payload.get('force', False))

    if not project_key or not code_studio_id or not new_template_id:
        return jsonify({
            'status': 'error',
            'error': 'projectKey, codeStudioId, newTemplateId are required',
        })

    started = time.time()
    steps: List[Dict[str, Any]] = []

    def step(step_name: str, status: str, **extra: Any) -> None:
        steps.append({'name': step_name, 'status': status, **extra})

    client = g.client
    template_index = _cs_tmpl_template_index(client)
    if new_template_id not in template_index:
        return jsonify({
            'status': 'error',
            'error': f'Unknown templateId: {new_template_id}',
            'validTemplateIds': sorted(template_index.keys()),
        })

    try:
        project = client.get_project(project_key)
        cs = project.get_code_studio(code_studio_id)
        old_raw = cs.get_settings().get_raw()
    except Exception as exc:
        return jsonify({
            'status': 'error',
            'error': f'Failed to read code studio settings: {str(exc)[:300]}',
        })

    old_template_id = str(old_raw.get('templateId') or '')
    old_lib_name = str(old_raw.get('libName') or '')
    old_name = str(old_raw.get('name') or code_studio_id)
    old_owner = str(old_raw.get('owner') or '')

    if old_template_id == new_template_id:
        return jsonify({
            'status': 'error',
            'error': 'Code studio is already on the target template',
            'old': {
                'id': code_studio_id, 'name': old_name,
                'templateId': old_template_id, 'libName': old_lib_name,
            },
        })

    src_dir = _cs_tmpl_lib_dir(project_key, old_lib_name) if old_lib_name else ''
    ver_src_dir = _cs_tmpl_versioned_dir(project_key, code_studio_id)
    # Both walks go through the macro (runs as `dataiku`) so they can see
    # mode-0700 dirs the impersonated webapp user can't read.
    src_walk = _cs_tmpl_macro_files(project, src_dir) if src_dir else {'count': 0, 'totalBytes': 0, 'errors': []}
    ver_walk = _cs_tmpl_macro_files(project, ver_src_dir)
    src_count = src_walk.get('count') or 0
    src_bytes = src_walk.get('totalBytes') or 0
    ver_count = ver_walk.get('count') or 0
    ver_bytes = ver_walk.get('totalBytes') or 0
    _walk_errors = (src_walk.get('errors') or []) + (ver_walk.get('errors') or [])
    step('walk-source',
         'ok' if not _walk_errors else 'error',
         resources={'sourceDir': src_dir, 'count': src_count, 'totalBytes': src_bytes,
                    'errors': len(src_walk.get('errors') or [])},
         versioned={'sourceDir': ver_src_dir, 'count': ver_count, 'totalBytes': ver_bytes,
                    'errors': len(ver_walk.get('errors') or [])},
         count=src_count + ver_count,
         totalBytes=src_bytes + ver_bytes)

    try:
        state = cs.get_status().state
    except Exception as exc:
        state = None
        step('read-state', 'error', error=str(exc)[:300])
    else:
        step('read-state', 'ok', state=state)

    planned_name = _cs_tmpl_planned_name(old_name, new_template_id)

    base_response = {
        'old': {
            'id': code_studio_id,
            'name': old_name,
            'templateId': old_template_id,
            'libName': old_lib_name,
            'state': state,
            'owner': old_owner,
        },
        'new': {
            'plannedName': planned_name,
            'plannedTemplateId': new_template_id,
            'plannedTemplateLabel': template_index[new_template_id]['label'],
        },
        'files': {
            'count': src_count + ver_count,
            'totalBytes': src_bytes + ver_bytes,
            'resources': {
                'sourceDir': src_dir, 'count': src_count, 'totalBytes': src_bytes,
                'walked': src_walk.get('walked') or [],
            },
            'versioned': {
                'sourceDir': ver_src_dir, 'count': ver_count, 'totalBytes': ver_bytes,
                'walked': ver_walk.get('walked') or [],
            },
        },
        'steps': steps,
        'warnings': [],
        'durationMs': int((time.time() - started) * 1000),
    }

    if dry_run:
        base_response['status'] = 'planned'
        base_response['durationMs'] = int((time.time() - started) * 1000)
        return jsonify(base_response)

    # Live migration
    if state == 'RUNNING':
        try:
            fut = cs.stop()
            fut.wait_for_result(timeout=120)
            step('stop-old', 'ok')
        except Exception as exc:
            step('stop-old', 'error', error=str(exc)[:300])
            if not force:
                base_response['status'] = 'error'
                base_response['error'] = f'Failed to stop running code studio: {str(exc)[:300]}'
                base_response['durationMs'] = int((time.time() - started) * 1000)
                return jsonify(base_response)
            base_response['warnings'].append('proceeded despite stop failure (force=true)')

    try:
        new_handle = project.create_code_studio(planned_name, new_template_id)
        final_name = planned_name
        step('create-new', 'ok', createdName=final_name)
    except Exception as exc:
        step('create-new', 'error', error=str(exc)[:300])
        base_response['status'] = 'error'
        base_response['error'] = f'Failed to create new code studio: {str(exc)[:300]}'
        base_response['durationMs'] = int((time.time() - started) * 1000)
        return jsonify(base_response)

    try:
        new_raw = new_handle.get_settings().get_raw()
    except Exception as exc:
        step('read-new-settings', 'error', error=str(exc)[:300])
        base_response['status'] = 'error'
        base_response['error'] = f'Created CS but failed to read its settings: {str(exc)[:300]}'
        base_response['durationMs'] = int((time.time() - started) * 1000)
        return jsonify(base_response)

    new_lib_name = str(new_raw.get('libName') or '')
    new_cs_id = str(new_raw.get('id') or '')
    dst_dir = _cs_tmpl_lib_dir(project_key, new_lib_name) if new_lib_name else ''
    ver_dst_dir = _cs_tmpl_versioned_dir(project_key, new_cs_id) if new_cs_id else ''

    _empty_summary: Dict[str, Any] = {'count': 0, 'totalBytes': 0, 'copied': [], 'skipped': [], 'errors': []}
    # Always call the macro for live copy — it short-circuits cleanly when src
    # doesn't exist or is empty. Skip only if we have no destination to copy to.
    if dst_dir and src_dir:
        resources_summary = _cs_tmpl_macro_files(project, src_dir, dst_dir)
    else:
        resources_summary = dict(_empty_summary)
    if ver_dst_dir and ver_src_dir:
        versioned_summary = _cs_tmpl_macro_files(project, ver_src_dir, ver_dst_dir)
    else:
        versioned_summary = dict(_empty_summary)

    def _agg(*summaries: Dict[str, Any]) -> Dict[str, Any]:
        return {
            'count':      sum(s.get('count', 0)      for s in summaries),
            'totalBytes': sum(s.get('totalBytes', 0) for s in summaries),
            'copied':     [c for s in summaries for c in (s.get('copied') or [])],
            'skipped':    [k for s in summaries for k in (s.get('skipped') or [])],
            'errors':     [e for s in summaries for e in (s.get('errors') or [])],
        }
    copy_summary = _agg(resources_summary, versioned_summary)

    _res_dbg = resources_summary.get('debug') or {}
    _rt = _res_dbg.get('runtime') or {}
    _dst = _res_dbg.get('dst_dir_stat') or _res_dbg.get('dst_dir_stat_after') or {}
    _dst_parent = _res_dbg.get('dst_parent_stat') or {}
    step(
        'copy-files',
        'ok' if not copy_summary['errors'] else 'error',
        count=copy_summary['count'],
        totalBytes=copy_summary['totalBytes'],
        skipped=len(copy_summary['skipped']),
        errors=len(copy_summary['errors']),
        resources={'count': resources_summary.get('count', 0),
                   'totalBytes': resources_summary.get('totalBytes', 0),
                   'errors': len(resources_summary.get('errors') or [])},
        versioned={'count': versioned_summary.get('count', 0),
                   'totalBytes': versioned_summary.get('totalBytes', 0),
                   'errors': len(versioned_summary.get('errors') or [])},
        asUser=f"{_rt.get('euser')}({_rt.get('euid')}):{_rt.get('egroup')}({_rt.get('egid')})",
        dstOwner=f"{_dst.get('owner')}({_dst.get('uid')}):{_dst.get('group')}({_dst.get('gid')}) mode={_dst.get('mode')}",
        dstParentOwner=f"{_dst_parent.get('owner')}({_dst_parent.get('uid')}):{_dst_parent.get('group')}({_dst_parent.get('gid')}) mode={_dst_parent.get('mode')}",
    )
    _LOGGER.info(
        "[cs-tmpl] copy as %s(%s):%s(%s) -> resourcesDst=%s versionedDst=%s; resCount=%d verCount=%d errors=%d",
        _rt.get('euser'), _rt.get('euid'), _rt.get('egroup'), _rt.get('egid'),
        dst_dir, ver_dst_dir,
        resources_summary.get('count') or 0, versioned_summary.get('count') or 0,
        len(copy_summary['errors']),
    )

    # Sanity verify
    try:
        verify_raw = new_handle.get_settings().get_raw()
        if str(verify_raw.get('templateId') or '') == new_template_id:
            step('verify-new-template', 'ok')
        else:
            step('verify-new-template', 'error', got=verify_raw.get('templateId'))
            base_response['warnings'].append(
                f"new CS templateId={verify_raw.get('templateId')!r}, expected {new_template_id!r}"
            )
    except Exception as exc:
        step('verify-new-template', 'error', error=str(exc)[:300])

    _LOGGER.info(
        "[cs-tmpl] migrate pk=%s oldId=%s newId=%s oldTpl=%s newTpl=%s filesCopied=%d",
        project_key, code_studio_id, new_cs_id, old_template_id, new_template_id,
        copy_summary.get('count') or 0,
    )

    base_response['status'] = 'migrated'
    base_response['new'].update({
        'id': new_cs_id,
        'name': final_name,
        'templateId': new_template_id,
        'libName': new_lib_name,
    })
    base_response['files'] = {
        'count': src_count + ver_count,
        'totalBytes': src_bytes + ver_bytes,
        'copied': copy_summary.get('count', 0),
        'copiedBytes': copy_summary.get('totalBytes', 0),
        'skipped': copy_summary.get('skipped', []),
        'errors': copy_summary.get('errors', []),
        'resources': {
            'sourceDir': src_dir,
            'targetDir': dst_dir,
            'count': src_count,
            'totalBytes': src_bytes,
            'walked': src_walk.get('walked') or [],
            'copied': resources_summary.get('count', 0),
            'copiedBytes': resources_summary.get('totalBytes', 0),
            'skipped': resources_summary.get('skipped', []),
            'errors': resources_summary.get('errors', []),
            'debug': resources_summary.get('debug'),
        },
        'versioned': {
            'sourceDir': ver_src_dir,
            'targetDir': ver_dst_dir,
            'count': ver_count,
            'totalBytes': ver_bytes,
            'walked': ver_walk.get('walked') or [],
            'copied': versioned_summary.get('count', 0),
            'copiedBytes': versioned_summary.get('totalBytes', 0),
            'skipped': versioned_summary.get('skipped', []),
            'errors': versioned_summary.get('errors', []),
            'debug': versioned_summary.get('debug'),
        },
    }
    base_response['durationMs'] = int((time.time() - started) * 1000)
    return jsonify(base_response)
