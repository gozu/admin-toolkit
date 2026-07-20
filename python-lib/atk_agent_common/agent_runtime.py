"""Shared agent loop for the plugin agents (health-triage / scoping-architect /
ops-actuator): a hand-rolled LangChain tool-calling loop over DKUChatModel.

Hand-rolled rather than AgentExecutor so the loop is deterministic and
version-stable: bind tools → model → execute tool_calls → ToolMessages →
repeat (max_iterations) → stream the final text. Written for frontier
tool-calling models (parallel tool calls supported); no downgrade paths.
"""

import json
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from . import prompt_overrides
from .errors import ToolkitError

MAX_ITERATIONS = 12

# Tool names whose (JSON) results are surfaced to the UI as dedicated events,
# so a frontend can render plan/approval/checklist cards instead of parsing prose.
_PLAN_TOOL = 'plan_admin_action'
_EXECUTE_TOOL = 'execute_admin_action'
_ACTION_ITEMS_TOOL = 'propose_action_items'


def build_llm(llm_id):
    from dataiku.langchain.dku_llm import DKUChatModel
    return DKUChatModel(llm_id=llm_id)


def resolve_llm_id(client, config):
    """The LLM id an agent must use, by precedence: Agent Tuning live
    override > per-agent llm_id > plugin default_llm_id.

    Deliberately NOT validated against the LLM catalog: an override naming a
    deleted id fails loudly at call time — silently falling back would mask
    the admin's explicit model choice."""
    llm_id = (prompt_overrides.llm_override(client)
              or (config or {}).get('llm_id') or '').strip() \
        or (client.settings.get('default_llm_id') or '').strip()
    if not llm_id:
        raise ToolkitError(
            'No LLM configured.',
            remediation='Pick a model on the Agent Tuning page, or set llm_id on the '
                        'agent / default_llm_id in the plugin settings.')
    return llm_id


def messages_from_query(query, system_prompt):
    """DSS completion query → LangChain messages, system prompt first."""
    out = [SystemMessage(content=system_prompt)]
    for msg in (query or {}).get('messages', []):
        role = msg.get('role')
        content = msg.get('content') or ''
        if role == 'user':
            out.append(HumanMessage(content=content))
        elif role == 'assistant':
            out.append(AIMessage(content=content))
        elif role == 'system':
            out.append(SystemMessage(content=content))
    return out


def _result_event(name, result, duration_ms, call_id=None):
    """tool_result event (+ plan/execution card events for the actuator tools).

    Results are tool-function return values: JSON strings for the actuator
    tools, str(dict) or JSON for the sensor tools. Parse defensively — the
    UI events are best-effort sugar, never load-bearing for the loop.

    call_id (the model's tool_call id) correlates this result with its
    tool_call event so the frontend can settle the right chip when several
    calls share a name; omitted (older callers) the frontend falls back to
    name matching."""
    parsed = None
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except ValueError:
            parsed = None
    elif isinstance(result, dict):
        parsed = result
    error = (parsed or {}).get('error') if isinstance(parsed, dict) else None
    events = [{'name': name, 'durationMs': duration_ms, 'ok': error is None,
               'error': error or None}]
    if call_id:
        events[0]['id'] = call_id
    out = [{'chunk': {'type': 'event', 'eventKind': 'tool_result', 'eventData': events[0]}}]
    if isinstance(parsed, dict):
        if name == _PLAN_TOOL and parsed.get('confirm_token'):
            out.append({'chunk': {'type': 'event', 'eventKind': 'plan', 'eventData': parsed}})
        elif name == _EXECUTE_TOOL and 'status' in parsed:
            out.append({'chunk': {'type': 'event', 'eventKind': 'execution', 'eventData': parsed}})
        elif name == _ACTION_ITEMS_TOOL and isinstance(parsed.get('items'), list):
            out.append({'chunk': {'type': 'event', 'eventKind': 'action_items', 'eventData': parsed}})
    return out


# ── DSS trace spans (best-effort sugar: a tracing failure must NEVER break
# the loop, so every span touch is wrapped and None-safe) ────────────────────


def _now_ms():
    return int(time.time() * 1000)


def _span_begin(trace, name):
    if trace is None:
        return None
    try:
        span = trace.subspan(name)
        span.begin(_now_ms())
        return span
    except Exception:
        return None


def _span_end(span, **attrs):
    if span is None:
        return
    try:
        for key, value in attrs.items():
            if value is not None:
                span.attributes[key] = value
        span.end(_now_ms())
    except Exception:
        pass


def _redacted_args(args):
    try:
        return {k: ('<redacted>' if k == 'confirm_token' else v) for k, v in (args or {}).items()}
    except Exception:
        return None


async def run_tool_loop(llm, tools, messages, trace=None):
    """Async generator: yields DSS LLM chunks; text streams as token deltas
    (DKUChatModel._stream bridged via astream), tool activity as events.

    When DSS hands us a trace SpanBuilder, each model turn becomes an
    `llm-turn-N` span and each tool execution a `tool:<name>` span (args with
    confirm_token redacted), so the run is inspectable in the Mesh trace
    explorer."""
    tool_map = {t.name: t for t in tools}
    llm_with_tools = llm.bind_tools(tools) if tools else llm

    for iteration in range(MAX_ITERATIONS):
        # Stream the model turn: yield text deltas as they arrive, sum the
        # AIMessageChunks so tool_calls reassemble from tool_call_chunks.
        turn_span = _span_begin(trace, 'llm-turn-%d' % (iteration + 1))
        response = None
        async for piece in llm_with_tools.astream(messages):
            response = piece if response is None else response + piece
            delta = piece.content if isinstance(piece.content, str) else ''
            if delta:
                yield {'chunk': {'text': delta}}
        if response is None:
            _span_end(turn_span, outcome='no-response')
            return
        tool_calls = getattr(response, 'tool_calls', None) or []
        text = response.content if isinstance(response.content, str) else ''
        _span_end(turn_span, toolCalls=len(tool_calls) or None, textChars=len(text) or None,
                  outcome='tool-calls' if tool_calls else 'final-answer')
        if not tool_calls:
            return
        messages.append(AIMessage(content=text, tool_calls=tool_calls))
        for call in tool_calls:
            name = call.get('name')
            args = call.get('args') or {}
            yield {'chunk': {'type': 'event', 'eventKind': 'tool_call',
                             'eventData': {'name': name, 'args': args,
                                           'id': call.get('id')}}}
            tool = tool_map.get(name)
            tool_span = _span_begin(trace, 'tool:%s' % name)
            if tool_span is not None:
                try:
                    tool_span.inputs['args'] = _redacted_args(args)
                except Exception:
                    pass
            started = time.monotonic()
            if tool is None:
                result = json.dumps({'error': {'code': 'unknown-tool', 'message': 'No tool named %r' % name}})
            else:
                try:
                    result = tool.func(**args)
                except Exception as exc:
                    result = json.dumps({'error': {'code': 'tool-crash',
                                                   'message': '%s: %s' % (type(exc).__name__, str(exc)[:200])}})
            events = _result_event(name, result, int((time.monotonic() - started) * 1000),
                                   call_id=call.get('id'))
            summary = events[0]['chunk']['eventData']
            err = summary.get('error')
            _span_end(tool_span, durationMs=summary.get('durationMs'), ok=summary.get('ok'),
                      error=err.get('message') if isinstance(err, dict) else (str(err) if err else None))
            for event in events:
                yield event
            messages.append(ToolMessage(content=result, tool_call_id=call.get('id') or name))

    yield {'chunk': {'text': '\n\n[stopped: tool-call iteration limit reached — '
                             'narrow the request or ask me to continue]'}}
