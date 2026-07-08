"""propose_action_items targets[] normalization tests."""

from atk_agent_common import action_items


def _one(items, **overrides):
    item = {'title': 'Do the thing', 'why': 'because', 'risk': 'amber'}
    item.update(overrides)
    result = action_items.propose_action_items(None, [item])
    assert 'items' in result, result
    return result['items'][0]


def test_single_target_back_compat():
    out = _one([], action='code-env-delete', target={'name': 'py36', 'lang': 'python'})
    assert out['actionable'] is True
    assert out['target'] == {'name': 'py36', 'lang': 'python'}
    assert out['targets'] == [{'name': 'py36', 'lang': 'python'}]
    assert out['targetCount'] == 1


def test_batched_targets_one_item():
    targets = [{'name': 'env%d' % i, 'lang': 'python'} for i in range(6)]
    out = _one([], action='code-env-delete', targets=targets)
    assert out['actionable'] is True
    assert out['targetCount'] == 6
    assert out['targets'] == targets
    assert out['target'] == targets[0]  # back-compat mirror
    assert out['validation'] is None


def test_large_target_list_uncapped():
    targets = [{'name': 'env%d' % i} for i in range(25)]
    out = _one([], action='code-env-delete', targets=targets)
    assert out['targetCount'] == 25
    assert out['targets'] == targets
    assert out['validation'] is None


def test_non_batchable_multi_target_keeps_first():
    targets = [{'configName': 'a', 'changes': {'memLimitMB': 1}},
               {'configName': 'b', 'changes': {'memLimitMB': 1}}]
    out = _one([], action='k8s-exec-config-tune', targets=targets)
    assert out['actionable'] is True
    assert out['targetCount'] == 1
    assert out['targets'] == targets[:1]
    assert 'not batchable' in out['validation']


def test_cluster_detach_is_batchable():
    """A fleet audit finds a whole k8s_health list of DNS-dead stale
    attachments — cluster-detach must carry them all in ONE item (regression:
    it silently kept only the first, forcing the rest into prose)."""
    from atk_agent_common import actuator
    assert 'cluster-detach' in actuator.BATCHABLE_ACTIONS
    targets = [{'clusterId': 'dead%d' % i} for i in range(9)]
    out = _one([], action='cluster-detach', targets=targets)
    assert out['actionable'] is True
    assert out['targetCount'] == 9
    assert out['targets'] == targets
    assert out['validation'] is None


def test_action_without_any_target_advisory():
    out = _one([], action='code-env-delete')
    assert out['actionable'] is False
    assert out['action'] is None
    assert out['targets'] is None and out['targetCount'] == 0
    assert 'without any target' in out['validation']


def test_unknown_action_still_advisory():
    out = _one([], action='reboot-the-world', target={'x': 1})
    assert out['actionable'] is False
    assert 'not in the actuator catalog' in out['validation']


def test_non_dict_targets_dropped():
    out = _one([], action='code-env-delete',
               targets=[{'name': 'a'}, 'garbage', {'name': 'b'}])
    assert out['targetCount'] == 2
    assert 'non-dict entries' in out['validation']


def test_description_mentions_batching():
    assert 'targets' in action_items.TOOL_DESCRIPTION
    assert 'ONE item with' in action_items.PROMPT_ADDENDUM
    assert 'never items suppressed' not in action_items.PROMPT_ADDENDUM
    assert 'do not hedge' in action_items.PROMPT_ADDENDUM
