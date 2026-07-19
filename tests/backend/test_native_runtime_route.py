"""Native agent runtime wiring: the agent_runtime knob, the /api/agents/chat
runtime branch, the virtual-agent row, and the generalist shared assembly the
kernel and native runtimes both consume."""

import json
from unittest import mock

import conftest  # noqa: F401

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'webapps', 'admin-toolkit'))

import backend  # noqa: E402
from adk_backend import agent_native  # noqa: E402
from atk_agent_common import generalist  # noqa: E402


# ── runtime_mode ─────────────────────────────────────────────────────────────

def test_runtime_mode_defaults_to_native():
    assert agent_native.runtime_mode({}) == 'native'
    assert agent_native.runtime_mode({'agent_runtime': ''}) == 'native'
    assert agent_native.runtime_mode({'agent_runtime': 'bogus'}) == 'native'


def test_runtime_mode_reads_knob():
    assert agent_native.runtime_mode({'agent_runtime': 'dataiku'}) == 'dataiku'
    assert agent_native.runtime_mode({'agent_runtime': ' Native '}) == 'native'


# ── chat route branching ─────────────────────────────────────────────────────

def _scripted_native_turn(agent_id, messages):
    yield 'chunk', {'text': 'hello '}
    yield 'agent_event', {'eventKind': 'tool_call', 'eventData': {'name': 'instance_health', 'args': {}}}
    yield 'ping', {}
    yield 'final', {'finishReason': 'stop', 'durationMs': 42, 'trace': {'type': 'span', 'name': 't'}}


class _NoAgentsClient:
    def get_project(self, key):
        raise RuntimeError('no ADMINTOOLKIT here')


def test_chat_local_native_streams_native_turn():
    with mock.patch.object(agent_native, 'runtime_mode', return_value='native'), \
            mock.patch.object(agent_native, 'stream_native_turn',
                              side_effect=_scripted_native_turn) as native_turn, \
            mock.patch.object(backend, '_resolve_client', return_value=_NoAgentsClient()):
        resp = backend.app.test_client().post(
            '/api/agents/chat',
            json={'agentId': 'a1', 'messages': [{'role': 'user', 'content': 'hi'}]})
    body = resp.get_data(as_text=True)
    assert native_turn.called
    assert 'event: chunk' in body and '"hello "' in body
    assert 'event: agent_event' in body
    assert 'event: ping' in body
    assert 'event: done' in body
    done = json.loads(body.split('event: done\ndata: ')[1].split('\n')[0])
    assert done['runtime'] == 'native'
    assert done['traceAvailable'] is True and done['traceId']
    assert done['durationMs'] == 42


def test_chat_native_turn_gets_clipped_history():
    captured = {}

    def capture_turn(agent_id, messages):
        captured['agent_id'] = agent_id
        captured['messages'] = messages
        yield 'final', {'finishReason': 'stop', 'durationMs': 1, 'trace': None}

    long_text = 'x' * 30000
    with mock.patch.object(agent_native, 'runtime_mode', return_value='native'), \
            mock.patch.object(agent_native, 'stream_native_turn', side_effect=capture_turn), \
            mock.patch.object(backend, '_resolve_client', return_value=_NoAgentsClient()):
        backend.app.test_client().post(
            '/api/agents/chat',
            json={'agentId': 'a1',
                  'messages': [{'role': 'system', 'content': 'drop me'},
                               {'role': 'user', 'content': long_text}]})
    assert captured['agent_id'] == 'a1'
    assert [m['role'] for m in captured['messages']] == ['user']
    assert len(captured['messages'][0]['content']) == 20000


def test_chat_remote_host_keeps_kernel_relay():
    with mock.patch.object(agent_native, 'runtime_mode', return_value='native'), \
            mock.patch.object(agent_native, 'stream_native_turn') as native_turn, \
            mock.patch.object(backend, '_resolve_client', return_value=_NoAgentsClient()), \
            mock.patch('adk_backend.clients._remote_host_config', return_value={}):
        resp = backend.app.test_client().post(
            '/api/agents/chat',
            headers={'X-DSS-Host-Id': 'tam-global'},
            json={'agentId': 'a1', 'messages': [{'role': 'user', 'content': 'hi'}]})
    assert not native_turn.called
    assert 'event: error' in resp.get_data(as_text=True)  # fake client can't relay


def test_chat_runtime_override_forces_kernel_relay():
    with mock.patch.object(agent_native, 'runtime_mode', return_value='native'), \
            mock.patch.object(agent_native, 'stream_native_turn') as native_turn, \
            mock.patch.object(backend, '_resolve_client', return_value=_NoAgentsClient()):
        resp = backend.app.test_client().post(
            '/api/agents/chat',
            json={'agentId': 'a1', 'runtime': 'dataiku',
                  'messages': [{'role': 'user', 'content': 'hi'}]})
    assert not native_turn.called
    assert 'event: error' in resp.get_data(as_text=True)


# ── agents list: virtual generalist ──────────────────────────────────────────

def test_agents_list_serves_virtual_row_when_unprovisioned_native():
    with mock.patch.object(agent_native, 'runtime_mode', return_value='native'), \
            mock.patch.object(backend, '_resolve_client', return_value=_NoAgentsClient()):
        resp = backend.app.test_client().get('/api/agents')
    data = resp.get_json()
    assert data['available'] is True and data['runtime'] == 'native'
    assert data['agents'][0]['id'] == agent_native.VIRTUAL_AGENT_ID


def test_agents_list_keeps_empty_state_on_dataiku_runtime():
    with mock.patch.object(agent_native, 'runtime_mode', return_value='dataiku'), \
            mock.patch.object(backend, '_resolve_client', return_value=_NoAgentsClient()):
        resp = backend.app.test_client().get('/api/agents')
    data = resp.get_json()
    assert data['available'] is False and data['agents'] == []


# ── generalist shared assembly (used by BOTH runtimes) ───────────────────────

class FakeToolkitClient:
    """Duck-typed ToolkitClient: settings snapshot + canned GET responses."""

    def __init__(self):
        self.settings = {'agent_action_gates': {}}

    def get(self, path, **kwargs):
        if path == '/api/agents/action-settings':
            return {'gates': {}}
        if path == '/api/agents/tuning/prompts':
            return {'values': {}, 'settings': {}}
        raise AssertionError('unexpected GET %s' % path)


def test_generalist_toolset_and_prompt_cover_the_protocol():
    client = FakeToolkitClient()
    behavior = generalist.agent_behavior({'allow_red_actions': True})
    tools = generalist.build_toolset(client, behavior, 'openai:x:gpt')
    names = {t.name for t in tools}
    assert {'triage_sweep', 'propose_action_items',
            'plan_admin_action', 'execute_admin_action'} <= names
    assert 'instance_health' in names  # sensors present (default-enabled)
    prompt = generalist.build_system_prompt(client, behavior, tools)
    for slot in ('{max_recommendations}', '{remediation_map}', '{severity_rubric}',
                 '{action_safety_rubric}', '{allowed_actions}', '{action_items_addendum}',
                 '{sensor_manifest}'):
        assert slot not in prompt


def test_generalist_execute_gate_refuses_when_disabled():
    client = FakeToolkitClient()
    behavior = generalist.agent_behavior({})  # allow_red_actions defaults False
    tools = {t.name: t for t in generalist.build_toolset(client, behavior, 'llm')}
    result = json.loads(tools['execute_admin_action'].func(
        action='log-cleanup', target={}, confirm=True, confirm_token='t'))
    assert result['error']['code'] == 'agent-execution-disabled'


def test_virtual_agent_behavior_uses_master_switch():
    assert agent_native._behavior_for(None, {'enable_red_actions': True})['allow_execute'] is True
    assert agent_native._behavior_for(None, {'enable_red_actions': False})['allow_execute'] is False
    # A real instance keeps kernel-identical semantics.
    assert agent_native._behavior_for({'allow_red_actions': True}, {})['allow_execute'] is True
