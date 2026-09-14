#!/usr/bin/env python3
"""Render the 64-capability inventory with measured (never inferred) verdicts."""
import argparse
import csv
import datetime
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'python-lib'))
from atk_agent_common import actions, actuator, tools_impl

PURPOSE = {
    'list_hosts': 'List connected DSS instances',
    'instance_health': 'Summarize DSS health and findings',
    'compute_cost': 'Inspect resource use and estimated compute cost',
    'config_inspect': 'Inspect DSS configuration and object inventories',
    'log_errors': 'Group errors found in DSS logs',
    'log_tail': 'Read a filtered tail of DSS logs',
    'storage_footprint': 'Measure disk use by project and category',
    'k8s_health': 'Inspect Kubernetes clusters and findings',
    'db_health': 'Inspect PostgreSQL health',
    'toolkit_get': 'Read an allowed ADTK endpoint',
    'list_capabilities': 'List enabled agent tools and actions',
    'project-delete': 'Back up and delete a project',
    'code-env-delete': 'Back up and delete a code environment',
    'image-delete': 'Delete selected container images',
    'db-vacuum': 'Vacuum a selected PostgreSQL table',
    'db-analyze': 'Refresh statistics for a PostgreSQL table',
    'plugin-deploy': 'Copy a plugin to another managed instance',
    'k8s-exec-config-tune': 'Change execution-config CPU and memory limits',
    'log-cleanup': 'Delete eligible old rotated logs',
    'docker-prune': 'Prune Docker images or build cache',
    'k8s-apply-fix': 'Apply a reviewed Kubernetes repair',
    'code-env-consolidate': 'Move usages from one environment to another',
    'settings-set': 'Change an allowed DSS setting',
    'connection-test': 'Test a DSS connection',
    'connection-index': 'Refresh connection catalog metadata',
    'connection-update': 'Change a non-secret connection setting',
    'connection-delete': 'Back up and delete a connection',
    'cluster-detach': 'Remove a DSS cluster attachment',
    'cluster-stop': 'Stop a managed cluster through its plugin',
    'cluster-start': 'Start a managed cluster through its plugin',
    'cluster-pods-cleanup': 'Delete completed Kubernetes pods and jobs',
    'plugin-uninstall': 'Back up and uninstall an unused plugin',
    'plugin-update': 'Back up and update a plugin from the store',
    'plugin-code-env-rebuild': 'Rebuild a plugin code environment',
    'code-env-update': 'Update packages in a code environment',
    'project-clear-webapp-runs': 'Remove eligible dead webapp run directories',
    'project-export': 'Export a project archive',
    'project-set-cluster': 'Change a project’s selected Kubernetes cluster',
    'project-change-owner': 'Change a project owner',
    'project-variables-set': 'Change a project variable',
    'job-kill': 'Abort a DSS job',
    'scenario-disable': 'Disable a scenario’s automatic triggers',
    'scenario-enable': 'Enable a scenario’s automatic triggers',
    'scenario-kill': 'Abort a running scenario',
    'scenario-run': 'Start a scenario manually',
    'continuous-activity-stop': 'Stop a continuous recipe activity',
    'webapp-backend-stop': 'Stop a webapp backend',
    'webapp-backend-restart': 'Start or restart a webapp backend',
    'notebook-kernels-shutdown': 'Shut down active notebook kernels',
    'notebook-clear-outputs': 'Clear saved notebook outputs',
    'variables-set': 'Change an instance variable',
    'user-disable': 'Disable a user account',
    'user-enable': 'Enable a user account',
    'user-update': 'Change a user’s profile, name or email',
    'api-key-delete': 'Delete a selected API key',
    'tmp-cleanup': 'Delete eligible old DSS temporary files',
    'exports-cleanup': 'Delete eligible old export files',
    'job-logs-cleanup': 'Delete eligible old job log directories',
    'dataset-clear': 'Clear a dataset’s data, keeping its definition',
    'dataset-delete': 'Back up and delete a dataset definition',
    'db-reindex': 'Rebuild indexes on a PostgreSQL table',
    'notification-send': 'Send through a configured notification channel',
    'toolkit-scenario-write': 'Create or rewrite an ADMINTOOLKIT scenario',
    'python-run': 'Run Python after per-run human code approval',
}

PENDING = {
    'db_health': 'DB Health needs its database password; identical errors are not a pass.',
    'db-vacuum': 'Needs a reachable database and a disposable table.',
    'db-analyze': 'Needs a reachable database and a disposable table.',
    'db-reindex': 'Needs a reachable database and a disposable table.',
    'notification-send': 'Needs a controlled recipient or test sink; no real messages sent.',
    'python-run': 'Needs a concrete code plan and the existing per-run human acknowledgment.',
    'docker-prune': 'Need to isolate cache/images; current action has instance-wide scope.',
    'plugin-deploy': 'Needs a disposable plugin and an authorized second host.',
    'continuous-activity-stop': 'Needs a running disposable continuous recipe.',
    'notebook-kernels-shutdown': 'Needs an active disposable notebook kernel.',
    'image-delete': 'Needs an approved old image: server cutoff excludes freshly created images. No existing image deleted.',
    'cluster-start': 'Existing provisioned one t3.small; paired run interrupted by deployment. Needs a stable deployment window.',
    'cluster-stop': 'Existing stop interrupted by backend restart; AWS cleanup completed. Paired lifecycle comparison still pending.',
    'cluster-pods-cleanup': 'Async deletion wait is fixed in the test; live comparison still needs a stable window for a new disposable cluster.',
    'plugin-uninstall': 'Installed-plugin backup is fixed; re-run on a disposable unused plugin if no successful evidence is present.',
    'plugin-update': 'Installed-plugin backup and long-operation timeout are fixed; live store-update verification is required.',
    'project-delete': 'Earlier attempts were interrupted by backend restarts; those were not three meaningful Cobuild improvement attempts.',
}


def collect(inputs):
    by_name = {}
    for path in sorted(inputs, key=lambda p: p.stat().st_mtime):
        data = json.loads(path.read_text())
        if not isinstance(data, list):
            continue
        for row in data:
            if not isinstance(row, dict) or row.get('capability') not in PURPOSE:
                continue
            if row.get('provider') == 'existing':
                continue  # One path alone is never a comparison.
            row = dict(row)
            if row['capability'] == 'db_health' and row.get('baseline', {}).get('error'):
                row['status'] = 'blocked'
            if row.get('status') == 'same' or by_name.get(row['capability'], {}).get('status') != 'same':
                by_name[row['capability']] = row
    return by_name


def rows_for(evidence):
    catalog = list(tools_impl.SENSOR_DESCRIPTIONS) + list(actuator.ACTIONS)
    assert set(catalog) == set(PURPOSE) and len(catalog) == 64
    rows = []
    for name in catalog:
        e = evidence.get(name, {})
        status = {'same': 'Matched scope', 'blocked': 'Blocked', 'failed': 'Needs fix',
                  'needs_review': 'Needs review', 'baseline_failed': 'Blocked'}.get(e.get('status'), 'Not tested')
        if name == 'db_health' and e.get('transport') != 'deployed HTTP bridge':
            status = 'Blocked'
        existing, cobuild = e.get('existing_seconds', ''), e.get('cobuild_seconds', '')
        scope = e.get('scope') or ('Read with bounded/default arguments' if e and name in tools_impl.SENSOR_DESCRIPTIONS else '')
        if name == 'k8s_health' and status == 'Matched scope':
            scope = 'Empty cluster inventory only; no active-cluster health parity established'
        if name == 'config_inspect' and status == 'Matched scope':
            scope = 'Projects domain filtered to ATKCOBUILDTEST; other domains not yet compared'
        if name == 'toolkit_get' and status == 'Matched scope':
            scope = 'Version endpoint only; other endpoints not yet compared'
        if name == 'db_health' and status == 'Matched scope':
            scope = 'Tables view, top 2 rows, with DB Health credential temporarily configured; original config restored'
        rows.append({
            'Tool / action': name, 'What it does': PURPOSE[name],
            'Type': 'Read' if name in tools_impl.SENSOR_DESCRIPTIONS else actions.MODES[name],
            'Switch': 'Existing / Headless-Cobuild',
            'Cobuild path': 'Cobuild requests operation → ADTK executes → Cobuild explains result',
            'What remains in ADTK': 'Executor, permissions, host routing, evidence' +
                (', plans, confirmation and audit' if name in actuator.ACTIONS else ''),
            'Functional verdict': status,
            'Existing seconds': existing, 'Cobuild seconds': cobuild,
            'Latency verdict': ('Slower in sample' if cobuild > existing else 'Cache/order affected; inconclusive')
                if isinstance(existing, (float, int)) and isinstance(cobuild, (float, int)) else 'Not measured',
            'Scope / remaining work': scope if status == 'Matched scope' else PENDING.get(name, e.get('reason') or 'Disposable fixture and both-path execution still required'),
            'Candidate attempt': e.get('attempt', ''),
            'Evidence transport': e.get('transport') or ('Direct Cobuild SDK + real ADTK reads' if e else ''),
            'Measured credits': 'Not returned by API',
            'Tested at UTC': e.get('at', ''),
        })
    return rows


HTML = r'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ADTK · Cobuild comparison</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f5f6f8;color:#19212b;font:14px system-ui,sans-serif}
main{padding:24px}h1{font-size:21px;margin:0 0 8px}p{max-width:1100px;color:#4c5766;line-height:1.5;margin:6px 0}
.controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:20px 0 12px}
input,select,button{font:inherit;border:1px solid #c7ced8;border-radius:6px;padding:8px 10px;background:white;color:inherit}
input{min-width:270px}button{cursor:pointer}button:hover{background:#eef3fa}button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #6fa4ee;outline-offset:2px}
.table{max-height:74vh;overflow:auto;border:1px solid #d7dce4;border-radius:8px;background:white}
table{border-collapse:separate;border-spacing:0;width:100%;font-size:13px}thead{position:sticky;top:0;z-index:2;background:#edf1f6}
th{text-align:left;white-space:nowrap;padding:4px 8px;border-bottom:1px solid #c5cedb}th button{border:0;background:transparent;font-weight:600;padding:9px 0}
td{padding:10px 12px;border-bottom:1px solid #e6e9ee;vertical-align:top;min-width:150px;max-width:300px;line-height:1.4}
td:first-child{font:12px ui-monospace,monospace;white-space:nowrap;position:sticky;left:0;background:inherit;min-width:205px}
tr{background:white}tbody tr:nth-child(even){background:#fafbfc}tbody tr:hover{background:#edf4fe}.match{color:#14673d;font-weight:600}.blocked{color:#845d06;font-weight:600}
.count{margin-left:auto;color:#5c6674;font-variant-numeric:tabular-nums}label{display:flex;align-items:center;gap:6px}.meta{font-size:12px}
</style><main><h1>ADTK · Cobuild comparison</h1>
<p>64 switches. This implementation uses Cobuild with ADTK as the executor.
It does not replace the underlying tools with native Headless tools or move the outer chat model.</p>
<p>“Matched scope” means successful results matched within the stated test.
Latency is graded separately. Untested branches, billing totals and end-to-end chat quality remain unproven.</p>
<p class="meta">Updated __DATE__. Cobuild usage credits and BYO LLM token charges are separate. Per-call credit totals were not returned.</p>
<div class="controls"><input id="search" type="search" aria-label="Search all columns" placeholder="Search tool, scope, blocker…">
<select id="status" aria-label="Filter by verdict"><option value="">All verdicts</option></select>
<select id="kind" aria-label="Filter by type"><option value="">All types</option></select>
<label><input id="details" type="checkbox" style="min-width:0">Show route details</label>
<button id="reset">Reset</button><button id="download">Download filtered CSV</button>
<span id="count" class="count" aria-live="polite"></span></div>
<div class="table"><table><thead><tr id="head"></tr></thead><tbody id="body"></tbody></table></div>
<p class="meta">All mutations use disposable fixtures unless the scope states a read-only probe.
Audit and history were verified separately for project-variable changes in DSS.
Results are not 64 full tool certifications.</p></main>
<script id="data" type="application/json">__DATA__</script>
<script>
const rows=JSON.parse(document.getElementById('data').textContent),$=id=>document.getElementById(id);
const brief=['Tool / action','What it does','Functional verdict','Existing seconds','Cobuild seconds','Latency verdict','Scope / remaining work'];
let sort='Tool / action',dir=1,current=[];
for(const [id,key] of [['status','Functional verdict'],['kind','Type']])for(const v of [...new Set(rows.map(r=>r[key]))].sort()){const o=document.createElement('option');o.value=o.textContent=v;$(id).append(o)}
function render(){
 const q=$('search').value.toLowerCase();current=rows.filter(r=>(!$('status').value||r['Functional verdict']===$('status').value)&&(!$('kind').value||r.Type===$('kind').value)&&Object.values(r).some(v=>String(v).toLowerCase().includes(q)));
 current.sort((a,b)=>dir*(typeof a[sort]==='number'&&typeof b[sort]==='number'?a[sort]-b[sort]:String(a[sort]).localeCompare(String(b[sort]),undefined,{numeric:true})));
 const cols=$('details').checked?Object.keys(rows[0]):brief;$('head').replaceChildren();$('body').replaceChildren();
 for(const key of cols){const th=document.createElement('th'),b=document.createElement('button');th.scope='col';th.setAttribute('aria-sort',sort===key?(dir===1?'ascending':'descending'):'none');b.textContent=key+(sort===key?(dir===1?' ↑':' ↓'):'');b.onclick=()=>{dir=sort===key?-dir:1;sort=key;render()};th.append(b);$('head').append(th)}
 for(const row of current){const tr=document.createElement('tr');for(const key of cols){const td=document.createElement('td');td.textContent=row[key];if(key==='Functional verdict')td.className=row[key]==='Matched scope'?'match':'blocked';tr.append(td)}$('body').append(tr)}
 $('count').textContent=`${current.length} of ${rows.length} capabilities`;
}
for(const id of ['search','status','kind','details'])$(id).addEventListener('input',render);
$('reset').onclick=()=>{$('search').value=$('status').value=$('kind').value='';$('details').checked=false;sort='Tool / action';dir=1;render()};
$('download').onclick=()=>{const cols=Object.keys(rows[0]),quote=v=>'"'+String(v??'').replaceAll('"','""')+'"';const csv=[cols,...current.map(r=>cols.map(k=>r[k]))].map(r=>r.map(quote).join(',')).join('\r\n');const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='adtk-cobuild-filtered.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};render();
</script></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    evidence = parser.add_mutually_exclusive_group(required=True)
    evidence.add_argument('--evidence-dir', type=pathlib.Path)
    evidence.add_argument('--evidence-file', type=pathlib.Path)
    parser.add_argument('--output-dir', required=True, type=pathlib.Path)
    args = parser.parse_args()
    inputs = [args.evidence_file] if args.evidence_file else list(args.evidence_dir.glob('*.json'))
    rows = rows_for(collect(inputs))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / 'adtk-cobuild-comparison.csv'
    with csv_path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    encoded = json.dumps(rows, ensure_ascii=False).replace('<', '\\u003c')
    page = HTML.replace('__DATA__', encoded).replace('__DATE__', datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    (args.output_dir / 'adtk-cobuild-comparison.html').write_text(page)
    print(json.dumps({status: sum(r['Functional verdict'] == status for r in rows)
                      for status in sorted(set(r['Functional verdict'] for r in rows))}))


if __name__ == '__main__':
    main()
