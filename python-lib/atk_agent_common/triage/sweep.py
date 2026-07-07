"""Deterministic fleet triage sweep — no LLM anywhere in the ranking.

Scores every reachable host with the ported UI health score (health.py),
collects the top issues + supporting signals, and ranks hosts worst-first.
LLMs only ever DRAFT recommendations on top of this data (agent or daily
runnable); they never decide what is unhealthy.
"""

from .. import health, shaping, tools_impl
from ..errors import ScanTimeout, ToolkitError

# Issue keys forwarded to LLM consumers — single-sourced from health.py
# (whitelistRule/whitelistItems stay out; see the note there).
_ISSUE_KEYS = health.ISSUE_PICK_KEYS


def sweep_fleet(client, hosts=None, score_threshold=75, payload_sink=None):
    """Score each host; returns {'hosts': [ranked rows], 'flagged': [...ids]}.

    A row: {host, nodeId?, score, status, categoryScores, topIssues, signals?,
    error?}. Hosts whose heavy scans are still warming get status='scan_running'
    and sort last (unknown ≠ healthy, but they can't be ranked yet).

    `payload_sink`: optional dict — filled with {host_id: raw scan payloads}
    for the snapshot zip (empty for hosts whose scoring errored).
    """
    ids = hosts or [h.get('id') for h in client.list_hosts()]
    rows = []
    for host_id in ids:
        row = {'host': host_id}
        collect = {} if payload_sink is not None else None
        if payload_sink is not None:
            payload_sink[host_id] = collect
        try:
            score = health.score_host(client, host=host_id, collect=collect)
            row.update({
                'score': score['overall'],
                'status': score['status'],
                'categoryScores': {c['category']: round(c['score'], 1) for c in score['categories']},
                'topIssues': [shaping.pick(i, _ISSUE_KEYS) for i in score['issues'][:8]],
                'criticalCount': score['criticalCount'],
                'warningCount': score['warningCount'],
                'whitelistSuppressed': score.get('whitelistSuppressed', 0),
            })
        except ScanTimeout as exc:
            row.update({'status': 'scan_running', 'score': None,
                        'note': exc.message, 'progress': exc.progress})
        except ToolkitError as exc:
            row.update({'status': 'error', 'score': None, 'error': exc.to_output()['error']})
        rows.append(row)

    def sort_key(r):
        return r['score'] if isinstance(r.get('score'), (int, float)) else 999
    rows.sort(key=sort_key)

    flagged = [r['host'] for r in rows
               if isinstance(r.get('score'), (int, float)) and r['score'] < score_threshold]

    # enrich flagged hosts with the cheap supporting signals recommendations need
    for row in rows:
        if row['host'] not in flagged:
            continue
        signals = {}
        try:
            signals['logErrors'] = tools_impl.log_errors(client, host=row['host'], top_n=3)
        except ToolkitError as exc:
            signals['logErrors'] = exc.to_output()
        try:
            signals['sanity'] = tools_impl.instance_health(
                client, host=row['host'], sections=['sanity'], top_n=5).get('sanity')
        except ToolkitError as exc:
            signals['sanity'] = exc.to_output()
        row['signals'] = signals

    return {'hosts': rows, 'flagged': flagged, 'scoreThreshold': score_threshold}
