#!/usr/bin/env python3
"""Pure-Python unit checks for atk_agent_common.action_items (no DSS needed).

    .venv/bin/python scripts/agents/test_action_items.py
"""

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'agents-plugin' / 'python-lib'))

from atk_agent_common import action_items  # noqa: E402

FAILURES = []


def check(name, cond, detail=''):
    status = 'ok' if cond else 'FAIL'
    print('  [%s] %s %s' % (status, name, detail))
    if not cond:
        FAILURES.append(name)


def main():
    p = action_items.propose_action_items

    print('empty / invalid input')
    check('empty list is bad-input', p(None, [])['error']['code'] == 'bad-input')
    check('non-list is bad-input', p(None, 'nope')['error']['code'] == 'bad-input')
    check('titleless items rejected', p(None, [{'why': 'no title'}])['error']['code'] == 'bad-input')

    print('normalization')
    out = p(None, [{
        'title': 'T' * 200, 'why': 'W' * 900, 'risk': 'GREEN',
        'action': 'db-analyze', 'target': {'connection': 'db', 'table': 't'},
        'evidence': ['e1', '', 'e2', 'e3', 'e4', 'e5', 'e6', 'e7'],
    }])
    item = out['items'][0]
    check('batchId assigned', str(out.get('batchId', '')).startswith('aib-'))
    check('server id assigned', item['id'].startswith('ai-') and len(item['id']) == 11)
    check('title clipped to 120', len(item['title']) == 120)
    check('why clipped to 500', len(item['why']) == 500)
    check('risk case-normalized', item['risk'] == 'green')
    check('valid action actionable', item['actionable'] is True and item['validation'] is None)
    check('evidence capped at 6 non-empty', len(item['evidence']) == 6)
    check('host defaults to local', item['host'] == 'local')

    print('downgrades (never dropped)')
    out = p(None, [
        {'title': 'bad action', 'action': 'rm-rf-slash', 'target': {'x': 1}},
        {'title': 'no target', 'action': 'db-vacuum'},
        {'title': 'bad risk', 'risk': 'purple'},
    ])
    a, b, c = out['items']
    check('unknown action -> advisory', a['actionable'] is False and a['action'] is None
          and 'not in the actuator catalog' in a['validation'])
    check('target-less action -> advisory', b['actionable'] is False and b['action'] is None
          and 'without a target' in b['validation'])
    check('bad risk -> amber + note', c['risk'] == 'amber' and 'defaulted to amber' in c['validation'])
    check('all three kept', out['count'] == 3)

    print('cap')
    out = p(None, [{'title': 'item %d' % i} for i in range(14)])
    check('capped at 10', out['count'] == 10)
    check('droppedCount = 4', out.get('droppedCount') == 4)
    check('cap noted in nextStep', 'dropped' in out['nextStep'])

    print('ids unique')
    out = p(None, [{'title': 'a'}, {'title': 'b'}, {'title': 'c'}])
    ids = [i['id'] for i in out['items']]
    check('unique ids', len(set(ids)) == 3)

    if FAILURES:
        print('\n%d FAILURE(S): %s' % (len(FAILURES), ', '.join(FAILURES)))
        sys.exit(1)
    print('\nall action_items checks passed')


if __name__ == '__main__':
    main()
