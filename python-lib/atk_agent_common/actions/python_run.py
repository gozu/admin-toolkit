"""Power-Up: agent-authored Python (`python-run`).

Design principle: a NORMAL catalog action, so it inherits all six write gates
(master kill-switch, per-action gate, per-agent allowlist, HMAC confirm
token, confirm:true, audit) with ZERO new enforcement paths. What is unusual
about it:

* The signed canonical target is {codeSha256, purpose} — a small token, and
  any edit to the code after planning changes the sha and kills the token
  (the settings-set drift-kill precedent).
* The FULL code rides in the plan payload (the UI renders it verbatim for
  the per-run "I have read this code" ack) and in a kernel-local plan cache
  {sha → code}; the executor re-reads the cache and re-verifies the sha.
  A kernel recycle between plan and execute loses the cache → a clean
  "plan cache lost — re-plan" error. The cache entry is POPPED at execute,
  so a token is single-use for this action: every revision (or retry) is a
  new plan + a new ack.
* Execution venue (v1): a subprocess of the kernel's own Python
  (sys.executable), script in a tempfile, environment inherited so
  dataiku.api_client() authenticates as the toolkit — admin credentials BY
  DESIGN; that is exactly why the double gate (Agent Settings enable-confirm
  + per-run code ack) exists. local_only: remote-host credentials never
  enter agent kernels, so a remote venue is a later phase.
"""

import hashlib
import os
import subprocess
import sys
import tempfile
import time

from ..errors import ToolkitError
from . import _base

MAX_CODE_CHARS = 20_000
DEFAULT_TIMEOUT_S = 120
_OUTPUT_TAIL_CHARS = 4_000

# Kernel-local {sha: (code, expires_ts)} — TTL mirrors the confirm-token TTL
# (a cached script the token can no longer redeem is useless), capped so a
# planning loop can't grow it unboundedly.
_PLAN_CACHE = {}
_CACHE_MAX = 32
_CACHE_TTL_S = 15 * 60  # == confirm.TOKEN_TTL_SECONDS


def _cache_put(sha, code):
    now = time.time()
    for key in [k for k, (_, exp) in _PLAN_CACHE.items() if exp < now]:
        _PLAN_CACHE.pop(key, None)
    if len(_PLAN_CACHE) >= _CACHE_MAX:
        oldest = min(_PLAN_CACHE, key=lambda k: _PLAN_CACHE[k][1])
        _PLAN_CACHE.pop(oldest, None)
    _PLAN_CACHE[sha] = (code, now + _CACHE_TTL_S)


def _cache_pop(sha):
    code, exp = _PLAN_CACHE.pop(sha, (None, 0))
    if code is None or exp < time.time():
        return None
    return code


def _timeout_s(client):
    try:
        return max(5, min(int(client.settings.get('python_run_timeout_seconds')
                              or DEFAULT_TIMEOUT_S), 600))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S


def _require_local(host):
    if host not in (None, '', 'local'):
        raise ToolkitError(
            'python-run is local-only (v1): remote-host credentials never enter '
            'agent kernels, so scripts cannot run against host %r.' % host,
            remediation='Target the local host, or use catalogued actions / sensors '
                        'for remote hosts.')


def _tail(text, limit=_OUTPUT_TAIL_CHARS):
    text = text or ''
    if len(text) <= limit:
        return text
    return '…[%d chars trimmed]…\n%s' % (len(text) - limit, text[-limit:])


def _plan_python_run(client, host, target, params):
    _require_local(host)
    purpose = _base.require_str(target, 'purpose', 'python-run')
    code = str((target or {}).get('code') or '')
    if not code.strip():
        raise ToolkitError('python-run target needs non-empty `code`.')
    if len(code) > MAX_CODE_CHARS:
        raise ToolkitError('python-run code is %d chars — the cap is %d. Split the task '
                           'or tighten the script.' % (len(code), MAX_CODE_CHARS))
    sha = hashlib.sha256(code.encode('utf-8')).hexdigest()
    _cache_put(sha, code)
    timeout = _timeout_s(client)
    canonical = {'codeSha256': sha, 'purpose': purpose}
    return canonical, {
        'summary': 'POWER-UP: run an agent-authored Python script with the toolkit\'s '
                   'admin credentials. Purpose: %s' % purpose,
        'purpose': purpose,
        'code': code,
        'codeSha256': sha,
        'codeChars': len(code),
        'codeLines': code.count('\n') + 1,
        'timeoutSeconds': timeout,
        'venue': ('subprocess of the agent kernel\'s Python on the local DSS host; '
                  'environment inherited (dataiku.api_client() authenticates as the '
                  'toolkit\'s admin identity)'),
        'warnings': [
            'This script runs with ADMIN credentials. Read the code itself before '
            'approving — the purpose line is the agent\'s claim, not a guarantee.',
            'The confirm token is bound to this exact code (sha256) and is single-use: '
            'any edit or retry needs a fresh plan and a fresh acknowledgment.',
        ],
    }


def _exec_python_run(client, host, target):
    _require_local(host)
    sha = str((target or {}).get('codeSha256') or '')
    purpose = str((target or {}).get('purpose') or '')
    code = _cache_pop(sha)
    if code is None:
        raise ToolkitError(
            'python-run plan cache lost — the code behind sha %s… is no longer held '
            'by this kernel (kernel recycled, plan expired, or token already used).'
            % (sha[:12] or '?'),
            remediation='Re-plan with the same code to mint a fresh token, and get a '
                        'fresh user acknowledgment.')
    if hashlib.sha256(code.encode('utf-8')).hexdigest() != sha:
        raise ToolkitError('python-run cached code no longer matches its sha — re-plan.')
    timeout = _timeout_s(client)
    path = None
    try:
        with tempfile.NamedTemporaryFile('w', suffix='_atk_python_run.py',
                                         delete=False, encoding='utf-8') as handle:
            handle.write(code)
            path = handle.name
        started = time.monotonic()
        try:
            proc = subprocess.run([sys.executable, path], capture_output=True, text=True,
                                  timeout=timeout, cwd=tempfile.gettempdir())
            exit_code, stdout, stderr, timed_out = (proc.returncode, proc.stdout,
                                                    proc.stderr, False)
        except subprocess.TimeoutExpired as exc:
            def _text(stream):
                if isinstance(stream, bytes):
                    return stream.decode('utf-8', 'replace')
                return stream or ''
            exit_code, stdout, stderr, timed_out = (None, _text(exc.stdout),
                                                    _text(exc.stderr), True)
        duration_ms = int((time.monotonic() - started) * 1000)
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
    failed = timed_out or exit_code != 0
    out = {
        'purpose': purpose,
        'codeSha256': sha,
        'exitCode': exit_code,
        'timedOut': timed_out,
        'timeoutSeconds': timeout,
        'durationMs': duration_ms,
        'scriptFailed': failed,
        'stdout': _tail(stdout),
        'stderr': _tail(stderr),
    }
    if timed_out:
        out['note'] = ('Script exceeded the %ds wall-clock timeout and was killed. '
                       'A revision is a NEW plan + NEW acknowledgment.' % timeout)
    elif failed:
        out['note'] = ('Script exited non-zero — the full traceback is in stderr. '
                       'A revision is a NEW plan + NEW acknowledgment (tokens are '
                       'single-use).')
    return out


SPECS = [
    _base.spec(
        'python-run',
        'python-run {code, purpose} (POWER-UP: agent-authored Python, LAST resort when '
        'no catalogued action or sensor covers the task; runs local-only with admin '
        'credentials; token is sha-bound to the exact code and single-use; EVERY run '
        'needs the user\'s explicit per-run code acknowledgment)',
        'red', _plan_python_run, _exec_python_run, batchable=False, local_only=True),
]
