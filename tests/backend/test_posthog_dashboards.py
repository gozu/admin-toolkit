import json
from pathlib import Path
import runpy

from adk_backend.product_analytics import EVENTS

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = runpy.run_path(str(ROOT / 'scripts/setup_posthog_analytics.py'))


def test_dashboards_separate_audiences_and_retention_excludes_background_work():
    dashboards = SCRIPT['definitions']()
    assert len(dashboards) == 2
    assert dashboards == json.loads((ROOT / 'docs/posthog/dashboard-definitions.json').read_text())
    for dashboard, audience in zip(dashboards, ['customer', 'internal']):
        assert len(dashboard['insights']) == 10
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
