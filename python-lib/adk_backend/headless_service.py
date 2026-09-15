"""Actual Dataiku Headless MCP transport, retained in one backend event loop.

The upstream Cobuild module is bound to a request-local DSS client, not its
CLI's mutable active-instance setting. No native MCP operations are exposed.
Private prompts/results stay in bounded process memory; a restart loses them.
"""
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import json
import threading
import time
from types import SimpleNamespace
import uuid

_BOUND = ContextVar('adtk_headless_identity')
_LOCK = threading.RLock()
_LOOP = None
_SESSIONS = {}
_SDK_TASKS = set()
MAX_SESSIONS = 8
TURN_SECONDS = 300
IDLE_SECONDS = 3600  # ADTK cluster/host operations can take tens of minutes.
TRANSPORT = 'dataiku-headless-mcp-in-process'


class ProviderFailure(ValueError):
    """A retained terminal response was observed, rather than a lost transport."""


@dataclass
class Session:
    owner: str
    client: object
    project: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    revision: int = 0
    status: str = 'pending'
    message: str = ''
    stopped: bool = False
    conversation: str = ''
    turn: str = ''
    seconds: float = 0
    touched: float = field(default_factory=time.monotonic)
    queue: object = None
    active: bool = True
    sdk_pending: int = 0


def _retire_upstream(row):
    """Discard private local state only after its workers have finished."""
    if row.active or row.sdk_pending:
        return
    from dataiku_mcp.tools import cobuild
    for key, entry in list(cobuild._conversations.items()):
        if entry.instance_name == row.id:
            if entry.turn is None or entry.turn.task.done():
                cobuild._conversations.pop(key, None)
            else:
                entry.turn.task.add_done_callback(lambda _: _retire_upstream(row))


def _leased_executor(executor):
    async def run(func, *args, **kwargs):
        row = _BOUND.get()
        with _LOCK:
            row.sdk_pending += 1
        # Cancellation of local MCP polling must not release capacity while a
        # blocking SDK call is still queued/running. Retain the actual worker
        # task until completion; never retry it or infer remote cancellation.
        task = asyncio.create_task(executor(func, *args, **kwargs))
        _SDK_TASKS.add(task)

        def finished(done):
            _SDK_TASKS.discard(done)
            if not done.cancelled():
                done.exception()  # Consume errors without logging private SDK text.
            with _LOCK:
                row.sdk_pending -= 1
            asyncio.get_running_loop().call_soon(_retire_upstream, row)

        task.add_done_callback(finished)
        return await asyncio.shield(task)
    return run


def _server():
    from dataiku_mcp import mcp
    from dataiku_mcp.tools import cobuild
    from dataiku_mcp.tools.utils import async_executor
    # Request identity and worker accounting are bound here. Conversation/turn handling and
    # MCP serialization remain intact. ContextVars propagate through upstream's
    # copy_context executor; concurrent calls never mutate active-instance state.
    cobuild.get_dss_client = lambda: _BOUND.get().client
    cobuild.get_current_instance_for_tool = lambda: SimpleNamespace(
        name=_BOUND.get().id, url=_BOUND.get().client.host)
    cobuild.run_blocking = _leased_executor(async_executor.run_blocking)
    cobuild.run_cobuild_blocking = _leased_executor(async_executor.run_cobuild_blocking)
    return mcp


def _loop():
    global _LOOP
    with _LOCK:
        if _LOOP is None:
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name='adtk-headless', daemon=True).start()
            _LOOP = loop
        return _LOOP


async def _call(mcp, name, args):
    if name not in {'start_cobuild_conversation', 'send_cobuild_message', 'get_cobuild_turn_status'}:
        raise ValueError('Native Headless tools are unavailable.')
    if name == 'send_cobuild_message':
        args = dict(args, allow_edit_project=False)
    result = await mcp.call_tool(name, args)
    if result.is_error:
        raise ValueError('Headless MCP rejected the request.')
    value = json.loads(''.join(getattr(c, 'text', '') for c in result.content))
    if not isinstance(value, dict):
        raise ValueError('Invalid Headless MCP response.')
    return value


async def _run(row, first_message):
    token = _BOUND.set(row)
    try:
        from fastmcp import Client
        row.queue = asyncio.Queue(maxsize=1)
        async with Client(_server(), timeout=TURN_SECONDS + 15) as mcp:
            opened = await asyncio.wait_for(_call(mcp, 'start_cobuild_conversation',
                                                {'project_key': row.project}), 30)
            row.conversation = opened['conversation_id']
            message = first_message
            while not row.stopped:
                started = time.monotonic()
                args = {'project_key': row.project, 'conversation_id': row.conversation,
                        'message': message}
                result = await asyncio.wait_for(_call(mcp, 'send_cobuild_message', args), TURN_SECONDS)
                while result.get('status') in {'queued', 'in_progress'}:
                    row.turn = result['turn_id']
                    remaining = TURN_SECONDS - (time.monotonic() - started)
                    if remaining <= 0 or row.stopped:
                        raise asyncio.TimeoutError()
                    result = await asyncio.wait_for(_call(mcp, 'get_cobuild_turn_status', {
                        'project_key': row.project, 'conversation_id': row.conversation,
                        'turn_id': row.turn}), remaining)
                row.seconds = time.monotonic() - started
                row.turn = result.get('turn_id', row.turn)
                if (result.get('status') != 'completed' or result.get('is_error')
                        or result.get('is_confirmation_request') or result.get('is_question_request')):
                    raise ProviderFailure('Headless requires unsupported interaction or failed.')
                with _LOCK:
                    row.message = str(result.get('message') or '')
                    row.status = 'completed'
                    row.touched = time.monotonic()
                try:
                    message = await asyncio.wait_for(row.queue.get(), IDLE_SECONDS)
                except asyncio.TimeoutError:
                    break
    except asyncio.TimeoutError:
        row.status = 'unknown'
        row.message = ('Headless wait expired. Remote cancellation is not verified; '
                       'the Cobuild turn may still be running. No task was retried.')
    except Exception as exc:
        # SDK/MCP exceptions can embed prompts or credentials. Return only type.
        row.status = 'failed' if not row.conversation or isinstance(exc, ProviderFailure) else 'unknown'
        row.message = 'Headless transport failed (%s); no fallback or retry.' % type(exc).__name__
        if row.status == 'unknown':
            row.message += ' Remote turn outcome and cancellation are not verified.'
        if isinstance(exc, ModuleNotFoundError):
            name = exc.name or ''
            if name and all(c.isalnum() or c in '._' for c in name):
                row.message = ('Headless dependency missing: %s. Install the release ZIP and '
                               'update the plugin environment; no fallback or retry.' % name)
    finally:
        row.stopped = True
        row.active = False
        _BOUND.reset(token)
        # Only retire completed upstream entries. A pending SDK call may still
        # run remotely; do not describe discarding local state as cancellation.
        if row.conversation:
            _retire_upstream(row)


def _owned(task_id, owner):
    row = _SESSIONS.get(task_id)
    if row is None or row.owner != owner:
        raise ValueError('Headless conversation unavailable for this caller, or backend restarted.')
    return row


def submit(client, owner, project, message, task_id=None, revision=None):
    if not isinstance(message, str) or not message.strip() or len(message) > 500000:
        raise ValueError('A nonempty Headless message within 500 KB is required.')
    with _LOCK:
        now = time.monotonic()
        for key, value in list(_SESSIONS.items()):
            if not value.active and not value.sdk_pending and now - value.touched > IDLE_SECONDS:
                del _SESSIONS[key]
        if task_id:
            row = _owned(task_id, owner)
            if row.project != project or row.client.host != client.host:
                raise ValueError('Headless host/project binding changed.')
            if row.stopped or row.status != 'completed' or row.revision != revision:
                raise ValueError('Headless turn unavailable, pending or already submitted; do not retry.')
            row.status, row.message = 'pending', ''
            row.revision += 1
            _loop().call_soon_threadsafe(row.queue.put_nowait, message)
        else:
            if sum(s.active or bool(s.sdk_pending) for s in _SESSIONS.values()) >= MAX_SESSIONS:
                raise ValueError('Headless capacity is full; inspect outstanding tasks.')
            if len(_SESSIONS) >= 128:
                completed = [s for s in _SESSIONS.values() if not s.active and not s.sdk_pending]
                if completed:
                    del _SESSIONS[min(completed, key=lambda s: s.touched).id]
            row = Session(owner, client, project)
            _SESSIONS[row.id] = row
            asyncio.run_coroutine_threadsafe(_run(row, message), _loop())
        return snapshot(row.id, owner)


def snapshot(task_id, owner):
    with _LOCK:
        row = _owned(task_id, owner)
        return {'taskId': row.id, 'revision': row.revision, 'status': row.status,
                'message': row.message, 'seconds': round(row.seconds, 3),
                'mode': 'headless', 'transport': TRANSPORT,
                'remoteCancellationVerified': False}


def stop(task_id, owner):
    with _LOCK:
        row = _owned(task_id, owner)
        row.stopped = True
        if row.status == 'pending':
            row.status = 'unknown'
            row.message = 'Local task stopped; remote Cobuild cancellation is not verified.'
        if row.queue is not None and row.queue.empty():
            _loop().call_soon_threadsafe(row.queue.put_nowait, '')
        return snapshot(task_id, owner)


def owner_key(client, host, caller):
    # Credential rotations partition even when the browser identity is stable.
    auth = getattr(getattr(client, '_session', None), 'auth', None)
    auth = vars(auth) if hasattr(auth, '__dict__') else auth
    material = repr((client.host, host, caller, getattr(client, 'api_key', None), auth))
    return hashlib.sha256(material.encode()).hexdigest()
