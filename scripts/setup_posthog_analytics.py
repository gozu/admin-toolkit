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
    """Three decision-focused dashboards per audience, sharing saved insights."""
    dashboards = []
    for audience, label in [('customer', 'Customers'), ('internal', 'Internal')]:
        audience_filter = [{'key': 'audience', 'type': 'event', 'operator': 'exact', 'value': [audience]}]
        insights = {}

        def add(name, description, query, window='-90d'):
            insights[name] = {
                'name': f'Admin Toolkit — {label} — {name}',
                'description': description,
                'tags': [TAG, audience],
                'query': {'dateRange': {'date_from': window}, **query, 'properties': audience_filter},
            }

        def trends(name, description, series, breakdown=None, interval='week',
                   window='-90d', display='ActionsLineGraph', compare=False):
            query = {'kind': 'TrendsQuery', 'series': series, 'interval': interval,
                     'trendsFilter': {'display': display, 'showLegend': True}}
            if breakdown:
                query['breakdownFilter'] = {'breakdown': breakdown, 'breakdown_type': 'event',
                                            'breakdown_limit': 50}
            if compare:
                query['compareFilter'] = {'compare': True}
            add(name, description, query, window)

        installations = {**event('adtk_activity', 'hogql'),
                         'math_hogql': 'uniq(properties.installation_id)'}
        trends('Active installations — 30 days',
               'Distinct installations with a webapp open or module visit in the last 30 days, compared with the preceding 30 days. '
               'This is observed reach, not customer companies or the installed base. Opt-outs, offline and older installations are absent.',
               [installations], window='-30d', display='BoldNumber', compare=True)
        trends('Active users — 30 days',
               'Distinct pseudonymous users in the whole 30-day window, compared with the preceding 30 days. '
               'Do not sum weekly unique users. Identity is scoped to each installation; browser fallback can count one person more than once.',
               [event('adtk_activity', 'dau')], window='-30d', display='BoldNumber', compare=True)
        trends('Monthly active installations',
               'Distinct active installations in each calendar month. The current month is incomplete; first observed activity is not an installation date.',
               [installations], interval='month', window='-6m')
        trends('Active users',
               'Distinct users per calendar week opening the app or navigating modules. Automatic scans do not count. Current week is incomplete.',
               [event('adtk_activity', 'dau')])
        trends('Module adoption',
               'Distinct visitors per module over the whole last 30 days. Users can appear in several modules; do not sum rows as total users.',
               [event('adtk_module_opened', 'dau')], 'module_id', window='-30d', display='ActionsTable')
        trends('Plugin versions',
               'Distinct active installations by observed plugin version in the last 30 days. An installation that upgrades appears under both versions; '
               'this is observed version usage, not an inventory of the latest installed version.',
               [installations], 'plugin_version', window='-30d', display='ActionsTable')
        trends('Exports and comparisons',
               'Diagnostic bundles built and handed to the browser, and successfully computed comparisons. Download events do not prove files were saved. '
               'These are optional workflows, not prerequisites for meaningful use.',
               [event('adtk_snapshot_created', 'total'), event('adtk_comparison_completed', 'total')])

        def funnel(name, description, series, aggregate, breakdown):
            add(name, description, {
                'kind': 'FunnelsQuery', 'series': series,
                'breakdownFilter': {'breakdown': breakdown, 'breakdown_type': 'event'},
                'funnelsFilter': {'funnelWindowInterval': 1, 'funnelWindowIntervalUnit': 'hour',
                                  'funnelOrderType': 'ordered', 'funnelAggregateByHogQL': aggregate},
            }, '-30d')

        funnel('Module opened → results viewed',
               'Per visit, including ready-on-open pages. Results means at least one completed data source was displayed, not proof of reading. '
               'Settings, help, and other modules without scan results are not expected to convert; inspect data-bearing modules individually.',
               [event('adtk_module_opened'), event('adtk_results_viewed')], 'properties.visit_id', 'module_id')
        observed_start = {**event('adtk_scan_started'), 'properties': [
            {'key': 'start_observed', 'type': 'event', 'operator': 'exact', 'value': [True]},
        ]}
        funnel('Observed scan completion',
               'Observed starts reaching completion within one hour, matched by scan_id. Excludes starts inferred after completion. '
               'Breakdown separates automatic/manual/on-demand work. Failed/cancelled scans, tab closes, host switches, missing events, and longer scans can all leave a gap.',
               [observed_start, event('adtk_scan_completed')], 'properties.scan_id', 'trigger')
        funnel('Scan started → completed → results viewed',
               'Observed starts, completion, then visible results for the same scan_id within one hour. '
               'Includes background scans; viewing every background result is not expected. Filter trigger to focus on deliberate tasks.',
               [observed_start, event('adtk_scan_completed'), event('adtk_scan_results_viewed')],
               'properties.scan_id', 'scan_key')
        trends('Scan outcomes',
               'Recorded lifecycle starts, completions, failures, and cancellations. These are event volumes, not a completion percentage: '
               'events can cross date boundaries. Use Observed scan completion for a matched-start denominator.',
               [event(name, 'total') for name in ['adtk_scan_started', 'adtk_scan_completed', 'adtk_scan_failed', 'adtk_scan_cancelled']],
               interval='day', window='-30d')
        trends('Slow scans (p95)',
               '95th-percentile duration of successful runs, in milliseconds, by scan source over the last 30 days. '
               'Failed or stalled scans are absent. Read alongside sample counts; small samples are unstable. A scan source can serve several modules.',
               [{**event('adtk_scan_completed', 'p95'), 'math_property': 'duration_ms'}],
               'scan_key', window='-30d', display='ActionsTable')
        trends('Completed scan samples',
               'Successful run counts per scan source for the same 30-day window as the p95 table. Use these counts before prioritizing slow sources.',
               [event('adtk_scan_completed', 'total')], 'scan_key', window='-30d', display='ActionsTable')
        trends('Failures by plugin version',
               'Recorded scan failure counts by version. This is volume, not a failure rate: popular versions naturally have more opportunities to fail. '
               'Filter the completion funnel to the same version before calling a release a regression.',
               [event('adtk_scan_failed', 'total')], 'plugin_version', interval='day', window='-30d')
        trends('Scan trigger mix',
               'Starts by automatic/manual/on_demand/unknown trigger. On-demand means a page requested data, not necessarily a button click. '
               'Use this to interpret background work before judging scan funnel drop-off.',
               [event('adtk_scan_started', 'total')], 'trigger', interval='day', window='-30d')

        for period, intervals, window in [('Week', 9, '-90d'), ('Month', 7, '-12m')]:
            entity = {'id': 'adtk_activity', 'name': 'adtk_activity', 'type': 'events'}
            add(f'{period} retention',
                'First-ever observed activity cohorts returning to open or navigate the app in a later calendar period. '
                'Use cohort counts alongside percentages; exclude incomplete periods. Collection starts at rollout, not original installation. '
                'This is user retention, not customer/company retention.', {
                    'kind': 'RetentionQuery',
                    'retentionFilter': {'period': period, 'totalIntervals': intervals,
                                        'targetEntity': entity, 'returningEntity': entity,
                                        'retentionType': 'retention_first_ever_occurrence',
                                        'retentionReference': 'total', 'cumulative': False,
                                        'timeWindowMode': 'strict_calendar_dates'},
                }, window)

        boards = [
            ('', 'All-hands overview: observed reach, repeat use, and useful workflows.', [
                'Active installations — 30 days', 'Active users — 30 days', 'Monthly active installations',
                'Active users', 'Module adoption', 'Month retention', 'Exports and comparisons',
            ]),
            (' — Adoption', 'Product review: which modules earn use, where visits reach results, and whether users return.', [
                'Module adoption', 'Plugin versions', 'Module opened → results viewed', 'Week retention', 'Month retention',
            ]),
            (' — Reliability', 'Engineering review: scan outcomes, observed completion, latency, and release investigation.', [
                'Scan outcomes', 'Observed scan completion', 'Scan started → completed → results viewed',
                'Slow scans (p95)', 'Completed scan samples', 'Failures by plugin version', 'Scan trigger mix',
            ]),
        ]
        for suffix, purpose, names in boards:
            dashboards.append({
                'name': f'Admin Toolkit — {label}{suffix}', 'tags': [TAG, audience],
                'description': purpose + ' Internal = tam-global/akaos serving installations; all others = customers. '
                               'Only observed installations with analytics enabled, working network access, and an instrumented version are represented. '
                               'Read each insight description for its denominator and limits. Current calendar periods are incomplete.',
                'pinned': audience == 'customer' and not suffix,
                'insights': [insights[name] for name in names],
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
                saved_insight = self.request(f'insights/{match["id"]}/' if match else 'insights/', 'PATCH' if match else 'POST', body)
                # An insight can be shared across boards created in this run.
                # Keep the local snapshot current to avoid duplicate creations
                # or dropping a membership added by the preceding board.
                current = {**body, **saved_insight}
                if match:
                    match.update(current)
                else:
                    existing_insights.append(current)
            print(f'{dashboard["name"]}: {self.origin}/project/{self.base.split("/projects/")[1].split("/")[0]}/dashboard/{dashboard_id}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Create/update dashboards in PostHog')
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/posthog/dashboard-definitions.json')
    args = parser.parse_args()
    dashboard_definitions = definitions()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dashboard_definitions, indent=2) + '\n')
    unique_insights = {i['name'] for d in dashboard_definitions for i in d['insights']}
    print(f'Prepared {len(dashboard_definitions)} dashboards and {len(unique_insights)} shared insights: {args.output}')
    if args.apply:
        config = json.loads((ROOT / 'python-lib/adk_backend/product_analytics_config.json').read_text())
        key = os.environ.get('POSTHOG_PERSONAL_API_KEY')
        for filename in ('.posthog-api.key', '.posthog-api-key'):
            key_file = Path.home() / filename
            if not key and key_file.is_file():
                key = key_file.read_text().strip()
        if not key:
            key = getpass.getpass('PostHog personal API key (hidden): ')
        if not key or key.startswith('phc_'):
            raise SystemExit('A personal API key is required; the public capture token cannot manage dashboards.')
        PostHog(config['project_url'], key).apply(dashboard_definitions)


if __name__ == '__main__':
    main()
