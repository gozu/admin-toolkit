"""Toolkit-scenario actuator action — agents author scenarios, but ONLY in
the ADMINTOOLKIT support project and only from the whitelisted step
vocabulary in policies.toolkit_scenarios. Operating existing scenarios
(run / kill / enable / disable) stays with the generic scenario-* actions —
this action covers create + rewrite.
"""

from ..errors import ToolkitError
from ..policies import toolkit_scenarios as policy
from . import _base


def _existing_scenarios(client, host):
    data = client.get('/api/tools/admin-actions/inventory', host=host,
                      params={'domain': 'scenarios', 'projectKey': policy.PROJECT_KEY})
    return data.get('scenarios') or []


def _plan_toolkit_scenario_write(client, host, target, params):
    name = _base.require_str(target, 'name', 'toolkit-scenario-write')
    scenario_id = str((target or {}).get('scenarioId') or '').strip() or None
    steps = (target or {}).get('steps') or []
    hour = (target or {}).get('dailyTriggerHour')
    active = (target or {}).get('active')

    ok, reason = policy.validate_name(name)
    if not ok:
        raise ToolkitError('toolkit-scenario-write refused: %s' % reason,
                           remediation='Relay this refusal to the user verbatim.')
    ok, reason = policy.validate_steps(steps)
    if not ok:
        raise ToolkitError('toolkit-scenario-write refused: %s' % reason,
                           remediation='Only whitelisted step types are agent-writable. '
                                       'Relay this refusal to the user verbatim.')
    if hour is not None:
        hour = int(hour)
        if not 0 <= hour <= 23:
            raise ToolkitError('dailyTriggerHour must be 0-23.')

    rows = _existing_scenarios(client, host)
    existing = None
    if scenario_id:
        existing = next((s for s in rows if s.get('id') == scenario_id), None)
        if existing is None:
            raise ToolkitError(
                'Scenario %r not found in %s. Scenarios: %s'
                % (scenario_id, policy.PROJECT_KEY,
                   ', '.join(sorted(str(s.get('id')) for s in rows)[:20]) or '(none)'),
                remediation='Omit scenarioId to create a new scenario.')
        if str(existing.get('name') or '') in policy.PROTECTED_SCENARIO_NAMES:
            raise ToolkitError('Scenario %r is toolkit-provisioned automation — agents may '
                               'not rewrite it.' % existing.get('name'))
    canonical = {'name': name, 'scenarioId': scenario_id, 'steps': steps,
                 'dailyTriggerHour': hour, 'active': bool(active) if active is not None else None}
    step_kinds = [str(s.get('type')) for s in steps]
    return canonical, {
        'summary': '%s scenario %r in project %s with %d step(s)%s%s.' % (
            'REWRITE' if existing else 'CREATE',
            name, policy.PROJECT_KEY, len(steps),
            ', daily trigger at %02d:00' % hour if hour is not None else '',
            {None: '', True: ', auto-triggers ON', False: ', auto-triggers OFF'}[
                canonical['active']]),
        'projectKey': policy.PROJECT_KEY,
        'stepTypes': step_kinds,
        'replaces': ({'scenarioId': scenario_id, 'name': existing.get('name')}
                     if existing else None),
        'warnings': (['Existing steps/triggers of %s are fully replaced by this '
                      'definition.' % scenario_id] if existing else None),
        'note': 'Scoped to the ADMINTOOLKIT support project — agent-authored scenarios '
                'can never live in user projects. Step types are policy-whitelisted '
                '(no custom code).',
    }


def _exec_toolkit_scenario_write(client, host, target):
    return _base.post_backend_action(client, host, 'toolkit-scenario-write', {
        'name': target['name'], 'scenarioId': target.get('scenarioId'),
        'steps': target.get('steps') or [],
        'dailyTriggerHour': target.get('dailyTriggerHour'),
        'active': target.get('active')})


SPECS = [
    _base.spec('toolkit-scenario-write',
               'toolkit-scenario-write {name, scenarioId?, steps[]?, dailyTriggerHour?, '
               'active?} (create or rewrite a scenario in the ADMINTOOLKIT project ONLY; '
               'step types whitelisted: build_flowitem, run_scenario, runnable '
               '[toolkit macros only], clear_items; operate scenarios with the '
               'scenario-run/kill/enable/disable actions)', 'amber',
               _plan_toolkit_scenario_write, _exec_toolkit_scenario_write),
]
