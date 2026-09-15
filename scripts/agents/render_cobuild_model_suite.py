#!/usr/bin/env python3
"""Publish only allowlisted replacement measurements, never raw diagnostics."""
import argparse
import csv
import html
import json
import pathlib
import statistics

from cobuild_model_compare import TRANSPORT
from cobuild_model_suite import inventory, PREREQUISITES
from render_cobuild_comparison import PURPOSE

FIELDS = ('at', 'capability', 'status', 'transport', 'scope', 'reason', 'provider',
          'model', 'existing_seconds', 'cobuild_seconds', 'latency_ratio',
          'existing_model_calls', 'cobuild_model_calls', 'context_sha256',
          'execution_attempted', 'final_answers_present')


def collect(paths):
    history = {name: [] for name in inventory()}
    cleanup = []
    for path in paths:
        # Callers name aggregate files explicitly. Never glob transcript files.
        if path.name.endswith('-private.json'):
            raise ValueError('Private transcripts are not report inputs')
        value = json.loads(path.read_text())
        rows = value.get('rows', []) if isinstance(value, dict) else value
        for row in rows:
            name = row.get('capability', '')
            if name.startswith('_') and row.get('status') in ('cleanup_failed', 'restore_pending'):
                cleanup.append({'status': row['status'], 'source': path.name})
            if row.get('transport') == TRANSPORT and name in history:
                history[name].append({k: row[k] for k in FIELDS if k in row})
    output = []
    for name, group in inventory().items():
        attempts = sorted(history[name], key=lambda r: r.get('at', ''))
        latest = attempts[-1] if attempts else {
            'capability': name, 'status': 'skipped' if group == 'excluded' else 'unmeasured',
            'reason': PREREQUISITES.get(group, 'Awaiting a whole-task measurement on the new approach.')}
        output.append(dict(latest, group=group, purpose=PURPOSE[name], attempts=attempts))
    return {'transport': TRANSPORT, 'rows': output, 'cleanup_findings': cleanup}


def render(report, output):
    output.mkdir(parents=True, exist_ok=True)
    rows = report['rows']
    matched = [r for r in rows if r['status'] == 'same']
    ratio = statistics.median(r['latency_ratio'] for r in matched) if matched else None
    summary = ('%d of 64 have matching new measurements. ' % len(matched)
               + ('Median Cobuild / Mesh time: %.2f× across matching pairs. ' % ratio if ratio is not None else '')
               + 'Other rows retain explicit pending, failed or excluded status.')
    if report['cleanup_findings']:
        summary += ' Cleanup needs reconciliation; inspect the cleanup findings in the JSON report.'
    methodology = ('Both paths receive the same ADTK instructions, tool catalog and task. '
        'Each model chooses its tools and produces the final answer. ADTK retains tool execution, '
        'permissions and confirmation checks. Timings include model calls and execution; fixture '
        'creation/reset and independent verification are outside the clock. Each pair runs Mesh '
        'then Cobuild once, so cache/order effects and model variability remain. Functional matching '
        'does not verify every sentence of the final answer. The model column is the configured '
        'main model; Cobuild may internally route service calls. Historical operation-bridge '
        'passes do not count. This is the comparison suite, not a live chat routing change.')
    stem = output / 'adtk-cobuild-model-replacement'
    stem.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    columns = ('capability', 'status', 'existing_seconds', 'cobuild_seconds', 'latency_ratio',
               'existing_model_calls', 'cobuild_model_calls', 'scope', 'reason', 'model')
    with stem.with_suffix('.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore', lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    table = []
    for row in rows:
        table.append('<tr>' + ''.join('<td>' + html.escape(str(row.get(k, '—'))) + '</td>'
                                     for k in columns) + '</tr>')
    page = '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ADTK — whole-task Cobuild comparison</title><style>
body{font:14px/1.5 system-ui;margin:32px;background:#111923;color:#e4eaf3}
p{max-width:1100px;color:#bac6d7}input{padding:10px;width:320px;margin:12px 0}
.scroll{overflow:auto}table{border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:9px;text-align:left;border-bottom:1px solid #304054;max-width:420px;min-width:80px}
th{position:sticky;top:0;background:#1d2b3b}tr:hover{background:#1a2938}
</style><h1>Whole-task model replacement · 64 checks</h1>'''
    page += '<p>' + html.escape(summary) + '</p><p>' + html.escape(methodology) + '</p>'
    page += '<label>Filter checks <input id="filter" type="search"></label><div class="scroll"><table><thead><tr>'
    page += ''.join('<th>' + html.escape(k.replace('_', ' ')) + '</th>' for k in columns)
    page += '</tr></thead><tbody>' + ''.join(table) + '</tbody></table></div>'
    page += '''<script>document.getElementById('filter').addEventListener('input',e=>{
const term=e.target.value.toLowerCase();document.querySelectorAll('tbody tr').forEach(r=>{
r.hidden=!r.textContent.toLowerCase().includes(term);});});</script></html>'''
    stem.with_suffix('.html').write_text(page + '\n')
    stem.with_suffix('.md').write_text('# Whole-task Cobuild comparison\n\n' + summary + '\n\n'
        + methodology + '\n\nSee the adjacent CSV, JSON and searchable HTML for all 64 checks.\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=pathlib.Path, action='append', required=True)
    parser.add_argument('--output-dir', type=pathlib.Path, required=True)
    args = parser.parse_args()
    print(render(collect(args.input), args.output_dir))


if __name__ == '__main__':
    main()
