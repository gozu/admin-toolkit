"""LLM tooling routes: LLM Mesh catalog, the LLM audit scan (+progress
polling), AI log analysis (SSE) and quarterly report generation (SSE)."""
import json
import logging
import os
import re
import time
from concurrent.futures import as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, g, jsonify, request

from adk_backend.caching import CacheLoaderTimeout, _cache_get
from adk_backend.clients import ThreadPoolExecutor, _local_toolkit_project
from adk_backend.logparse import _parse_log_errors
from adk_backend.progress import (
    _append_progress_event,
    _append_progress_partial_row,
    _finish_progress,
    _read_progress,
    _set_progress_summary,
    _start_progress,
)
from adk_backend.settings import _BACKEND_SETTINGS
from adk_backend.sysinfo import _dip_home, _safe_read_text
from adk_backend.utils import _coerce_progress_params, _find_llm_ids, _sse_response

bp = Blueprint('llm_tools', __name__)

_LOGGER = logging.getLogger(__name__)


try:
    import llm_audit
    _llm_audit_available = True
except Exception:
    _llm_audit_available = False


@bp.route('/api/llms')
def api_llms():
    def loader():
        project = _local_toolkit_project()
        llms = project.list_llms()
        return [
            {
                'id': llm['id'],
                'label': llm.get('friendlyName') or llm['id'],
                'type': llm.get('type', ''),
                'connection': llm.get('connection') or '',
                'model': llm.get('friendlyNameShort') or llm.get('model') or llm.get('deployment') or llm['id'],
            }
            for llm in llms if llm.get('type') != 'RETRIEVAL_AUGMENTED'
        ]
    try:
        completion_llms = _cache_get('llms', 60, loader)
        return jsonify({'llms': completion_llms})
    except CacheLoaderTimeout:
        raise
    except Exception as e:
        return jsonify({'error': str(e), 'llms': []}), 500


_LLM_AUDIT_STRUCTURED_RECIPE_PREFIXES = ('prompt', 'nlp_llm_')
_LLM_AUDIT_CODE_RECIPE_TYPES = frozenset({
    'python', 'r', 'pyspark', 'spark_scala', 'scala', 'sql_query', 'sql_script',
})


def _llm_audit_scan_project_references(
    client: Any,
    project_key: str,
) -> Dict[str, Any]:
    """Collect per-asset llmId references in one project.

    Structured assets (prompt/LLM recipes, knowledge banks, agents) produce
    hits directly. Code recipes and Jupyter notebooks need the full llmId
    universe (built by the list_llms pass that runs concurrently), so their
    payload texts are returned for deferred matching via
    _llm_audit_match_deferred_texts.

    Returns {'hits': [...], 'seen': set, 'texts': [(assetType, assetName,
    recipeType, text)]}. Each hit: {llmId, assetType: 'recipe'|'notebook'|
    'knowledge_bank'|'agent', assetName, recipeType}. Deduped by (assetType,
    assetName, llmId). Per-asset try/except — one bad asset can't take out
    the project scan.
    """
    hits: List[Dict[str, Any]] = []
    seen_hits: set = set()

    def add_hit(llm_id: str, asset_type: str, asset_name: str, recipe_type: Optional[str]) -> None:
        k = (asset_type, asset_name, llm_id)
        if k in seen_hits:
            return
        seen_hits.add(k)
        hits.append({
            'llmId': llm_id,
            'assetType': asset_type,
            'assetName': asset_name,
            'recipeType': recipe_type,
        })

    project = client.get_project(project_key)

    try:
        recipes = project.list_recipes() or []
    except Exception as exc:
        _LOGGER.debug("[llm_audit_usage] list_recipes failed for %s: %s", project_key, exc)
        recipes = []

    structured_recipes = []
    code_recipes = []
    for r in recipes:
        if not isinstance(r, dict):
            continue
        rtype = r.get('type', '') or ''
        if rtype.startswith(_LLM_AUDIT_STRUCTURED_RECIPE_PREFIXES) or 'llm' in rtype.lower():
            structured_recipes.append(r)
        elif rtype in _LLM_AUDIT_CODE_RECIPE_TYPES:
            code_recipes.append(r)

    for r in structured_recipes:
        rtype = r.get('type', '') or ''
        rname = r.get('name') or ''
        try:
            recipe = project.get_recipe(rname)
            settings = recipe.get_settings()
            payload = settings.get_json_payload() if hasattr(settings, 'get_json_payload') else None
            if not payload:
                raw_str = settings.get_payload() if hasattr(settings, 'get_payload') else ''
                try:
                    payload = json.loads(raw_str) if raw_str else {}
                except Exception:
                    payload = {}
            if not payload:
                continue
            for llm_id in _find_llm_ids(payload):
                add_hit(llm_id, 'recipe', rname, rtype)
        except Exception as exc:
            _LOGGER.debug("[llm_audit_usage] recipe %s/%s failed: %s",
                             project_key, rname, exc)

    try:
        kbs = project.list_knowledge_banks() or []
    except Exception as exc:
        _LOGGER.debug("[llm_audit_usage] list_knowledge_banks failed for %s: %s", project_key, exc)
        kbs = []
    for kb in kbs:
        kb_id = kb.get('id') if isinstance(kb, dict) else None
        if not kb_id:
            continue
        try:
            kb_settings = project.get_knowledge_bank(kb_id).get_settings()
            raw = kb_settings.get_raw() if hasattr(kb_settings, 'get_raw') else kb_settings
            for llm_id in _find_llm_ids(raw):
                add_hit(llm_id, 'knowledge_bank', kb_id, None)
        except Exception as exc:
            _LOGGER.debug("[llm_audit_usage] knowledge_bank %s/%s failed: %s",
                             project_key, kb_id, exc)

    try:
        agents = project.list_agents() or []
    except Exception as exc:
        _LOGGER.debug("[llm_audit_usage] list_agents failed for %s: %s", project_key, exc)
        agents = []
    for ag in agents:
        ag_id = ag.get('id') if isinstance(ag, dict) else None
        if not ag_id:
            continue
        try:
            ag_settings = project.get_agent(ag_id).get_settings()
            raw = ag_settings.get_raw() if hasattr(ag_settings, 'get_raw') else ag_settings
            for llm_id in _find_llm_ids(raw):
                add_hit(llm_id, 'agent', ag_id, None)
        except Exception as exc:
            _LOGGER.debug("[llm_audit_usage] agent %s/%s failed: %s",
                             project_key, ag_id, exc)

    deferred_texts: List[Tuple[str, str, Optional[str], str]] = []
    for r in code_recipes:
        rtype = r.get('type', '') or ''
        rname = r.get('name') or ''
        try:
            recipe = project.get_recipe(rname)
            settings = recipe.get_settings()
            payload_str = settings.get_payload() if hasattr(settings, 'get_payload') else ''
            if not payload_str:
                continue
            deferred_texts.append(('recipe', rname, rtype, payload_str))
        except Exception as exc:
            _LOGGER.debug("[llm_audit_usage] code_recipe %s/%s failed: %s",
                             project_key, rname, exc)

    try:
        notebooks = project.list_jupyter_notebooks() or []
    except Exception as exc:
        _LOGGER.debug("[llm_audit_usage] list_jupyter_notebooks failed for %s: %s",
                         project_key, exc)
        notebooks = []
    for nb in notebooks:
        nb_name = getattr(nb, 'notebook_name', None)
        if not nb_name:
            continue
        try:
            raw = nb.get_content().get_raw()
            if isinstance(raw, str):
                source_text = raw
            else:
                try:
                    source_text = json.dumps(raw)
                except Exception:
                    source_text = str(raw)
            deferred_texts.append(('notebook', nb_name, None, source_text))
        except Exception as exc:
            _LOGGER.debug("[llm_audit_usage] notebook %s/%s failed: %s",
                             project_key, nb_name, exc)

    return {'hits': hits, 'seen': seen_hits, 'texts': deferred_texts}


def _llm_audit_match_deferred_texts(scan_result: Dict[str, Any], llm_id_regex: Optional[Any]) -> None:
    """Regex-match the deferred asset texts (code recipes, notebooks) collected
    by _llm_audit_scan_project_references, folding matches into its hits with
    the same (assetType, assetName, llmId) dedup. Texts are dropped after
    matching so large notebook payloads don't outlive this call."""
    texts = scan_result.get('texts') or []
    scan_result['texts'] = []
    if llm_id_regex is None:
        return
    hits: List[Dict[str, Any]] = scan_result['hits']
    seen_hits: set = scan_result['seen']
    for asset_type, asset_name, recipe_type, text in texts:
        for match in llm_id_regex.findall(text):
            k = (asset_type, asset_name, match)
            if k in seen_hits:
                continue
            seen_hits.add(k)
            hits.append({
                'llmId': match,
                'assetType': asset_type,
                'assetName': asset_name,
                'recipeType': recipe_type,
            })


def _llm_audit_scan_project(client: Any, project_key: str) -> List[Dict[str, Any]]:
    """List LLMs for one project and tag each row with the project key."""
    project = client.get_project(project_key)
    out: List[Dict[str, Any]] = []
    for llm in project.list_llms() or []:
        if not isinstance(llm, dict):
            continue
        # Skip meta-wrappers (agents, retrieval-augmented LLMs) — they are compositions
        # over real LLMs, not models that can be obsolete/current themselves.
        # Mirrors llm_audit.NOT_APPLICABLE_TYPES.
        if llm.get('type') in llm_audit.NOT_APPLICABLE_TYPES:
            continue
        out.append({
            'projectKey': project_key,
            'llmId': llm.get('id'),
            'type': llm.get('type'),
            'connection': llm.get('connection'),
            'rawModel': llm.get('model') or llm.get('deployment'),
            'model': llm.get('model'),
            'deployment': llm.get('deployment'),
            'friendlyName': llm.get('friendlyName'),
            'friendlyNameShort': llm.get('friendlyNameShort'),
        })
    return out


@bp.route('/api/llm-audit')
def api_llm_audit():
    if not _llm_audit_available:
        return jsonify({'error': 'llm_audit module unavailable',
                        'rows': [], 'summary': {}, 'pricingFetchedAt': None}), 500

    def loader():
        client = g.client
        started = time.time()
        run_id = _start_progress('llm_audit')
        events: List[Dict[str, Any]] = []

        def add_event(step: str, message: str, level: str = 'info', project_key: Optional[str] = None) -> None:
            ev: Dict[str, Any] = {
                'tMs': round((time.time() - started) * 1000.0, 2),
                'level': level,
                'step': step,
                'message': message,
            }
            if project_key:
                ev['projectKey'] = project_key
            events.append(ev)
            _append_progress_event('llm_audit', run_id, ev)

        def set_summary(progress_pct: float, phase: str, **extra: Any) -> None:
            payload: Dict[str, Any] = {
                'progressPct': int(max(0, min(100, round(progress_pct)))),
                'phase': phase,
                'totalElapsedMs': round((time.time() - started) * 1000.0, 2),
            }
            payload.update(extra)
            _set_progress_summary('llm_audit', run_id, payload)

        try:
            # Phase 1: pricing catalog (cached separately so multiple runs share it).
            set_summary(2, 'pricing')
            add_event('pricing_fetch', 'fetching LiteLLM pricing catalog')
            pricing_timeout = int(_BACKEND_SETTINGS.get('llm_audit_pricing_timeout_sec', 30))
            pricing_ttl = int(_BACKEND_SETTINGS.get('cache_ttl_llm_pricing', 21600))
            pricing_fetched_at: List[Optional[str]] = [None]

            def _pricing_loader() -> Dict[str, Any]:
                lookup = llm_audit.build_lookup(timeout=pricing_timeout)
                pricing_fetched_at[0] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                return {'lookup': lookup, 'fetchedAt': pricing_fetched_at[0]}

            try:
                pricing_blob = _cache_get('llm_audit_pricing', pricing_ttl, _pricing_loader)
            except llm_audit.PricingFetchError as exc:
                add_event('pricing_fetch_failed', f'pricing fetch failed: {exc}', 'error')
                raise
            lookup = pricing_blob['lookup']
            pricing_fetched_at_iso = pricing_blob.get('fetchedAt')
            add_event('pricing_ready', f'pricing lookup has {len(lookup)} entries')

            # Phase 2: instance connections (for CustomLLM unwrap).
            set_summary(8, 'connections')
            add_event('connections_fetch', 'fetching instance connections')
            try:
                connections_by_name = client.list_connections() or {}
            except Exception as exc:
                connections_by_name = {}
                add_event('connections_failed', f'list_connections failed: {exc}', 'warn')

            # Phase 3: project catalog.
            set_summary(12, 'catalog')
            projects = client.list_projects() or []
            project_keys = [p.get('projectKey') for p in projects if isinstance(p, dict) and p.get('projectKey')]
            total_projects = len(project_keys)
            add_event('catalog_ready', f'found {total_projects} project(s)')

            # Phase 4: parallel per-project list_llms().
            set_summary(15, 'scan', projectsTotal=total_projects, projectsDone=0)
            llm_rows: List[Dict[str, Any]] = []
            workers = max(1, int(_BACKEND_SETTINGS.get('parallel_workers_default', 8) or 8))
            project_name_lookup: Dict[str, str] = {}
            for _p in projects:
                if isinstance(_p, dict) and _p.get('projectKey'):
                    project_name_lookup[_p['projectKey']] = _p.get('name') or _p['projectKey']
            projects_using_by_llm_id: Dict[str, set] = {}
            assets_by_project_llm: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
            llm_id_regex = None
            if total_projects > 0:
                # The phase-4b asset fetch (recipes/notebooks per project) does not
                # depend on this pass's output — only its regex matching does — so
                # both sweeps run concurrently and matching is deferred until the
                # llmId universe is known.
                with ThreadPoolExecutor(max_workers=workers) as ex, \
                        ThreadPoolExecutor(max_workers=workers) as ref_pool:
                    ref_futures = {
                        ref_pool.submit(_llm_audit_scan_project_references, client, pk): pk
                        for pk in project_keys
                    }
                    futures = {ex.submit(_llm_audit_scan_project, client, pk): pk for pk in project_keys}
                    done = 0
                    for fut in as_completed(futures):
                        pk = futures[fut]
                        try:
                            project_rows = fut.result()
                            llm_rows.extend(project_rows)
                            for pr in project_rows:
                                if not isinstance(pr, dict):
                                    continue
                                _append_progress_partial_row('llm_audit', run_id, {
                                    'projectKey': pr.get('projectKey') or pk,
                                    'projectName': project_name_lookup.get(pr.get('projectKey') or pk, pk),
                                    'llmId': pr.get('llmId'),
                                    'friendlyName': pr.get('friendlyName'),
                                    'friendlyNameShort': pr.get('friendlyNameShort'),
                                    'type': pr.get('type'),
                                    'connection': pr.get('connection'),
                                    'rawModel': pr.get('rawModel'),
                                    'partial': True,
                                })
                        except Exception as exc:
                            add_event('scan_project_failed', f'{pk}: {exc}', 'warn', project_key=pk)
                        done += 1
                        # Throttle progress updates every project (lightweight).
                        scan_pct = 15.0 + 70.0 * (done / max(1, total_projects))
                        set_summary(scan_pct, 'scan',
                                    projectsTotal=total_projects, projectsDone=done,
                                    llmRowsTotal=len(llm_rows))

                    add_event('scan_done', f'collected {len(llm_rows)} LLM profile rows across {total_projects} project(s)')

                    # Phase 4b: build the llmId regex from the universe, then drain
                    # the overlapped asset-fetch futures and run deferred matching.
                    set_summary(50, 'usage_scan', projectsTotal=total_projects, projectsDone=0)
                    llm_id_universe = sorted({row.get('llmId') for row in llm_rows if row.get('llmId')})
                    if llm_id_universe:
                        try:
                            llm_id_regex = re.compile('|'.join(re.escape(i) for i in llm_id_universe))
                        except Exception as exc:
                            add_event('usage_regex_failed', f'failed to compile llmId regex: {exc}', 'warn')

                    done = 0
                    for fut in as_completed(ref_futures):
                        pk = ref_futures[fut]
                        try:
                            scan_result = fut.result()
                            _llm_audit_match_deferred_texts(scan_result, llm_id_regex)
                            for hit in scan_result['hits']:
                                llm_id = hit.get('llmId')
                                if not llm_id:
                                    continue
                                projects_using_by_llm_id.setdefault(llm_id, set()).add(pk)
                                assets_by_project_llm.setdefault(pk, {}).setdefault(llm_id, []).append({
                                    'assetType': hit.get('assetType'),
                                    'assetName': hit.get('assetName'),
                                    'recipeType': hit.get('recipeType'),
                                })
                        except Exception as exc:
                            add_event('usage_scan_project_failed', f'{pk}: {exc}', 'warn', project_key=pk)
                        done += 1
                        usage_pct = 50.0 + 35.0 * (done / max(1, total_projects))
                        set_summary(usage_pct, 'usage_scan',
                                    projectsTotal=total_projects, projectsDone=done,
                                    llmRowsTotal=len(llm_rows))
            else:
                add_event('scan_done', 'collected 0 LLM profile rows across 0 project(s)')
                set_summary(50, 'usage_scan', projectsTotal=0, projectsDone=0)

            add_event('usage_scan_done',
                      f'{sum(len(v) for v in projects_using_by_llm_id.values())} project-references '
                      f'across {len(projects_using_by_llm_id)} distinct llmId(s)')

            # Phase 5: classify and dedupe by llmId — project.list_llms() returns the
            # whole visible catalog per project, so the same LLM repeats across projects
            # and a per-(projectKey, llmId) payload balloons to tens of MB. One row per
            # distinct llmId carries `projectKeys` (all exposing projects) instead.
            set_summary(88, 'classify', llmRowsTotal=len(llm_rows))
            project_names: Dict[str, str] = {}
            for p in projects:
                if isinstance(p, dict) and p.get('projectKey'):
                    project_names[p['projectKey']] = p.get('name') or p['projectKey']

            first_row_by_llm: Dict[str, Dict[str, Any]] = {}
            exposing_by_llm: Dict[str, set] = {}
            for row in llm_rows:
                llm_id = row.get('llmId') or ''
                if not llm_id:
                    continue
                if llm_id not in first_row_by_llm:
                    first_row_by_llm[llm_id] = row
                pk = row.get('projectKey')
                if pk:
                    exposing_by_llm.setdefault(llm_id, set()).add(pk)

            # Merge usage assets across referencing projects, tagging each asset
            # with the project it lives in.
            assets_by_llm: Dict[str, List[Dict[str, Any]]] = {}
            for pk, by_llm in assets_by_project_llm.items():
                for llm_id, assets in by_llm.items():
                    bucket = assets_by_llm.setdefault(llm_id, [])
                    for asset in assets:
                        bucket.append(dict(asset, projectKey=pk))

            classified_rows: List[Dict[str, Any]] = []
            for llm_id, row in first_row_by_llm.items():
                verdict = llm_audit.classify_llm(row, lookup, connections_by_name=connections_by_name)
                using_set = projects_using_by_llm_id.get(llm_id, set())
                first_pk = row.get('projectKey')
                merged = {
                    'projectKey': first_pk,
                    'projectName': project_names.get(first_pk or '', first_pk or ''),
                    'projectKeys': sorted(exposing_by_llm.get(llm_id, set())),
                    'llmId': llm_id,
                    'friendlyName': row.get('friendlyName'),
                    'friendlyNameShort': row.get('friendlyNameShort'),
                    'type': row.get('type'),
                    'connection': row.get('connection'),
                    'rawModel': row.get('rawModel'),
                }
                merged.update(verdict)
                merged['projectsUsing'] = len(using_set)
                merged['referencingProjects'] = sorted(using_set)[:50]
                merged['usageAssets'] = assets_by_llm.get(llm_id, [])
                classified_rows.append(merged)

            summary = llm_audit.summarize_rows(classified_rows)
            summary['pricingFetchedAt'] = pricing_fetched_at_iso
            summary['totalElapsedMs'] = round((time.time() - started) * 1000.0, 2)

            # Surface per-project scan failures collected during phases 4/4b.
            _scan_error_area = {
                'scan_project_failed': 'scan',
                'usage_scan_project_failed': 'usage_scan',
            }
            scan_errors: List[Dict[str, Any]] = []
            failed_project_keys: set = set()
            for ev in events:
                area = _scan_error_area.get(ev.get('step'))
                if not area:
                    continue
                pk = ev.get('projectKey') or ''
                scan_errors.append({
                    'projectKey': pk,
                    'area': area,
                    'error': str(ev.get('message') or '')[:240],
                })
                if pk:
                    failed_project_keys.add(pk)
            summary['scanErrors'] = scan_errors
            summary['failedProjectCount'] = len(failed_project_keys)
            summary['scannedProjectCount'] = total_projects

            set_summary(100, 'done',
                        projectsTotal=total_projects,
                        projectsDone=total_projects,
                        llmsTotal=summary.get('llmsTotal', 0),
                        countsByStatus=summary.get('countsByStatus', {}),
                        distinctModelsByStatus=summary.get('distinctModelsByStatus', {}))
            _finish_progress('llm_audit', run_id, status='ok', summary=None)

            return {
                'rows': classified_rows,
                'summary': summary,
                'pricingFetchedAt': pricing_fetched_at_iso,
                'events': events,
            }
        except Exception as exc:
            _finish_progress('llm_audit', run_id, status='error', error=str(exc))
            raise

    try:
        ttl = int(_BACKEND_SETTINGS.get('cache_ttl_llm_audit', 600))
        data = _cache_get('llm_audit', ttl, loader)
        return jsonify(data)
    except Exception as exc:
        return jsonify({'error': str(exc), 'rows': [], 'summary': {}, 'pricingFetchedAt': None}), 500


@bp.route('/api/llm-audit/progress')
def api_llm_audit_progress():
    since_raw = request.args.get('since', '0')
    run_id = request.args.get('runId')
    rows_since_raw = request.args.get('rowsSince', '0')
    since, rows_since = _coerce_progress_params(since_raw, rows_since_raw)
    payload = _read_progress('llm_audit', since=since, run_id=run_id, rows_since=rows_since)
    return jsonify(payload)


@bp.route('/api/logs/ai-analysis', methods=['POST'])
def api_logs_ai_analysis():
    """Stream AI log analysis via SSE with phase updates and token streaming."""
    body = request.get_json(force=True)
    llm_id = body.get('llmId', '').strip()
    custom_system_prompt = (body.get('systemPrompt') or '').strip()
    client_user_message = (body.get('userMessage') or '').strip()

    _DEFAULT_SYSTEM_PROMPT = (
        "You are an expert Dataiku DSS administrator and backend engineer "
        "analyzing error logs from a DSS instance's backend.log file.\n\n"
        "Before answering, think step-by-step through each error carefully. For each error pattern:\n"
        "- Reason through what component, subsystem, or configuration could cause it.\n"
        "- Search the web for the specific error message, Java exception, or stack trace to find "
        "known issues, Dataiku Knowledge Base articles, community posts, or release notes.\n"
        "- Cross-reference with official Dataiku documentation (doc.dataiku.com) for configuration "
        "guidance, known limitations, and recommended fixes.\n"
        "- Only after researching, provide your diagnosis and remediation.\n\n"
        "Your task:\n"
        "1. Identify the root cause of each distinct error or error pattern.\n"
        "2. Assess severity (Critical / Warning / Informational).\n"
        "3. Provide specific, actionable remediation steps, including links to relevant "
        "documentation or KB articles when available.\n"
        "4. Group related errors sharing a root cause.\n"
        "5. Highlight data loss risk, security issues, or service outage indicators.\n\n"
        "Format: markdown with headings per issue, bullet points for remediation. "
        "Start with a 2-3 sentence Executive Summary."
    )
    system_prompt = custom_system_prompt if custom_system_prompt else _DEFAULT_SYSTEM_PROMPT

    def generate():
        if not llm_id:
            yield "event: error\ndata: %s\n\n" % json.dumps({"error": "llmId is required"})
            return

        try:
            yield "event: phase\ndata: %s\n\n" % json.dumps({"phase": "Preparing log data"})

            project = _local_toolkit_project()

            if client_user_message:
                # Frontend provided the pre-built user message — use it directly
                user_message = client_user_message
                log_chars = len(user_message)
            else:
                # Fallback: build user message from cache/disk (backward compat)
                dip_home = _dip_home()

                def loader():
                    log_content = None
                    try:
                        log_content = client.get_log('backend.log')
                    except Exception:
                        log_content = _safe_read_text(os.path.join(dip_home, 'run', 'backend.log'))
                    return _parse_log_errors(log_content)

                log_data = _cache_get('log_errors', _BACKEND_SETTINGS['cache_ttl_log_errors'], loader)
                raw_errors = log_data.get('rawLogErrors', [])

                if not raw_errors:
                    yield "event: done\ndata: %s\n\n" % json.dumps({
                        "analysis": "No log errors found to analyze.",
                        "llmId": llm_id, "logCharsAnalyzed": 0,
                    })
                    return

                error_text = '\n---\n'.join('\n'.join(block.get('data', [])) for block in raw_errors)
                max_chars = 100_000
                if len(error_text) > max_chars:
                    error_text = error_text[-max_chars:]
                log_chars = len(error_text)

                log_stats = log_data.get('logStats', {})
                user_message = (
                    "Analyze the following DSS backend.log errors.\n"
                    "Stats: %d unique errors, %d total log lines.\n\n"
                    "```\n%s\n```"
                ) % (log_stats.get('Unique Errors', 0), log_stats.get('Total Lines', 0), error_text)

            yield "event: phase\ndata: %s\n\n" % json.dumps({"phase": "Sending to LLM"})

            completion = project.get_llm(llm_id).new_completion()
            completion.settings['maxOutputTokens'] = 4096
            # completion.settings['temperature'] = 0.3  # disabled – not supported by some small LLMs (e.g. GPT-5 mini/nano)
            completion.with_message(message=system_prompt, role='system')
            completion.with_message(message=user_message, role='user')

            # Try streaming first, fall back to non-streamed
            streamed = False
            try:
                yield "event: phase\ndata: %s\n\n" % json.dumps({"phase": "Generating analysis"})
                resp_stream = completion.execute_streamed()
                for chunk in resp_stream:
                    text = str(chunk.text) if hasattr(chunk, 'text') else ''
                    if text:
                        streamed = True
                        yield "event: chunk\ndata: %s\n\n" % json.dumps({"text": text})
            except (AttributeError, TypeError):
                # execute_streamed() not available, fall back
                resp = completion.execute()
                analysis_text = str(resp.text)
                yield "event: chunk\ndata: %s\n\n" % json.dumps({"text": analysis_text})
                streamed = False

            yield "event: done\ndata: %s\n\n" % json.dumps({
                "llmId": llm_id,
                "logCharsAnalyzed": log_chars,
                "streamed": streamed,
            })
        except Exception as e:
            yield "event: error\ndata: %s\n\n" % json.dumps({"error": str(e)})

    return _sse_response(generate)


@bp.route('/api/report/generate', methods=['POST'])
def api_report_generate():
    """Generate a quarterly health check report via LLM Mesh. SSE with phase-only events."""
    body = request.get_json(force=True)
    llm_id = (body.get('llmId') or '').strip()
    diagnostic_data = body.get('diagnosticData') or {}

    _REPORT_SYSTEM_PROMPT = (
        "You are a senior Dataiku Technical Account Manager (TAM) creating a quarterly health check "
        "presentation for a customer's technical leadership. This will be rendered as an 18-20 slide "
        "HTML slideshow that the TAM presents live to the customer.\n\n"
        "Think deeply about the diagnostic data before writing. Analyze cross-cutting patterns, "
        "correlate issues across sections, and identify root causes. Take your time.\n\n"
        "=== VOICE & TONE ===\n"
        "- You are a trusted advisor, not a monitoring tool.\n"
        "- Use first-person plural: 'we recommend', 'our analysis shows', 'we observed'.\n"
        "- Lead with POSITIVES before concerns. Always acknowledge what's working well.\n"
        "- Frame findings in BUSINESS IMPACT: 'training pipeline reliability' not 'OutOfMemoryError'.\n"
        "- Cite exact numbers, project names, config values. Never be vague.\n"
        "- Reference doc.dataiku.com links where relevant.\n\n"
        "=== SLIDE LAYOUT DETAILS ===\n"
        "The deck is an editorial, annual-report-style presentation: dark green paper, serif "
        "headlines, real charts drawn from the data. Your output populates its slides:\n\n"
        "SLIDE 1 (Title): Static - company name, date, DSS version. You don't write this.\n\n"
        "HEADLINES: Every slide object may include a 'headline' - a short editorial headline "
        "(4-9 words, sentence case, no trailing period) rendered as the slide's large serif "
        "title. Make it SPECIFIC to this instance's data, like a magazine pull-line. "
        "Always include it. Calibrate against these pairs (weak -> strong):\n"
        "  'Filesystem Health' -> '/data is 77% full and climbing'\n"
        "  'Code Environment Overview' -> 'Python 3.6 refuses to die'\n"
        "  'Projects Overview' -> '451 projects, little governance'\n"
        "  'Memory & JVM Analysis' -> 'No swap, no safety net'\n"
        "  'Users & Activity' -> 'Sixty designers, one reader'\n"
        "The test: if the headline could appear on ANY instance's report, it is too "
        "generic - rewrite it around this instance's own numbers, names, or tension. "
        "A number in the headline is usually a good sign; a category label never is. "
        "Do not force drama where the data is calm: 'Remarkably clean logs' beats "
        "false alarm. Never reuse the example headlines verbatim unless the data "
        "genuinely matches.\n\n"
        "SLIDE 2 (Executive Summary): LEFT shows a large health-score gauge (computed separately). "
        "RIGHT shows your 'overall_status' as a large italic pull-quote, then your 3 'findings' "
        "as numbered editorial rows. Each finding should be ONE bullet point "
        "(1-2 sentences max) that a VP can read in 5 seconds.\n\n"
        "DATA SLIDES (Instance Overview through Log Analysis): Each pairs a chart or stat band "
        "computed from the actual data (you don't write those) with your 'narrative'. "
        "The narrative renders as elegant serif paragraphs - each of your bullets becomes "
        "one short paragraph. Optional extras (highlights, risks, warnings, upgrade_paths) "
        "render as an em-dash list or warning chips beside it.\n\n"
        "  CRITICAL: Use BULLET POINTS (with bullet char), NOT prose paragraphs. 3-5 bullets "
        "per slide. Each bullet: one clear observation with a specific number or finding.\n"
        "  Format example:\n"
        "    '\\u2022 42 projects in healthy active use across the organization\\n"
        "\\u2022 ML Pipeline (PROJ1) leads with 156 versions, indicating critical production use\\n"
        "\\u2022 Consider version retention policy for projects exceeding 100 versions'\n\n"
        "  The data slides are, in order:\n"
        "    Instance Overview - DSS version, OS, CPU, Python\n"
        "    Projects Overview - project count, health score\n"
        "    Project Footprint - storage analysis, top projects by size\n"
        "    Code Environments - env count, Python/R version distribution\n"
        "    Code Env Health - health score, unused envs, upgrade paths\n"
        "    Filesystem Health - mount point usage percentages\n"
        "    Memory & JVM - heap settings, system RAM\n"
        "    Connections - connection types, counts\n"
        "    Issues & Risks - disabled features, plugins, risk level\n"
        "    Users & Activity - user counts by role\n"
        "    Compute & Cost (ONLY if 'computeCost' key is in the data) - compute resource "
        "usage from the audit-log window: memory GB-hours, CPU hours, LLM spend in USD, "
        "top projects/users by consumption, idle resources. Note the window dates "
        "(auditWindow) - this is days/weeks, not the full quarter.\n"
        "    Log Analysis - error counts, patterns\n\n"
        "  ADDITIONAL CONTEXT KEYS (no dedicated slide - weave into narratives and "
        "recommendations where relevant): 'llmMesh' (LLM Mesh audit: obsolete or "
        "mispriced models -> Connections / Issues / recommendations), 'k8sClusters' "
        "(attached K8s clusters -> Instance Overview / Issues), 'connectionHealth' "
        "(live connection probe failures -> Connections), 'sanityCheck' (DSS instance "
        "sanity-check findings -> Issues & Risks).\n\n"
        "  For 'highlights', 'risks', 'warnings', 'upgrade_paths' arrays: "
        "these render as short list items or warning chips. Keep each item UNDER 10 words.\n"
        "  For 'patterns' array: renders in monospace. Keep each under 80 chars.\n\n"
        "RECOMMENDATION SLIDES (three, after Log Analysis): Each is an editorial numbered list.\n"
        "  Each item has: a large outlined numeral, a bold serif TITLE (~5 words), "
        "a DESCRIPTION paragraph (1-2 sentences with specific action), "
        "and an IMPACT line (~5-8 words on business value).\n"
        "  Critical (2-3 items) - production stability / data loss risks\n"
        "  Important (3-5 items) - address this quarter to prevent escalation\n"
        "  Nice-to-Have (2-3 items) - efficiency and governance optimizations\n\n"
        "ACTION PLAN SLIDE: Vertical timeline with numbered steps.\n"
        "  Each step: action text (what to do), timeline (when), effort badge (low/medium/high).\n"
        "  Include 5-7 items ordered by priority. Use concrete timelines: "
        "'next maintenance window', 'within 30 days', 'next quarter', NOT 'soon' or 'when possible'.\n\n"
        "CLOSING SLIDE: Static - 'Next Steps' with TAM contact prompt. You don't write this.\n\n"
        "=== OUTPUT FORMAT ===\n"
        "Return ONLY valid JSON (no markdown fences, no commentary outside the JSON).\n"
        '{\n'
        '  "slides": {\n'
        '    "executive_summary": {\n'
        '      "findings": [\n'
        '        "One-sentence finding for card 1 (most impactful)",\n'
        '        "One-sentence finding for card 2",\n'
        '        "One-sentence finding for card 3"\n'
        '      ],\n'
        '      "overall_status": "STATUS_LABEL - one sentence summary",\n'
        '      "headline": "Editorial headline, 4-9 words"\n'
        '    },\n'
        '    "instance_overview": { "headline": "...", "narrative": "bullet point text with newlines" },\n'
        '    "projects": { "headline": "...", "narrative": "...", "highlights": ["short badge text", "..."] },\n'
        '    "project_footprint": { "headline": "...", "narrative": "...", "risks": ["short risk badge", "..."] },\n'
        '    "code_envs": { "headline": "...", "narrative": "..." },\n'
        '    "code_env_health": { "headline": "...", "narrative": "...", "upgrade_paths": ["short path", "..."] },\n'
        '    "filesystem": { "headline": "...", "narrative": "...", "warnings": ["short warning", "..."] },\n'
        '    "memory": { "headline": "...", "narrative": "...", "tuning_recs": ["short rec", "..."] },\n'
        '    "connections": { "headline": "...", "narrative": "..." },\n'
        '    "issues": { "headline": "...", "narrative": "...", "risk_level": "low|medium|high|critical" },\n'
        '    "users": { "headline": "...", "narrative": "..." },\n'
        '    "compute_cost": { "headline": "...", "narrative": "...", "drivers": ["short cost driver", "..."] },\n'
        '    "logs": { "headline": "...", "narrative": "...", "patterns": ["error pattern < 80 chars", "..."] },\n'
        '    "rec_critical": { "items": [{\n'
        '      "title": "Short Title (3-5 words)",\n'
        '      "description": "Specific action: what to change, where, and why. 1-2 sentences.",\n'
        '      "impact": "Business impact in 5-8 words"\n'
        '    }] },\n'
        '    "rec_important": { "items": [{ "title": "...", "description": "...", "impact": "..." }] },\n'
        '    "rec_nice_to_have": { "items": [{ "title": "...", "description": "...", "impact": "..." }] },\n'
        '    "action_plan": { "headline": "...", "priorities": [{\n'
        '      "action": "Specific task an admin can execute",\n'
        '      "timeline": "Concrete timeframe",\n'
        '      "effort": "low|medium|high"\n'
        '    }] }\n'
        '  }\n'
        '}\n\n'
        "STATUS_LABEL must be one of: HEALTHY, GOOD WITH CAVEATS, MODERATE RISK, or NEEDS ATTENTION.\n\n"
        "Include the 'compute_cost' key ONLY when the data contains 'computeCost' - omit it "
        "otherwise (its slide is hidden when the module has no data).\n\n"
        "Remember: ALL narrative fields must use bullet points (\\u2022), not paragraphs. "
        "3-5 bullets per narrative. Each bullet starts with \\u2022 and contains ONE observation with a number."
    )

    def generate():
        if not llm_id:
            yield "event: error\ndata: %s\n\n" % json.dumps({"error": "llmId is required"})
            return
        if not diagnostic_data:
            yield "event: error\ndata: %s\n\n" % json.dumps({"error": "No diagnostic data provided. Please wait for all data to load."})
            return

        try:
            yield "event: phase\ndata: %s\n\n" % json.dumps({"phase": "Preparing data"})

            project = _local_toolkit_project()

            user_message = "Analyze this DSS instance diagnostic data:\n\n" + json.dumps(diagnostic_data, indent=None, default=str)

            yield "event: phase\ndata: %s\n\n" % json.dumps({"phase": "Analyzing diagnostics"})

            completion = project.get_llm(llm_id).new_completion()
            completion.settings['maxOutputTokens'] = 32768
            # Allow extended thinking for deeper analysis
            try:
                completion.settings['budgetTokens'] = 100000
            except Exception:
                pass  # Not all LLM backends support budgetTokens
            completion.with_message(message=_REPORT_SYSTEM_PROMPT, role='system')
            completion.with_message(message=user_message, role='user')

            # Streamed call — avoids LLM Mesh gateway timeout (~263s)
            report_parts = []
            char_count = 0
            for chunk in completion.execute_streamed():
                if chunk.type == "footer":
                    break
                if chunk.type == "content" and chunk.text:
                    report_parts.append(chunk.text)
                    char_count += len(chunk.text)
                    yield "event: chunk\ndata: %s\n\n" % json.dumps({
                        "text": chunk.text,
                        "totalChars": char_count,
                    })
                elif chunk.type == "event":
                    yield "event: phase\ndata: %s\n\n" % json.dumps({
                        "phase": "Thinking: %s" % (chunk.event_kind or "reasoning"),
                    })

            report_text = ''.join(report_parts)

            # Strip markdown fences if present
            import re
            report_text = re.sub(r'^```(?:json)?\s*\n?', '', report_text)
            report_text = re.sub(r'\n?```\s*$', '', report_text).strip()

            yield "event: done\ndata: %s\n\n" % json.dumps({
                "report": report_text,
                "llmId": llm_id,
            })
        except Exception as e:
            yield "event: error\ndata: %s\n\n" % json.dumps({"error": str(e)})

    return _sse_response(generate)
