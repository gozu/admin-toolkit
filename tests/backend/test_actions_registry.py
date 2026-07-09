"""Registry-integrity tests: every catalogued action is fully wired — planner,
executor, shape prose, risk, edu content, and either a remediation-map row or
nothing (advisory-only mapping is allowed; a missing UI entry is not)."""

import os
import re

from atk_agent_common import actions, actuator

_EDU_TS = os.path.join(os.path.dirname(__file__), '..', '..', 'resource', 'frontend',
                       'src', 'utils', 'agentEduContent.ts')


def _edu_action_ids():
    with open(_EDU_TS, encoding='utf-8') as fh:
        return set(re.findall(r"'action\.([a-z0-9-]+)':", fh.read()))


def test_every_action_has_planner_and_executor():
    for action in actuator.ACTIONS:
        assert action in actuator._PLANNERS, 'missing planner: %s' % action
        assert action in actuator._EXECUTORS, 'missing executor: %s' % action


def test_registry_spec_completeness():
    for spec in actions.SPECS:
        assert spec['action'], spec
        assert spec['risk'] in ('red', 'amber', 'green'), spec['action']
        assert spec['action'] in spec['shape'], \
            'shape prose must name the action: %s' % spec['action']
        assert callable(spec['planner']) and callable(spec['executor']), spec['action']


def test_no_duplicate_action_ids():
    assert len(set(actuator.ACTIONS)) == len(actuator.ACTIONS)


def test_generated_shapes_cover_whole_catalog():
    for action in actuator.ACTIONS:
        base = action.split('/')[0]
        assert base in actuator.TARGET_SHAPES, \
            'TARGET_SHAPES prose missing action %s' % action


def test_every_action_has_edu_entry():
    edu = _edu_action_ids()
    missing = [a for a in actuator.ACTIONS if a not in edu]
    assert not missing, 'agentEduContent.ts missing action.<id> entries: %s' % missing


def test_batchable_actions_exist():
    unknown = actuator.BATCHABLE_ACTIONS - set(actuator.ACTIONS)
    assert not unknown, 'batchable set names unknown actions: %s' % unknown


def test_local_only_actions_exist():
    unknown = set(actuator._LOCAL_ONLY_ACTIONS) - set(actuator.ACTIONS)
    assert not unknown


def test_settings_hooks_reference_known_actions():
    unknown = set(actuator._SETTINGS_CHANGE_HOOKS) - set(actuator.ACTIONS)
    assert not unknown


def test_remediation_map_actions_are_catalogued():
    from atk_agent_common import remediation_map
    for glob, specs in remediation_map.REMEDIATIONS:
        for spec in specs or []:
            assert spec['action'] in actuator.ACTIONS, \
                '%s maps to uncatalogued action %s' % (glob, spec['action'])


def test_required_target_keys_cover_whole_catalog():
    for action in actuator.ACTIONS:
        assert action in actions.REQUIRED_TARGET_KEYS, \
            'REQUIRED_TARGET_KEYS missing action %s' % action


def test_required_target_keys_parsed_from_shapes():
    req = actions.REQUIRED_TARGET_KEYS
    assert req['connection-update'] == frozenset({'name', 'path', 'newValue'})
    assert req['settings-set'] == frozenset({'path', 'newValue'})
    assert req['db-vacuum'] == frozenset({'connection', 'table'})
    assert req['db-analyze'] == frozenset({'connection', 'table'})
    assert req['k8s-exec-config-tune'] == frozenset({'configName', 'changes'})
    assert req['api-key-delete'] == frozenset({'keyType', 'keyId'})
    assert req['connection-index'] == frozenset()
    assert req['log-cleanup'] == frozenset()


def test_required_target_keys_match_planner_defaults():
    # These keys are defaulted inside their planners, so the shape prose must
    # mark them optional or the propose-time shape check would downgrade
    # perfectly plannable items.
    req = actions.REQUIRED_TARGET_KEYS
    assert req['code-env-delete'] == frozenset({'name'})            # lang -> 'python'
    assert req['image-delete'] == frozenset({'images', 'cutoff'})   # provider -> 'ecr'
    assert req['docker-prune'] == frozenset()                       # mode -> 'builder'
    assert req['k8s-apply-fix'] == frozenset({'clusterId'})         # commands OR execConfigPatch
