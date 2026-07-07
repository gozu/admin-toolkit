"""Dataset lineage assembly (`_dataset_lineage`): producers/consumers from
recipe IO, webapp/scenario name-token references, and the two rollups the
delete sweeps ground on — `unreferenced` (no direct reference) and
`deleteCandidates` (unreachable walking upstream from any root)."""

import json

from adk_backend.routes.admin_actions import _dataset_lineage, _name_refs, _recipe_io_refs


def _recipe(name, inputs=(), outputs=()):
    return {'name': name,
            'inputs': {'main': {'items': [{'ref': r} for r in inputs]}},
            'outputs': {'main': {'items': [{'ref': r} for r in outputs]}}}


def _rows(*names, exposed=()):
    return [{'name': n, 'type': 'Filesystem', 'exposed': n in exposed} for n in names]


def test_recipe_io_refs_strip_project_prefix_and_ignore_folders():
    raw = _recipe('r', inputs=('OTHER_PROJ.upstream', 'folderId123'), outputs=('out',))
    assert _recipe_io_refs(raw, 'inputs') == ['upstream', 'folderId123']
    assert _recipe_io_refs(raw, 'outputs') == ['out']


def test_name_refs_are_token_bounded():
    blob = json.dumps({'code': 'ds = dataiku.Dataset("stats"); x = cgroup_stats'})
    assert _name_refs({'stats', 'group_stats'}, blob) == {'stats'}


def test_producers_consumers_and_webapp_refs():
    rows = _rows('src', 'mid', 'sink', 'standalone')
    recipes = [_recipe('r1', inputs=('src',), outputs=('mid',)),
               _recipe('r2', inputs=('mid',), outputs=('sink',))]
    webapps = {'app (STANDARD)': json.dumps({'python': 'Dataset("sink")'})}
    out_rows, summary = _dataset_lineage(rows, recipes, webapps, {})
    by_name = {r['name']: r for r in out_rows}
    assert by_name['mid']['producers'] == ['r1']
    assert by_name['mid']['consumers'] == ['r2']
    assert by_name['sink']['webappRefs'] == ['app (STANDARD)']
    # sink is webapp-referenced -> src and mid survive via upstream closure
    assert summary['deleteCandidates'] == ['standalone']
    # 'sink' consumed by nothing but referenced by a webapp -> not unreferenced
    assert summary['unreferenced'] == ['standalone']


def test_exposed_dataset_anchors_upstream_closure():
    rows = _rows('a', 'b', exposed=('b',))
    recipes = [_recipe('prep', inputs=('a',), outputs=('b',))]
    out_rows, summary = _dataset_lineage(rows, recipes, {}, {})
    # 'a' feeds the exposed 'b' through the prep recipe -> kept by the closure
    # (and consumed by prep, so not unreferenced either)
    assert summary['deleteCandidates'] == []
    assert summary['unreferenced'] == []


def test_inactive_scenario_refs_do_not_anchor_but_are_reported():
    rows = _rows('old_out')
    scenarios = {'monitor': (json.dumps({'step': 'build old_out'}), False)}
    out_rows, summary = _dataset_lineage(rows, [], {}, scenarios)
    assert out_rows[0]['scenarioRefs'] == ['monitor (inactive)']
    assert summary['deleteCandidates'] == ['old_out']
    # an ACTIVE scenario reference anchors the dataset instead
    scenarios = {'monitor': (json.dumps({'step': 'build old_out'}), True)}
    out_rows, summary = _dataset_lineage(rows, [], {}, scenarios)
    assert out_rows[0]['scenarioRefs'] == ['monitor']
    assert summary['deleteCandidates'] == []


def test_dead_chain_is_fully_deletable():
    # src -> mid -> sink with NO reference anywhere: the whole chain goes
    rows = _rows('src', 'mid', 'sink')
    recipes = [_recipe('r1', inputs=('src',), outputs=('mid',)),
               _recipe('r2', inputs=('mid',), outputs=('sink',))]
    out_rows, summary = _dataset_lineage(rows, recipes, {}, {})
    assert summary['deleteCandidates'] == ['mid', 'sink', 'src']
    # only 'sink' has no consumers at all; src/mid feed the dead chain
    assert summary['unreferenced'] == ['sink']
