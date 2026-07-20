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

def _scripted_native_turn(agent_id, messages, user=None):
    yield 'chunk', {'text': 'hello '}
    yield 'agent_event', {'eventKind': 'tool_call', 'eventData': {'name': 'instance_health', 'args': {}}}
    yield 'ping', {}
    yield 'final', {'finishReason': 'stop', 'durationMs': 42, 'trace': {'type': 'span', 'name': 't'},
                    'llmTurns': 2, 'toolsRun': 3,
                    'usage': {'promptTokens': 100, 'completionTokens': 20,
                              'totalTokens': 120, 'estimatedCost': 0.01}}


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
    # Turn stats ride the done event when the native generator provides them.
    assert done['llmTurns'] == 2 and done['toolsRun'] == 3
    assert done['usage']['totalTokens'] == 120


def test_chat_done_omits_stats_when_generator_has_none():
    def bare_turn(agent_id, messages, user=None):
        yield 'final', {'finishReason': 'stop', 'durationMs': 5, 'trace': None}

    with mock.patch.object(agent_native, 'runtime_mode', return_value='native'), \
            mock.patch.object(agent_native, 'stream_native_turn', side_effect=bare_turn), \
            mock.patch.object(backend, '_resolve_client', return_value=_NoAgentsClient()):
        resp = backend.app.test_client().post(
            '/api/agents/chat',
            json={'agentId': 'a1', 'messages': [{'role': 'user', 'content': 'hi'}]})
    done = json.loads(resp.get_data(as_text=True).split('event: done\ndata: ')[1].split('\n')[0])
    assert 'llmTurns' not in done and 'toolsRun' not in done and 'usage' not in done


def test_chat_native_turn_gets_clipped_history():
    captured = {}

    def capture_turn(agent_id, messages, user=None):
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


# ── setup-bundle cache ───────────────────────────────────────────────────────

class _BundleBuilders:
    """Patches every per-turn assembly step with counters."""

    def __init__(self):
        self.builds = 0

    def __enter__(self):
        def counted_config():
            self.builds += 1
            return {'k': 1}

        self._patches = [
            mock.patch.object(agent_native, '_get_plugin_config', side_effect=counted_config),
            mock.patch.object(agent_native.atk_config, 'resolve', return_value={'s': 1}),
            mock.patch.object(agent_native, 'build_client', return_value=object()),
            mock.patch.object(agent_native, 'agent_instance_config_local', return_value=None),
            mock.patch.object(agent_native.agent_runtime, 'resolve_llm_id', return_value='llm:x'),
            mock.patch.object(agent_native.agent_runtime, 'build_llm', return_value=object()),
            mock.patch.object(agent_native, '_behavior_for', return_value={'b': 1}),
            mock.patch.object(agent_native.generalist, 'build_toolset', return_value=[]),
            mock.patch.object(agent_native.generalist, 'build_system_prompt', return_value='sys'),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


def test_setup_bundle_caches_within_ttl_and_clears():
    agent_native.clear_bundle_cache()
    try:
        with _BundleBuilders() as builders:
            first = agent_native._setup_bundle('a1')
            assert agent_native._setup_bundle('a1') is first  # cache hit
            assert builders.builds == 1
            agent_native._setup_bundle('a2')  # keyed per agent
            assert builders.builds == 2
            agent_native.clear_bundle_cache()
            assert agent_native._setup_bundle('a1') is not first
            assert builders.builds == 3
    finally:
        agent_native.clear_bundle_cache()


def test_setup_bundle_ttl_expires(monkeypatch):
    agent_native.clear_bundle_cache()
    monkeypatch.setattr(agent_native, '_BUNDLE_TTL_S', 0.0)
    try:
        with _BundleBuilders() as builders:
            agent_native._setup_bundle('a1')
            agent_native._setup_bundle('a1')
            assert builders.builds == 2  # deadline already passed → rebuild
    finally:
        agent_native.clear_bundle_cache()


def test_setup_bundle_failures_are_not_cached():
    from atk_agent_common.errors import ToolkitError
    agent_native.clear_bundle_cache()
    try:
        with _BundleBuilders() as builders:
            with mock.patch.object(agent_native.agent_runtime, 'resolve_llm_id',
                                   side_effect=ToolkitError('No LLM configured.')):
                for _ in range(2):
                    try:
                        agent_native._setup_bundle('a1')
                        assert False, 'expected ToolkitError'
                    except ToolkitError:
                        pass
            assert builders.builds == 2  # each attempt rebuilt — nothing cached
            agent_native._setup_bundle('a1')  # recovers once the LLM resolves
            assert builders.builds == 3
    finally:
        agent_native.clear_bundle_cache()


def test_agents_settings_update_clears_bundle_cache(monkeypatch):
    from adk_backend.routes import settings as settings_routes
    monkeypatch.setattr(backend, '_verify_red_token', lambda token: True)

    class _FakePluginSettings:
        def __init__(self):
            self.raw = {'config': {}}

        def get_raw(self):
            return self.raw

        def save(self):
            pass

    fake_settings = _FakePluginSettings()
    fake_client = mock.Mock()
    fake_client.get_plugin.return_value.get_settings.return_value = fake_settings
    with mock.patch.object(settings_routes, '_local_thread_client', return_value=fake_client), \
            mock.patch.object(agent_native, 'clear_bundle_cache') as clear:
        resp = backend.app.test_client().post(
            '/api/settings/agents/update',
            json={'values': {'agent_runtime': 'dataiku'}})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert fake_settings.raw['config']['agent_runtime'] == 'dataiku'
    assert clear.called


# ── agent-id verification: fail closed, virtual only when verified empty ─────

class _FakeAgent:
    def __init__(self, raw=None, settings_error=None):
        self._raw = raw or {}
        self._settings_error = settings_error

    def get_settings(self):
        if self._settings_error:
            raise self._settings_error

        class _S:
            def get_raw(_self):
                return self._raw
        return _S()


class _FakeProject:
    def __init__(self, agents=None, list_error=None, agent_raw=None, settings_error=None):
        self._agents = agents
        self._list_error = list_error
        self._agent_raw = agent_raw
        self._settings_error = settings_error

    def list_agents(self):
        if self._list_error:
            raise self._list_error
        return self._agents

    def get_agent(self, agent_id):
        return _FakeAgent(self._agent_raw, self._settings_error)


class _FakeDSSClient:
    def __init__(self, project, project_keys=('ADMINTOOLKIT',)):
        self._project = project
        self._project_keys = list(project_keys)

    def get_project(self, key):
        return self._project

    def list_project_keys(self):
        return self._project_keys


def _with_local_client(client):
    import dataiku
    return mock.patch.object(dataiku, 'api_client', return_value=client)


def test_unknown_agent_id_is_rejected_not_promoted_to_virtual():
    from atk_agent_common.errors import ToolkitError
    client = _FakeDSSClient(_FakeProject(agents=[{'id': 'real1'}]))
    with _with_local_client(client):
        try:
            agent_native.agent_instance_config_local('bogus-id')
            assert False, 'expected ToolkitError'
        except ToolkitError as exc:
            assert 'bogus-id' in exc.message or 'No agent instance' in exc.message


def test_virtual_id_rejected_when_real_agents_exist():
    from atk_agent_common.errors import ToolkitError
    client = _FakeDSSClient(_FakeProject(agents=[{'id': 'real1'}]))
    with _with_local_client(client):
        try:
            agent_native.agent_instance_config_local(agent_native.VIRTUAL_AGENT_ID)
            assert False, 'expected ToolkitError'
        except ToolkitError:
            pass


def test_virtual_id_allowed_when_project_has_zero_agents():
    client = _FakeDSSClient(_FakeProject(agents=[]))
    with _with_local_client(client):
        assert agent_native.agent_instance_config_local(agent_native.VIRTUAL_AGENT_ID) is None


def test_virtual_id_allowed_when_project_verifiably_absent():
    client = _FakeDSSClient(_FakeProject(list_error=RuntimeError('no such project')),
                            project_keys=('OTHER',))
    with _with_local_client(client):
        assert agent_native.agent_instance_config_local(agent_native.VIRTUAL_AGENT_ID) is None


def test_lookup_failure_fails_closed_never_virtual():
    from atk_agent_common.errors import ToolkitError
    # Project exists (in the key list) but listing agents errors: a transient
    # failure must raise, not fall through to the master-switch virtual agent.
    client = _FakeDSSClient(_FakeProject(list_error=RuntimeError('boom')))
    with _with_local_client(client):
        for agent_id in ('real1', agent_native.VIRTUAL_AGENT_ID):
            try:
                agent_native.agent_instance_config_local(agent_id)
                assert False, 'expected ToolkitError'
            except ToolkitError as exc:
                assert 'agent' in exc.message.lower()


def test_listed_agent_with_unreadable_settings_fails_closed():
    from atk_agent_common.errors import ToolkitError
    client = _FakeDSSClient(_FakeProject(agents=[{'id': 'real1'}],
                                         settings_error=RuntimeError('read denied')))
    with _with_local_client(client):
        try:
            agent_native.agent_instance_config_local('real1')
            assert False, 'expected ToolkitError'
        except ToolkitError as exc:
            assert 'agent' in exc.message.lower()


def test_listed_agent_returns_its_plugin_config():
    raw = {'activeVersion': 'v2',
           'versions': [{'versionId': 'v1', 'pluginAgentConfig': {'old': True}},
                        {'versionId': 'v2', 'pluginAgentConfig': {'allow_red_actions': True}}]}
    client = _FakeDSSClient(_FakeProject(agents=[{'id': 'real1'}], agent_raw=raw))
    with _with_local_client(client):
        config = agent_native.agent_instance_config_local('real1')
    assert config == {'allow_red_actions': True}


def test_bundle_cache_is_size_bounded():
    agent_native.clear_bundle_cache()
    try:
        with _BundleBuilders():
            for i in range(agent_native._BUNDLE_MAX * 2):
                agent_native._setup_bundle('agent-%d' % i)
            assert len(agent_native._bundle_cache) <= agent_native._BUNDLE_MAX
    finally:
        agent_native.clear_bundle_cache()


def test_chat_route_rejects_overlong_agent_id():
    resp = backend.app.test_client().post(
        '/api/agents/chat',
        json={'agentId': 'x' * 65, 'messages': [{'role': 'user', 'content': 'hi'}]})
    assert resp.status_code == 400
