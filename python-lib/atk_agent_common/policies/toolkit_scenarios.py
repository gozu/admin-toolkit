"""Policy for agent-authored scenarios — ADMINTOOLKIT project ONLY.

The toolkit-scenario-write action lets agents create/update scenarios, but
only inside the toolkit's own support project and only from a whitelisted
step vocabulary. Anything that would hand the model arbitrary code execution
(custom Python steps, SQL steps, non-toolkit macros) is refused here AND
re-refused inside the backend impl — a rephrased target cannot widen the
blast radius.
"""

PROJECT_KEY = 'ADMINTOOLKIT'

# Scenario names the agent may never create/overwrite: the toolkit's own
# provisioned automation.
PROTECTED_SCENARIO_NAMES = (
    'Agents — Daily health triage',
    'Agents — Notification relay',
)

# Step types an agent-authored scenario may contain. Deliberately excluded:
# custom_python / exec_sql (arbitrary code), package/plugin steps, anything
# touching security. 'runnable' is allowed ONLY for this plugin's own macros.
ALLOWED_STEP_TYPES = ('build_flowitem', 'run_scenario', 'runnable', 'clear_items')

RUNNABLE_TYPE_PREFIX = 'pyrunnable_admin-toolkit_'


def validate_project(project_key):
    if str(project_key or PROJECT_KEY) != PROJECT_KEY:
        return False, ('agent-authored scenarios are restricted to the %s project; '
                       'project %r is refused.' % (PROJECT_KEY, project_key))
    return True, None


def validate_name(name):
    if str(name or '').strip() in PROTECTED_SCENARIO_NAMES:
        return False, ('scenario name %r is protected (toolkit-provisioned automation) — '
                       'agents may not create or rewrite it.' % name)
    return True, None


def validate_steps(steps):
    """(ok, reason) for a raw steps list."""
    if not isinstance(steps, list):
        return False, 'steps must be a list of step dicts.'
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            return False, 'step %d is not a dict.' % (i + 1)
        step_type = str(step.get('type') or '').strip()
        if step_type not in ALLOWED_STEP_TYPES:
            return False, ('step %d has type %r — allowed step types: %s. Custom-code '
                           'steps are never agent-writable.'
                           % (i + 1, step_type, ', '.join(ALLOWED_STEP_TYPES)))
        if step_type == 'runnable':
            runnable_type = str(((step.get('params') or {}).get('runnableType')) or '')
            if not runnable_type.startswith(RUNNABLE_TYPE_PREFIX):
                return False, ('step %d runs macro %r — only this plugin\'s own macros '
                               '(%s*) are allowed.' % (i + 1, runnable_type, RUNNABLE_TYPE_PREFIX))
        if step_type == 'run_scenario':
            params = step.get('params') or {}
            target_project = str(params.get('projectKey') or PROJECT_KEY)
            if target_project != PROJECT_KEY:
                return False, ('step %d runs a scenario in project %r — cross-project '
                               'scenario chaining is refused.' % (i + 1, target_project))
    return True, None
