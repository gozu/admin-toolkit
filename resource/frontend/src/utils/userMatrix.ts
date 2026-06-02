import type {
  CodeEnv,
  LlmAuditRow,
  ParsedData,
  ProjectFootprintRow,
  User,
} from '../types';
import { getDssBaseUrl } from './codeEnvUsageLinks';
import {
  buildApiServicesDaughter,
  buildBundlesDaughter,
  buildCodeEnvsDaughter,
  buildDatasetsDaughter,
  buildLlmConnectionsDaughter,
  buildNotebooksDaughter,
  buildProjectsDaughter,
  buildRecipesDaughter,
  buildSavedModelsDaughter,
  buildScenariosDaughter,
  buildWebappsDaughter,
  type DaughterSpec,
} from './userDaughterSpecs';

export type UserColumnId =
  | 'projects'
  | 'codeEnvs'
  | 'savedModels'
  | 'llmConnections'
  | 'scenarios'
  | 'datasets'
  | 'recipes'
  | 'notebooks'
  | 'webapps'
  | 'apiServices'
  | 'bundles';

export interface UserColumnDef {
  id: UserColumnId;
  label: string;
  tooltip?: string;
  accessor: (login: string, ctx: UserMatrixCtx) => number;
  daughter: (login: string, ctx: UserMatrixCtx) => DaughterSpec;
}

export interface UserMatrixCtx {
  parsedData: ParsedData;
  baseUrl: string;
  users: User[];
  projectsByOwner: Map<string, ProjectFootprintRow[]>;
  codeEnvsByOwner: Map<string, CodeEnv[]>;
  llmRowsByOwner: Map<string, LlmAuditRow[]>;
  flaggedUsers: Set<string>;
}

const PROJECT_OWNER_ATTR_TOOLTIP =
  'Attributed to the project owner — DSS does not expose per-object owners cheaply, so all objects inside a project count toward its owner.';

const sumUsageBreakdown = (
  rows: ProjectFootprintRow[] | undefined,
  key: string,
): number => {
  if (!rows) return 0;
  let total = 0;
  for (const row of rows) {
    total += row.usageBreakdown?.[key] || 0;
  }
  return total;
};

export const USER_COLUMNS: readonly UserColumnDef[] = [
  {
    id: 'projects',
    label: 'Projects',
    accessor: (login, ctx) => ctx.projectsByOwner.get(login)?.length || 0,
    daughter: (login, ctx) => buildProjectsDaughter(login, ctx),
  },
  {
    id: 'codeEnvs',
    label: 'Code Envs',
    accessor: (login, ctx) => ctx.codeEnvsByOwner.get(login)?.length || 0,
    daughter: (login, ctx) => buildCodeEnvsDaughter(login, ctx),
  },
  {
    id: 'savedModels',
    label: 'Saved Models',
    tooltip: PROJECT_OWNER_ATTR_TOOLTIP,
    accessor: (login, ctx) => {
      const rows = ctx.projectsByOwner.get(login);
      if (!rows) return 0;
      let total = 0;
      for (const row of rows) total += row.savedModels?.length ?? row.savedModelCount ?? 0;
      return total;
    },
    daughter: (login, ctx) => buildSavedModelsDaughter(login, ctx),
  },
  {
    id: 'llmConnections',
    label: 'LLM Connections',
    tooltip: PROJECT_OWNER_ATTR_TOOLTIP,
    accessor: (login, ctx) => {
      const rows = ctx.llmRowsByOwner.get(login);
      if (!rows) return 0;
      const seen = new Set<string>();
      for (const r of rows) {
        const key = r.connection || r.llmId;
        if (key) seen.add(key);
      }
      return seen.size;
    },
    daughter: (login, ctx) => buildLlmConnectionsDaughter(login, ctx),
  },
  {
    id: 'scenarios',
    label: 'Scenarios',
    tooltip: PROJECT_OWNER_ATTR_TOOLTIP,
    accessor: (login, ctx) => sumUsageBreakdown(ctx.projectsByOwner.get(login), 'SCENARIO'),
    daughter: (login, ctx) => buildScenariosDaughter(login, ctx),
  },
  {
    id: 'datasets',
    label: 'Datasets',
    tooltip: PROJECT_OWNER_ATTR_TOOLTIP,
    accessor: (login, ctx) => sumUsageBreakdown(ctx.projectsByOwner.get(login), 'DATASET'),
    daughter: (login, ctx) => buildDatasetsDaughter(login, ctx),
  },
  {
    id: 'recipes',
    label: 'Recipes',
    tooltip: PROJECT_OWNER_ATTR_TOOLTIP,
    accessor: (login, ctx) => sumUsageBreakdown(ctx.projectsByOwner.get(login), 'RECIPE'),
    daughter: (login, ctx) => buildRecipesDaughter(login, ctx),
  },
  {
    id: 'notebooks',
    label: 'Notebooks',
    tooltip: PROJECT_OWNER_ATTR_TOOLTIP,
    accessor: (login, ctx) => sumUsageBreakdown(ctx.projectsByOwner.get(login), 'NOTEBOOK'),
    daughter: (login, ctx) => buildNotebooksDaughter(login, ctx),
  },
  {
    id: 'webapps',
    label: 'Webapps',
    tooltip: PROJECT_OWNER_ATTR_TOOLTIP,
    accessor: (login, ctx) => sumUsageBreakdown(ctx.projectsByOwner.get(login), 'WEBAPP'),
    daughter: (login, ctx) => buildWebappsDaughter(login, ctx),
  },
  {
    id: 'apiServices',
    label: 'API Services',
    tooltip: PROJECT_OWNER_ATTR_TOOLTIP,
    accessor: (login, ctx) => sumUsageBreakdown(ctx.projectsByOwner.get(login), 'API_SERVICE'),
    daughter: (login, ctx) => buildApiServicesDaughter(login, ctx),
  },
  {
    id: 'bundles',
    label: 'Bundles',
    tooltip: PROJECT_OWNER_ATTR_TOOLTIP,
    accessor: (login, ctx) => {
      const rows = ctx.projectsByOwner.get(login);
      if (!rows) return 0;
      let total = 0;
      for (const row of rows) total += row.bundleCount || 0;
      return total;
    },
    daughter: (login, ctx) => buildBundlesDaughter(login, ctx),
  },
];

interface FlagOptions {
  codeEnvCountUnhealthy: number;
  deprecatedPythonPrefixes: string[];
}

function isDeprecatedEnv(env: CodeEnv, prefixes: string[]): boolean {
  if (env.language !== 'python') return false;
  const version = String(env.version || '').trim();
  if (!version) return false;
  return prefixes.some((prefix) => version.startsWith(prefix));
}

function computeFlaggedUsers(
  users: User[],
  projectsByOwner: Map<string, ProjectFootprintRow[]>,
  codeEnvsByOwner: Map<string, CodeEnv[]>,
  llmRowsByOwner: Map<string, LlmAuditRow[]>,
  opts: FlagOptions,
): Set<string> {
  const flagged = new Set<string>();
  for (const user of users) {
    const login = user.login;
    const projects = projectsByOwner.get(login) || [];
    const envs = codeEnvsByOwner.get(login) || [];
    const llmRows = llmRowsByOwner.get(login) || [];

    if (user.enabled === false && projects.length > 0) {
      flagged.add(login);
      continue;
    }
    if (projects.some((p) => (p.codeEnvCount || 0) > opts.codeEnvCountUnhealthy)) {
      flagged.add(login);
      continue;
    }
    if (envs.some((e) => isDeprecatedEnv(e, opts.deprecatedPythonPrefixes))) {
      flagged.add(login);
      continue;
    }
    if (llmRows.some((r) => r.status === 'obsolete' || r.status === 'ripoff')) {
      flagged.add(login);
      continue;
    }
    if (projects.length > 0 || envs.length > 0 || llmRows.length > 0) {
      flagged.delete(login);
    }
  }
  return flagged;
}

export interface BuildContextOptions {
  codeEnvCountUnhealthy: number;
  deprecatedPythonPrefixes: string;
}

export function buildUserMatrixContext(
  parsedData: ParsedData,
  options: BuildContextOptions,
): UserMatrixCtx {
  const users = parsedData.users || [];
  const projectFootprint = parsedData.projectFootprint || [];
  const codeEnvs = parsedData.codeEnvs || [];
  const llmRows = parsedData.llmAudit?.rows || [];

  const projectsByOwner = new Map<string, ProjectFootprintRow[]>();
  for (const row of projectFootprint) {
    const owner = row.owner || 'Unknown';
    const list = projectsByOwner.get(owner) || [];
    list.push(row);
    projectsByOwner.set(owner, list);
  }

  const codeEnvsByOwner = new Map<string, CodeEnv[]>();
  for (const env of codeEnvs) {
    const owner = env.owner || 'Unknown';
    const list = codeEnvsByOwner.get(owner) || [];
    list.push(env);
    codeEnvsByOwner.set(owner, list);
  }

  const projectOwnerByKey = new Map<string, string>();
  for (const row of projectFootprint) {
    projectOwnerByKey.set(row.projectKey, row.owner || 'Unknown');
  }
  const llmRowsByOwner = new Map<string, LlmAuditRow[]>();
  for (const row of llmRows) {
    if (row.status === 'not_applicable') continue;
    const owner = projectOwnerByKey.get(row.projectKey) || 'Unknown';
    const list = llmRowsByOwner.get(owner) || [];
    list.push(row);
    llmRowsByOwner.set(owner, list);
  }

  const prefixes = options.deprecatedPythonPrefixes
    .split(',')
    .map((p) => p.trim())
    .filter(Boolean);

  const flaggedUsers = computeFlaggedUsers(users, projectsByOwner, codeEnvsByOwner, llmRowsByOwner, {
    codeEnvCountUnhealthy: options.codeEnvCountUnhealthy,
    deprecatedPythonPrefixes: prefixes,
  });

  return {
    parsedData,
    baseUrl: getDssBaseUrl(),
    users,
    projectsByOwner,
    codeEnvsByOwner,
    llmRowsByOwner,
    flaggedUsers,
  };
}
