"""Native (in-process) tool-calling loop — the kernel-free twin of
agent_runtime.run_tool_loop, run directly inside the webapp backend.

Same protocol (yields the same chunk/event dicts, same span names, same
iteration ceiling), minus the Dataiku-agent-kernel restrictions — so it can do
what the kernel loop can't:

  • parallel tool execution — a model turn's tool calls run concurrently
    (bounded pool); every tool_call event is emitted up front and each
    tool_result streams out the moment that call finishes, out-of-order.
    History stays deterministic: ToolMessages are appended in call order.
  • heartbeats — while tools run, `{'heartbeat': True}` markers surface every
    few seconds so the SSE layer can keep proxies from killing long turns.
  • cold-start-free and abort-aware — no kernel to spin up or recycle, and a
    closed stream (user hit Stop / navigated away) stops the loop at the next
    yield instead of burning to completion server-side.
  • one retry on a transient model-stream failure, only when the turn has not
    produced any output yet (nothing to duplicate).

Sync by design: the Flask SSE generator drives it directly; the model streams
through LangChain's sync .stream() bridge.
"""

import json
import logging
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from langchain_core.messages import AIMessage, ToolMessage

from .agent_runtime import (MAX_ITERATIONS, _redacted_args, _result_event,
                            _span_begin, _span_end)

logger = logging.getLogger('atk-agents')

# Bounded so a tool-happy turn can't monopolize the backend worker pool the
# tools themselves call back into (self-HTTP): depth is 1, width is this.
MAX_PARALLEL_TOOLS = 3
HEARTBEAT_EVERY_S = 8


def _stream_model_turn(llm_with_tools, messages):
    """Generator over one model turn: yields text-delta chunk dicts, returns
    the summed response via StopIteration.value. Retries ONCE on a failure
    that happens before any output chunk arrived (transient mesh hiccup)."""
    for attempt in (1, 2):
        response = None
        try:
            for piece in llm_with_tools.stream(messages):
                response = piece if response is None else response + piece
                delta = piece.content if isinstance(piece.content, str) else ''
                if delta:
                    yield {'chunk': {'text': delta}}
            return response
        except Exception as exc:
            if response is not None or attempt == 2:
                raise
            logger.warning('model stream failed pre-output (%s: %s) — retrying once',
                           type(exc).__name__, str(exc)[:200])
            time.sleep(1.0)
    return None


def _run_tool(tool_map, call):
    """Execute one tool call; mirrors run_tool_loop's error envelope."""
    name = call.get('name')
    args = call.get('args') or {}
    tool = tool_map.get(name)
    started = time.monotonic()
    if tool is None:
        result = json.dumps({'error': {'code': 'unknown-tool', 'message': 'No tool named %r' % name}})
    else:
        try:
            result = tool.func(**args)
        except Exception as exc:
            result = json.dumps({'error': {'code': 'tool-crash',
                                           'message': '%s: %s' % (type(exc).__name__, str(exc)[:200])}})
    return result, int((time.monotonic() - started) * 1000)


def run_native_loop(llm, tools, messages, trace=None, max_iterations=MAX_ITERATIONS):
    """Sync generator: yields the same dicts as run_tool_loop ({'chunk': ...})
    plus {'heartbeat': True} markers while tools are executing."""
    tool_map = {t.name: t for t in tools}
    llm_with_tools = llm.bind_tools(tools) if tools else llm
    executor = None

    try:
        for iteration in range(max_iterations):
            turn_span = _span_begin(trace, 'llm-turn-%d' % (iteration + 1))
            turn = _stream_model_turn(llm_with_tools, messages)
            response = None
            while True:
                try:
                    yield next(turn)
                except StopIteration as stop:
                    response = stop.value
                    break
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

            # All chips first: the user sees the whole turn's activity appear
            # at once, then each one resolves live as its call completes.
            spans = {}
            for call in tool_calls:
                yield {'chunk': {'type': 'event', 'eventKind': 'tool_call',
                                 'eventData': {'name': call.get('name'), 'args': call.get('args') or {}}}}
                span = _span_begin(trace, 'tool:%s' % call.get('name'))
                if span is not None:
                    try:
                        span.inputs['args'] = _redacted_args(call.get('args') or {})
                        if len(tool_calls) > 1:
                            span.attributes['parallelGroup'] = len(tool_calls)
                    except Exception:
                        pass
                spans[id(call)] = span

            if executor is None:
                executor = ThreadPoolExecutor(max_workers=MAX_PARALLEL_TOOLS,
                                              thread_name_prefix='atk-native-tool')
            futures = {executor.submit(_run_tool, tool_map, call): call for call in tool_calls}
            results = {}
            last_beat = time.monotonic()
            pending = set(futures)
            while pending:
                done, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
                for future in done:
                    call = futures[future]
                    result, duration_ms = future.result()
                    results[id(call)] = result
                    events = _result_event(call.get('name'), result, duration_ms)
                    summary = events[0]['chunk']['eventData']
                    err = summary.get('error')
                    _span_end(spans.get(id(call)), durationMs=duration_ms, ok=summary.get('ok'),
                              error=err.get('message') if isinstance(err, dict)
                              else (str(err) if err else None))
                    for event in events:
                        yield event
                if pending and time.monotonic() - last_beat >= HEARTBEAT_EVERY_S:
                    last_beat = time.monotonic()
                    yield {'heartbeat': True}

            # Model history in call order, whatever order execution finished in.
            for call in tool_calls:
                messages.append(ToolMessage(content=results[id(call)],
                                            tool_call_id=call.get('id') or call.get('name')))

        yield {'chunk': {'text': '\n\n[stopped: tool-call iteration limit reached — '
                                 'narrow the request or ask me to continue]'}}
    finally:
        if executor is not None:
            # Abandon, don't wait: on abort (generator closed) running tools
            # can't be killed, but nothing new starts and this thread returns.
            executor.shutdown(wait=False, cancel_futures=True)
