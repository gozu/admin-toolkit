// Education content for the Agents module's ⓘ InfoDots. Every concept a
// first-time admin meets on the page has an entry; InfoDot renders nothing
// for ids that are absent, so dynamic ids (`tool.<name>`) degrade safely.

export interface EduEntry {
  title: string;
  body: string[];
}

export const EDU: Record<string, EduEntry> = {
  /* ── agents ─────────────────────────────────────────────────────────── */
  'agent.health-triage': {
    title: 'Health Triage agent',
    body: [
      'A read-only "sensor" agent. It sweeps every DSS host with the same 0–100 health score the toolkit UI uses, digs into logs, storage, databases and Kubernetes, and reports what needs attention.',
      'It can propose action items for the Ops Actuator, but it can never change anything itself — it has no plan or execute tools.',
    ],
  },
  'agent.scoping-architect': {
    title: 'Scoping Architect agent',
    body: [
      'A read-only analyst for sizing, adoption and architecture questions ("who builds here?", "what would a migration involve?"). Every claim is grounded in a tool call and cited with the host and tool that produced it.',
      'Like Health Triage, it can propose action items but never executes anything.',
    ],
  },
  'agent.ops-actuator': {
    title: 'Ops Actuator agent',
    body: [
      'The only agent that can change your instances — and only through a strict protocol: it PLANS an action (a read-only dry run showing the exact blast radius), shows you the plan, and executes only after you approve that specific plan.',
      'Five independent safety gates stand between a request and a mutation; the most important one (the kill switch) can only be flipped by a human administrator in the plugin settings.',
    ],
  },

  /* ── core concepts ──────────────────────────────────────────────────── */
  'concept.plan': {
    title: 'What is a plan?',
    body: [
      'A plan is a read-only dry run of an admin action. The agent gathers the real targets and consequences from live scans — sizes, owners, projects affected, backup destination — so you see exactly what would happen before anything happens.',
      'Approving a plan is the only way to authorize the action, and the approval covers exactly what the plan shows: if anything drifts (different target, host or action), the execution is automatically refused.',
    ],
  },
  'concept.confirm-token': {
    title: 'Confirm tokens',
    body: [
      'Every plan carries a cryptographically signed token binding the exact action + host + target with a 15-minute expiry. Execution recomputes the signature: a changed target, a forged token or an expired window is rejected outright.',
      'This means an agent can only ever execute the EXACT plan a human saw — nothing more, nothing else, nothing later.',
    ],
  },
  'concept.kill-switch': {
    title: 'The master kill switch',
    body: [
      'The agents plugin has an enable_red_actions master switch, OFF by default. While it is off, every execution — even a fully approved one — is refused at the last gate.',
      'Only a human administrator can turn it on, in the plugin settings. Agents cannot; that is the point.',
    ],
  },
  'concept.audit-trail': {
    title: 'Action audit trail',
    body: [
      'Every executed action — success or failure — writes one immutable row to the audit table (Postgres) before the result even reaches the agent: timestamp, agent, host, action, target, outcome, and a non-reversible hash of the confirm token.',
      'Rows proposed from an action-item checklist also carry the batch/item provenance, so you can trace an execution all the way back to the finding that motivated it.',
    ],
  },
  'concept.risk-colors': {
    title: 'Risk colors',
    body: [
      'Red = destructive or configuration-changing (deletes, settings writes) — read the plan carefully.',
      'Amber = maintenance that can briefly lock or slow things (e.g. VACUUM).',
      'Green = safe, low-impact housekeeping (e.g. ANALYZE, read-only checks).',
    ],
  },
  'concept.action-items': {
    title: 'Action items',
    body: [
      'When a sensor agent finds work worth doing, it proposes structured action items: title, reasoning, evidence, risk color, and — when the work maps exactly to the actuator catalog — a ready-to-plan action + target.',
      'Nothing is planned or executed at this stage. You check the items you want and hand them to the Ops Actuator, which plans each one fresh so blast radius and tokens are current at approval time.',
    ],
  },
  'concept.handoff': {
    title: 'Agent → actuator handoff',
    body: [
      'Checked action items are sent to the Ops Actuator as one batch message. The actuator plans every item (one plan card each) and then waits.',
      'You stay in the loop exactly where it matters: each plan still needs your explicit approval before execution, individually or via "Approve all".',
    ],
  },

  /* ── actuator actions (blast radius per catalog entry) ──────────────── */
  'action.project-delete': {
    title: 'project-delete',
    body: [
      'Backs the project up as a zip into a managed folder, then deletes it. The plan shows size, owner, inactivity and warns when the project is not on the inactive list.',
      'Irreversible apart from the zip backup — red risk, always.',
    ],
  },
  'action.code-env-delete': {
    title: 'code-env-delete',
    body: [
      'Backs up a code environment definition, then deletes it. The plan lists every project still using the env — deleting a used env breaks those projects.',
    ],
  },
  'action.image-delete': {
    title: 'image-delete',
    body: [
      'Deletes container images older than a cutoff from your registry (e.g. ECR). The plan includes a dry-run of exactly which images match. Frees registry storage; running workloads are unaffected.',
    ],
  },
  'action.db-vacuum': {
    title: 'db-vacuum',
    body: [
      'PostgreSQL VACUUM on one runtime-database table: reclaims space held by dead rows. Takes brief locks — amber risk, best in maintenance windows.',
    ],
  },
  'action.db-analyze': {
    title: 'db-analyze',
    body: [
      'PostgreSQL ANALYZE on one table: refreshes planner statistics so queries pick good plans. Cheap and safe — green risk.',
    ],
  },
  'action.plugin-deploy': {
    title: 'plugin-deploy',
    body: [
      'Deploys a plugin from this hub instance to another DSS host in the fleet. The plan shows the plugin version and whether it is a dev plugin.',
    ],
  },
  'action.k8s-exec-config-tune': {
    title: 'k8s-exec-config-tune',
    body: [
      'Right-sizes the CPU/memory requests and limits of one containerized execution config. The plan shows current vs proposed values and warns on cuts big enough to throttle or OOM-kill workloads.',
      'Affects new workloads using that config; running ones keep their old resources until restarted.',
    ],
  },

  /* ── tools (sensor + actuator surface) ──────────────────────────────── */
  'tool.triage_sweep': {
    title: 'triage_sweep',
    body: [
      'Deterministic fleet triage: scores every configured host with the toolkit health score, ranks worst-first and flags hosts under the threshold. The agent must use this ranking, not invent its own.',
    ],
  },
  'tool.propose_action_items': {
    title: 'propose_action_items',
    body: [
      'How a sensor agent turns findings into the checklist you see: up to 10 structured items, validated server-side (invalid actions become advisory, never silently dropped).',
    ],
  },
  'tool.plan_admin_action': {
    title: 'plan_admin_action',
    body: [
      'Builds the read-only dry run for one admin action: real targets, blast radius, warnings, backup destination — plus the signed confirm token that makes approval binding.',
    ],
  },
  'tool.execute_admin_action': {
    title: 'execute_admin_action',
    body: [
      'Executes a planned action. Refused unless ALL gates pass: master kill switch on, agent allowed to execute, action on the agent allowlist, explicit confirm, and a valid unexpired token matching the exact plan.',
    ],
  },
  'tool.list_hosts': {
    title: 'list_hosts',
    body: ['Lists the DSS hosts the toolkit can reach (id, label, url), optionally probing reachability.'],
  },
  'tool.instance_health': {
    title: 'instance_health',
    body: ['Health snapshot of one host: system checks, sanity checks, Java memory, issues, and optionally the full 0–100 health score.'],
  },
  'tool.adoption_metrics': {
    title: 'adoption_metrics',
    body: ['Adoption and engagement metrics from persistent project git history: activity trends, top builders, cohorts — reliable far beyond audit-log retention.'],
  },
  'tool.compute_cost': {
    title: 'compute_cost',
    body: ['Compute + LLM cost from Compute Resource Usage audit records, grouped by project, user or context type. Span limited to audit retention.'],
  },
  'tool.config_inspect': {
    title: 'config_inspect',
    body: ['Inspects one config domain — connections, code envs, plugins or LLM Mesh — with health or usage detail.'],
  },
  'tool.log_errors': {
    title: 'log_errors',
    body: ['Groups recent backend.log errors by signature; can also grep the raw tail with a custom pattern.'],
  },
  'tool.storage_footprint': {
    title: 'storage_footprint',
    body: ['Project storage totals, largest projects, and inactive+large cleanup candidates. A heavy scan — may report scan_running while warming.'],
  },
  'tool.usage_analytics': {
    title: 'usage_analytics',
    body: ['Persistent Story analytics: user activity, event counts, licenses, inventory — trends that outlive audit-log retention.'],
  },
  'tool.k8s_health': {
    title: 'k8s_health',
    body: ['Kubernetes clusters for a host: states plus a reachability sweep; can run a deep audit of one cluster.'],
  },
  'tool.db_health': {
    title: 'db_health',
    body: ['Runtime-database PostgreSQL health: overview, biggest/bloated tables, or per-project usage.'],
  },
};

export function eduEntry(id: string | undefined): EduEntry | null {
  if (!id) return null;
  return EDU[id] || null;
}
