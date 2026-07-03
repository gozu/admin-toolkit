#!/usr/bin/env python3
"""Streamed event-protocol checks for agents-plugin v2 (live DSS, read-only).

    .venv/bin/python scripts/agents/test_stream_events.py [--project AGENTSSANDBOX]

Asserts:
  1. health-triage emits an `action_items` event (items normalized, ids server-assigned)
  2. ops-actuator emits `plan` event(s) echoing item_ref (kill-switch state
     irrelevant — plans are read-only)
"""

import argparse
import json
import sys
import time

from test_agent import ensure_agent, get_client


def stream_events(llm, prompt):
    completion = llm.new_completion()
    completion.with_message(prompt, role='user')
    events, text = [], []
    for chunk in completion.execute_streamed():
        data = getattr(chunk, 'data', None) or {}
        if data.get('type') == 'event':
            events.append((data.get('eventKind'), data.get('eventData') or {}))
        elif data.get('type') == 'content' and data.get('text'):
            text.append(data['text'])
    return events, ''.join(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project', default='AGENTSSANDBOX')
    ap.add_argument('--llm-id', default='')
    args = ap.parse_args()

    client = get_client()
    project = client.get_project(args.project)
    failures = []

    # 1. health-triage → action_items event
    triage = ensure_agent(project, 'ATK Health Triage', 'health-triage', args.llm_id)
    t0 = time.time()
    events, text = stream_events(triage.as_llm(), (
        'Check backend log errors and db health on the local host only (skip the sweep), then '
        'call propose_action_items with at least 2 items covering what you found — include one '
        'db-analyze item with a real connection/table target if the runtime DB is observable.'))
    kinds = [k for k, _ in events]
    print('[triage %.0fs] events: %s' % (time.time() - t0, kinds))
    ai = [d for k, d in events if k == 'action_items']
    if not ai:
        failures.append('no action_items event from health-triage')
    else:
        batch = ai[0]
        items = batch.get('items') or []
        ok_ids = all(str(i.get('id', '')).startswith('ai-') for i in items)
        print('  batch %s: %d item(s), ids ok=%s, risks=%s' % (
            batch.get('batchId'), len(items), ok_ids, [i.get('risk') for i in items]))
        if not batch.get('batchId', '').startswith('aib-') or not items or not ok_ids:
            failures.append('action_items payload malformed: %s' % json.dumps(batch)[:300])

    # 2. ops-actuator → plan event echoing item_ref
    actuator = ensure_agent(project, 'ATK Ops Actuator', 'ops-actuator', args.llm_id)
    t0 = time.time()
    events, text = stream_events(actuator.as_llm(), (
        'Action-item batch handoff (batch aib-test0001, 1 item selected by the user).\n'
        'Plan EVERY item below — one plan_admin_action call per item, passing its item_ref '
        'verbatim. Present each plan and WAIT for my approval. Do NOT execute anything.\n\n'
        '1. [ai-test0001] ANALYZE a story table — action=db-analyze host=local '
        'target={"connection": "kaosdb", "table": <call db_health view=tables connection=kaosdb '
        'ONCE and use the exact name of the smallest table>} '
        'item_ref={"batchId": "aib-test0001", "itemId": "ai-test0001"}'))
    kinds = [k for k, _ in events]
    print('[actuator %.0fs] events: %s' % (time.time() - t0, kinds))
    plans = [d for k, d in events if k == 'plan']
    if not plans:
        failures.append('no plan event from ops-actuator (text: %s)' % text[:200])
    else:
        ref = plans[0].get('itemRef')
        print('  plan action=%s itemRef=%s token=%s...' % (
            plans[0].get('action'), ref, str(plans[0].get('confirm_token'))[:12]))
        if not ref or ref.get('itemId') != 'ai-test0001':
            failures.append('plan did not echo item_ref: %r' % (ref,))
        if [k for k, d in events if k == 'execution']:
            failures.append('actuator EXECUTED during a plan-only handoff!')

    if failures:
        print('\nFAILURES:\n- ' + '\n- '.join(failures))
        sys.exit(1)
    print('\nall streamed event checks passed')


if __name__ == '__main__':
    main()
