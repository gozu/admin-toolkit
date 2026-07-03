#!/usr/bin/env python3
"""Phase C groundedness gate: run golden_questions.json against the live
scoping-architect agent (virtual LLM through the Mesh) and check that every
answer contains the expected facts AND cites its sources.

    .venv/bin/python scripts/agents/golden_check.py [--project AGENTSSANDBOX] [--limit N]
"""

import argparse
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))

from test_agent import ensure_agent, get_client  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project', default='AGENTSSANDBOX')
    ap.add_argument('--agent', default='ATK Scoping Architect')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--llm-id', default='')
    args = ap.parse_args()

    golden = json.loads((HERE / 'golden_questions.json').read_text())
    questions = golden['questions'][:args.limit or None]

    client = get_client()
    project = client.get_project(args.project)
    agent = ensure_agent(project, args.agent, 'scoping-architect', args.llm_id)
    llm = agent.as_llm()

    passed = failed = 0
    for i, q in enumerate(questions, 1):
        t0 = time.time()
        completion = llm.new_completion()
        completion.with_message(q['q'])
        try:
            resp = completion.execute()
            answer = resp.text if resp.success else ''
        except Exception as exc:
            answer = ''
            print(f"[{i}] EXCEPTION: {exc}")
        elapsed = time.time() - t0
        missing = [e for e in q['expect'] if e.lower() not in (answer or '').lower()]
        cites = ('host=' in (answer or '')) or ('(list_hosts' in (answer or '')) or \
                any(t in (answer or '') for t in ('instance_health', 'config_inspect', 'compute_cost',
                                                  'storage_footprint', 'k8s_health', 'adoption_metrics',
                                                  'db_health', 'usage_analytics'))
        ok = not missing and cites
        passed += ok
        failed += (not ok)
        print(f"[{i}] {'PASS' if ok else 'FAIL'} ({elapsed:.0f}s) {q['q'][:70]}")
        if missing:
            print(f"     missing facts: {missing}")
        if not cites:
            print("     no tool/host citation found")
        if not ok:
            print("     answer head:", (answer or '(empty)')[:300].replace('\n', ' '))
    print(f"\n{passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
