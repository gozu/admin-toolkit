"""Native in-process agent loop (atk_agent_common/native_loop.py): protocol
parity with the kernel loop plus its own powers — parallel tool execution with
live out-of-order results, deterministic history order, heartbeat markers,
retry-once on pre-output stream failures."""

import json
import time
from unittest import mock

import conftest  # noqa: F401
import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

from atk_agent_common import native_loop


def text_chunk(text):
    return AIMessageChunk(content=text)


def tool_call_chunk(calls):
    """One streamed chunk carrying complete tool calls (args as JSON text)."""
    return AIMessageChunk(content='', tool_call_chunks=[
        {'name': name, 'args': json.dumps(args), 'id': call_id, 'index': i, 'type': 'tool_call_chunk'}
        for i, (name, args, call_id) in enumerate(calls)])


class FakeLLM:
    """Duck-typed stand-in: bind_tools returns self, stream replays scripted
    turns. A turn is a list of chunks, or an Exception raised on first pull,
    or ('raise_after', [chunks], exc)."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.streams_started = 0

    def bind_tools(self, tools):
        return self

    def stream(self, messages):
        turn = self.turns.pop(0)
        self.streams_started += 1
        if isinstance(turn, Exception):
            raise turn
        if isinstance(turn, tuple) and turn[0] == 'raise_after':
            for piece in turn[1]:
                yield piece
            raise turn[2]
        for piece in turn:
            yield piece


def make_tool(name, fn):
    tool = StructuredTool.from_function(fn, name=name, description='%s test tool' % name)
    return tool


def run(llm, tools, messages=None, **kwargs):
    messages = messages or [SystemMessage(content='sys'), HumanMessage(content='hi')]
    return list(native_loop.run_native_loop(llm, tools, messages, **kwargs)), messages


def events_of(items, kind):
    return [i['chunk']['eventData'] for i in items
            if i.get('chunk', {}).get('type') == 'event' and i['chunk'].get('eventKind') == kind]


def test_text_only_turn_streams_deltas_and_ends():
    llm = FakeLLM([[text_chunk('Hello '), text_chunk('world')]])
    items, _ = run(llm, [])
    texts = [i['chunk']['text'] for i in items if 'text' in i.get('chunk', {})]
    assert texts == ['Hello ', 'world']
    assert events_of(items, 'tool_call') == []


def test_parallel_tools_chips_first_results_live_history_ordered():
    order = []

    def slow(x: int) -> str:
        time.sleep(0.4)
        order.append('slow')
        return json.dumps({'slow': x})

    def fast(x: int) -> str:
        order.append('fast')
        return json.dumps({'fast': x})

    llm = FakeLLM([
        [text_chunk('checking… '),
         tool_call_chunk([('slow', {'x': 1}, 'c1'), ('fast', {'x': 2}, 'c2')])],
        [text_chunk('done')],
    ])
    items, messages = run(llm, [make_tool('slow', slow), make_tool('fast', fast)])

    kinds = [(i['chunk'].get('eventKind') if i['chunk'].get('type') == 'event' else 'text')
             for i in items if 'chunk' in i]
    # Both chips appear before ANY result lands.
    assert max(i for i, k in enumerate(kinds) if k == 'tool_call') < \
        min(i for i, k in enumerate(kinds) if k == 'tool_result')
    assert kinds.count('tool_call') == 2 and kinds.count('tool_result') == 2
    # fast finished first (parallel execution), and its result streamed first…
    results = events_of(items, 'tool_result')
    assert results[0]['name'] == 'fast' and results[1]['name'] == 'slow'
    assert order == ['fast', 'slow']
    # …but the model history keeps call order (slow first), by tool_call_id.
    tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in tool_messages] == ['c1', 'c2']
    assert json.loads(tool_messages[0].content) == {'slow': 1}


def test_unknown_tool_yields_error_envelope():
    llm = FakeLLM([
        [tool_call_chunk([('nope', {}, 'c1')])],
        [text_chunk('recovered')],
    ])
    items, messages = run(llm, [])
    result = events_of(items, 'tool_result')[0]
    assert result['ok'] is False and result['error']['code'] == 'unknown-tool'
    assert 'unknown-tool' in [m for m in messages if isinstance(m, ToolMessage)][0].content


def test_plan_tool_result_emits_plan_card_event():
    def plan_admin_action(action: str) -> str:
        return json.dumps({'action': action, 'confirm_token': 'tok-1', 'plan': {}})

    llm = FakeLLM([
        [tool_call_chunk([('plan_admin_action', {'action': 'log-cleanup'}, 'c1')])],
        [text_chunk('planned')],
    ])
    items, _ = run(llm, [make_tool('plan_admin_action', plan_admin_action)])
    plans = events_of(items, 'plan')
    assert len(plans) == 1 and plans[0]['confirm_token'] == 'tok-1'


def test_iteration_limit_stops_with_notice():
    def loop_tool() -> str:
        return '{}'

    llm = FakeLLM([[tool_call_chunk([('loop_tool', {}, 'c%d' % i)])] for i in range(5)])
    items, _ = run(llm, [make_tool('loop_tool', loop_tool)], max_iterations=2)
    final_text = ''.join(i['chunk'].get('text', '') for i in items if 'chunk' in i)
    assert 'iteration limit' in final_text
    assert len(events_of(items, 'tool_call')) == 2


def test_pre_output_stream_failure_retries_once():
    llm = FakeLLM([RuntimeError('mesh hiccup'), [text_chunk('second try')]])
    with mock.patch.object(native_loop.time, 'sleep'):
        items, _ = run(llm, [])
    assert ''.join(i['chunk'].get('text', '') for i in items) == 'second try'
    assert llm.streams_started == 2


def test_mid_stream_failure_after_output_propagates():
    llm = FakeLLM([('raise_after', [text_chunk('partial ')], RuntimeError('died mid-stream'))])
    gen = native_loop.run_native_loop(llm, [], [HumanMessage(content='hi')])
    assert next(gen)['chunk']['text'] == 'partial '
    with pytest.raises(RuntimeError, match='died mid-stream'):
        list(gen)


def test_heartbeats_surface_while_tools_run(monkeypatch):
    monkeypatch.setattr(native_loop, 'HEARTBEAT_EVERY_S', 0)

    def sleepy() -> str:
        time.sleep(1.3)
        return '{}'

    llm = FakeLLM([
        [tool_call_chunk([('sleepy', {}, 'c1')])],
        [text_chunk('done')],
    ])
    items, _ = run(llm, [make_tool('sleepy', sleepy)])
    assert any(i.get('heartbeat') for i in items)
