#!/usr/bin/env python3
"""Publish timing aggregates only; never load the private observed tool data."""
import argparse
import html
import json
from pathlib import Path
import shutil


def seconds(group, metric='data'):
    value = group.get(metric)
    return value['median'] if value else None


def number(value):
    return '—' if value is None else f'{value:.2f}s'


def render(summary):
    lines = [
        '# Cobuild: ten read-task performance measurements', '',
        f"Measured 2026-09-15 on internal DEV (akaos), running Admin Toolkit {summary['version']}.", '',
        f"{summary['repetitions']} repetitions per task and route. Table values are medians in seconds. "
        'The CSV preserves every sample; the JSON includes minimum and maximum values.', '',
        '| Task | Existing data | Original Cobuild completion | Optimized data | Optimized completion | Data / Existing | Completion / Existing | Completion speedup |',
        '|---|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for name, groups in summary['tasks'].items():
        base, old = seconds(groups['existing']), seconds(groups['bridge'], 'complete')
        data, complete = seconds(groups['optimized']), seconds(groups['optimized'], 'complete')
        ratio = f'{data / base:.2f}×' if base and data is not None else '—'
        speedup = f'{old / complete:.2f}×' if complete and old is not None else '—'
        complete_ratio = f'{complete / base:.2f}×' if base and complete is not None else '—'
        lines.append(f'| {name} | {number(base)} | {number(old)} | {number(data)} | {number(complete)} | {ratio} | {complete_ratio} | {speedup} |')
    lines += ['', '## What was measured', '',
        '- Existing: deterministic ADTK tools, without Cobuild explanation. Original bridge: an operation-request turn, read execution, and result-acknowledgment turn for every read.',
        '- Optimized: one backend task request checks permissions, dispatches existing read handlers, returns their data, and queues one Cobuild interpretation. A separate SSE connection receives the finished explanation.',
        '- Data time ends when the calling client receives the full deterministic output. Completion time includes the explanation. Both clocks start at tool dispatch, so these are not end-to-end natural-language chat timings.',
        '- Installation overview combines version, hosts, and plugins. Estate inventory combines connections, projects, and Kubernetes reachability. Existing and original bridge execute these reads sequentially; the task API executes up to three concurrently.',
        '- The combined tasks explicitly exercise the batch API. Ordinary eligible single sensor calls use the fused endpoint automatically; the outer chat planner has not been changed to combine separate tool calls.',
        '- Routes rotate order on each repetition. Shared caches are not cleared. Capability inventory may use a 30-second permission cache on Existing; the optimized route reloads permissions each time. This is a small, warm-instance sample, not a load test or a tail-latency estimate.',
        '- The client runs from the development workstation against DEV. Network and proxy latency are included. Cobuild send-message timing includes server orchestration and response handling, not just model generation.',
        '- All tasks are read-only. No mutations, permission changes, or provider-setting changes are part of the benchmark. Detailed scans and action execution retain their previous routing.', '',
        '## Validation and failures', '',
        '| Task | Existing success | Original success | Optimized success | Optimized raw output matches | Exact interpretation evidence matches |',
        '|---|---:|---:|---:|---:|---:|',
    ]
    for name, groups in summary['tasks'].items():
        counts = [f"{groups[v]['successes']}/{groups[v]['samples']}" for v in ('existing', 'bridge', 'optimized')]
        group = groups['optimized']
        lines.append(f"| {name} | {' | '.join(counts)} | {group['full_output_matches']}/{group['samples']} | {group['evidence_matches']}/{group['samples']} |")
    lines += ['', 'Raw output comparisons use the Existing sample from the same repetition. Live observations can change between calls; an output mismatch alone is not proof of incorrect execution. Interpretation validation checks the exact typed, allowlisted evidence and digest; it does not prove every sentence of model prose correct. Private observed data is kept outside the repository and is not included in these artifacts.', '',
        '## Remaining Cobuild time', '',
        '| Task | Conversation creation | Send message / response |', '|---|---:|---:|']
    for name, groups in summary['tasks'].items():
        group = groups['optimized']
        lines.append(f"| {name} | {number(seconds(group, 'conversation'))} | {number(seconds(group, 'send'))} |")
    lines += ['', '## Verification', '',
        '890 backend tests and 23 subtests passed. Three browser tests covered pending, completed, failed, and expired interpretations while retaining read data. Frontend typecheck, contracts, and packaged production build passed. Version 0.4.864 was deployed to DEV and TAMGLOBAL.', '',
        'Maintenance audit: no errors; the existing unreferenced `docs/screenshots/overview.png` warning remains. The build also retains its existing bundle-size/deprecated-plugin warnings.', '',
        'Reproduce with `scripts/agents/benchmark_cobuild_tasks.py --samples 3 --output-dir <private-directory>`, then publish its aggregates using `scripts/agents/render_cobuild_task_benchmark.py`.', '']
    return '\n'.join(lines)


def as_html(markdown):
    parts, table = [], False
    for line in markdown.splitlines():
        if line.startswith('|'):
            if line.startswith('|---'):
                continue
            cells = [html.escape(c.strip()) for c in line.strip('|').split('|')]
            if not table:
                parts.append('<div class="scroll"><table><thead><tr>' + ''.join('<th>'+c+'</th>' for c in cells) + '</tr></thead><tbody>')
                table = True
            else:
                parts.append('<tr>' + ''.join('<td>'+c+'</td>' for c in cells) + '</tr>')
            continue
        if table:
            parts.append('</tbody></table></div>')
            table = False
        if line.startswith('# '):
            parts.append('<h1>'+html.escape(line[2:])+'</h1>')
        elif line.startswith('## '):
            parts.append('<h2>'+html.escape(line[3:])+'</h2>')
        elif line:
            parts.append('<p>'+html.escape(line.removeprefix('- '))+'</p>')
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cobuild: ten task benchmark</title><style>
body{font:15px/1.6 system-ui,sans-serif;background:#10151d;color:#e2e8f0;max-width:1250px;margin:40px auto;padding:0 24px}
h1{font-size:30px}h2{margin-top:34px;color:#8cd9ca}p{max-width:1050px;color:#b6c2d1}.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{text-align:right;padding:10px 12px;border-bottom:1px solid #293444;white-space:nowrap}
th:first-child,td:first-child{text-align:left}th{color:#8cd9ca;font-size:13px}tbody tr:hover{background:#182432}
</style><main>''' + '\n'.join(parts) + '</main></html>\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--public-dir', type=Path)
    args = parser.parse_args()
    summary = json.loads((args.input_dir / 'summary.json').read_text())
    markdown = render(summary)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = 'adtk-cobuild-ten-task-benchmark'
    (args.output_dir / (stem+'.md')).write_text(markdown)
    (args.output_dir / (stem+'.html')).write_text(as_html(markdown))
    shutil.copyfile(args.input_dir / 'summary.json', args.output_dir / (stem+'.json'))
    shutil.copyfile(args.input_dir / 'timings.csv', args.output_dir / (stem+'.csv'))
    if args.public_dir:
        args.public_dir.mkdir(parents=True, exist_ok=True)
        for suffix in ('.md', '.html', '.json', '.csv'):
            shutil.copyfile(args.output_dir / (stem+suffix), args.public_dir / (stem+suffix))
    print(args.output_dir / (stem+'.html'))


if __name__ == '__main__':
    main()
