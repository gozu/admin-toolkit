"""Toolkit-scenario actuator action — agents author scenarios, but ONLY in
the ADMINTOOLKIT support project. Any step type is writable; CODE-BEARING
steps (custom Python, SQL, non-toolkit macros, any script/sql/code payload —
fail-safe: unknown step types count as code) require an explicit
ackCustomCode: the plan surfaces the step params VERBATIM so the human reads
the actual code and takes responsibility before it runs (ackExposed/
ackReferenced precedent). Operating existing scenarios (run / kill / enable /
disable) stays with the generic scenario-* actions — this action covers
create + rewrite.
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
    ack_custom_code = bool((target or {}).get('ackCustomCode'))

    ok, reason = policy.validate_name(name)
    if not ok:
        raise ToolkitError('toolkit-scenario-write refused: %s' % reason,
                           remediation='Relay this refusal to the user verbatim.')
    ok, reason = policy.validate_steps(steps)
    if not ok:
        raise ToolkitError('toolkit-scenario-write refused: %s' % reason,
                           remediation='Relay this refusal to the user verbatim.')
    code_steps = policy.code_bearing_steps(steps)
    if code_steps and not ack_custom_code:
        raise ToolkitError(
            'Scenario contains code-bearing step(s): %s. Custom code only runs after '
            'the user has reviewed it and explicitly accepted responsibility.'
            % '; '.join('step %d (%s: %s)' % (i + 1, t or '?', r)
                        for i, t, r in code_steps),
            remediation='Show the user the full step definitions (the code included), '
                        'and only after they explicitly accept, re-plan with '
                        '"ackCustomCode": true in the target.')
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
    if code_steps:
        # Bound into the HMAC token ONLY when code is present, so the ack the
        # human approves is exactly the ack the executor forwards.
        canonical['ackCustomCode'] = True
    step_kinds = [str(s.get('type')) for s in steps]
    warnings = []
    if existing:
        warnings.append('Existing steps/triggers of %s are fully replaced by this '
                        'definition.' % scenario_id)
    if code_steps:
        warnings.append('This scenario embeds CUSTOM CODE that will run with the '
                        'scenario\'s privileges. The full step definitions are shown '
                        'verbatim under codeSteps — the user must review them and '
                        'takes responsibility by approving this plan.')
    return canonical, {
        'summary': '%s scenario %r in project %s with %d step(s)%s%s%s.' % (
            'REWRITE' if existing else 'CREATE',
            name, policy.PROJECT_KEY, len(steps),
            ', daily trigger at %02d:00' % hour if hour is not None else '',
            {None: '', True: ', auto-triggers ON', False: ', auto-triggers OFF'}[
                canonical['active']],
            ', including %d CODE-BEARING step(s) (ackCustomCode)' % len(code_steps)
            if code_steps else ''),
        'projectKey': policy.PROJECT_KEY,
        'stepTypes': step_kinds,
        # Full params VERBATIM — the human reviews the actual code, not a
        # summary (the exact code-carrying leaf key is unconfirmable).
        'codeSteps': ([{'step': i + 1, 'type': t, 'reason': r,
                        'params': (steps[i] or {}).get('params')}
                       for i, t, r in code_steps] or None),
        'replaces': ({'scenarioId': scenario_id, 'name': existing.get('name')}
                     if existing else None),
        'warnings': warnings or None,
        'note': 'Scoped to the ADMINTOOLKIT support project — agent-authored scenarios '
                'can never live in user projects. Code-bearing steps require '
                'ackCustomCode, re-checked at execute.',
    }


def _exec_toolkit_scenario_write(client, host, target):
    return _base.post_backend_action(client, host, 'toolkit-scenario-write', {
        'name': target['name'], 'scenarioId': target.get('scenarioId'),
        'steps': target.get('steps') or [],
        'dailyTriggerHour': target.get('dailyTriggerHour'),
        'active': target.get('active'),
        'ackCustomCode': bool(target.get('ackCustomCode'))})


SPECS = [
    _base.spec('toolkit-scenario-write',
               'toolkit-scenario-write {name, scenarioId?, steps[]?, dailyTriggerHour?, '
               'active?, ackCustomCode?} (create or rewrite a scenario in the '
               'ADMINTOOLKIT project ONLY; code-bearing steps — custom_python, '
               'exec_sql, non-toolkit macros, any script/sql/code payload — require '
               'ackCustomCode after the user reviews the code; operate scenarios with '
               'the scenario-run/kill/enable/disable actions)', 'amber',
               _plan_toolkit_scenario_write, _exec_toolkit_scenario_write),
]
