"""Story provisioning — create vs repair, idempotency, reporter verification."""
import pytest

from adk_backend.story import provision


# ── Fakes: a project whose scenarios persist settings like DSS does ──

class FakeSettings:
    def __init__(self, store):
        self._store = store
        self.raw_triggers = list(store['triggers'])
        self.raw_steps = list(store['steps'])
        self.raw_reporters = list(store['reporters'])
        self._raw = dict(store['raw'])

    def get_raw(self):
        return self._raw

    def add_daily_trigger(self, hour=2, minute=0, timezone='SERVER', **kwargs):
        self.raw_triggers.append({
            'type': 'temporal', 'active': True,
            'params': {'frequency': 'Daily', 'hour': hour, 'minute': minute,
                       'timezone': timezone},
        })

    def save(self):
        self._store['triggers'] = list(self.raw_triggers)
        self._store['steps'] = list(self.raw_steps)
        self._store['reporters'] = [self._store.get('mangler', lambda r: r)(r)
                                    for r in self.raw_reporters]
        self._store['raw'] = dict(self._raw)


class FakeScenario:
    def __init__(self, scenario_id, name, mangler=None):
        self.id = scenario_id
        self.store = {'triggers': [], 'steps': [], 'reporters': [],
                      'raw': {'name': name}}
        if mangler:
            self.store['mangler'] = mangler

    def get_settings(self):
        return FakeSettings(self.store)


class FakeProject:
    def __init__(self, mangler=None):
        self.scenarios = {}
        self._mangler = mangler
        self._next_id = 0

    def list_scenarios(self):
        return [{'id': sid, 'name': s.store['raw']['name']}
                for sid, s in self.scenarios.items()]

    def get_scenario(self, scenario_id):
        return self.scenarios[scenario_id]

    def create_scenario(self, scenario_name, type='step_based'):
        assert type == 'step_based'
        self._next_id += 1
        sid = 'scn%d' % self._next_id
        scenario = FakeScenario(sid, scenario_name, mangler=self._mangler)
        self.scenarios[sid] = scenario
        return scenario

    def get_summary(self):
        return {}


class FakeClient:
    def __init__(self, project=None):
        self.project = project or FakeProject()

    def get_project(self, key):
        assert key == provision.MACRO_PROJECT_KEY
        return self.project

    def create_project(self, key, name, owner):
        raise AssertionError('project already exists in these tests')


class _Cfg:
    connection_name = 'story-pg'
    mail_channel = ''
    alert_email = 'alex.kaos@dataiku.com'
    collect_hour = 2
    run_as_user = ''
    audit_lookback_days = 14
    inventory_items_retention_days = 30


@pytest.fixture(autouse=True)
def _stub_mail_channels(monkeypatch):
    monkeypatch.setattr(provision, 'resolve_mail_channel',
                        lambda client, preferred='': 'mail-channel-1')


class TestEnsureStoryScenario:
    def test_create_path(self):
        project = FakeProject()
        result = provision.ensure_story_scenario(project, _Cfg(), 'mail-channel-1')
        assert result['status'] == 'created'
        scenario = result['scenario']
        assert len(project.scenarios) == 1
        assert len(scenario.store['steps']) == 1
        assert len(scenario.store['triggers']) == 1
        assert len(scenario.store['reporters']) == 1
        step = scenario.store['steps'][0]
        assert step['params']['runnableType'] == provision.MACRO_TYPE
        assert step['params']['proceedOnFailure'] is False
        assert step['maxRetriesOnFail'] == 0
        assert scenario.store['triggers'][0]['params']['hour'] == 2
        assert scenario.store['raw']['active'] is True
        assert 'runAsUser' not in scenario.store['raw']

    def test_repair_path_converges_to_single_everything(self):
        project = FakeProject()
        provision.ensure_story_scenario(project, _Cfg(), 'mail-channel-1')
        # sabotage: duplicate steps + reporters, wrong trigger hour
        scenario = list(project.scenarios.values())[0]
        scenario.store['steps'].append({'type': 'junk'})
        scenario.store['reporters'].append({'phase': 'JUNK'})
        result = provision.ensure_story_scenario(project, _Cfg(), 'mail-channel-1')
        assert result['status'] == 'repaired'
        assert len(project.scenarios) == 1  # no second scenario
        scenario = result['scenario']
        assert len(scenario.store['steps']) == 1
        assert len(scenario.store['reporters']) == 1
        assert len(scenario.store['triggers']) == 1

    def test_run_as_user_only_when_set(self):
        project = FakeProject()
        cfg = _Cfg()
        cfg.run_as_user = 'svc-story'
        result = provision.ensure_story_scenario(project, cfg, 'mail-channel-1')
        assert result['scenario'].store['raw']['runAsUser'] == 'svc-story'
        cfg.run_as_user = ''
        result = provision.ensure_story_scenario(project, cfg, 'mail-channel-1')
        assert 'runAsUser' not in result['scenario'].store['raw']


class TestReporterShapes:
    def test_primary_shape(self):
        reporter = provision.build_reporter('ch1', 'alex.kaos@dataiku.com')
        assert reporter['phase'] == 'END_OF_RUN'
        assert reporter['runConditionType'] == 'CUSTOM'
        assert "outcome != 'SUCCESS'" in reporter['runConditionExpression']
        config = reporter['messaging']['configuration']
        assert config['channelId'] == 'ch1'
        assert config['recipient'] == 'alex.kaos@dataiku.com'

    def test_fallback_shape(self):
        reporter = provision.build_reporter_fallback('ch1', 'alex.kaos@dataiku.com')
        assert reporter['phase'] == 'END_OF_RUN'
        assert reporter['runConditionStatuses'] == ['FAILED', 'ABORTED']

    def test_verify_accepts_both_shapes(self):
        for builder in (provision.build_reporter, provision.build_reporter_fallback):
            assert provision._reporter_matches(
                builder('ch1', 'alex.kaos@dataiku.com'), 'alex.kaos@dataiku.com')

    def test_verify_rejects_wrong_recipient_phase_or_condition(self):
        good = provision.build_reporter('ch1', 'alex.kaos@dataiku.com')
        assert not provision._reporter_matches(good, 'other@dataiku.com')
        bad_phase = dict(good, phase='BEFORE_RUN')
        assert not provision._reporter_matches(bad_phase, 'alex.kaos@dataiku.com')
        no_condition = dict(good, runConditionExpression='', runConditionStatuses=[])
        assert not provision._reporter_matches(no_condition, 'alex.kaos@dataiku.com')
        assert not provision._reporter_matches(None, 'alex.kaos@dataiku.com')


class TestProvisionAll:
    def test_happy_path_primary_shape(self):
        client = FakeClient()
        result = provision.provision_all(client, _Cfg())
        assert result['ok'] is True
        assert result['reporterVerified'] is True
        assert result['reporterShape'] == 'primary'
        statuses = {s['step']: s['status'] for s in result['steps']}
        assert statuses['config'] == 'ok'
        assert statuses['project:ADMINTOOLKIT'] == 'already_exists'
        assert statuses['scenario:%s' % provision.SCENARIO_NAME] == 'created'
        assert statuses['reporter'] == 'verified'

    def test_reprovision_is_idempotent(self):
        client = FakeClient()
        provision.provision_all(client, _Cfg())
        result = provision.provision_all(client, _Cfg())
        assert result['ok'] is True
        statuses = {s['step']: s['status'] for s in result['steps']}
        assert statuses['scenario:%s' % provision.SCENARIO_NAME] == 'repaired'
        assert len(client.project.scenarios) == 1
        scenario = list(client.project.scenarios.values())[0]
        assert len(scenario.store['reporters']) == 1

    def test_fallback_when_primary_shape_mangled_on_save(self):
        # This DSS version strips the CUSTOM expression on save.
        def mangler(reporter):
            if reporter.get('runConditionType') == 'CUSTOM':
                reporter = dict(reporter, runConditionExpression='')
            return reporter

        client = FakeClient(project=FakeProject(mangler=mangler))
        result = provision.provision_all(client, _Cfg())
        assert result['reporterShape'] == 'fallback'
        assert result['reporterVerified'] is True
        scenario = list(client.project.scenarios.values())[0]
        assert scenario.store['reporters'][0]['runConditionStatuses'] == ['FAILED', 'ABORTED']

    def test_unconfigured_story_fails_fast(self):
        cfg = _Cfg()
        cfg.connection_name = None
        result = provision.provision_all(FakeClient(), cfg)
        assert result['ok'] is False
        assert result['steps'][0]['step'] == 'config'
        assert result['steps'][0]['status'] == 'error'

    def test_missing_mail_channel_is_an_error_step(self, monkeypatch):
        def _raise(client, preferred=''):
            raise provision.MailChannelMissing('no channels')
        monkeypatch.setattr(provision, 'resolve_mail_channel', _raise)
        result = provision.provision_all(FakeClient(), _Cfg())
        assert result['ok'] is False
        statuses = {s['step']: s['status'] for s in result['steps']}
        assert statuses['mail-channel'] == 'error'
