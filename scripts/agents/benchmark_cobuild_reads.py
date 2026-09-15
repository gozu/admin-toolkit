#!/usr/bin/env python3
"""Alternating live read trials; no gate/provider changes or mutations.

Run after deploying the compact-read bridge. Private output includes observed
tool data; publish only the aggregate summary. Failed samples remain failures.
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import time
import urllib.parse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'python-lib'))
from atk_agent_common import capability_routing as routing, config, tools_impl
from atk_agent_common.client import ToolkitClient
from atk_agent_common.read_interpretation import evidence

OPERATIONS = [('toolkit_get', {'endpoint': 'version'}),
              ('list_hosts', {'probe': False}), ('list_capabilities', {})]


def legacy(client, name, arguments):
    fn = getattr(tools_impl, name)
    # Match the original wrapper's default-expanded arguments exactly.
    import inspect
    bound = inspect.signature(fn).bind(client, **arguments)
    bound.apply_defaults()
    args = {k: v for k, v in bound.arguments.items() if k != 'client'}
    with routing.override('cobuild'):
        route = routing.request_operation(client, name, 'read', args)
    with routing.override('existing'):
        result = fn(client, **arguments)
    return routing.finish_operation(client, result, route)


def stats(values):
    return {'median': round(statistics.median(values), 3),
            'min': round(min(values), 3), 'max': round(max(values), 3)} if values else None


def summarize(rows):
    result = {}
    for name, _ in OPERATIONS:
        result[name] = {}
        for variant in ('existing', 'bridge', 'compact'):
            samples = [r for r in rows if r['capability'] == name and r['variant'] == variant]
            successful = [r for r in samples if r['status'] == 'completed']
            result[name][variant] = {
                'samples': len(samples), 'successes': len(successful),
                'failures': len(samples) - len(successful),
                'data': stats([r['data_seconds'] for r in samples if 'data_seconds' in r]),
                'complete': stats([r['total_seconds'] for r in successful]),
                'full_output_matches': sum(r.get('full_output_match') is True for r in samples),
                'fact_matches': sum(r.get('facts_match') is True for r in samples),
                'failure_categories': [r.get('error', 'unknown') for r in samples if r['status'] != 'completed'],
            }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=int, default=10)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    import dataikuapi
    dss = dataikuapi.DSSClient((ROOT / '.dss-url').read_text().strip(),
                               (ROOT / '.dss-api-key').read_text().strip())
    settings = dss.get_plugin('admin-toolkit').get_settings().get_raw().get('config', {})
    client = ToolkitClient(config.resolve(settings))
    parts = urllib.parse.urlsplit(client.base_url).path.strip('/').split('/')
    i = parts.index('web-apps-backends')
    backend = dss.get_project(parts[i + 1]).get_webapp(parts[i + 2]).get_backend_client()
    client.base_url = backend.base_url.rstrip('/')
    client.session.auth = backend.session.auth
    rows = []
    baseline = {}
    for sample in range(args.samples):
        for name, arguments in OPERATIONS:
            variants = ['existing', 'bridge', 'compact']
            # Rotate order across repetitions; never clear shared caches.
            offset = sample % 3
            for variant in variants[offset:] + variants[:offset]:
                row = {'capability': name, 'variant': variant, 'sample': sample + 1,
                       'status': 'failed', 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
                started = time.monotonic()
                try:
                    if variant == 'bridge':
                        output = legacy(client, name, arguments)
                    else:
                        with routing.override('existing' if variant == 'existing' else 'cobuild'):
                            output = getattr(tools_impl, name)(client, **arguments)
                    row['data_seconds'] = round(time.monotonic() - started, 3)
                    route = output.get('executionRoute', {})
                    raw = {k: v for k, v in output.items() if k != 'executionRoute'}
                    row['output'] = raw
                    if variant == 'existing':
                        baseline[name] = raw
                    if name in baseline:
                        row['full_output_match'] = raw == baseline[name]
                        row['facts_match'] = evidence(name, raw) == evidence(name, baseline[name])
                    if variant == 'compact':
                        if route.get('variant') != 'compact-read':
                            raise ValueError('compact route not installed')
                        job = route['interpretation']
                        while job['status'] == 'pending':
                            if time.monotonic() - started > 240:
                                raise ValueError('interpretation timed out')
                            job = client.post('/api/agents/cobuild-read-status', json={
                                'turnId': route['interpretation']['turnId'],
                                'viewTicket': route['interpretation']['viewTicket']})
                            if job['status'] == 'pending':
                                time.sleep(1)
                        row['interpretation'] = job
                        if job['status'] != 'completed':
                            raise ValueError(job.get('message', 'interpretation unavailable'))
                    elif variant == 'bridge':
                        row['interpretation'] = route
                        if not route.get('resultAcknowledged'):
                            raise ValueError('bridge explanation failed')
                    row['status'] = 'completed'
                except Exception as exc:
                    # No SDK error text, URLs or auth in the public aggregate.
                    row['error'] = str(exc)[:100] if isinstance(exc, ValueError) else type(exc).__name__
                row['total_seconds'] = round(time.monotonic() - started, 3)
                rows.append(row)
                (args.output_dir / 'samples.json').write_text(json.dumps(rows, indent=2))
                (args.output_dir / 'summary.json').write_text(json.dumps(summarize(rows), indent=2))
                print(name, variant, sample + 1, row['status'], row['total_seconds'], flush=True)


if __name__ == '__main__':
    main()
