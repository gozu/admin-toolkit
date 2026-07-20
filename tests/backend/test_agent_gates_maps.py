"""Two-flag capability model: the pure merge helper's invariants
(autonomous ⇒ enabled, python-run floor, unknown keys), the CSV → autonomy
seeding semantics, and the catalog row contract."""

import conftest  # noqa: F401  (installs the python-lib path + DSS stubs)

import pytest

from adk_backend.routes import agent_gates
from atk_agent_common import config as atk_config
from atk_agent_common import tools_impl
from atk_agent_common import actuator


_SENSORS = set(tools_impl.SENSOR_DESCRIPTIONS)
_KNOWN = set(actuator.ACTIONS) | _SENSORS


def _apply(gates=None, autonomous=None, gate_updates=None, auto_updates=None):
    return agent_gates.apply_capability_updates(
        gates or {}, autonomous or {}, gate_updates, auto_updates,
        known=_KNOWN, sensors=_SENSORS)


# ── apply_capability_updates invariants ──────────────────────────────────────

def test_python_run_autonomous_is_rejected():
    with pytest.raises(ValueError) as exc:
        _apply(auto_updates={'python-run': True})
    assert 'python-run' in str(exc.value)
    # ...but clearing it is always fine.
    _gates, autonomous = _apply(autonomous={'python-run': True},
                                auto_updates={'python-run': False})
    assert autonomous['python-run'] is False


def test_autonomous_true_forces_gate_on():
    gates, autonomous = _apply(auto_updates={'log-cleanup': True})
    assert gates['log-cleanup'] is True
    assert autonomous['log-cleanup'] is True


def test_autonomous_wins_over_contradictory_gate_delta():
    gates, autonomous = _apply(gate_updates={'log-cleanup': False},
                               auto_updates={'log-cleanup': True})
    assert gates['log-cleanup'] is True and autonomous['log-cleanup'] is True


def test_disabling_gate_clears_autonomous():
    gates, autonomous = _apply(gates={'log-cleanup': True},
                               autonomous={'log-cleanup': True},
                               gate_updates={'log-cleanup': False})
    assert gates['log-cleanup'] is False
    assert autonomous['log-cleanup'] is False


def test_disabling_sensor_records_autonomy_revocation():
    # Sensors default autonomous ON — turning the sensor off must persist an
    # explicit False so re-enabling does not silently restore autonomy.
    gates, autonomous = _apply(gate_updates={'instance_health': False})
    assert autonomous['instance_health'] is False


def test_unknown_keys_rejected():
    with pytest.raises(ValueError) as exc:
        _apply(gate_updates={'not-a-thing': True})
    assert 'not-a-thing' in str(exc.value)
    with pytest.raises(ValueError):
        _apply(auto_updates={'nope': True})


def test_empty_updates_rejected():
    with pytest.raises(ValueError):
        _apply()


# ── autonomy seeding (route + config.resolve) ────────────────────────────────

def test_read_autonomous_seeds_from_legacy_csv():
    config = {'auto_remediate_actions': 'log-cleanup, docker-prune,python-run'}
    seeded = agent_gates._read_autonomous(config)
    assert seeded == {'docker-prune': True, 'log-cleanup': True}


def test_read_autonomous_never_reseeds_persisted_empty():
    config = {'agent_autonomous_gates': '{}',
              'auto_remediate_actions': 'log-cleanup'}
    assert agent_gates._read_autonomous(config) == {}


def test_read_autonomous_prefers_stored_map():
    config = {'agent_autonomous_gates': '{"connection-test": true}',
              'auto_remediate_actions': 'log-cleanup'}
    assert agent_gates._read_autonomous(config) == {'connection-test': True}


def test_config_resolve_seeds_and_respects_persisted_empty():
    resolved = atk_config.resolve({'auto_remediate_actions': 'log-cleanup,python-run'})
    assert resolved['agent_autonomous_gates'] == {'log-cleanup': True}
    resolved = atk_config.resolve({'agent_autonomous_gates': '{}',
                                   'auto_remediate_actions': 'log-cleanup'})
    assert resolved['agent_autonomous_gates'] == {}


# ── catalog rows contract ────────────────────────────────────────────────────

def test_catalog_rows_carry_two_flags():
    sensors, actions = agent_gates._catalog({}, {})
    assert all(set(s) >= {'name', 'enabled', 'autonomous'} for s in sensors)
    assert all(set(a) >= {'action', 'enabled', 'autonomous', 'autoCapable'}
               for a in actions)
    # defaults: sensors enabled+autonomous, actions neither
    assert all(s['enabled'] and s['autonomous'] for s in sensors)
    assert all(not a['enabled'] and not a['autonomous'] for a in actions)


def test_catalog_autonomous_is_anded_with_enabled_and_capable():
    _s, actions = agent_gates._catalog(
        {'log-cleanup': True, 'python-run': True},
        {'log-cleanup': True, 'docker-prune': True, 'python-run': True})
    by = {a['action']: a for a in actions}
    assert by['log-cleanup']['autonomous'] is True
    assert by['docker-prune']['autonomous'] is False   # gate off
    assert by['python-run']['autonomous'] is False     # hard floor
    assert by['python-run']['autoCapable'] is False
