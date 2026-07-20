// Prompt library for the Agents page: dozens of curated prompts organized in
// two headline groups (Triage, Scoping) plus an Actions group, each mapped to
// the backend specialist that handles it. The UI presents ONE agent; the
// group on a prompt is the routing signal that picks the specialist under
// the hood (specialists are matched by NAME substring — ids differ per
// provisioning). The landing page features a hand-picked subset via
// HERO_CARDS below — the library modal always shows the full catalog.

/** Which backend specialist a prompt (or free-form message) is routed to. */
export type AgentRole = 'triage' | 'scoping' | 'actuator';

export interface CatalogPrompt {
  id: string;
  label: string;
  prompt: string;
}

export interface CatalogSection {
  id: string;
  title: string;
  blurb: string;
  eduId?: string;
  prompts: CatalogPrompt[];
}

export interface CatalogGroup {
  role: AgentRole;
  title: string;
  blurb: string;
  megapromptTitle: string;
  megapromptBlurb: string;
  megaprompt: string;
  sections: CatalogSection[];
}

const TRIAGE_MEGAPROMPT = `Run an exhaustive fleet audit — every host, every domain. Step by step:
1. triage_sweep once for the deterministic fleet ranking.
2. For EVERY host (worst first): instance_health (with issues), log_errors (top groups), storage_footprint (largest + inactive projects), db_health (overview, then worst tables), k8s_health (cluster states), config_inspect for connections, code-envs, plugins AND llms.
3. Cross-reference: which findings reinforce each other (e.g. a full disk + a bloated runtime DB + vacuum-hungry tables)?
Report per host: score, top issues with evidence citations, then a fleet-level summary ordered by your severity rubric — always-lead criticals first (H2 runtime DB, DIP_HOME on NFS, missing cgroups, data mount ≥75%, recently-broken active connections, deprecated Python in use, exec configs without limits, >1h retry storms), then the rest, medium+ only.
Finish by calling propose_action_items with EVERY concrete piece of admin work you found — exact actions and targets where they map to the actuator catalog, advisory items otherwise, honest risk colors, evidence on every item.`;

const SCOPING_MEGAPROMPT = `Build a full scoping dossier of this fleet — every tool, every host. Cover:
1. Hosts and reachability (list_hosts probe=true).
2. Instance health and sizing signals per host (instance_health).
3. Project landscape: storage totals, largest and inactive projects (storage_footprint).
4. Compute + LLM cost by project and context type (compute_cost).
5. Configuration: connections, code envs, plugins, LLM Mesh (config_inspect, each domain).
6. Kubernetes capability and cluster states (k8s_health).
7. Runtime database health (db_health).
Structure the dossier: executive summary → per-domain findings with citations → gaps ("not observable from the toolkit") → risks and recommendations. Apply your severity rubric throughout: always-lead criticals open the risk section, medium+ floor, cost-class findings (image sprawl, oversized containers, idle capacity) reported as cost, never as health.
Close with propose_action_items for any admin work your findings imply.`;

const ACTUATOR_MEGAPROMPT = `Take a full maintenance-opportunity inventory of this instance — DO NOT plan or execute anything yet, this pass is read-only. Sweep:
1. storage_footprint: large + inactive projects (project-delete candidates, with backup notes).
2. config_inspect code-envs: unused or duplicate code envs (code-env-delete candidates).
3. db_health: tables with the most dead tuples (db-vacuum) and stale-stats tables (db-analyze).
4. compute_cost + instance_health: oversized containerized execution configs (k8s-exec-config-tune candidates).
5. config_inspect plugins: version drift across hosts (plugin-deploy candidates).
Present a prioritized list — most value first, medium+ severity only (whitelist-suppressed findings are already removed from your data — treat everything you see as live) — with the evidence, the exact action + target you would plan for each, and the risk color. Then STOP and wait: I will tell you which ones to plan.`;

const COST_MEGAPROMPT = `Run a full cost review of this instance — where the money goes and what it would take to shrink it. Step by step:
1. compute_cost grouped by project, by user, and by context type — top spenders with numbers, and the span the data covers.
2. LLM spend: LLM usage cost from compute_cost, plus config_inspect domain=llms for the connections and models behind it.
3. Storage as cost: storage_footprint — the largest projects and how much is inactive dead weight.
4. Kubernetes: k8s_health cluster states, plus containerized execution configs vs observed usage — flag oversized configs and idle capacity.
Report where the money goes, then the top saving opportunities ranked by impact, each with evidence citations. Cost-class findings (image sprawl, oversized containers, idle capacity) stay cost, never health. Close with propose_action_items for the savings that map to concrete admin work.`;

export const PROMPT_GROUPS: readonly CatalogGroup[] = [
  {
    role: 'triage',
    title: 'Health & Triage',
    blurb: 'Health sweeps over logs, storage, database, Kubernetes and config — find what is broken or about to break.',
    megapromptTitle: 'Full instance audit',
    megapromptBlurb:
      'Sweeps every domain — health, logs, storage, database, Kubernetes, config, LLM Mesh — and ends with a ready-to-action checklist.',
    megaprompt: TRIAGE_MEGAPROMPT,
    sections: [
      {
        id: 'fleet',
        title: 'Fleet sweep',
        blurb: 'The whole fleet at a glance, ranked by urgency.',
        eduId: 'tool.triage_sweep',
        prompts: [
          { id: 'fleet-sweep', label: 'Fleet health sweep', prompt: 'Run a fleet health sweep and give me the triage report — worst host first, with evidence for every claim.' },
          { id: 'fleet-risks', label: 'Top risks right now', prompt: 'What are the top 5 risks across the fleet right now? Rank by how likely they are to cause an outage or data loss, and cite the evidence.' },
          { id: 'fleet-compare', label: 'Compare all hosts', prompt: 'Compare all hosts side by side: health score, top issue, error volume, storage pressure, DB health. One row per host, then tell me which host needs attention first and why.' },
          { id: 'fleet-quiet', label: 'Anything I missed this week?', prompt: 'Assume I have not looked at these instances for a week. What changed or degraded that I should know about? Check health, logs and database on every host.' },
          { id: 'fleet-outage', label: 'Is anything about to break?', prompt: 'Hunt for early failure signals on every host: disks filling up, error bursts in the logs, DB bloat, unreachable clusters. Tell me what will break first if nothing is done.' },
        ],
      },
      {
        id: 'storage',
        title: 'Storage',
        blurb: 'Who is eating the disk, and what is safe to clean.',
        eduId: 'tool.storage_footprint',
        prompts: [
          { id: 'st-hogs', label: 'Biggest storage hogs', prompt: 'Which projects use the most storage on this host? Top 10 with sizes and owners.' },
          { id: 'st-cleanup', label: 'Cleanup candidates', prompt: 'Find projects that are both large AND inactive — the best cleanup candidates. For each: size, owner, days inactive. End with action items for the ones you would clean up.' },
          { id: 'st-growth', label: 'Storage pressure check', prompt: 'How much storage do projects use in total on this host, and how does it split across the biggest projects? Is any single project dominating?' },
          { id: 'st-fleetwide', label: 'Fleet-wide storage', prompt: 'Check the storage footprint on EVERY host and tell me where the pressure is worst. Cite totals per host.' },
        ],
      },
      {
        id: 'database',
        title: 'Database',
        blurb: 'Runtime PostgreSQL health — bloat, dead tuples, size.',
        eduId: 'tool.db_health',
        prompts: [
          { id: 'db-overview', label: 'Runtime DB overview', prompt: 'Give me a runtime database health overview for this host: total size, biggest tables, anything abnormal.' },
          { id: 'db-vacuum', label: 'Tables needing vacuum', prompt: 'Which runtime DB tables have the most dead tuples and need a VACUUM? List the worst 5 with dead-tuple counts and sizes, then propose action items for the worst ones.' },
          { id: 'db-perproject', label: 'DB usage by project', prompt: 'Break down runtime database usage per project. Which projects dominate, and is that expected given their activity?' },
          { id: 'db-fleet', label: 'DB health, all hosts', prompt: 'Check runtime database health on every host and rank them by how badly they need maintenance.' },
        ],
      },
      {
        id: 'kubernetes',
        title: 'Kubernetes',
        blurb: 'Cluster states, reachability, deep audits.',
        eduId: 'tool.k8s_health',
        prompts: [
          { id: 'k8s-states', label: 'Cluster states', prompt: 'List all Kubernetes clusters on this host with their states and reachability. Flag anything not running or unreachable.' },
          { id: 'k8s-audit', label: 'Deep-audit a cluster', prompt: 'Run a deep audit of the most important Kubernetes cluster on this host and summarize what it found — capacity, versions, anything concerning.' },
          { id: 'k8s-fleet', label: 'K8s across the fleet', prompt: 'Check Kubernetes health on every host: which clusters exist, which are reachable, which are stopped or broken?' },
        ],
      },
      {
        id: 'logs',
        title: 'Logs & errors',
        blurb: 'What the backend log is screaming about.',
        eduId: 'tool.log_errors',
        prompts: [
          { id: 'log-top', label: 'Top error groups', prompt: 'What are the top error groups in the backend log on this host? For each: signature, count, and what it likely means.' },
          { id: 'log-new', label: 'New or unusual errors', prompt: 'Look at the backend log error groups and tell me which ones look unusual or serious (not routine noise). Explain your reasoning.' },
          { id: 'log-oom', label: 'Memory / OOM hunt', prompt: 'Grep the backend log for memory pressure: pattern "OutOfMemory|GC overhead|heap". Anything found? How serious?' },
          { id: 'log-fleet', label: 'Errors across the fleet', prompt: 'Check backend log errors on every host and rank hosts by error severity. Which host has the scariest log right now?' },
        ],
      },
      {
        id: 'config',
        title: 'Config & plugins',
        blurb: 'Connections, code envs, plugins, projects — health, drift and dataset cleanup grounding.',
        eduId: 'tool.config_inspect',
        prompts: [
          { id: 'cfg-conn', label: 'Connection health', prompt: 'Inspect all connections on this host (detail=health). Which ones are broken or misconfigured?' },
          { id: 'cfg-envs', label: 'Code env health', prompt: 'Inspect code envs on this host: which are unused, duplicated, or broken? Propose action items for the ones worth deleting.' },
          { id: 'cfg-plugins', label: 'Plugin inventory', prompt: 'List installed plugins on this host with versions. Anything outdated or unused?' },
          { id: 'cfg-drift', label: 'Config drift between hosts', prompt: 'Compare plugins and code envs across all hosts. Where has the configuration drifted (different versions, missing plugins)?' },
        ],
      },
      {
        id: 'llm',
        title: 'LLM Mesh',
        blurb: 'LLM connections, usage and cost.',
        eduId: 'tool.compute_cost',
        prompts: [
          { id: 'llm-inventory', label: 'LLM Mesh inventory', prompt: 'Inspect the LLM Mesh on this host (config_inspect domain=llms): which connections and models are configured, and are they healthy?' },
          { id: 'llm-cost', label: 'LLM cost check', prompt: 'What are we spending on LLMs? Pull compute_cost grouped by context type and highlight LLM usage cost, per project if possible.' },
          { id: 'llm-usage', label: 'Who uses the LLM Mesh?', prompt: 'Which projects and users drive LLM Mesh usage on this host? Use compute_cost; cite the span the data covers.' },
        ],
      },
    ],
  },

  {
    role: 'scoping',
    title: 'Scoping & Architecture',
    blurb: 'Sizing, migration, capacity and cost questions — build the dossier a field engineer needs.',
    megapromptTitle: 'Full scoping dossier',
    megapromptBlurb:
      'A complete instance dossier — hosts, health, projects, cost, config, Kubernetes, database — with citations and explicit gaps.',
    megaprompt: SCOPING_MEGAPROMPT,
    sections: [
      {
        id: 'projects',
        title: 'Projects',
        blurb: 'The project landscape: size, activity, concentration.',
        eduId: 'tool.storage_footprint',
        prompts: [
          { id: 'pr-landscape', label: 'Project landscape', prompt: 'Describe the project landscape on this host: how many projects, size distribution, largest ones, share that is inactive.' },
          { id: 'pr-dominant', label: 'Dominant projects', prompt: 'Which projects dominate storage and compute? Cross-reference storage_footprint and compute_cost and name the top 5 with numbers.' },
          { id: 'pr-dead', label: 'Dead weight', prompt: 'How much of this instance is dead weight — large inactive projects nobody touches? Quantify it and estimate what cleanup would reclaim.' },
          { id: 'pr-migration', label: 'Migration sizing', prompt: 'If we had to migrate this instance to new infrastructure, what would we move? Project count, total storage, code envs, plugins, connections — a migration-scoping summary with citations.' },
        ],
      },
      {
        id: 'cost',
        title: 'Compute cost',
        blurb: 'Where the compute and LLM money goes.',
        eduId: 'tool.compute_cost',
        prompts: [
          { id: 'co-byproject', label: 'Cost by project', prompt: 'Break down compute cost by project on this host. Who are the top spenders, and what is the span of the data?' },
          { id: 'co-byuser', label: 'Cost by user', prompt: 'Break down compute cost by user. Is spend concentrated in a few power users?' },
          { id: 'co-context', label: 'Cost by workload type', prompt: 'Split compute cost by context type (jobs, notebooks, webapps, LLM…). Which workload class dominates, and does that match how the instance is supposed to be used?' },
          { id: 'co-k8s', label: 'K8s spend sanity check', prompt: 'How much of the compute cost is Kubernetes workloads? Given the observed usage, do the containerized execution configs look right-sized?' },
        ],
      },
      {
        id: 'envs',
        title: 'Envs & plugins',
        blurb: 'Code environments and plugin estate.',
        eduId: 'tool.config_inspect',
        prompts: [
          { id: 'env-estate', label: 'Code env estate', prompt: 'Map the code environment estate: how many envs, which languages/versions, usage counts, how many are unused?' },
          { id: 'env-python', label: 'Python version story', prompt: 'What Python versions are in use across code envs, and is there legacy debt (old interpreters, abandoned envs)?' },
          { id: 'env-plugins', label: 'Plugin estate', prompt: 'Inventory the plugins: which are installed, which versions, which look unused? Anything that would complicate an upgrade?' },
          { id: 'env-drift', label: 'Cross-host drift', prompt: 'Compare code envs and plugins across all hosts. Report the drift: what exists where, version mismatches, and what harmonizing would take.' },
        ],
      },
      {
        id: 'capacity',
        title: 'Capacity',
        blurb: 'Sizing, headroom and infrastructure capability.',
        eduId: 'tool.instance_health',
        prompts: [
          { id: 'cap-now', label: 'Current sizing & headroom', prompt: 'Assess the current sizing of this host: memory, system state, health issues. How much headroom is left before we need to scale?' },
          { id: 'cap-k8s', label: 'K8s capability', prompt: 'What Kubernetes capability does this fleet have? Clusters, states, what runs containerized today — and what a team planning heavy container workloads should know.' },
          { id: 'cap-db', label: 'Runtime DB capacity', prompt: 'How big is the runtime database and how fast is it growing (bloat, biggest tables)? Will it become a problem?' },
          { id: 'cap-bottleneck', label: 'Next bottleneck', prompt: 'Based on everything observable — health, storage, DB, cost — what is the next bottleneck this instance will hit, and what would you do about it?' },
        ],
      },
    ],
  },

  {
    role: 'actuator',
    title: 'Admin Actions',
    blurb: 'Plan and (with your approval) execute maintenance: cleanup, vacuum, right-sizing, deploys.',
    megapromptTitle: 'Maintenance inventory',
    megapromptBlurb:
      'Read-only sweep of everything worth maintaining — cleanup, vacuum, right-sizing, deploys — prioritized, with explicit "nothing executes yet".',
    megaprompt: ACTUATOR_MEGAPROMPT,
    sections: [
      {
        id: 'projects',
        title: 'Project cleanup',
        blurb: 'Find, plan and (with your approval) delete dead projects.',
        eduId: 'action.project-delete',
        prompts: [
          { id: 'pc-find', label: 'Find cleanup candidates', prompt: 'Find the best project cleanup candidates: large AND inactive. List them with size, owner and days inactive — do not plan anything yet.' },
          { id: 'pc-worst', label: 'Plan the worst offender', prompt: 'Find the largest inactive project on this host and plan its cleanup (project-delete). Show me the full plan with blast radius and backup destination.' },
          { id: 'pc-verify', label: 'Check a specific project', prompt: 'I am considering deleting a project. Ask me for the key, then check its size, owner, activity and whether anything would break, and plan the delete only if it looks safe.' },
        ],
      },
      {
        id: 'codeenvs',
        title: 'Code-env hygiene',
        blurb: 'Unused environments are upgrade debt.',
        eduId: 'action.code-env-delete',
        prompts: [
          { id: 'ce-unused', label: 'Find unused envs', prompt: 'Which code envs have zero usage? List them with language and version — no planning yet.' },
          { id: 'ce-plan', label: 'Plan an env delete', prompt: 'Pick the most clearly-unused code env and plan its deletion. Show the usage evidence and the backup destination in the plan.' },
          { id: 'ce-dupes', label: 'Duplicate env check', prompt: 'Look for near-duplicate code envs (same language, similar purpose). Which could be consolidated, and what would that break?' },
        ],
      },
      {
        id: 'images',
        title: 'Container images',
        blurb: 'Registry housekeeping — old images cost storage.',
        eduId: 'action.image-delete',
        prompts: [
          { id: 'im-what', label: 'What would image cleanup do?', prompt: 'Explain what the image-delete action does on this instance, what evidence you would gather first, and what a safe cutoff would be. Do not plan yet.' },
          { id: 'im-plan', label: 'Plan an image cleanup', prompt: 'Plan a container-image cleanup for images older than 90 days. The plan must include the dry-run list of exactly which images match before I decide.' },
        ],
      },
      {
        id: 'db',
        title: 'DB maintenance',
        blurb: 'Vacuum and analyze — the boring work that keeps DSS fast.',
        eduId: 'action.db-vacuum',
        prompts: [
          { id: 'db-worst', label: 'Plan worst-table vacuum', prompt: 'Which runtime DB tables need a VACUUM most? Plan the worst one (db-vacuum) and show me dead tuples, row count and size in the plan.' },
          { id: 'db-analyze', label: 'Plan an ANALYZE', prompt: 'Find the table whose planner statistics look most stale and plan a db-analyze on it. ANALYZE is cheap — but show me the plan anyway.' },
          { id: 'db-batch', label: 'Batch DB maintenance', prompt: 'Identify the 3 tables that most need maintenance (vacuum or analyze). Plan all 3 — one plan each — and present them together so I can approve them as a batch.' },
        ],
      },
      {
        id: 'plugins',
        title: 'Plugin deploys',
        blurb: 'Ship a hub plugin to the rest of the fleet.',
        eduId: 'action.plugin-deploy',
        prompts: [
          { id: 'pd-drift', label: 'Find version drift', prompt: 'Compare plugin versions between this hub and the other hosts. Where is a host running an older version of a plugin the hub has?' },
          { id: 'pd-plan', label: 'Plan a plugin deploy', prompt: 'Plan deploying the admin-toolkit plugin from the hub to another host (plugin-deploy). Show version and dev-status in the plan.' },
        ],
      },
      {
        id: 'k8s',
        title: 'K8s tuning',
        blurb: 'Right-size containerized execution configs.',
        eduId: 'action.k8s-exec-config-tune',
        prompts: [
          { id: 'kt-review', label: 'Review exec configs', prompt: 'Review the containerized execution configs against observed K8s compute usage. Which ones look oversized or undersized? Evidence first, no planning yet.' },
          { id: 'kt-plan', label: 'Plan a right-size', prompt: 'Pick the most clearly-oversized execution config and plan a k8s-exec-config-tune. The plan must show current vs proposed values and any throttling/OOM warnings.' },
        ],
      },
      {
        id: 'drills',
        title: 'Safety drills',
        blurb: 'Learn the gates by watching them refuse things.',
        eduId: 'concept.kill-switch',
        prompts: [
          { id: 'sd-gates', label: 'Explain your safety gates', prompt: 'Explain every safety gate between me asking for an action and it actually executing. What blocks what, and who can override each gate?' },
          { id: 'sd-noconfirm', label: 'Drill: execute without approval', prompt: 'SAFETY DRILL: plan a db-analyze on any small table, then attempt to execute it WITHOUT waiting for my approval. Report exactly which gate refused you and why. (I expect this to be refused.)' },
          { id: 'sd-drift', label: 'Drill: tampered target', prompt: 'SAFETY DRILL: plan a db-analyze on one table, then attempt to execute it against a DIFFERENT table with the same token. Report exactly what happens. (I expect the token check to refuse.)' },
          { id: 'sd-expired', label: 'What happens on expiry?', prompt: 'Explain what happens if I approve a plan after its confirm token expired, and what the correct recovery is.' },
        ],
      },
    ],
  },
];

export function groupForRole(role: AgentRole): CatalogGroup {
  // PROMPT_GROUPS covers every AgentRole — the find can't miss.
  return PROMPT_GROUPS.find((group) => group.role === role) as CatalogGroup;
}

/** The specialist an agent-instance name maps to (names are the stable
 * identity across installs; ids differ per provisioning). */
export function roleForAgentName(agentName: string | undefined): AgentRole | null {
  if (!agentName) return null;
  if (/actuator/i.test(agentName)) return 'actuator';
  if (/scoping/i.test(agentName)) return 'scoping';
  if (/triage/i.test(agentName)) return 'triage';
  return null;
}

// ── landing-page hero cards ─────────────────────────────────────────────────
// Four themed columns on the empty-transcript landing. Three mirror the
// catalog groups; Cost & Usage is a cross-cutting cut through the same
// catalog with its own flagship. `prompts` is a hand-picked feature list —
// question-shaped prompts that show judgment, not inventory dumps.

export interface HeroCard {
  id: string;
  title: string;
  blurb: string;
  flagshipTitle: string;
  flagshipBlurb: string;
  flagshipPrompt: string;
  prompts: CatalogPrompt[];
}

const ALL_PROMPTS: readonly CatalogPrompt[] = PROMPT_GROUPS.flatMap((group) =>
  group.sections.flatMap((section) => section.prompts),
);

function pick(...ids: string[]): CatalogPrompt[] {
  return ids.flatMap((id) => ALL_PROMPTS.filter((p) => p.id === id));
}

export const HERO_CARDS: readonly HeroCard[] = (() => {
  const triage = groupForRole('triage');
  const scoping = groupForRole('scoping');
  const actuator = groupForRole('actuator');
  return [
    {
      id: 'triage',
      title: triage.title,
      blurb: 'Find what is broken or about to break — logs, storage, database, Kubernetes, config.',
      flagshipTitle: triage.megapromptTitle,
      flagshipBlurb: triage.megapromptBlurb,
      flagshipPrompt: triage.megaprompt,
      prompts: pick('fleet-outage', 'fleet-risks', 'fleet-quiet', 'log-new', 'cfg-conn'),
    },
    {
      id: 'scoping',
      title: scoping.title,
      blurb: 'Sizing, migration and capacity — the dossier a field engineer needs.',
      flagshipTitle: scoping.megapromptTitle,
      flagshipBlurb: scoping.megapromptBlurb,
      flagshipPrompt: scoping.megaprompt,
      prompts: pick('pr-migration', 'cap-bottleneck', 'cap-now', 'env-python', 'pr-landscape'),
    },
    {
      id: 'cost',
      title: 'Cost & Usage',
      blurb: 'Where compute, LLM and storage money goes — and the savings hiding in it.',
      flagshipTitle: 'Full cost review',
      flagshipBlurb:
        'Compute, LLM, storage and Kubernetes spend in one pass — top spenders, idle capacity, and the biggest saving opportunities.',
      flagshipPrompt: COST_MEGAPROMPT,
      prompts: pick('co-byproject', 'co-context', 'llm-cost', 'st-hogs', 'co-byuser'),
    },
    {
      id: 'actions',
      title: actuator.title,
      blurb: 'Plan and — with your approval — execute maintenance: cleanup, vacuum, right-sizing, deploys.',
      flagshipTitle: actuator.megapromptTitle,
      flagshipBlurb: actuator.megapromptBlurb,
      flagshipPrompt: actuator.megaprompt,
      prompts: pick('pc-find', 'db-worst', 'ce-unused', 'kt-review', 'sd-gates'),
    },
  ];
})();

// ── composer slash-palette ──────────────────────────────────────────────────

export interface PaletteEntry {
  id: string;
  label: string;
  prompt: string;
  /** Where the prompt lives in the catalog — shown as the row's origin chip. */
  section: string;
  mega?: boolean;
}

// Flattened catalog, megaprompts first — the palette's discovery order when
// the query is still empty ("/" just typed).
const PALETTE_ENTRIES: PaletteEntry[] = (() => {
  const out: PaletteEntry[] = [];
  for (const card of HERO_CARDS) {
    out.push({
      id: `${card.id}-mega`,
      label: card.flagshipTitle,
      prompt: card.flagshipPrompt,
      section: card.title,
      mega: true,
    });
  }
  for (const group of PROMPT_GROUPS) {
    for (const section of group.sections) {
      for (const p of section.prompts) {
        out.push({ id: p.id, label: p.label, prompt: p.prompt, section: section.title });
      }
    }
  }
  return out;
})();

const MAX_PALETTE_MATCHES = 8;

/** Rank: label prefix → label substring → prompt/section substring. */
export function filterPaletteEntries(query: string): PaletteEntry[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return PALETTE_ENTRIES.slice(0, MAX_PALETTE_MATCHES);
  const scored: { entry: PaletteEntry; score: number }[] = [];
  for (const entry of PALETTE_ENTRIES) {
    const label = entry.label.toLowerCase();
    const score = label.startsWith(needle)
      ? 0
      : label.includes(needle)
        ? 1
        : entry.prompt.toLowerCase().includes(needle) || entry.section.toLowerCase().includes(needle)
          ? 2
          : -1;
    if (score >= 0) scored.push({ entry, score });
  }
  scored.sort((a, b) => a.score - b.score);
  return scored.slice(0, MAX_PALETTE_MATCHES).map((s) => s.entry);
}
