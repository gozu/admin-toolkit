/** All copy + step definitions for the Agents explainer.
 *
 * NUMERIC TRUTH: every number and mechanism named here was verified against
 * the backend source — python-lib/atk_agent_common/ (agent_runtime.py:18
 * MAX_ITERATIONS=12; actuator.py execute_admin_action gate order;
 * confirm.py:17 TOKEN_TTL_SECONDS=900; action_gates.py:27 _TTL_S=30;
 * settings_paths.py:34 BLOCKED_SEGMENT_RE; auto_agent.py / auto_remediate.py /
 * sweep.py for the nightly tier) and python-lib/adk_backend/
 * (routes/agent_gates.py, agent_native.py, agent_tools.py). If the backend
 * changes, this file is wrong until re-verified. Do not add capabilities that
 * are not in the code.
 */

export interface SceneStep {
  title: string;
  body: string;
}

export interface SceneCopy {
  eyebrow: string;
  title: string;
  intro: string;
  steps: readonly SceneStep[];
}

/* ------------------------------------------------------- verified facts -- */

export const FACTS = {
  /** agent_runtime.py:18 MAX_ITERATIONS — both async and native loops. */
  maxIterations: 12,
  /** tools_impl.py SENSOR_DESCRIPTIONS — read-only sensors. */
  sensors: 11,
  /** actuator.py _LEGACY_ACTIONS (12) + actions_registry NEW_ACTIONS (41). */
  actions: 53,
  /** Risk tiers across the 53 actions (actions/__init__.py ALL_RISKS). */
  tierGreen: 4,
  tierAmber: 35,
  tierRed: 14,
  /** action_gates.py:27 _TTL_S — live gate cache. */
  gateCacheSeconds: 30,
  /** confirm.py:17 TOKEN_TTL_SECONDS = 15 * 60. */
  tokenTtlMinutes: 15,
  /** sweep.py:17 default triage_score_threshold. */
  healthThreshold: 75,
  /** auto_agent.py:33-34. */
  plannerMaxTurns: 8,
  plannerMaxProposals: 10,
  /** auto_remediate.py:158-159 defaults. */
  budgetGb: 20,
  budgetObjects: 25,
  /** triage/provision.py:120 — daily digest trigger, server time. */
  digestHour: '07:00',
} as const;

/* ---------------------------------------------------------------- hero -- */

export const HERO = {
  eyebrow: 'Agents',
  title: 'What actually happens when you ask',
  subtitle:
    'The Admin Toolkit agent can read, diagnose, and — with your permission — change things on your DSS instance. ' +
    'This page walks through the machinery underneath: how a question becomes a plan, and how many independent ' +
    'layers stand between the model and a mutation.',
  question: 'Which projects are safe to clean up on this host?',
} as const;

/* ------------------------------------------------------------- scene 1 -- */

export const PLAN_LOOP: SceneCopy = {
  eyebrow: '01 · The request path',
  title: 'The plan is the dry run',
  intro:
    'Your message never touches the instance directly. It streams to a planner that may only read — and for ' +
    'anything that would change state, the most it can produce is a plan for you to approve.',
  steps: [
    {
      title: 'You ask',
      body:
        'The message streams to the backend over SSE. The model receives your question plus a manifest of ' +
        'exactly the read-only sensors you have enabled — nothing else.',
    },
    {
      title: 'The read-only loop',
      body:
        'The planner calls sensors in a tool loop that is hard-capped at 12 iterations. Sensors observe — health, ' +
        'cost, logs, config — they cannot mutate anything.',
    },
    {
      title: 'Out comes a plan',
      body:
        'A mutation proposal is just a card: the action, the exact target, and a signed confirm ticket. ' +
        'Planning IS the dry run — nothing has happened yet.',
    },
    {
      title: 'No side door',
      body:
        'There is no single-call path from the model to a mutation. The only way forward is the separate ' +
        'execute call — with your confirmation and the signed ticket in hand.',
    },
  ],
};

/* ------------------------------------------------------------- scene 2 -- */

export const CATALOG: SceneCopy = {
  eyebrow: '02 · The capability surface',
  title: 'A fixed menu, tiered by risk',
  intro:
    'The agent cannot invent capabilities. It picks from a fixed catalog — read-only sensors plus a closed set ' +
    'of admin actions, each carrying a risk tier. Everything else is structurally out of reach.',
  steps: [
    {
      title: 'Count the menu',
      body:
        '11 read-only sensors and 53 admin actions. That is the entire surface — every one individually ' +
        'switchable on the Permissions page.',
    },
    {
      title: 'Tiered by risk',
      body:
        'Each action carries a tier: 4 green (routine), 35 amber (changes state), 14 red (destructive or ' +
        'high-blast-radius — deletes, python-run, cluster stops).',
    },
    {
      title: 'Outside the wall',
      body:
        'Permanently excluded, by structure not policy: restarting DSS, license operations, reading credentials, ' +
        'creating users or resetting passwords, SSO/LDAP config, arbitrary shell — and uninstalling itself.',
    },
    {
      title: 'Never even bound',
      body:
        'Disable a sensor and it is dropped before the tool list is built. The model does not see a refused ' +
        'tool — it never learns the tool exists at all.',
    },
  ],
};

/** Permanently excluded from the catalog — structural, not policy
 * (actuator.py:15-24 module docstring; settings blacklist settings_paths.py). */
export const CATALOG_EXCLUDED = [
  'Restart DSS',
  'License operations',
  'Read credentials',
  'Create users / reset passwords',
  'SSO / LDAP config',
  'Arbitrary shell',
  'Uninstall itself',
] as const;

/* ------------------------------------------------------------- scene 3 -- */

export const GAUNTLET: SceneCopy = {
  eyebrow: '03 · Execute time',
  title: 'The execute-time gauntlet',
  intro:
    'Approving a plan does not run it. The execute call walks every checkpoint below, in this order, on the ' +
    'server — and the audit row at the end is written whether the action succeeded or failed.',
  steps: [
    {
      title: 'The happy path',
      body:
        'Catalog membership, the global kill-switch, the per-action Enabled flag (re-read live, 30-second ' +
        'cache), your explicit confirm, the signed ticket, the policy floor — then, and only then, the action runs.',
    },
    {
      title: 'Flip the kill-switch',
      body:
        'One master toggle — enable_red_actions — sits above everything. Turn it off and every execute dies at ' +
        'gate 2, no matter what was approved a minute ago.',
    },
    {
      title: 'Drift voids the ticket',
      body:
        'The HMAC ticket signs the action, the host, and the exact canonical target, and expires after 15 ' +
        'minutes. If anything drifted since planning — even a settings value — the signature no longer matches ' +
        'and gate 5 refuses.',
    },
  ],
};

export const GAUNTLET_GATES = [
  { label: 'Catalog check', sub: 'Is this action on the menu at all?' },
  { label: 'Kill-switch', sub: 'enable_red_actions — the master toggle', badge: 'global' },
  { label: 'Enabled gate', sub: 'Per-action flag, re-read at execute', badge: '30s cache' },
  { label: 'Human confirm', sub: 'confirm=true — your click, not the model' },
  { label: 'Signed ticket', sub: 'HMAC over action · host · exact target', badge: '15-min TTL' },
  { label: 'Policy floor', sub: 'Path/log/docker/kubectl whitelists below the model' },
  { label: 'Audit row', sub: 'Written in a finally — success or failure' },
] as const;

/* ------------------------------------------------------------- scene 4 -- */

export const TWO_FLAGS: SceneCopy = {
  eyebrow: '04 · Permissions',
  title: 'Two flags, three outcomes',
  intro:
    'Every capability has an Enabled switch and an Auto switch. Together they decide whether the agent cannot ' +
    'use it, must ask you first, or may run it in the nightly tier. Defaults fail closed.',
  steps: [
    {
      title: 'Blocked',
      body:
        'Enabled off means the action cannot even be planned — and granting Auto without Enabled is refused; ' +
        'the API forces Auto to imply Enabled.',
    },
    {
      title: 'Ask first',
      body:
        'Enabled on, Auto off: the agent can propose, you approve every run. This is the default posture for ' +
        'every action — actions ship OFF, sensors ship ON.',
    },
    {
      title: 'Auto-run, on rails',
      body:
        'Enabled and Auto both on: the nightly tier may execute it — still through the same gauntlet, budgets ' +
        'and kill-switch included.',
    },
    {
      title: 'The exception',
      body:
        'python-run can never be autonomous. That is enforced at 4 independent layers: the API refuses the ' +
        'grant, the gate reader hard-floors it to off, the nightly candidate list subtracts it, and the planner ' +
        'itself refuses to propose it.',
    },
  ],
};

/* ------------------------------------------------------------- scene 5 -- */

export const SECRETS: SceneCopy = {
  eyebrow: '05 · Secrets',
  title: 'One regex, both directions',
  intro:
    'A single blocklist pattern guards secrets on the way in and on the way out: the agent can neither write ' +
    'to a secret-shaped setting nor read one back unredacted. One pattern means no gap between the two.',
  steps: [
    {
      title: 'Writes bounce',
      body:
        'A settings change whose path contains a secret-shaped segment — password, passphrase, secret, ' +
        'credential, token, keytab, keyfile, anything ending in "key" — is refused at plan time and again at ' +
        'execute.',
    },
    {
      title: 'Reads come back masked',
      body:
        'The same pattern redacts values before they reach the model. The agent sees that a field exists — ' +
        'never what is in it.',
    },
    {
      title: 'The ticket pins the value',
      body:
        'A settings ticket signs the path, the new value, AND the current value it expects to replace. If the ' +
        'live value drifts before execute, the signature no longer matches and the ticket is void.',
    },
  ],
};

/* ------------------------------------------------------------- scene 6 -- */

export const SANDBOX: SceneCopy = {
  eyebrow: '06 · The blast wall',
  title: 'The sandbox',
  intro:
    'When an action must touch the host — files, docker, kubectl — it does not run in the webapp. It runs as a ' +
    'macro inside the dedicated ADMINTOOLKIT project, behind policy whitelists the model cannot see or edit.',
  steps: [
    {
      title: 'One labeled door',
      body:
        'Host-bound work enters through a single macro port into the ADMINTOOLKIT project. The webapp process ' +
        'itself never touches the filesystem on the agent’s behalf.',
    },
    {
      title: 'Paths are whitelisted',
      body:
        'File cleanup only operates under approved roots — webapp runs, job logs, tmp, exports — with realpath ' +
        'containment, symlink refusal, and age gates re-applied inside the macro.',
    },
    {
      title: 'Local host only',
      body:
        'The riskiest actions — log cleanup, docker prune, k8s fixes, settings writes, python-run — refuse ' +
        'remote hosts outright. Remote credentials never enter an agent kernel.',
    },
    {
      title: 'Backup before delete',
      body:
        'Destructive actions are two-beat: a backup export lands in the ADMINTOOLKIT project first, then the ' +
        'delete proceeds. No backup, no delete.',
    },
  ],
};

/* ------------------------------------------------------------- scene 7 -- */

export const AUTONOMY: SceneCopy = {
  eyebrow: '07 · The nightly tier',
  title: 'Nightly autonomy, on rails',
  intro:
    'Once a night, the toolkit sweeps the fleet and fixes what you have pre-approved. The model proposes; ' +
    'deterministic code decides who is unhealthy, validates every proposal, and enforces the budget.',
  steps: [
    {
      title: 'Math picks the patients',
      body:
        'A fixed-weight health score — pure code, no LLM anywhere in the ranking — flags hosts scoring below ' +
        '75. The model never chooses who gets attention.',
    },
    {
      title: 'A planner on rails',
      body:
        'The triage planner proposes fixes in at most 8 turns and 10 proposals. Every proposal then passes a ' +
        '6-check code re-validator: catalogued, not python-run, live Auto grant, host flagged tonight, not ' +
        'already handled, budget headroom.',
    },
    {
      title: 'The same gauntlet',
      body:
        'Auto-tier and ask-tier candidates funnel through the exact same plan-then-execute path you just saw — ' +
        'kill-switch, gates, signed ticket, audit row. Autonomy adds checks; it removes none.',
    },
    {
      title: 'Budgets, a pause lever, a receipt',
      body:
        'A night is capped at 20 GB freed and 25 objects touched by default. One switch pauses the whole tier. ' +
        'At 07:00 the digest email lists everything done and everything merely proposed.',
    },
  ],
};

/* ------------------------------------------------------------- scene 8 -- */

export const LEDGER: SceneCopy = {
  eyebrow: '08 · Accountability',
  title: 'Every action leaves a receipt',
  intro:
    'The last gate is memory. Executions land in an insert-only audit table, settings changes carry their ' +
    'before and after, and traces are redacted before they are stored.',
  steps: [
    {
      title: 'Insert-only ledger',
      body:
        'Every execute writes one audit row — action, target, who confirmed, outcome — inside a finally block. ' +
        'Failures are recorded just as faithfully as successes.',
    },
    {
      title: 'Before and after',
      body:
        'Settings changes store the value they replaced next to the value they wrote, so any change can be ' +
        'read back — and reversed — later.',
    },
    {
      title: 'Redacted at the source',
      body:
        'Confirm tokens are stripped from tool-call traces before they are written, and secret values were ' +
        'already masked before reaching the model. Chat persistence itself is OFF by default — conversations ' +
        'stay in your browser until you opt in.',
    },
  ],
};

/* ------------------------------------------------------------- closing -- */

export const CLOSING = [
  {
    page: 'agent-settings',
    title: 'Set the rules',
    body: 'Every capability has an Enabled switch and an Auto switch. You hold both.',
    cta: 'Agent Permissions →',
  },
  {
    page: 'agent-tuning',
    title: 'Shape the voice',
    body: 'Versioned system prompts with runtime overrides — tune how the agent thinks and reports.',
    cta: 'Agent Tuning →',
  },
  {
    page: 'agents',
    title: 'Start a conversation',
    body: 'Ask about health, cost, cleanup, or anything on the instance — the plan comes before any action.',
    cta: 'Open Agents →',
  },
] as const;
