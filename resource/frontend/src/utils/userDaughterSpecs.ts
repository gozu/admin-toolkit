import type { ReactNode } from 'react';
import { createElement } from 'react';
import type {
  CodeEnv,
  CodeEnvUsageRef,
  LlmAuditRow,
  ProjectFootprintRow,
  ProjectSavedModelRef,
} from '../types';
import { dssUrls } from './codeEnvUsageLinks';
import { formatGb } from './formatters';
import type { UserMatrixCtx } from './userMatrix';
import type { ColumnDef } from './dataGridTypes';

// DaughterColumn is the spec-driven subset of the unified ColumnDef contract.
// Aliasing keeps the 11 build*Daughter factories compiling untouched while the
// modal renders through the shared DataGrid engine.
export type DaughterColumn<R> = ColumnDef<R>;

export interface DaughterSpec {
  title: string;
  columns: DaughterColumn<unknown>[];
  rows: unknown[];
  emptyMessage?: string;
  defaultSortColumn?: string;
  defaultSortDir?: 'asc' | 'desc';
}

const LINK_CLASS =
  'text-[var(--neon-cyan)] hover:underline truncate inline-block max-w-full';

function ExternalLink(href: string, label: string, key?: string): ReactNode {
  return createElement(
    'a',
    {
      href,
      target: '_blank',
      rel: 'noopener noreferrer',
      className: LINK_CLASS,
      title: label,
      key,
    },
    label,
  );
}

function PlainText(value: string | number | undefined | null): ReactNode {
  if (value == null || value === '') {
    return createElement('span', { className: 'text-[var(--text-muted)]' }, '—');
  }
  return createElement('span', null, String(value));
}

// =============================================================================
// Projects daughter
// =============================================================================

interface ProjectsDaughterRow {
  project: ProjectFootprintRow;
}

export function buildProjectsDaughter(login: string, ctx: UserMatrixCtx): DaughterSpec {
  const rows: ProjectsDaughterRow[] = (ctx.projectsByOwner.get(login) || []).map((project) => ({
    project,
  }));

  const columns: DaughterColumn<ProjectsDaughterRow>[] = [
    {
      id: 'project',
      label: 'Project',
      render: ({ project }) => ExternalLink(dssUrls.project(project.projectKey), project.name || project.projectKey),
      sortValue: ({ project }) => project.name || project.projectKey,
    },
    {
      id: 'totalGB',
      label: 'Total GB',
      align: 'right',
      render: ({ project }) => PlainText(formatGb(project.totalBytes)),
      sortValue: ({ project }) => project.totalBytes || 0,
    },
    {
      id: 'codeEnvs',
      label: 'Code Envs',
      align: 'right',
      render: ({ project }) => PlainText(project.codeEnvCount || 0),
      sortValue: ({ project }) => project.codeEnvCount || 0,
    },
    {
      id: 'models',
      label: 'Models',
      align: 'right',
      render: ({ project }) => PlainText(project.savedModelCount ?? 0),
      sortValue: ({ project }) => project.savedModelCount ?? 0,
    },
    {
      id: 'bundles',
      label: 'Bundles',
      align: 'right',
      render: ({ project }) => PlainText(project.bundleCount ?? 0),
      sortValue: ({ project }) => project.bundleCount ?? 0,
    },
    {
      id: 'health',
      label: 'Health',
      render: ({ project }) => PlainText(project.projectSizeHealth || '—'),
      sortValue: ({ project }) => project.projectSizeHealth || '',
    },
  ];

  return {
    title: `Projects owned by ${login}`,
    columns: columns as DaughterColumn<unknown>[],
    rows,
    emptyMessage: 'This user owns no projects.',
    defaultSortColumn: 'totalGB',
    defaultSortDir: 'desc',
  };
}

// =============================================================================
// Code Envs daughter — (project × env) pairs where env.owner == login
// =============================================================================

interface CodeEnvDaughterRow {
  project: ProjectFootprintRow | null;
  projectKey: string;
  env: CodeEnv;
  recipes: number;
  notebooks: number;
  scenarios: number;
  webapps: number;
}

function countByType(usages: CodeEnvUsageRef[] | undefined, projectKey: string, type: string): number {
  if (!usages) return 0;
  let n = 0;
  for (const u of usages) {
    if (u.projectKey !== projectKey) continue;
    const t = String(u.objectType || u.usageType || '').toUpperCase();
    if (t.includes(type)) n += 1;
  }
  return n;
}

export function buildCodeEnvsDaughter(login: string, ctx: UserMatrixCtx): DaughterSpec {
  const envs = ctx.codeEnvsByOwner.get(login) || [];
  const projectByKey = new Map<string, ProjectFootprintRow>();
  for (const row of ctx.parsedData.projectFootprint || []) {
    projectByKey.set(row.projectKey, row);
  }

  const rows: CodeEnvDaughterRow[] = [];
  for (const env of envs) {
    const keys = env.projectKeys && env.projectKeys.length > 0 ? env.projectKeys : [''];
    for (const pk of keys) {
      const usages = env.usageDetails;
      rows.push({
        projectKey: pk,
        project: pk ? projectByKey.get(pk) || null : null,
        env,
        recipes: countByType(usages, pk, 'RECIPE'),
        notebooks: countByType(usages, pk, 'NOTEBOOK'),
        scenarios: countByType(usages, pk, 'SCENARIO'),
        webapps: countByType(usages, pk, 'WEBAPP'),
      });
    }
  }

  const columns: DaughterColumn<CodeEnvDaughterRow>[] = [
    {
      id: 'project',
      label: 'Project',
      render: (r) =>
        r.projectKey
          ? ExternalLink(dssUrls.project(r.projectKey), r.project?.name || r.projectKey)
          : PlainText('—'),
      sortValue: (r) => r.project?.name || r.projectKey,
    },
    {
      id: 'env',
      label: 'Env',
      render: (r) =>
        ExternalLink(dssUrls.codeEnv(r.env.language, r.env.name), r.env.name),
      sortValue: (r) => r.env.name,
    },
    {
      id: 'lang',
      label: 'Lang/Ver',
      render: (r) => PlainText(`${r.env.language}${r.env.version ? ` ${r.env.version}` : ''}`),
      sortValue: (r) => `${r.env.language} ${r.env.version || ''}`,
    },
    {
      id: 'recipes',
      label: 'Recipes',
      align: 'right',
      render: (r) => PlainText(r.recipes),
      sortValue: (r) => r.recipes,
    },
    {
      id: 'notebooks',
      label: 'Notebooks',
      align: 'right',
      render: (r) => PlainText(r.notebooks),
      sortValue: (r) => r.notebooks,
    },
    {
      id: 'scenarios',
      label: 'Scenarios',
      align: 'right',
      render: (r) => PlainText(r.scenarios),
      sortValue: (r) => r.scenarios,
    },
    {
      id: 'webapps',
      label: 'Webapps',
      align: 'right',
      render: (r) => PlainText(r.webapps),
      sortValue: (r) => r.webapps,
    },
    {
      id: 'total',
      label: 'Total',
      align: 'right',
      render: (r) => PlainText(r.recipes + r.notebooks + r.scenarios + r.webapps),
      sortValue: (r) => r.recipes + r.notebooks + r.scenarios + r.webapps,
    },
  ];

  return {
    title: `Code Envs owned by ${login}`,
    columns: columns as DaughterColumn<unknown>[],
    rows,
    emptyMessage: 'This user owns no code environments.',
    defaultSortColumn: 'total',
    defaultSortDir: 'desc',
  };
}

// =============================================================================
// Saved Models daughter
// =============================================================================

interface SavedModelRow {
  projectKey: string;
  projectName: string;
  model: ProjectSavedModelRef;
}

export function buildSavedModelsDaughter(login: string, ctx: UserMatrixCtx): DaughterSpec {
  const projects = ctx.projectsByOwner.get(login) || [];
  const rows: SavedModelRow[] = [];
  for (const project of projects) {
    for (const model of project.savedModels || []) {
      rows.push({ projectKey: project.projectKey, projectName: project.name, model });
    }
  }

  const columns: DaughterColumn<SavedModelRow>[] = [
    {
      id: 'project',
      label: 'Project',
      render: (r) => ExternalLink(dssUrls.project(r.projectKey), r.projectName || r.projectKey),
      sortValue: (r) => r.projectName || r.projectKey,
    },
    {
      id: 'model',
      label: 'Model',
      render: (r) =>
        ExternalLink(dssUrls.savedModel(r.projectKey, r.model.id), r.model.name || r.model.id),
      sortValue: (r) => r.model.name || r.model.id,
    },
    {
      id: 'type',
      label: 'Type',
      render: (r) => PlainText(r.model.predictionType || r.model.type || '—'),
      sortValue: (r) => r.model.predictionType || r.model.type || '',
    },
    {
      id: 'versions',
      label: 'Versions',
      align: 'right',
      render: (r) => PlainText(r.model.versionsCount ?? 0),
      sortValue: (r) => r.model.versionsCount ?? 0,
    },
    {
      id: 'active',
      label: 'Active version',
      render: (r) => PlainText(r.model.activeVersionId || '—'),
      sortValue: (r) => r.model.activeVersionId || '',
    },
  ];

  return {
    title: `Saved Models owned by ${login}`,
    columns: columns as DaughterColumn<unknown>[],
    rows,
    emptyMessage: 'No saved models in projects this user owns.',
    defaultSortColumn: 'versions',
    defaultSortDir: 'desc',
  };
}

// =============================================================================
// LLM Connections daughter — usage assets across owned projects
// =============================================================================

interface LlmAssetRow {
  projectKey: string;
  projectName: string;
  connection: string;
  effectiveModel: string;
  status: LlmAuditRow['status'];
  assetType: string;
  assetName: string;
  href: string;
}

function assetUrl(projectKey: string, assetType: string, assetName: string): string {
  switch (assetType) {
    case 'recipe':
      return dssUrls.recipe(projectKey, assetName);
    case 'notebook':
      return dssUrls.notebook(projectKey, assetName);
    case 'knowledge_bank':
      return `${dssUrls.project(projectKey)}knowledge-banks/${encodeURIComponent(assetName)}/`;
    case 'agent':
      return `${dssUrls.project(projectKey)}agents/${encodeURIComponent(assetName)}/`;
    default:
      return dssUrls.project(projectKey);
  }
}

export function buildLlmConnectionsDaughter(login: string, ctx: UserMatrixCtx): DaughterSpec {
  const llmRows = ctx.llmRowsByOwner.get(login) || [];
  const projectNameByKey = new Map<string, string>();
  for (const project of ctx.parsedData.projectFootprint || []) {
    projectNameByKey.set(project.projectKey, project.name);
  }

  const rows: LlmAssetRow[] = [];
  for (const row of llmRows) {
    const assets = row.usageAssets || [];
    if (assets.length === 0) {
      rows.push({
        projectKey: row.projectKey,
        projectName: projectNameByKey.get(row.projectKey) || row.projectName || row.projectKey,
        connection: row.connection || '—',
        effectiveModel: row.effectiveModel || row.rawModel || '—',
        status: row.status,
        assetType: '—',
        assetName: '—',
        href: dssUrls.project(row.projectKey),
      });
    } else {
      for (const a of assets) {
        rows.push({
          projectKey: row.projectKey,
          projectName: projectNameByKey.get(row.projectKey) || row.projectName || row.projectKey,
          connection: row.connection || '—',
          effectiveModel: row.effectiveModel || row.rawModel || '—',
          status: row.status,
          assetType: a.assetType,
          assetName: a.assetName,
          href: assetUrl(row.projectKey, a.assetType, a.assetName),
        });
      }
    }
  }

  const columns: DaughterColumn<LlmAssetRow>[] = [
    {
      id: 'project',
      label: 'Project',
      render: (r) => ExternalLink(dssUrls.project(r.projectKey), r.projectName),
      sortValue: (r) => r.projectName,
    },
    {
      id: 'connection',
      label: 'Connection',
      render: (r) => ExternalLink(dssUrls.llmConn(r.connection), r.connection),
      sortValue: (r) => r.connection,
    },
    {
      id: 'model',
      label: 'Model',
      render: (r) => PlainText(r.effectiveModel),
      sortValue: (r) => r.effectiveModel,
    },
    {
      id: 'status',
      label: 'Status',
      render: (r) => PlainText(r.status),
      sortValue: (r) => r.status,
    },
    {
      id: 'asset',
      label: 'Asset',
      render: (r) =>
        r.assetName === '—' ? PlainText('—') : ExternalLink(r.href, `${r.assetType}: ${r.assetName}`),
      sortValue: (r) => `${r.assetType}:${r.assetName}`,
    },
  ];

  return {
    title: `LLM Connections referenced by ${login}'s projects`,
    columns: columns as DaughterColumn<unknown>[],
    rows,
    emptyMessage: 'No LLM connections referenced by projects this user owns.',
  };
}

// =============================================================================
// Per-usage-type daughters (Scenarios, Datasets, Recipes, Notebooks, Webapps,
// API Services). All flatten projectFootprint[].usageDetails for the given
// objectType and link via dssUrls.<x>(projectKey, objectId).
// =============================================================================

interface UsageRow {
  projectKey: string;
  projectName: string;
  objectType: string;
  objectId: string;
  objectName: string;
  href: string;
  codeEnv: string;
}

function buildUsageDaughter(
  login: string,
  ctx: UserMatrixCtx,
  typeMatcher: (type: string) => boolean,
  urlFn: (pk: string, id: string, name?: string) => string,
  title: string,
  emptyMessage: string,
): DaughterSpec {
  const projects = ctx.projectsByOwner.get(login) || [];
  const rows: UsageRow[] = [];

  for (const project of projects) {
    for (const usage of project.usageDetails || []) {
      const type = String(usage.objectType || usage.usageType || '').toUpperCase();
      if (!typeMatcher(type)) continue;
      const id = usage.objectId || '';
      const name = usage.objectName || id;
      rows.push({
        projectKey: project.projectKey,
        projectName: project.name,
        objectType: type,
        objectId: id,
        objectName: name,
        href: id ? urlFn(project.projectKey, id, usage.objectName) : dssUrls.project(project.projectKey),
        codeEnv: usage.codeEnvName ? `${usage.codeEnvLanguage || ''}:${usage.codeEnvName}` : '',
      });
    }
  }

  // De-dupe by (projectKey, objectId, objectType)
  const seen = new Set<string>();
  const deduped = rows.filter((r) => {
    const key = `${r.projectKey}|${r.objectType}|${r.objectId}|${r.objectName}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  const columns: DaughterColumn<UsageRow>[] = [
    {
      id: 'project',
      label: 'Project',
      render: (r) => ExternalLink(dssUrls.project(r.projectKey), r.projectName || r.projectKey),
      sortValue: (r) => r.projectName,
    },
    {
      id: 'name',
      label: 'Name',
      render: (r) => ExternalLink(r.href, r.objectName),
      sortValue: (r) => r.objectName,
    },
    {
      id: 'codeEnv',
      label: 'Code Env',
      render: (r) => PlainText(r.codeEnv || '—'),
      sortValue: (r) => r.codeEnv,
    },
  ];

  return {
    title,
    columns: columns as DaughterColumn<unknown>[],
    rows: deduped,
    emptyMessage,
  };
}

export function buildScenariosDaughter(login: string, ctx: UserMatrixCtx): DaughterSpec {
  return buildUsageDaughter(
    login,
    ctx,
    (t) => t.includes('SCENARIO'),
    dssUrls.scenario,
    `Scenarios in projects owned by ${login}`,
    'No scenarios attributed to this user.',
  );
}

export function buildRecipesDaughter(login: string, ctx: UserMatrixCtx): DaughterSpec {
  return buildUsageDaughter(
    login,
    ctx,
    (t) => t.includes('RECIPE'),
    dssUrls.recipe,
    `Recipes in projects owned by ${login}`,
    'No recipes attributed to this user.',
  );
}

export function buildNotebooksDaughter(login: string, ctx: UserMatrixCtx): DaughterSpec {
  return buildUsageDaughter(
    login,
    ctx,
    (t) => t.includes('NOTEBOOK') || t.includes('JUPYTER'),
    dssUrls.notebook,
    `Notebooks in projects owned by ${login}`,
    'No notebooks attributed to this user.',
  );
}

export function buildWebappsDaughter(login: string, ctx: UserMatrixCtx): DaughterSpec {
  return buildUsageDaughter(
    login,
    ctx,
    (t) => t.includes('WEBAPP'),
    dssUrls.webapp,
    `Webapps in projects owned by ${login}`,
    'No webapps attributed to this user.',
  );
}

export function buildApiServicesDaughter(login: string, ctx: UserMatrixCtx): DaughterSpec {
  return buildUsageDaughter(
    login,
    ctx,
    (t) => t.includes('API_SERVICE') || t.includes('APISERVICE'),
    dssUrls.apiService,
    `API Services in projects owned by ${login}`,
    'No API services attributed to this user.',
  );
}

export function buildDatasetsDaughter(login: string, ctx: UserMatrixCtx): DaughterSpec {
  return buildUsageDaughter(
    login,
    ctx,
    (t) => t.includes('DATASET'),
    dssUrls.dataset,
    `Datasets referenced by projects owned by ${login}`,
    'No datasets attributed to this user.',
  );
}

// =============================================================================
// Bundles daughter — projectFootprint rows with bundleCount > 0
// =============================================================================

interface BundleRow {
  projectKey: string;
  projectName: string;
  count: number;
  bytes: number;
}

export function buildBundlesDaughter(login: string, ctx: UserMatrixCtx): DaughterSpec {
  const projects = ctx.projectsByOwner.get(login) || [];
  const rows: BundleRow[] = projects
    .filter((p) => (p.bundleCount || 0) > 0)
    .map((p) => ({
      projectKey: p.projectKey,
      projectName: p.name || p.projectKey,
      count: p.bundleCount || 0,
      bytes: p.bundleBytes || 0,
    }));

  const columns: DaughterColumn<BundleRow>[] = [
    {
      id: 'project',
      label: 'Project',
      render: (r) => ExternalLink(`${dssUrls.project(r.projectKey)}bundles/exported/`, r.projectName),
      sortValue: (r) => r.projectName,
    },
    {
      id: 'count',
      label: 'Bundles',
      align: 'right',
      render: (r) => PlainText(r.count),
      sortValue: (r) => r.count,
    },
    {
      id: 'bytes',
      label: 'Size',
      align: 'right',
      render: (r) => PlainText(formatGb(r.bytes)),
      sortValue: (r) => r.bytes,
    },
  ];

  return {
    title: `Bundles in projects owned by ${login}`,
    columns: columns as DaughterColumn<unknown>[],
    rows,
    emptyMessage: 'No exported bundles in projects this user owns.',
    defaultSortColumn: 'count',
    defaultSortDir: 'desc',
  };
}
