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

// Reachability — is the page navigable? Today all pages are always navigable;
// the placeholder exists so the registry contract-check can distinguish
// reachability from lifecycle without inventing the dimension later.
export type ReachabilityPolicy = 'always';

export interface ModuleDefinition {
  id: PageId;
  label: string;
  navLabel?: string;
  commandLabel?: string;
  section: string;
  navSection: string;
  keywords: string[];
  reachability: ReachabilityPolicy;
  lifecycle: LifecycleSource;
  // Action pages (manual advanced tools) carry no sidebar load glyph and are
  // excluded from the global "Analysis complete" aggregate: their lifecycle
  // field exists only to drive in-page UI, never a startup ritual.
  noLoadGlyph?: true;
  trends?: true;
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
  { id: 'mission-control', label: 'Mission Control', section: 'Overview', navSection: 'OVERVIEW', keywords: ['mission', 'control', 'wall', 'noc', 'dashboard', 'all'], reachability: 'always', lifecycle: { fields: [
    'summaryLoading',
    'filesystemLoading',
    'memoryLoading',
    'connectionsInventoryLoading',
    'projectFootprintLoading',
    'usersLoading',
  ] } },
  { id: 'summary', label: 'Summary', section: 'Overview', navSection: 'OVERVIEW', keywords: ['health', 'score', 'overview', 'dashboard'], reachability: 'always', lifecycle: { fields: ['summaryLoading'] } },
  { id: 'filesystem', label: 'Filesystem', section: 'Overview', navSection: 'OVERVIEW', keywords: ['disk', 'storage', 'mount', 'partition'], reachability: 'always', lifecycle: { fields: ['filesystemLoading'] } },
  { id: 'memory', label: 'Memory', section: 'Overview', navSection: 'OVERVIEW', keywords: ['ram', 'swap', 'memory', 'usage', 'pid', 'process'], reachability: 'always', lifecycle: { fields: ['memoryLoading'] } },
  { id: 'cpu', label: 'CPU', section: 'Overview', navSection: 'OVERVIEW', keywords: ['cpu', 'process', 'pid', 'load', 'usage'], reachability: 'always', lifecycle: { fields: ['cpuLoading'] } },

  // CONNECTIONS
  { id: 'connections-inventory', label: 'Inventory', section: 'Connections', navSection: 'CONNECTIONS', keywords: ['database', 'connector', 'type', 'inventory'], trends: true, reachability: 'always', lifecycle: { fields: ['connectionsInventoryLoading'] } },
  { id: 'connections-insights', label: 'Insights', section: 'Connections', navSection: 'CONNECTIONS', keywords: ['connection', 'insights', 'matrix', 'audit', 'usage', 'consumption', 'health', 'projects'], reachability: 'always', lifecycle: { fields: [
    'connectionsInventoryLoading',
    'connectionUsageLoading',
    'connectionsHealthLoading',
    'connectionsAuditLoading',
  ] } },
  { id: 'connections-health', label: 'Health', section: 'Connections', navSection: 'CONNECTIONS', keywords: ['connection', 'test', 'health', 'diagnostic'], reachability: 'always', lifecycle: { fields: ['connectionsHealthLoading'] } },
  { id: 'connections-fs-migration', label: 'FS Migration', section: 'Connections', navSection: 'CONNECTIONS', keywords: ['filesystem', 'migration', 'local', 'fs', 'outreach', 'owner'], tool: true, reachability: 'always', lifecycle: { fields: ['connectionUsageLoading'] } },

  // PROJECTS
  { id: 'project-cleaner', label: 'Project Cleaner', navLabel: 'Cleaner', section: 'Projects', navSection: 'PROJECTS', keywords: ['clean', 'delete', 'inactive', 'project'], tool: true, reachability: 'always', lifecycle: { fields: ['projectCleanerLoading'] } },
  { id: 'projects', label: 'Projects', navLabel: 'Insights', section: 'Projects', navSection: 'PROJECTS', keywords: ['project', 'footprint', 'permissions'], trends: true, reachability: 'always', lifecycle: { fields: ['projectFootprintLoading'] } },
  { id: 'project-compute', label: 'Compute', section: 'Projects', navSection: 'PROJECTS', keywords: ['compute', 'project', 'usage', 'workload'], reachability: 'always', lifecycle: { fields: ['projectComputeLoading'] } },
  { id: 'project-cost', label: 'Cost / CRU', navLabel: 'Cost', section: 'Projects', navSection: 'PROJECTS', keywords: ['cost', 'cru', 'compute', 'resource', 'usage', 'memory', 'cpu', 'llm', 'audit'], streamEndpoint: '/api/cru/stream', reachability: 'always', lifecycle: { fields: ['projectCostLoading'] } },

  // USERS
  { id: 'users', label: 'Users', section: 'Users', navSection: 'USERS', keywords: ['user', 'owner', 'login', 'email', 'accountability', 'ownership'], reachability: 'always', lifecycle: { fields: [
    'usersLoading',
    'projectFootprintLoading',
    'codeEnvsLoading',
    'llmAuditLoading',
  ] } },
  // On-demand deep dive (loads on mount, like k8s-insights): noLoadGlyph keeps
  // adoptionLoading out of SHARED_LOADING_FIELDS so the global "Analysis
  // complete" aggregate never waits on a page the user may not visit.
  { id: 'adoption', label: 'Adoption', section: 'Users', navSection: 'USERS', keywords: ['adoption', 'engagement', 'usage', 'logins', 'active', 'trend', 'cohort', 'retention', 'builders', 'people', 'commits', 'growth'], reachability: 'always', noLoadGlyph: true, lifecycle: { fields: ['adoptionLoading'] } },

  // PLUGINS
  { id: 'plugins-installed', label: 'Installed', section: 'Plugins', navSection: 'PLUGINS', keywords: ['plugin', 'installed', 'list', 'version', 'projects', 'usage'], reachability: 'always', lifecycle: { fields: ['pluginsLoading'] } },
  { id: 'plugins', label: 'Plugin Sync', section: 'Plugins', navSection: 'PLUGINS', keywords: ['plugin', 'sync', 'compare', 'version'], trends: true, tool: true, reachability: 'always', noLoadGlyph: true, lifecycle: { fields: ['pluginSyncLoading'] } },

  // CODE ENVS
  { id: 'code-envs', label: 'Cleaner', section: 'Code Envs', navSection: 'CODE ENVS', keywords: ['python', 'environment', 'package', 'clean', 'delete', 'unused', 'replace', 'migration'], tool: true, reachability: 'always', lifecycle: { fields: ['codeEnvsLoading', 'codeEnvSizesLoading', 'codeEnvCleanerLoading', 'codeEnvReplacementLoading'] } },
  { id: 'code-envs-cleaner', label: 'Insights', section: 'Code Envs', navSection: 'CODE ENVS', keywords: ['python', 'environment', 'package', 'clean', 'unused', 'review', 'read-only'], reachability: 'always', lifecycle: { fields: ['codeEnvsLoading', 'codeEnvSizesLoading'] } },
  { id: 'code-envs-comparison', label: 'Comparison', section: 'Code Envs', navSection: 'CODE ENVS', keywords: ['compare', 'duplicate', 'version', 'mismatch'], reachability: 'always', lifecycle: { fields: ['codeEnvsComparisonLoading'] } },

  // AI COMPUTE
  { id: 'container-execs', label: 'Container Execs', section: 'AI Compute', navSection: 'AI COMPUTE', keywords: ['container', 'execution', 'kubernetes', 'k8s', 'compute', 'gpu', 'project', 'recipe', 'webapp'], trends: true, streamEndpoint: '/api/container-execs/stream', tool: true, reachability: 'always', lifecycle: { fields: ['containerExecsLoading'] } },
  { id: 'image-cleaner', label: 'Docker Images', section: 'AI Compute', navSection: 'AI COMPUTE', keywords: ['ecr', 'acr', 'gar', 'docker', 'image', 'container', 'cleanup', 'aws', 'azure', 'gcp', 'registry'], tool: true, reachability: 'always', lifecycle: { fields: ['imageCleanerLoading'] } },
  { id: 'cs-template-replacement', label: 'Replace CS Template', navLabel: 'CS Templates', section: 'AI Compute', navSection: 'AI COMPUTE', keywords: ['code', 'studio', 'template', 'replace', 'migrate', 'cs'], tool: true, reachability: 'always', noLoadGlyph: true, lifecycle: { fields: ['csTemplateReplacementLoading'] } },
  { id: 'llm-audit', label: 'Model Audit', section: 'AI Compute', navSection: 'AI COMPUTE', keywords: ['llm', 'model', 'audit', 'pricing'], reachability: 'always', lifecycle: { fields: ['llmAuditLoading'] } },
  { id: 'k8s-insights', label: 'K8s Insights', section: 'AI Compute', navSection: 'AI COMPUTE', keywords: ['kubernetes', 'k8s', 'eks', 'cluster', 'gpu', 'cost', 'bin pack', 'autoscaler', 'nodes', 'pods', 'daemonset', 'findings', 'rules'], streamEndpoint: '/api/k8s-insights/stream', reachability: 'always', noLoadGlyph: true, lifecycle: { fields: ['k8sInsightsLoading'] } },

  // AGENTS — conversational ops surface over the agents plugin (LLM Mesh
  // proxy). Loads per conversation, never through the startup ritual.
  { id: 'agents', label: 'Agents', section: 'Agents', navSection: 'AGENTS', keywords: ['agent', 'chat', 'ops', 'actuator', 'triage', 'plan', 'approve', 'autonomous', 'ai'], reachability: 'always', noLoadGlyph: true, lifecycle: { fields: ['agentsLoading'] } },
  { id: 'agent-tuning', label: 'Agent Tuning', navLabel: 'Tuning', section: 'Agents', navSection: 'AGENTS', keywords: ['agent', 'tuning', 'prompt', 'system', 'rubric', 'version', 'customize', 'override'], reachability: 'always', noLoadGlyph: true, lifecycle: { fields: ['agentsLoading'] } },

  // MISC
  { id: 'settings', label: 'Settings', section: 'Misc', navSection: 'MISC', keywords: ['settings', 'mail', 'channel', 'email', 'config', 'preferences'], reachability: 'always', lifecycle: { fields: ['settingsLoading'] } },
  { id: 'logs', label: 'Errors', section: 'Misc', navSection: 'MISC', keywords: ['log', 'error', 'exception', 'stack'], badge: 'logs', reachability: 'always', lifecycle: { fields: ['logsLoading'] } },
  { id: 'sanity-check', label: 'Sanity Check', section: 'Misc', navSection: 'MISC', keywords: ['sanity', 'check', 'diagnostics', 'api'], reachability: 'always', lifecycle: { fields: ['sanityCheckLoading'] } },
  { id: 'db-health', label: 'DB Health', section: 'Misc', navSection: 'MISC', keywords: ['postgres', 'database', 'vacuum', 'tables', 'runtimedb', 'bloat'], trends: true, tool: true, reachability: 'always', noLoadGlyph: true, lifecycle: { fields: ['dbHealthLoading'] } },
  { id: 'report', label: 'Report', section: 'Misc', navSection: 'MISC', keywords: ['report', 'export', 'download'], tool: true, reachability: 'always', noLoadGlyph: true, lifecycle: { fields: ['reportLoading'] } },
  { id: 'feedback', label: 'Feedback', section: 'Misc', navSection: 'MISC', keywords: ['feedback', 'bug', 'idea', 'report', 'suggestion'], reachability: 'always', noLoadGlyph: true, lifecycle: { fields: ['feedbackLoading'] } },
] as const;

export const MODULE_BY_ID: Readonly<Record<PageId, ModuleDefinition>> = Object.freeze(
  MODULES.reduce((acc, mod) => {
    acc[mod.id] = mod;
    return acc;
  }, {} as Record<PageId, ModuleDefinition>),
);

export const MODULE_NAV_SECTIONS: readonly ModuleNavSection[] = [
  { title: 'OVERVIEW', items: ['mission-control', 'summary', 'filesystem', 'memory', 'cpu'] },
  { title: 'AGENTS', items: ['agents', 'agent-tuning'], experimental: true },
  { title: 'CONNECTIONS', items: ['connections-inventory', 'connections-insights', 'connections-health', 'connections-fs-migration'] },
  { title: 'PROJECTS', items: ['project-cleaner', 'projects', 'project-compute', 'project-cost'] },
  { title: 'USERS', items: ['users', 'adoption'] },   // restored from ['users']
  { title: 'PLUGINS', items: ['plugins-installed', 'plugins'] },
  { title: 'CODE ENVS', items: ['code-envs', 'code-envs-cleaner', 'code-envs-comparison'] },
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
