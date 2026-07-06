#!/usr/bin/env python3
"""Health-score parity gate: Python port (atk_agent_common.health) vs the real
TS scoring path (parity_harness.ts via tsx) on identical live payloads.

    .venv/bin/python scripts/agents/score_parity.py [--base URL] [--tolerance 2]

Fails (exit 1) if |python - ts| > tolerance on the overall score, and reports
per-category deltas + issue-id set differences for diagnosis.
"""

import argparse
import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'python-lib'))

from atk_agent_common import config, health  # noqa: E402
from atk_agent_common.client import ToolkitClient  # noqa: E402

DEFAULT_BASE = "https://tam-global.fe-aws.dkucloud-dev.com/web-apps-backends/DIAG_PARSER_BRANCH1/Gv9CLFn"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--host", default="local")
    ap.add_argument("--tolerance", type=float, default=2.0)
    ap.add_argument("--with-usages", action="store_true",
                    help="also fetch /api/connections/usages (memoized full-project scan) "
                         "to exercise the cap-connection-broken path")
    args = ap.parse_args()

    client = ToolkitClient(config.resolve({'backend_url': args.base, 'heavy_timeout_s': 600}))
    host = args.host

    print(f"fetching score inputs from {args.base} (host={host}) ...")
    payloads = {
        'overview': client.get('/api/overview', host=host),
        'rawSettings': client.get('/api/settings/raw', host=host),
        'javaMemoryText': client.get_text('/api/java-memory', host=host),
        'codeEnvs': client.get('/api/code-envs', host=host, heavy=True),
        'footprint': client.get('/api/project-footprint', host=host, heavy=True),
        'thresholds': client.get('/api/settings/threshold-defaults'),
        'whitelist': health.fetch_host_whitelist(client, host),
        # New score inputs — both twins ALWAYS receive identical values
        # (None ⇒ the component skips on both sides by construction).
        'sanity': health.fetch_sanity_messages(client, host),
        'connectionHealth': health.fetch_connection_health(client, host),
        'connectionUsages': (health.fetch_connection_usages(client, host)
                             if args.with_usages else None),
    }

    usages = payloads['connectionUsages']
    ds_usages = (usages.get('datasetUsages') or []) if usages is not None else None
    llm_usages = (usages.get('llmUsages') or []) if usages is not None else None

    from atk_agent_common.tools_impl import _parse_java_memory
    parsed = health.build_parsed_data(payloads['overview'], payloads['rawSettings'],
                                      _parse_java_memory(payloads['javaMemoryText']),
                                      payloads['codeEnvs'], payloads['footprint'],
                                      sanity_messages=payloads['sanity'],
                                      connection_health=payloads['connectionHealth'],
                                      connection_dataset_usages=ds_usages,
                                      connection_llm_usages=llm_usages)
    py_score = health.calculate_health_score(parsed, payloads['thresholds'],
                                             whitelist=payloads['whitelist'])

    tmp = REPO / 'scripts' / 'agents' / '.parity_payloads.json'
    tmp.write_text(json.dumps(payloads, default=str))
    try:
        proc = subprocess.run(
            ['npx', '-y', 'tsx', str(REPO / 'scripts' / 'agents' / 'parity_harness.ts'), str(tmp)],
            cwd=REPO / 'resource' / 'frontend', capture_output=True, text=True, timeout=300)
    finally:
        tmp.unlink(missing_ok=True)
    if proc.returncode != 0:
        print("TS harness failed:\n", proc.stderr[-3000:])
        return 1
    ts_score = json.loads(proc.stdout)

    print(f"\npython overall: {py_score['overall']}   ts overall: {ts_score['overall']}")
    ts_cats = {c['category']: c['score'] for c in ts_score['categories']}
    ok = True
    for cat in py_score['categories']:
        delta = abs(cat['score'] - ts_cats.get(cat['category'], float('nan')))
        flag = 'OK' if delta <= args.tolerance else 'DRIFT'
        if flag == 'DRIFT':
            ok = False
        print(f"  {cat['category']:<20} py={cat['score']:>7.2f}  ts={ts_cats.get(cat['category']):>7.2f}  Δ={delta:.2f} {flag}")
    py_ids = {i['id'] for i in py_score['issues']}
    ts_ids = set(ts_score['issueIds'])
    if py_ids != ts_ids:
        print("  issue-id diff: py-only=%s ts-only=%s" % (sorted(py_ids - ts_ids), sorted(ts_ids - py_ids)))
    overall_delta = abs(py_score['overall'] - ts_score['overall'])
    if overall_delta > args.tolerance:
        ok = False
    print(f"\noverall Δ={overall_delta}  tolerance={args.tolerance} → {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
