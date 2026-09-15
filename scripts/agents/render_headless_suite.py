#!/usr/bin/env python3
"""Publish only allowlisted evidence from one fresh Headless suite directory."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from statistics import median

from cobuild_model_suite import GROUPS, PREREQUISITES, inventory
from headless_model_compare import TRANSPORT


def number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def render(source, version):
    cases = inventory()
    attempts = {name: [] for name in cases}
    cleanup = {}
    for group in GROUPS:
        path = source / (group + '.json')
        if not path.exists():
            continue
        for raw in json.loads(path.read_text()).get('rows', []):
            if raw.get('transport') != TRANSPORT:
                raise ValueError('Refusing evidence from another comparison transport')
            name, status = raw.get('capability'), raw.get('status')
            if isinstance(name, str) and name.startswith('_'):
                # Publish no fixture names, targets, errors, credentials or transcripts.
                if status in {'deleted', 'cleanup_failed', 'restore_pending', 'not_deleted'}:
                    cleanup[group] = 'verified' if status == 'deleted' and not raw.get('errors') else 'needs reconciliation'
                continue
            if name not in cases or cases[name] != group:
                raise ValueError('Evidence does not match the 64-case inventory')
            if status not in {'passed', 'failed', 'blocked', 'excluded'}:
                raise ValueError('Unrecognized outcome')
            row = {'status': status}
            if raw.get('phase') in {'fixture-reset', 'reasoning', 'verification'}:
                row['phase'] = raw['phase']
            for key in ('legacy_seconds', 'headless_seconds', 'legacy_model_calls', 'headless_model_calls'):
                if number(raw.get(key)):
                    row[key] = raw[key]
            if row.get('legacy_seconds', 0) > 0 and 'headless_seconds' in row:
                row['headless_time_ratio'] = round(row['headless_seconds'] / row['legacy_seconds'], 3)
            if raw.get('reason') == 'Filtered log results match; live log-window metadata differs.':
                row['note'] = 'Filtered log observations match; only live window metadata differs.'
            elif status == 'passed':
                row['note'] = 'Both tasks completed with matching verified observations.'
            elif group in PREREQUISITES:
                row['note'] = PREREQUISITES[group]
            elif raw.get('reason') == 'Only live log-window metadata changed; filtered log results match.':
                row['note'] = 'Filtered log results matched; strict comparison failed on live window metadata.'
            else:
                row['note'] = 'Fresh run did not satisfy the completion/equivalence criterion; private evidence requires review.'
            attempts[name].append(row)
    rows = []
    for name, group in cases.items():
        history = attempts[name]
        latest = history[-1] if history else {
            'status': 'excluded' if group == 'excluded' else 'blocked',
            'note': PREREQUISITES.get(group, 'No fresh measurement produced.')}
        rows.append({'capability': name, 'group': group, **latest, 'attempts': history})
    pairs = [r for r in rows if r['status'] == 'passed' and 'headless_time_ratio' in r]
    report = {'generated_at': datetime.now(timezone.utc).isoformat(), 'plugin_version_measured': version,
              'transport': TRANSPORT, 'counts': dict(Counter(r['status'] for r in rows)),
              'legacy_model_selection': 'DSS local AI server mainLLMId; application agent overrides were not applied by this comparison harness.',
              'timing_scope': 'One paired trial per passing capability; task wall time including tool execution.',
              'paired_passes_with_timings': len(pairs), 'cleanup': cleanup, 'rows': rows}
    if pairs:
        report['median_paired_time_ratio'] = round(median(r['headless_time_ratio'] for r in pairs), 3)
        report['legacy_median_seconds'] = round(median(r['legacy_seconds'] for r in pairs), 3)
        report['headless_median_seconds'] = round(median(r['headless_seconds'] for r in pairs), 3)
    lines = ['# Fresh Legacy versus Headless validation', '',
             f'Measured plugin: **{version}**. Transport: actual Dataiku Headless MCP.', '',
             ', '.join(f'**{report["counts"].get(s, 0)} {s}**' for s in ('passed', 'failed', 'blocked', 'excluded')) + '.', '',
             'One paired trial per capability with owned, reset fixtures. These results test the comparison adapter; '
             'separate production-loop tests cover human approval and autonomous grants. Historical passes are not imported. '
             'The Legacy harness uses DSS local AI server mainLLMId; application agent model overrides are not applied. '
             'Legacy and Cobuild need not use the same underlying model. '
             'No Cobuild model identity or usage credits are assumed.', '']
    if pairs:
        lines += [f'Across the {len(pairs)} passing pairs, median Headless/Legacy task-time ratio: '
                  f'**{report["median_paired_time_ratio"]:.2f}×**. '
                  f'Legacy median: {report["legacy_median_seconds"]:.2f}s; '
                  f'Headless median: {report["headless_median_seconds"]:.2f}s. '
                  'This mixes different tasks and includes ADTK execution; it is not a model-only latency or reliability benchmark.', '']
    lines += ['| Capability | Outcome | Legacy | Headless | Headless / Legacy |',
              '|---|---|---:|---:|---:|']
    for r in rows:
        fmt = lambda key: f'{r[key]:.2f}s' if key in r else '—'
        ratio = f'{r["headless_time_ratio"]:.2f}×' if 'headless_time_ratio' in r else '—'
        lines.append(f'| {r["capability"]} | {r["status"]} | {fmt("legacy_seconds")} | {fmt("headless_seconds")} | {ratio} |')
    lines += ['', '## Remaining checks', '']
    for r in rows:
        if r['status'] != 'passed':
            lines.append(f'- **{r["capability"]} ({r["status"]}):** {r["note"]}')
    repeated = [r for r in rows if len(r['attempts']) > 1]
    if repeated:
        lines += ['', '## Repeated checks', '']
        for r in repeated:
            lines.append(f'- **{r["capability"]}:** ' + ' → '.join(a['status'] for a in r['attempts'])
                         + '. ' + r['note'] + ' Earlier attempts remain in the JSON evidence.')
    lines += ['', '## Fixture cleanup', '']
    lines.extend(f'- {group}: {status}.' for group, status in cleanup.items())
    lines += ['', 'Only allowlisted aggregate fields are published. Private transcripts, targets, fixture identifiers, '
              'postcondition payloads and raw exceptions remain outside this report.', '']
    return report, '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--version', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    report, markdown = render(args.source, args.version)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    args.output.with_suffix('.md').write_text(markdown)


if __name__ == '__main__':
    main()
