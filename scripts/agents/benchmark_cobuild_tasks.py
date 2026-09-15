#!/usr/bin/env python3
"""Ten read-only task benchmarks, alternating order without changing permissions.

Private samples retain observed data; public summary/CSV contain only timings,
status, and equality checks. Timings start at tool dispatch, not chat submission.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time
import urllib.parse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'python-lib'))
from atk_agent_common import capability_routing as routing, config, read_tasks, sse, tools_impl
from atk_agent_common.client import ToolkitClient
from atk_agent_common.read_interpretation import canonical
from benchmark_cobuild_reads import legacy


def op(name, **arguments):
    return {'name': name, 'arguments': arguments}


VERSION = op('toolkit_get', endpoint='version')
HOSTS = op('list_hosts', probe=False)
PLUGINS = op('config_inspect', domain='plugins', top_n=10)
CONNECTIONS = op('config_inspect', domain='connections', top_n=10)
PROJECTS = op('config_inspect', domain='projects', top_n=10)
K8S = op('k8s_health', top_n=10)
TASKS = [
    ('Version', [VERSION]), ('Hosts', [HOSTS]),
    ('Capabilities', [op('list_capabilities')]),
    ('System health', [op('instance_health', sections=['system'], top_n=10, include_score=False)]),
    ('Connections', [CONNECTIONS]), ('Plugins', [PLUGINS]), ('Projects', [PROJECTS]),
    ('Kubernetes reachability', [K8S]),
    ('Installation overview', [VERSION, HOSTS, PLUGINS]),
    ('Estate inventory', [CONNECTIONS, PROJECTS, K8S]),
]


def get_client():
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
    return client


def wait_interpretation(client, route):
    job = route['interpretation']
    if job['status'] != 'pending':
        return job
    path = '/api/agents/cobuild-read-stream'
    response = client._do('POST', path, host=route.get('host'), json={
        'turnId': job['turnId'], 'viewTicket': job['viewTicket']}, timeout=250, stream=True)
    try:
        client._raise_for_status(response, path, route.get('host'))
        _, result = sse.read_final_event(response, done_events=('done',))
        return result or {'status': 'failed'}
    finally:
        response.close()


def stats(values):
    return dict(median=round(statistics.median(values), 3), min=round(min(values), 3),
                max=round(max(values), 3)) if values else None


def failure_category(row):
    message = row.get('interpretation', {}).get('message')
    safe_codes = {'interpretation/' + c for c in (
        'provider-error', 'interaction-required', 'empty-response', 'malformed-json',
        'non-object', 'invalid-summary', 'mismatch/type', 'mismatch/request_id',
        'mismatch/evidence_sha256', 'mismatch/facts')}
    return message if isinstance(message, str) and message in safe_codes else row.get('error', 'unknown')


def publish(rows, output, samples, version):
    # Compare every variant to the Existing output in the same repetition,
    # regardless of the alternating execution order.
    baselines = {(r['task'], r['sample']): r.get('outputs') for r in rows if r['variant'] == 'existing'}
    for row in rows:
        baseline = baselines.get((row['task'], row['sample']))
        if baseline is not None and 'outputs' in row:
            row['full_output_match'] = row['outputs'] == baseline
    public_keys = ('task', 'sample', 'variant', 'status', 'data_seconds', 'total_seconds',
                   'full_output_match', 'evidence_match', 'conversation_seconds', 'send_seconds',
                   'server_data_seconds', 'error')
    summary = {'version': version, 'repetitions': samples, 'tasks': {}}
    for task, _ in TASKS:
        summary['tasks'][task] = {}
        for variant in ('existing', 'bridge', 'optimized'):
            group = [r for r in rows if r['task'] == task and r['variant'] == variant]
            good = [r for r in group if r['status'] == 'completed']
            summary['tasks'][task][variant] = dict(
                samples=len(group), successes=len(good), failures=len(group)-len(good),
                data=stats([r['data_seconds'] for r in group if r.get('data_ok')]),
                complete=stats([r['total_seconds'] for r in good]),
                conversation=stats([r['conversation_seconds'] for r in good if 'conversation_seconds' in r]),
                send=stats([r['send_seconds'] for r in good if 'send_seconds' in r]),
                full_output_matches=sum(r.get('full_output_match') is True for r in group),
                evidence_matches=sum(r.get('evidence_match') is True for r in group),
                errors=[failure_category(r) for r in group if r['status'] != 'completed'])
    (output / 'samples-private.json').write_text(json.dumps(rows, indent=2))
    (output / 'summary.json').write_text(json.dumps(summary, indent=2))
    with (output / 'timings.csv').open('w') as handle:
        writer = csv.DictWriter(handle, fieldnames=public_keys, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=int, default=3)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    client = get_client()
    with routing.override('existing'):
        version = tools_impl.toolkit_get(client, endpoint='version').get('runningVersion')
    rows = []
    for sample in range(1, args.samples + 1):
        for task, operations in TASKS:
            variants = ['existing', 'bridge', 'optimized']
            offset = (sample - 1) % 3
            for variant in variants[offset:] + variants[:offset]:
                row = dict(task=task, variant=variant, sample=sample, status='failed')
                started = time.monotonic()
                try:
                    if variant == 'optimized':
                        output = client.read_task(operations)
                        route = output['executionRoute']
                        raw = [r['data'] for r in output['results']]
                        row['server_data_seconds'] = route['dataSeconds']
                    else:
                        raw, routes = [], []
                        for operation in operations:
                            name, arguments = operation['name'], operation['arguments']
                            if variant == 'bridge':
                                result = legacy(client, name, arguments)
                            else:
                                with routing.override('existing'):
                                    result = getattr(tools_impl, name)(client, **arguments)
                            routes.append(result.get('executionRoute', {}))
                            raw.append({k: v for k, v in result.items() if k != 'executionRoute'})
                    row['data_seconds'] = round(time.monotonic() - started, 3)
                    row['outputs'] = raw
                    row['data_ok'] = all(not r.get('error') for r in raw)
                    if not row['data_ok']:
                        raise ValueError('read-error')
                    if variant == 'optimized':
                        job = wait_interpretation(client, route)
                        row['interpretation'] = job
                        if job.get('status') != 'completed':
                            raise ValueError('interpretation-' + job.get('status', 'missing'))
                        facts = {'reads': [dict(name=o['name'], facts=read_tasks.facts(o['name'], o['arguments'], r))
                                            for o, r in zip(operations, raw)]}
                        row['evidence_match'] = (job.get('facts') == facts and job.get('evidenceSha256') ==
                                                hashlib.sha256(canonical(facts).encode()).hexdigest())
                        if not row['evidence_match']:
                            raise ValueError('evidence-mismatch')
                        row['conversation_seconds'] = job['timings']['conversationSeconds']
                        row['send_seconds'] = job['timings']['sendSeconds']
                    elif variant == 'bridge' and not all(r.get('resultAcknowledged') for r in routes):
                        raise ValueError('bridge-explanation-failed')
                    row['status'] = 'completed'
                except Exception as exc:
                    # Deliberately omit exception messages: SDK errors can contain credentials.
                    row['error'] = type(exc).__name__
                row['total_seconds'] = round(time.monotonic() - started, 3)
                rows.append(row)
                publish(rows, args.output_dir, args.samples, version)
                print(task, variant, sample, row['status'], row.get('data_seconds'), row['total_seconds'], flush=True)


if __name__ == '__main__':
    main()
