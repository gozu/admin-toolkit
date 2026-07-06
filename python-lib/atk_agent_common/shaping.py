"""Output budgets: agent tool outputs must stay small and information-dense.

A tool result is context the model pays for on every subsequent turn, so the
contract is: top-N rows, allowlisted keys, and a hard ~12KB serialized cap.
When the cap bites, lists are trimmed (longest first) and the payload says so
via `truncated: true` + a note naming the pressure valves (top_n/name_filter).
"""

import json

MAX_OUTPUT_BYTES = 12_000
_MIN_LIST_KEEP = 3


def pick(row, keys):
    """Project a dict onto an allowlist of keys (missing keys skipped)."""
    return {k: row[k] for k in keys if k in row}


def top_rows(rows, key, n, keys=None, reverse=True):
    """Sort rows by `key` desc and keep the top n (optionally key-projected)."""
    rows = sorted(rows or [], key=lambda r: r.get(key) or 0, reverse=reverse)[:max(0, n)]
    return [pick(r, keys) for r in rows] if keys else rows


def _byte_size(payload):
    return len(json.dumps(payload, separators=(',', ':'), default=str).encode('utf-8'))


def _list_paths(node, path=()):
    """(path, length) of every list of dicts/values reachable in the payload."""
    found = []
    if isinstance(node, dict):
        for k, v in node.items():
            found.extend(_list_paths(v, path + (k,)))
    elif isinstance(node, list):
        found.append((path, len(node)))
        # Don't descend into list items — trimming whole lists is enough.
    return found


def _trim_at(payload, path, new_len):
    node = payload
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = node[path[-1]][:new_len]


def enforce_budget(payload, budget=MAX_OUTPUT_BYTES):
    """Shrink the longest lists in `payload` (in place) until it serializes
    under `budget`. Annotates the payload when anything was dropped."""
    if _byte_size(payload) <= budget:
        return payload
    trimmed = False
    for _ in range(40):
        lists = [(p, n) for p, n in _list_paths(payload) if n > _MIN_LIST_KEEP]
        if not lists:
            break
        path, length = max(lists, key=lambda t: t[1])
        _trim_at(payload, path, max(_MIN_LIST_KEEP, length // 2))
        trimmed = True
        if _byte_size(payload) <= budget:
            break
    if trimmed:
        payload['truncated'] = True
        payload['truncation_note'] = ('Lists were trimmed to fit the output budget. '
                                      'Narrow the request (top_n, name_filter, sections) '
                                      'to see specific rows.')
    return payload
