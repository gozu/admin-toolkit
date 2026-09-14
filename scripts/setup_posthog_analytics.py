#!/usr/bin/env python3
"""Render or idempotently provision the Admin Toolkit PostHog dashboards.

Dry run (default): writes reviewable definitions, no account credentials needed.
Apply: uses POSTHOG_PERSONAL_API_KEY or a hidden prompt, never writes the key.
Public capture tokens cannot create dashboards or read analytics.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
TAG = 'admin-toolkit-product-analytics-v1'


def event(name, math=None):
    node = {'kind': 'EventsNode', 'event': name}
    if math:
        node['math'] = math
    return node


def definitions():
    dashboards = []
    for audience, label in [('customer', 'Customers'), ('internal', 'Internal')]:
        properties = [{'key': 'audience', 'type': 'event', 'operator': 'exact', 'value': [audience]}]
        common = {'properties': properties, 'dateRange': {'date_from': '-90d'}}
        insights = []

        def add(name, description, query):
            insights.append({'name': f'Admin Toolkit — {label} — {name}', 'description': description,
                             'tags': [TAG, audience], 'query': {**common, **query}})

        def trends(name, description, series, breakdown=None, interval='week'):
            query = {'kind': 'TrendsQuery', 'series': series, 'interval': interval}
            if breakdown:
                query['breakdownFilter'] = {'breakdown': breakdown, 'breakdown_type': 'event'}
            add(name, description, query)

        trends('Active users', 'Distinct pseudonymous users opening the app or navigating modules. Automatic scans do not count.',
               [event('adtk_activity', 'dau')])
        trends('Module adoption', 'Distinct users visiting each module. Shared underlying scans do not inflate adoption.',
               [event('adtk_module_opened', 'dau')], 'module_id')
        trends('Plugin versions', 'Active users by the serving Toolkit version, independent of the selected managed host.',
               [event('adtk_activity', 'dau')], 'plugin_version')
        trends('Scan outcomes', 'Underlying lifecycle runs, counted once across modules sharing a scan. Includes automatic work; filter trigger for manual/on-demand runs.',
               [event(name, 'total') for name in ['adtk_scan_started', 'adtk_scan_completed', 'adtk_scan_failed', 'adtk_scan_cancelled']])
        trends('Slow scans (p95)', '95th-percentile successful scan duration in milliseconds, by scan source.',
               [{**event('adtk_scan_completed', 'p95'), 'math_property': 'duration_ms'}], 'scan_key')
        trends('Exports and comparisons', 'Diagnostic snapshots built and handed to the browser download, and successfully computed comparisons. Does not prove a file was saved to disk.',
               [event('adtk_snapshot_created', 'total'), event('adtk_comparison_completed', 'total')])
        add('Module opened → results viewed',
            'Per visit, including ready-on-open modules. Results means a visible page with at least one completed data source, not proof of reading every row. Breakdown is module_id.', {
                'kind': 'FunnelsQuery',
                'series': [event('adtk_module_opened'), event('adtk_results_viewed')],
                'breakdownFilter': {'breakdown': 'module_id', 'breakdown_type': 'event'},
                'funnelsFilter': {'funnelWindowInterval': 1, 'funnelWindowIntervalUnit': 'hour',
                                  'funnelOrderType': 'ordered', 'funnelAggregateByHogQL': 'properties.visit_id'},
            })
        add('Scan started → completed → results viewed',
            'Per scan_id, so a later retry cannot complete an earlier failed attempt. Includes background scans; inspecting every background scan is not expected. Use trigger to focus on manual/on-demand runs.', {
                'kind': 'FunnelsQuery',
                'series': [event(name) for name in ['adtk_scan_started', 'adtk_scan_completed', 'adtk_scan_results_viewed']],
                'breakdownFilter': {'breakdown': 'scan_key', 'breakdown_type': 'event'},
                'funnelsFilter': {'funnelWindowInterval': 1, 'funnelWindowIntervalUnit': 'hour',
                                  'funnelOrderType': 'ordered', 'funnelAggregateByHogQL': 'properties.scan_id'},
            })
        for period, intervals, window in [('Week', 9, '-90d'), ('Month', 7, '-12m')]:
            entity = {'id': 'adtk_activity', 'name': 'adtk_activity', 'type': 'events'}
            add(f'{period} retention',
                'First-ever observed activity cohorts returning to open or navigate the app. Weekly/monthly calendar periods; exclude incomplete periods when reporting. Collection begins at rollout, not original installation.', {
                    'kind': 'RetentionQuery', 'dateRange': {'date_from': window},
                    'retentionFilter': {'period': period, 'totalIntervals': intervals,
                                        'targetEntity': entity, 'returningEntity': entity,
                                        'retentionType': 'retention_first_ever_occurrence',
                                        'retentionReference': 'total', 'cumulative': False,
                                        'timeWindowMode': 'strict_calendar_dates'},
                })
        dashboards.append({
            'name': f'Admin Toolkit — {label}', 'tags': [TAG, audience],
            'description': 'Product usage, task funnels, and weekly/monthly retention. '
                           'Internal = tam-global/akaos serving installations. All others = customers. '
                           'Pseudonymous DSS users where available; browser fallback otherwise. '
                           'Only installations with analytics enabled and working network access are represented.',
            'pinned': audience == 'customer', 'insights': insights,
        })
    return dashboards


class PostHog:
    def __init__(self, project_url, key):
        parsed = urlsplit(project_url)
        if parsed.scheme != 'https' or parsed.hostname not in {'us.posthog.com', 'eu.posthog.com'}:
            raise ValueError('Expected an HTTPS PostHog Cloud project URL')
        project = parsed.path.strip('/').split('/')
        if len(project) < 2 or project[0] != 'project' or not project[1].isdigit():
            raise ValueError('Expected /project/<numeric ID>')
        self.origin = f'https://{parsed.hostname}'
        self.base = self.origin + f'/api/projects/{project[1]}/'
        self.key = key

    def request(self, path, method='GET', payload=None):
        url = path if path.startswith('https://') else self.base + path
        # Do not forward authorization across hosts via pagination.
        if not url.startswith(self.base):
            raise ValueError('Unexpected PostHog pagination URL')
        req = Request(url, method=method, headers={'Authorization': 'Bearer ' + self.key,
                                                'Content-Type': 'application/json'},
                      data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with urlopen(req, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            raise RuntimeError(f'PostHog {method} failed: HTTP {exc.code}. Check project access and dashboard/insight scopes.') from None

    def all(self, resource):
        rows, path = [], resource + '?limit=100'
        while path:
            data = self.request(path)
            rows.extend(data.get('results', []))
            path = data.get('next')
        return rows

    def apply(self, dashboards):
        existing_dashboards = self.all('dashboards/')
        existing_insights = self.all('insights/')
        for dashboard in dashboards:
            match = next((d for d in existing_dashboards if d['name'] == dashboard['name'] and TAG in (d.get('tags') or []) and not d.get('deleted')), None)
            body = {k: v for k, v in dashboard.items() if k != 'insights'}
            saved = self.request(f'dashboards/{match["id"]}/' if match else 'dashboards/', 'PATCH' if match else 'POST', body)
            dashboard_id = saved['id']
            for insight in dashboard['insights']:
                match = next((i for i in existing_insights if i.get('name') == insight['name'] and TAG in (i.get('tags') or []) and not i.get('deleted')), None)
                memberships = list(dict.fromkeys([*(match.get('dashboards', []) if match else []), dashboard_id]))
                body = {**insight, 'dashboards': memberships}
                self.request(f'insights/{match["id"]}/' if match else 'insights/', 'PATCH' if match else 'POST', body)
            print(f'{dashboard["name"]}: {self.origin}/project/{self.base.split("/projects/")[1].split("/")[0]}/dashboard/{dashboard_id}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Create/update dashboards in PostHog')
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/posthog/dashboard-definitions.json')
    args = parser.parse_args()
    dashboard_definitions = definitions()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dashboard_definitions, indent=2) + '\n')
    print(f'Prepared {len(dashboard_definitions)} dashboards and 20 insights: {args.output}')
    if args.apply:
        config = json.loads((ROOT / 'python-lib/adk_backend/product_analytics_config.json').read_text())
        key_file = Path.home() / '.posthog-api-key'
        key = os.environ.get('POSTHOG_PERSONAL_API_KEY')
        if not key and key_file.is_file():
            key = key_file.read_text().strip()
        if not key:
            key = getpass.getpass('PostHog personal API key (hidden): ')
        if not key or key.startswith('phc_'):
            raise SystemExit('A personal API key is required; the public capture token cannot manage dashboards.')
        PostHog(config['project_url'], key).apply(dashboard_definitions)


if __name__ == '__main__':
    main()
