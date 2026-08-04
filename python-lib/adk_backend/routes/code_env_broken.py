"""Code Envs -> Broken: which envs failed their LAST build attempt, plus a
per-env LLM remediation stream.

DSS keeps no "last build succeeded" flag (`info` is empty, `isUptodate` tracks
spec drift), so the only record of a post-upgrade rebuild failure is the build
log text — parsed by `adk_backend.code_env_build`. Both routes are SSE and read
`g.client` inside their generator, so they go out through `_sse_response`
(which applies `stream_with_context`); the per-env fan-out uses the
host-context-propagating ThreadPoolExecutor from adk_backend.clients.
"""

import json
import logging
import time
from concurrent.futures import as_completed
from typing import Any, Dict, List, Optional

from flask import Blueprint, g, request

from adk_backend.clients import ThreadPoolExecutor, _local_toolkit_project
from adk_backend.code_env_build import (
    _LOG_PREFERENCE,
    classify,
    derive_dates,
    extract_error,
    isolate_last_build,
)
from adk_backend.footprint import _footprint_available
from adk_backend.routes.code_envs import _get_code_env_size_map
from adk_backend.usage_scan import _normalize_language, _normalize_usage_entry
from adk_backend.utils import _parallel_workers, _sse_response

bp = Blueprint('code_env_broken', __name__)

_LOGGER = logging.getLogger(__name__)

_USAGES_DETAIL_CAP = 200


def _ceb_inspect_env(client: Any, env_info: Dict[str, Any], size_by_env: Dict[str, int]) -> Dict[str, Any]:
    """Classify one env's last build attempt; log-read failures become an
    indeterminate status rather than an exception."""
    name = env_info.get('envName') or ''
    lang = env_info.get('envLang') or ''
    row: Dict[str, Any] = {
        'name': name,
        'lang': lang,
        'deploymentMode': env_info.get('deploymentMode') or '',
        'pythonVersion': env_info.get('pythonInterpreter') or '',
        'status': 'OK',
        'failureClass': None,
        'failureLabel': None,
        'logName': None,
        'errorExcerpt': '',
        'createdOn': None,
        'lastBuildOn': None,
        'sizeBytes': size_by_env.get('%s:%s' % (_normalize_language(lang), name)),
        'usageCount': None,
        'usages': [],
        'usagesTruncated': False,
    }

    try:
        env = client.get_code_env(lang, name)
        log_entries = env.list_logs()
    except Exception as exc:
        row['status'] = 'LOG_UNAVAILABLE'
        row['errorExcerpt'] = '%s: %s' % (type(exc).__name__, str(exc)[:200])
        return row

    row['createdOn'], row['lastBuildOn'] = derive_dates(log_entries)
    log_names = {str(e.get('name') or '') for e in log_entries or [] if isinstance(e, dict)}
    log_name = next((n for n in _LOG_PREFERENCE if n in log_names), None)
    if log_name is None:
        row['status'] = 'NO_BUILD_LOG'
        row['errorExcerpt'] = 'No recognised build log (found: %s)' % ', '.join(sorted(log_names))
        return row

    try:
        text = env.get_log(log_name)
    except Exception as exc:
        row['status'] = 'LOG_UNAVAILABLE'
        row['errorExcerpt'] = '%s: %s' % (type(exc).__name__, str(exc)[:200])
        return row

    row['logName'] = log_name
    block = isolate_last_build(str(text or ''))
    failure_class, failure_label = classify(block)
    if not failure_class:
        return row

    row.update(status='FAILED', failureClass=failure_class, failureLabel=failure_label,
               errorExcerpt=extract_error(block))

    try:
        usages = client._perform_json('GET', '/admin/code-envs/%s/%s/usages' % (lang, name))
    except Exception:
        usages = None
    if isinstance(usages, list):
        row['usageCount'] = len(usages)
        row['usages'] = [_normalize_usage_entry(u, {}) for u in usages[:_USAGES_DETAIL_CAP]
                         if isinstance(u, dict)]
        row['usagesTruncated'] = len(usages) > _USAGES_DETAIL_CAP
    return row


@bp.route('/api/code-envs/broken/scan')
def api_code_env_broken_scan():
    """Stream per-env build-failure verdicts via SSE.

    Unlike the cleaner scan this covers DSS_INTERNAL and PLUGIN_MANAGED envs
    too — they rebuild on upgrade like any other env and their failures matter.
    """
    def generate():
        t0 = time.time()
        client = g.client

        try:
            all_envs = client.list_code_envs() or []
        except Exception as exc:
            yield "event: error\ndata: %s\n\n" % json.dumps({"error": str(exc)})
            return

        size_by_env: Dict[str, int] = {}
        sizes_available = False
        if _footprint_available():
            try:
                size_by_env = _get_code_env_size_map(client)
                sizes_available = bool(size_by_env)
            except Exception as exc:
                _LOGGER.warning("[code-env-broken] size map unavailable: %s", exc)

        yield "event: init\ndata: %s\n\n" % json.dumps({
            "total": len(all_envs),
            "sizesAvailable": sizes_available,
        })

        counts = {'FAILED': 0, 'OK': 0}
        indeterminate = 0
        workers = max(1, min(8, _parallel_workers()))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_ceb_inspect_env, client, env_info, size_by_env): env_info
                       for env_info in all_envs}
            for future in as_completed(futures):
                try:
                    row = future.result()
                except Exception as exc:
                    # A worker raising past its own guards must not kill the
                    # stream — the client would hang on partial rows with no
                    # done event. Degrade that env to indeterminate instead.
                    env_info = futures[future]
                    _LOGGER.exception("[code-env-broken] worker failed for %s",
                                      env_info.get('envName'))
                    row = {
                        'name': env_info.get('envName') or '',
                        'lang': env_info.get('envLang') or '',
                        'deploymentMode': env_info.get('deploymentMode') or '',
                        'pythonVersion': env_info.get('pythonInterpreter') or '',
                        'status': 'LOG_UNAVAILABLE',
                        'failureClass': None, 'failureLabel': None, 'logName': None,
                        'errorExcerpt': '%s: %s' % (type(exc).__name__, str(exc)[:200]),
                        'createdOn': None, 'lastBuildOn': None, 'sizeBytes': None,
                        'usageCount': None, 'usages': [], 'usagesTruncated': False,
                    }
                if row['status'] in counts:
                    counts[row['status']] += 1
                else:
                    indeterminate += 1
                yield "event: env\ndata: %s\n\n" % json.dumps(row)

        yield "event: done\ndata: %s\n\n" % json.dumps({
            "total": len(all_envs),
            "failed": counts['FAILED'],
            "ok": counts['OK'],
            "indeterminate": indeterminate,
            "totalMs": int((time.time() - t0) * 1000),
        })

    return _sse_response(generate)


_ADVICE_SYSTEM_PROMPT = (
    "You are advising a Dataiku DSS administrator whose code environment failed "
    "to rebuild (typically after a platform upgrade). Name the specific "
    "package(s) and version(s) involved and give the concrete fix: a version to "
    "pin, a constraint to loosen, a system library to install, a proxy/index "
    "setting to correct. If the log is insufficient to be certain, say what to "
    "check next. Do not restate the log and do not add pleasantries.\n\n"
    "Answer in markdown with exactly these sections:\n"
    "## Diagnosis — what broke and why, in two or three sentences.\n"
    "## Fix — numbered steps with the exact commands or DSS UI paths "
    "(Administration → Code Envs → <env> → Packages to install).\n"
    "## Verify — how the administrator confirms the rebuild succeeded."
)

_ADVICE_USER_PROMPT = """Code environment : {env}
Language         : {lang} ({python})
Failure category : {label}

Build log excerpt:
---
{error}
---"""


@bp.route('/api/code-envs/broken/advice', methods=['POST'])
def api_code_env_broken_advice():
    """Stream an LLM remediation for one failed code env via SSE."""
    body = request.get_json(force=True)
    llm_id = (body.get('llmId') or '').strip()
    env_name = (body.get('envName') or '').strip()
    env_lang = (body.get('envLang') or '').strip()
    python_version = (body.get('pythonVersion') or '').strip()
    failure_label = (body.get('failureLabel') or '').strip()
    error_excerpt = (body.get('errorExcerpt') or '').strip()

    def generate():
        if not llm_id:
            yield "event: error\ndata: %s\n\n" % json.dumps({"error": "llmId is required"})
            return

        try:
            yield "event: phase\ndata: %s\n\n" % json.dumps({"phase": "Sending to LLM"})

            # The mesh call runs on the local toolkit project even when the scan
            # targeted a remote host — the excerpt travels in the request.
            project = _local_toolkit_project()
            completion = project.get_llm(llm_id).new_completion()
            completion.settings['maxOutputTokens'] = 4096
            completion.with_message(message=_ADVICE_SYSTEM_PROMPT, role='system')
            completion.with_message(message=_ADVICE_USER_PROMPT.format(
                env=env_name,
                lang=env_lang,
                python=python_version or 'n/a',
                label=failure_label or 'unknown',
                error=error_excerpt or '(no detail)',
            ), role='user')

            # Try streaming first, fall back to non-streamed
            try:
                yield "event: phase\ndata: %s\n\n" % json.dumps({"phase": "Generating remediation"})
                for chunk in completion.execute_streamed():
                    text = str(chunk.text) if hasattr(chunk, 'text') else ''
                    if text:
                        yield "event: chunk\ndata: %s\n\n" % json.dumps({"text": text})
            except (AttributeError, TypeError):
                # execute_streamed() not available, fall back
                resp = completion.execute()
                yield "event: chunk\ndata: %s\n\n" % json.dumps({"text": str(resp.text)})

            yield "event: done\ndata: %s\n\n" % json.dumps({"llmId": llm_id})
        except Exception as exc:
            yield "event: error\ndata: %s\n\n" % json.dumps({"error": str(exc)})

    return _sse_response(generate)
