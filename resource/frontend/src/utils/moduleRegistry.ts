import type { Lifecycle, ParsedData, PageId } from '../types';

// A module's lifecycle is the aggregate of one or more Lifecycle-typed fields
// on ParsedData. The set of valid keys is derived directly from ParsedData —
// adding a Lifecycle field there is the only place a new lifecycle key is
// declared. Two pages today join data from multiple sources (Users and
// Connections → Insights); every other module lists a single field. The
// resolver and the analysis aggregator share the same composition rule.
type LifecycleKeys = {
  [K in keyof ParsedData]-?: NonNullable<ParsedData[K]> extends Lifecycle ? K : never;
}[keyof ParsedData];

export type LifecycleFieldName = Extract<LifecycleKeys, `${string}Loading`>;

export interface LifecycleSource {
  fields: readonly [LifecycleFieldName, ...LifecycleFieldName[]];
}

// Availability — is the module applicable on this host at all? 'always' is
// unconditional; every other policy names a definitive absence signal that
// hides the module from the nav surfaces (Sidebar, ⌘K) — never from
// PageRouter, so an open page is never yanked away. Resolution semantics live
// in utils/pageAvailability.ts: hide ONLY on settled, definitive absence;
// unknown / loading / error keeps the module visible.
export type ModuleAvailabilityPolicy =
  | 'always'
  | 'clusters'            // no K8s clusters registered in DSS
  | 'container-exec'      // no containerized-execution configs & both defaults local
  | 'container-registry'  // no Docker registry provider detected
  | 'llm'                 // LLM audit settled empty (no LLM connections/usage)
  | 'runtime-db';         // no Postgres runtime-DB connection configured

export interface ModuleDefinition {
  id: PageId;
  label: string;
  navLabel?: string;
  commandLabel?: string;
  section: string;
  navSection: string;
  keywords: string[];
  availability: ModuleAvailabilityPolicy;
  lifecycle: LifecycleSource;
  // Action pages (manual advanced tools) carry no sidebar load glyph and are
  // excluded from the global "Analysis complete" aggregate: their lifecycle
  // field exists only to drive in-page UI, never a startup ritual.
  noLoadGlyph?: true;
  streamEndpoint?: string;
  experimental?: boolean;
  deprecated?: boolean;
  tool?: boolean;
  badge?: 'logs';
}

export interface ModuleNavSection {
  title: string;
  items: PageId[];
  /** Marks the whole section as experimental — renders an `exp` badge on its header. */
  experimental?: boolean;
}

export const MODULES: readonly ModuleDefinition[] = [
  // OVERVIEW
  { id: 'mission-control', label: 'Mission Control', section: 'Overview', navSection: 'OVERVIEW', keywords: ['mission', 'control', 'wall', 'noc', 'dashboard', 'all'], availability: 'always', lifecycle: { fields: [
    'summaryLoading',
    'filesystemLoading',
    'memoryLoading',
    'connectionsInventoryLoading',
    'projectFootprintLoading',
    'usersLoading',
  ] } },
  { id: 'summary', label: 'Summary', section: 'Overview', navSection: 'OVERVIEW', keywords: ['health', 'score', 'overview', 'dashboard'], availability: 'always', lifecycle: { fields: ['summaryLoading'] } },
  { id: 'filesystem', label: 'Filesystem', section: 'Overview', navSection: 'OVERVIEW', keywords: ['disk', 'storage', 'mount', 'partition'], availability: 'always', lifecycle: { fields: ['filesystemLoading'] } },
  // Live page (SSE stream / poll chain, always ready): noLoadGlyph keeps the
  // sidebar row glyph-free and out of the global "Analysis complete" aggregate.
  { id: 'resources', label: 'Resources', section: 'Overview', navSection: 'OVERVIEW', keywords: ['ram', 'swap', 'memory', 'cpu', 'usage', 'pid', 'process', 'load', 'live', 'resources'], availability: 'always', noLoadGlyph: true, lifecycle: { fields: ['memoryLoading', 'cpuLoading'] } },

  // CONNECTIONS
  { id: 'connections-inventory', label: 'Inventory', section: 'Connections', navSection: 'CONNECTIONS', keywords: ['database', 'connector', 'type', 'inventory'], availability: 'always', lifecycle: { fields: ['connectionsInventoryLoading'] } },
  { id: 'connections-insights', label: 'Insights', section: 'Connections', navSection: 'CONNECTIONS', keywords: ['connection', 'insights', 'matrix', 'audit', 'usage', 'consumption', 'health', 'projects'], availability: 'always', lifecycle: { fields: [
    'connectionsInventoryLoading',
    'connectionUsageLoading',
    'connectionsHealthLoading',
    'connectionsAuditLoading',
  ] } },
  { id: 'connections-health', label: 'Health', section: 'Connections', navSection: 'CONNECTIONS', keywords: ['connection', 'test', 'health', 'diagnostic'], availability: 'always', lifecycle: { fields: ['connectionsHealthLoading'] } },
  { id: 'connections-fs-migration', label: 'FS Migration', section: 'Connections', navSection: 'CONNECTIONS', keywords: ['filesystem', 'migration', 'local', 'fs', 'outreach', 'owner'], tool: true, availability: 'always', lifecycle: { fields: ['connectionUsageLoading'] } },

  // PROJECTS
  { id: 'project-cleaner', label: 'Project Cleaner', navLabel: 'Cleaner', section: 'Projects', navSection: 'PROJECTS', keywords: ['clean', 'delete', 'inactive', 'project'], tool: true, availability: 'always', lifecycle: { fields: ['projectCleanerLoading'] } },
  { id: 'projects', label: 'Projects', navLabel: 'Insights', section: 'Projects', navSection: 'PROJECTS', keywords: ['project', 'footprint', 'permissions'], availability: 'always', lifecycle: { fields: ['projectFootprintLoading'] } },
  { id: 'project-compute', label: 'Compute', section: 'Projects', navSection: 'PROJECTS', keywords: ['compute', 'project', 'usage', 'workload'], availability: 'always', lifecycle: { fields: ['projectComputeLoading'] } },
  { id: 'project-cost', label: 'Cost / CRU', navLabel: 'Cost', section: 'Projects', navSection: 'PROJECTS', keywords: ['cost', 'cru', 'compute', 'resource', 'usage', 'memory', 'cpu', 'llm', 'audit'], streamEndpoint: '/api/cru/stream', availability: 'always', lifecycle: { fields: ['projectCostLoading'] } },

  // USERS
  { id: 'users', label: 'Users', section: 'Users', navSection: 'USERS', keywords: ['user', 'owner', 'login', 'email', 'accountability', 'ownership'], availability: 'always', lifecycle: { fields: [
    'usersLoading',
    'projectFootprintLoading',
    'codeEnvsLoading',
    'llmAuditLoading',
  ] } },
  // On-demand deep dive (loads on mount, like k8s-insights): noLoadGlyph keeps
  // adoptionLoading out of SHARED_LOADING_FIELDS so the global "Analysis
  // complete" aggregate never waits on a page the user may not visit.
  { id: 'adoption', label: 'Activity', section: 'Users', navSection: 'USERS', keywords: ['adoption', 'activity', 'engagement', 'usage', 'logins', 'active', 'trend', 'cohort', 'retention', 'builders', 'people', 'commits', 'growth'], availability: 'always', noLoadGlyph: true, lifecycle: { fields: ['adoptionLoading', 'adoptionInventoryLoading', 'adoptionEventsLoading'] } },
  // On-demand deep dive (loads on mount, like adoption): noLoadGlyph keeps
  // userChurnLoading out of the global "Analysis complete" aggregate.
  { id: 'user-churn', label: 'Churn & Seats', navLabel: 'Churn', section: 'Users', navSection: 'USERS', keywords: ['churn', 'license', 'seat', 'reassign', 'reassignment', 'disabled', 'dormant', 'reclaim', 'turnover', 'offboard', 'lifecycle', 'lifespan'], availability: 'always', noLoadGlyph: true, lifecycle: { fields: ['userChurnLoading'] } },

  // PLUGINS
  { id: 'plugins-installed', label: 'Installed', section: 'Plugins', navSection: 'PLUGINS', keywords: ['plugin', 'installed', 'list', 'version', 'projects', 'usage'], availability: 'always', lifecycle: { fields: ['pluginsLoading'] } },
  { id: 'plugins', label: 'Plugin Sync', section: 'Plugins', navSection: 'PLUGINS', keywords: ['plugin', 'sync', 'compare', 'version'], tool: true, availability: 'always', noLoadGlyph: true, lifecycle: { fields: ['pluginSyncLoading'] } },

  // CODE ENVS
  { id: 'code-envs', label: 'Cleaner', section: 'Code Envs', navSection: 'CODE ENVS', keywords: ['python', 'environment', 'package', 'clean', 'delete', 'unused', 'replace', 'migration'], tool: true, availability: 'always', lifecycle: { fields: ['codeEnvsLoading', 'codeEnvSizesLoading', 'codeEnvCleanerLoading', 'codeEnvReplacementLoading'] } },
  { id: 'code-envs-cleaner', label: 'Insights', section: 'Code Envs', navSection: 'CODE ENVS', keywords: ['python', 'environment', 'package', 'clean', 'unused', 'review', 'read-only'], availability: 'always', lifecycle: { fields: ['codeEnvsLoading', 'codeEnvSizesLoading'] } },
  { id: 'code-envs-comparison', label: 'Comparison', section: 'Code Envs', navSection: 'CODE ENVS', keywords: ['compare', 'duplicate', 'version', 'mismatch'], availability: 'always', lifecycle: { fields: ['codeEnvsComparisonLoading'] } },
  // On-demand build-log scan (one log read per env): noLoadGlyph keeps
  // codeEnvsBrokenLoading out of the global "Analysis complete" aggregate.
  { id: 'code-envs-broken', label: 'Broken', section: 'Code Envs', navSection: 'CODE ENVS', keywords: ['broken', 'failed', 'build', 'rebuild', 'upgrade', 'error', 'log', 'remediation', 'llm'], availability: 'always', noLoadGlyph: true, lifecycle: { fields: ['codeEnvsBrokenLoading'] } },

  // AI COMPUTE
  { id: 'container-execs', label: 'Container Execs', section: 'AI Compute', navSection: 'AI COMPUTE', keywords: ['container', 'execution', 'kubernetes', 'k8s', 'compute', 'gpu', 'project', 'recipe', 'webapp'], streamEndpoint: '/api/container-execs/stream', tool: true, availability: 'container-exec', lifecycle: { fields: ['containerExecsLoading'] } },
  { id: 'image-cleaner', label: 'Docker Images', section: 'AI Compute', navSection: 'AI COMPUTE', keywords: ['ecr', 'acr', 'gar', 'docker', 'image', 'container', 'cleanup', 'aws', 'azure', 'gcp', 'registry'], tool: true, availability: 'container-registry', lifecycle: { fields: ['imageCleanerLoading'] } },
  { id: 'cs-template-replacement', label: 'Replace CS Template', navLabel: 'CS Templates', section: 'AI Compute', navSection: 'AI COMPUTE', keywords: ['code', 'studio', 'template', 'replace', 'migrate', 'cs'], tool: true, availability: 'always', noLoadGlyph: true, lifecycle: { fields: ['csTemplateReplacementLoading'] } },
  { id: 'llm-audit', label: 'Model Audit', section: 'AI Compute', navSection: 'AI COMPUTE', keywords: ['llm', 'model', 'audit', 'pricing'], availability: 'llm', lifecycle: { fields: ['llmAuditLoading'] } },
  { id: 'k8s-insights', label: 'K8s Insights', section: 'AI Compute', navSection: 'AI COMPUTE', keywords: ['kubernetes', 'k8s', 'eks', 'cluster', 'gpu', 'cost', 'bin pack', 'autoscaler', 'nodes', 'pods', 'daemonset', 'findings', 'rules'], streamEndpoint: '/api/k8s-insights/stream', availability: 'clusters', noLoadGlyph: true, lifecycle: { fields: ['k8sInsightsLoading'] } },

  // AGENTS — conversational ops surface over the agents plugin (LLM Mesh
  // proxy). Loads per conversation, never through the startup ritual.
  { id: 'agents', label: 'Agents', section: 'Agents', navSection: 'AGENTS', keywords: ['agent', 'chat', 'ops', 'actuator', 'triage', 'plan', 'approve', 'autonomous', 'ai'], availability: 'always', noLoadGlyph: true, lifecycle: { fields: ['agentsLoading'] } },
  { id: 'agent-tuning', label: 'Agent Tuning', navLabel: 'Tuning', section: 'Agents', navSection: 'AGENTS', keywords: ['agent', 'tuning', 'prompt', 'system', 'rubric', 'version', 'customize', 'override'], availability: 'always', noLoadGlyph: true, lifecycle: { fields: ['agentsLoading'] } },
  { id: 'agent-settings', label: 'Agent Permissions', navLabel: 'Permissions', section: 'Agents', navSection: 'AGENTS', keywords: ['agent', 'permissions', 'settings', 'actions', 'gates', 'enable', 'disable', 'allow', 'read', 'write', 'execute', 'catalog'], availability: 'always', noLoadGlyph: true, lifecycle: { fields: ['agentsLoading'] } },
  { id: 'agent-explainer', label: 'How Agents Work', navLabel: 'How it works', commandLabel: 'Agents: How it works', section: 'Agents', navSection: 'AGENTS', keywords: ['agent', 'explainer', 'how', 'works', 'safety', 'guardrails', 'plan', 'confirm', 'token', 'audit', 'sandbox', 'autonomy', 'tour'], availability: 'always', noLoadGlyph: true, lifecycle: { fields: ['agentsLoading'] } },

  // MISC
  { id: 'settings', label: 'Settings', section: 'Misc', navSection: 'MISC', keywords: ['settings', 'mail', 'channel', 'email', 'config', 'preferences'], availability: 'always', lifecycle: { fields: ['settingsLoading'] } },
  { id: 'logs', label: 'Errors', section: 'Misc', navSection: 'MISC', keywords: ['log', 'error', 'exception', 'stack'], badge: 'logs', availability: 'always', lifecycle: { fields: ['logsLoading'] } },
  { id: 'sanity-check', label: 'Sanity Check', section: 'Misc', navSection: 'MISC', keywords: ['sanity', 'check', 'diagnostics', 'api'], availability: 'always', lifecycle: { fields: ['sanityCheckLoading'] } },
  { id: 'db-health', label: 'DB Health', section: 'Misc', navSection: 'MISC', keywords: ['postgres', 'database', 'vacuum', 'tables', 'runtimedb', 'bloat'], tool: true, availability: 'runtime-db', noLoadGlyph: true, lifecycle: { fields: ['dbHealthLoading'] } },
  { id: 'report', label: 'Report', section: 'Misc', navSection: 'MISC', keywords: ['report', 'export', 'download'], tool: true, availability: 'always', noLoadGlyph: true, lifecycle: { fields: ['reportLoading'] } },
  { id: 'feedback', label: 'Feedback', section: 'Misc', navSection: 'MISC', keywords: ['feedback', 'bug', 'idea', 'report', 'suggestion'], availability: 'always', noLoadGlyph: true, lifecycle: { fields: ['feedbackLoading'] } },
] as const;

export const MODULE_BY_ID: Readonly<Record<PageId, ModuleDefinition>> = Object.freeze(
  MODULES.reduce((acc, mod) => {
    acc[mod.id] = mod;
    return acc;
  }, {} as Record<PageId, ModuleDefinition>),
);

export const MODULE_NAV_SECTIONS: readonly ModuleNavSection[] = [
  { title: 'OVERVIEW', items: ['mission-control', 'summary', 'filesystem', 'resources'] },
  { title: 'AGENTS', items: ['agents', 'agent-tuning', 'agent-settings', 'agent-explainer'], experimental: true },
  { title: 'CONNECTIONS', items: ['connections-inventory', 'connections-insights', 'connections-health', 'connections-fs-migration'] },
  { title: 'PROJECTS', items: ['project-cleaner', 'projects', 'project-compute', 'project-cost'] },
  { title: 'USERS', items: ['users', 'adoption', 'user-churn'] },   // 'adoption' restored from ['users']
  { title: 'PLUGINS', items: ['plugins-installed', 'plugins'] },
  { title: 'CODE ENVS', items: ['code-envs', 'code-envs-cleaner', 'code-envs-comparison', 'code-envs-broken'] },
  { title: 'AI COMPUTE', items: ['container-execs', 'image-cleaner', 'cs-template-replacement', 'llm-audit', 'k8s-insights'] },
  { title: 'MISC', items: ['settings', 'logs', 'sanity-check', 'db-health', 'report', 'feedback'] },
] as const;

export const EXPERIMENTAL_PAGES: ReadonlySet<PageId> = new Set(
  MODULES.filter((mod) => mod.experimental).map((mod) => mod.id),
);

export const DEPRECATED_PAGES: ReadonlySet<PageId> = new Set(
  MODULES.filter((mod) => mod.deprecated).map((mod) => mod.id),
);

export const COMMAND_PALETTE_MODULES = MODULES.map((mod) => ({
  id: mod.id,
  label: mod.commandLabel || mod.label,
  section: mod.section,
  keywords: mod.keywords,
}));

// Every distinct lifecycle field declared by any module. The analysis
// aggregator walks this list to compute the global "Analysis complete"
// indicator; the orchestrator uses it to write the initial queued state.
export const SHARED_LOADING_FIELDS: readonly LifecycleFieldName[] = (() => {
  const seen = new Set<LifecycleFieldName>();
  const out: LifecycleFieldName[] = [];
  for (const mod of MODULES) {
    // Action pages opt out of the global aggregate — their lifecycle field
    // never passes through a startup ritual, so it would block the aggregate
    // at `queued` forever.
    if (mod.noLoadGlyph) continue;
    for (const field of mod.lifecycle.fields) {
      if (!seen.has(field)) {
        seen.add(field);
        out.push(field);
      }
    }
  }
  return out;
})();

export function getModuleLabel(pageId: PageId): string {
  return MODULE_BY_ID[pageId]?.label || pageId;
}
