#!/usr/bin/env python3
"""Publish an allowlisted follow-up report without copying private trial text."""
import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from headless_model_compare import TRANSPORT

CASES = ('cluster-start', 'cluster-stop', 'cluster-pods-cleanup', 'k8s-apply-fix', 'image-delete', 'python-run')


def render(source, images=None):
    state = json.loads((source / 'state.json').read_text())
    rows = {name: {'capability': name, 'status': 'blocked', 'trials': {}} for name in CASES}
    version = None
    for entry in state['runs']:
        if entry.get('status') != 'finished' or not entry.get('result'):
            continue
        raw = json.loads((source / entry['result']).read_text())
        if entry['operation'] == 'preflight':
            version = raw.get('mode', {}).get('runningVersion')
        if entry['operation'] == 'trial' and 'trial_private' in raw:
            name, provider, trial = entry['capability'], entry['provider'], raw['trial_private']
            verified = trial.get('verified') is True
            if name == 'cluster-start':
                ready = source / ('ready-' + provider + '.json')
                verified = ready.exists() and json.loads(ready.read_text()) == {
                    'nodes': 1, 'instance_type': 't3.small', 'ready': True}
            elif name == 'cluster-stop':
                later = state['runs'][state['runs'].index(entry) + 1:]
                verified = False
                for item in later:
                    if item['operation'] == 'trial':
                        break
                    if item['operation'] == 'cloud' and item.get('result'):
                        evidence = json.loads((source / item['result']).read_text())
                        if evidence.get('cloud_absent') is True:
                            verified = True
                            break
            mode = 'legacy' if provider == 'existing' else 'headless'
            rows[name]['trials'][mode] = {
                'status': 'passed' if trial['status'] == 'completed' and verified else 'failed',
                'task_seconds': trial['total_seconds'],
                'model_turns': len(trial['model_calls']),
                'model_seconds': round(sum(c['seconds'] for c in trial['model_calls']), 3),
                'tool_seconds': round(sum(t['seconds'] for t in trial['tools']), 3),
                'execution_attempted': trial['execution_attempted'],
                'postcondition_verified': verified}
        if entry['operation'] == 'kubernetes':
            for row in raw.get('rows', []):
                if row.get('capability') in rows:
                    if row.get('transport') != TRANSPORT:
                        raise ValueError('Foreign comparison transport')
                    target = rows[row['capability']]
                    target['status'] = row['status']
                    for key in ('legacy_seconds', 'headless_seconds'):
                        if key in row:
                            target[key] = row[key]
    if images:
        raw = json.loads(images.read_text())
        for row in raw:
            if row.get('capability') == 'image-delete':
                if row.get('transport') != TRANSPORT:
                    raise ValueError('Foreign image comparison transport')
                target = rows['image-delete']
                target['status'] = row['status']
                for key in ('legacy_seconds', 'headless_seconds'):
                    if key in row:
                        target[key] = row[key]
    for name, row in rows.items():
        trials = row['trials']
        if trials:
            statuses = [t['status'] for t in trials.values()]
            row['status'] = 'failed' if 'failed' in statuses else 'passed' if len(trials) == 2 else 'blocked'
            for mode, trial in trials.items():
                row[mode + '_seconds'] = trial['task_seconds']
        if 'legacy_seconds' in row and 'headless_seconds' in row and row['legacy_seconds'] > 0:
            row['headless_time_ratio'] = round(row['headless_seconds'] / row['legacy_seconds'], 3)
    cleanup = {'cluster_definition_deleted': state.get('cluster_deleted') is True,
               'comparison_plugin_and_environment_deleted': state.get('plugin_deleted') is True}
    report = {'generated_at': datetime.now(timezone.utc).isoformat(), 'plugin_version_measured': version,
              'transport': TRANSPORT, 'counts': dict(Counter(r['status'] for r in rows.values())),
              'cleanup': cleanup, 'rows': list(rows.values())}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--images', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(render(args.source, args.images), indent=2) + '\n')


if __name__ == '__main__':
    main()
