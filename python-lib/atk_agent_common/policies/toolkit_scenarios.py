"""Policy for agent-authored scenarios — ADMINTOOLKIT project ONLY.

The toolkit-scenario-write action lets agents create/update scenarios, but
only inside the toolkit's own support project. Step types are no longer
whitelisted: any step is writable, but CODE-BEARING steps (custom Python,
SQL, non-toolkit macros, anything carrying a script/sql/code payload — and,
fail-safe, any step type we cannot positively classify as code-free) demand
an explicit ackCustomCode acknowledgment. The planner surfaces the full step
params verbatim so the human reviews the actual code before approving, and
the backend impl re-runs the same scan — a rephrased target cannot skip it.
"""

PROJECT_KEY = 'ADMINTOOLKIT'

# Scenario names the agent may never create/overwrite: the toolkit's own
# provisioned automation.
PROTECTED_SCENARIO_NAMES = (
    'Agents — Daily health triage',
    'Agents — Notification relay',
)

# Step types known NOT to embed executable code. Anything outside this list —
# including step types added by future DSS versions — is treated as
# code-bearing (fail-safe: unknown ⇒ code).
NON_CODE_STEP_TYPES = ('build_flowitem', 'run_scenario', 'runnable', 'clear_items')

# Explicit code step types, named so refusals/plans can say WHY.
CODE_STEP_TYPES = ('custom_python', 'exec_sql')

RUNNABLE_TYPE_PREFIX = 'pyrunnable_admin-toolkit_'

# A params key with one of these names holding a non-empty string means the
# step carries code, whatever its type claims. The exact leaf key per step
# type is unconfirmable, hence the recursive scan.
_CODE_PARAM_KEYS = ('script', 'sql', 'code')


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
    """(ok, reason): structural checks + the run_scenario project scope.
    Code-bearing steps are NOT refused here — they are gated behind
    ackCustomCode (see code_bearing_steps)."""
    if not isinstance(steps, list):
        return False, 'steps must be a list of step dicts.'
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            return False, 'step %d is not a dict.' % (i + 1)
        step_type = str(step.get('type') or '').strip()
        if not step_type:
            return False, 'step %d has no type.' % (i + 1)
        if step_type == 'run_scenario':
            params = step.get('params') or {}
            target_project = str(params.get('projectKey') or PROJECT_KEY) \
                if isinstance(params, dict) else PROJECT_KEY
            if target_project != PROJECT_KEY:
                return False, ('step %d runs a scenario in project %r — cross-project '
                               'scenario chaining is refused.' % (i + 1, target_project))
    return True, None


def _params_carry_code(node):
    """True when any nested key named script/sql/code holds a non-empty string."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.lower() in _CODE_PARAM_KEYS \
                    and isinstance(value, str) and value.strip():
                return True
            if _params_carry_code(value):
                return True
        return False
    if isinstance(node, list):
        return any(_params_carry_code(v) for v in node)
    return False


def code_bearing_steps(steps):
    """[(index, type, reason)] of the steps that embed (or may embed) code.
    Fail-safe in every direction: unparseable step ⇒ code, unknown type ⇒
    code, non-toolkit macro ⇒ code."""
    out = []
    for i, step in enumerate(steps or []):
        if not isinstance(step, dict):
            out.append((i, '?', 'unparseable step — treated as code (fail-safe)'))
            continue
        step_type = str(step.get('type') or '').strip()
        params = step.get('params') or {}
        if step_type in CODE_STEP_TYPES:
            out.append((i, step_type, 'explicit code step type'))
        elif _params_carry_code(params):
            out.append((i, step_type, 'params carry a non-empty script/sql/code payload'))
        elif step_type == 'runnable':
            runnable_type = str((params if isinstance(params, dict) else {})
                                .get('runnableType') or '')
            if not runnable_type.startswith(RUNNABLE_TYPE_PREFIX):
                out.append((i, step_type, 'runs a non-toolkit macro (%r)' % runnable_type))
        elif step_type not in NON_CODE_STEP_TYPES:
            out.append((i, step_type, 'unknown step type — treated as code (fail-safe)'))
    return out
