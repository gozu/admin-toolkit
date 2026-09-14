import json
from pathlib import Path
import runpy

from adk_backend.product_analytics import EVENTS

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = runpy.run_path(str(ROOT / 'scripts/setup_posthog_analytics.py'))


def test_dashboards_separate_audiences_and_retention_excludes_background_work():
    dashboards = SCRIPT['definitions']()
    assert len(dashboards) == 6
    assert dashboards == json.loads((ROOT / 'docs/posthog/dashboard-definitions.json').read_text())
    for dashboard, audience in zip(dashboards, ['customer'] * 3 + ['internal'] * 3):
        assert len(dashboard['insights']) in {5, 7}
        for insight in dashboard['insights']:
            query = insight['query']
            assert query['properties'] == [{'key': 'audience', 'type': 'event', 'operator': 'exact', 'value': [audience]}]
            for series in query.get('series', []):
                assert series['event'] in EVENTS
            if query['kind'] == 'RetentionQuery':
                retention = query['retentionFilter']
                assert retention['targetEntity']['id'] == retention['returningEntity']['id'] == 'adtk_activity'
                assert retention['period'] in {'Week', 'Month'}
                assert retention['retentionType'] == 'retention_first_ever_occurrence'
            if query['kind'] == 'FunnelsQuery':
                assert query['funnelsFilter']['funnelAggregateByHogQL'] in {'properties.visit_id', 'properties.scan_id'}
                if query['funnelsFilter']['funnelAggregateByHogQL'] == 'properties.scan_id':
                    assert query['series'][0]['properties'] == [
                        {'key': 'start_observed', 'type': 'event', 'operator': 'exact', 'value': [True]},
                    ]


def test_overview_measures_reach_without_counting_background_events():
    overview = SCRIPT['definitions']()[0]
    assert overview['pinned']
    assert not any(board['pinned'] for board in SCRIPT['definitions']()[1:])
    installations, users = overview['insights'][:2]
    assert installations['query']['series'] == [{
        'kind': 'EventsNode', 'event': 'adtk_activity', 'math': 'hogql',
        'math_hogql': 'uniq(properties.installation_id)',
    }]
    assert users['query']['series'][0]['event'] == 'adtk_activity'
    assert users['query']['series'][0]['math'] == 'dau'
    for insight in [installations, users]:
        assert insight['query']['dateRange']['date_from'] == '-30d'
        assert insight['query']['trendsFilter']['display'] == 'BoldNumber'
        assert insight['query']['compareFilter']['compare'] is True


def test_provision_updates_only_owned_reports_and_preserves_other_memberships():
    PostHog = SCRIPT['PostHog']
    definitions = SCRIPT['definitions']()[:1]
    dashboard = definitions[0]
    calls = []
    class Fake(PostHog):
        def all(self, resource):
            if resource == 'dashboards/':
                return [{'id': 12, 'name': dashboard['name'], 'tags': dashboard['tags']}]
            return [{'id': 34, 'name': dashboard['insights'][0]['name'],
                     'tags': dashboard['tags'], 'dashboards': [12, 99]}]
        def request(self, path, method='GET', payload=None):
            calls.append((path, method, payload))
            return {'id': 12}
    Fake('https://us.posthog.com/project/609487', 'test-only').apply(definitions)
    assert calls[0][0:2] == ('dashboards/12/', 'PATCH')
    assert calls[1][0:2] == ('insights/34/', 'PATCH')
    assert calls[1][2]['dashboards'] == [12, 99]
    assert all(call[1] == 'POST' for call in calls[2:])


def test_provision_is_idempotent_and_shared_insights_keep_all_memberships():
    from copy import deepcopy
    PostHog = SCRIPT['PostHog']
    boards = SCRIPT['definitions']()

    class Fake(PostHog):
        def __init__(self):
            super().__init__('https://us.posthog.com/project/609487', 'test-only')
            # Same-named user-owned records must remain untouched.
            self.rows = {
                'dashboards': [{'id': 1000, 'name': boards[0]['name'], 'tags': []}],
                'insights': [{'id': 1001, 'name': boards[0]['insights'][0]['name'], 'tags': []}],
            }
            self.sequence = 1

        def all(self, resource):
            return deepcopy(self.rows[resource.rstrip('/')])

        def request(self, path, method='GET', payload=None):
            parts = path.strip('/').split('/')
            records = self.rows[parts[0]]
            if method == 'POST':
                record = {'id': self.sequence, **deepcopy(payload)}
                self.sequence += 1
                records.append(record)
            else:
                assert method == 'PATCH'
                record = next(r for r in records if r['id'] == int(parts[1]))
                record.update(deepcopy(payload))
            return deepcopy(record)

    client = Fake()
    client.apply(boards)
    assert len(client.rows['dashboards']) == 7  # six managed + one user-owned
    assert len(client.rows['insights']) == 35  # 34 unique managed + one user-owned
    shared = next(i for i in client.rows['insights'] if i['name'].endswith('Customers — Module adoption'))
    assert len(shared['dashboards']) == 2
    shared['dashboards'].append(999)  # Membership in an unrelated user's board.
    before = deepcopy(client.rows)
    client.apply(boards)
    assert client.rows == before
    assert client.rows['dashboards'][0] == {'id': 1000, 'name': boards[0]['name'], 'tags': []}
    assert client.rows['insights'][0] == {'id': 1001, 'name': boards[0]['insights'][0]['name'], 'tags': []}
