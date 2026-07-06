import type { CodeEnvUsageRef } from './codeEnvs';

// Project types
export interface Permission {
  type: 'Group' | 'User';
  name: string;
  permissions: Record<string, boolean>;
}

export interface Project {
  key: string;
  name: string;
  owner: string;
  permissions: Permission[];
  versionNumber: number;
}

export type ProjectFootprintHealth = 'green' | 'yellow' | 'orange' | 'red' | 'angry-red';

export interface ProjectSavedModelRef {
  id: string;
  name: string;
  type: 'PREDICTION' | 'CLUSTERING' | 'UNKNOWN' | string;
  savedModelType?: string;
  backendType?: string;
  predictionType?: string;
  versionsCount?: number;
  activeVersionId?: string;
}

export interface CodeStudioRef {
  id: string;
  name: string;
}

export interface FootprintBucketRef {
  name: string;
  label: string;
  bytes: number;
  location?: string;
}

export interface FootprintBreakdown {
  buckets: FootprintBucketRef[];
  otherCount: number;
  otherBytes: number;
}

export interface ProjectFootprintRow {
  projectKey: string;
  name: string;
  owner: string;
  codeEnvCount: number;
  codeEnvBytes?: number;
  managedDatasetsBytes: number;
  managedFoldersBytes: number;
  bundleBytes: number;
  bundleCount?: number;
  footprintBreakdown?: FootprintBreakdown;
  totalBytes: number;
  totalGB: number;
  instanceAvgProjectGB: number;
  projectSizeIndex: number;
  projectSizeHealth: ProjectFootprintHealth;
  codeStudioCount?: number;
  codeStudios?: CodeStudioRef[];
  codeEnvHealth: ProjectFootprintHealth;
  codeEnvRisk?: number;
  projectRisk?: number;
  usageBreakdown?: Record<string, number>;
  savedModelCount?: number;
  savedModels?: ProjectSavedModelRef[];
  savedModelTypeCounts?: Record<string, number>;
  savedModelSummary?: string;
  usageDetails?: CodeEnvUsageRef[];
  codeEnvKeys?: string[];
}

export interface SqlPushdownRecipeFinding {
  recipeName: string;
  recipeType: string;
  connection: string;
  inputs: string[];
  outputs: string[];
}

export interface SqlPushdownProjectFinding {
  projectKey: string;
  projectName: string;
  recipes: SqlPushdownRecipeFinding[];
}

export interface SqlPushdownOwnerGroup {
  ownerLogin: string;
  ownerDisplayName: string;
  ownerEmail: string | null;
  totalRecipes: number;
  projects: SqlPushdownProjectFinding[];
  scanErrors?: { projectKey: string; area: string; error: string }[];
  failedProjectCount?: number;
  scannedProjectCount?: number;
}

export interface ProjectFootprintSummary {
  instanceProjectRiskAvg: number;
  instanceAvgProjectGB: number;
  projectCount: number;
  scanErrors?: { projectKey: string; area: string; error: string }[];
  failedProjectCount?: number;
  scannedProjectCount?: number;
  benchmark?: {
    enabled?: boolean;
    projectLimit?: number;
    projectSelection?: string;
    timeoutMs?: number;
    timedOut?: boolean;
    timeoutAtStep?: string | null;
    totalElapsedMs?: number;
    remainingMs?: number;
    totalProjectCount?: number;
    selectedProjectCount?: number;
    steps?: Array<{
      name: string;
      calls: number;
      elapsedMs: number;
      avgMs: number;
      qps: number;
    }>;
    apiCalls?: Array<{
      operation: string;
      calls: number;
      elapsedMs: number;
      avgMs: number;
      qps: number;
    }>;
    events?: Array<{
      tMs?: number;
      level?: 'info' | 'warn' | 'error';
      step?: string;
      projectKey?: string;
      message?: string;
      elapsedMs?: number;
    }>;
  };
}

// User types
export interface User {
  login: string;
  email?: string;
  enabled?: boolean;
  userProfile?: string;
}

// User stats
export type UserStats = Record<string, string | number>;

// ── Cost / CRU (Compute Resource Usage, parsed from host audit logs) ──────────
// Shape mirrors python-runnables/cru-audit/runnable.py JSON output exactly.
export interface CruSpan {
  firstTs: string | null;
  lastTs: string | null;
  files: number;
  filesRead: number;
  linesScanned: number;
  cruRecords: number;
}

export interface CruTotals {
  memGBh: number;
  cpuH: number;
  llmUSD: number;
  sqlExecS?: number;
  k8sReservedGBh?: number;
  k8sActualGBh?: number;
  projectCount: number;
  userCount: number;
}

// Per-project drilldown row — one shape for byUser/byContextType/byConnection/
// byModel (the macro emits every metric field on each; the key field varies).
export interface CruDetailRow {
  memGBh: number;
  cpuH: number;
  llmUSD: number;
  sqlExecS: number;
  k8sGBh: number;
  records: number;
}

export type CruProjectUserBreakdown = CruDetailRow & { authIdentifier: string };
export type CruProjectContextBreakdown = CruDetailRow & { type: string };
export type CruProjectConnectionBreakdown = CruDetailRow & { connection: string };
export type CruProjectModelBreakdown = CruDetailRow & { model: string };

export interface CruProjectRow {
  projectKey: string;
  memGBh: number;
  cpuH: number;
  llmUSD: number;
  llmTokens: number;
  sqlExecS: number;
  sqlTotalS: number;
  sqlRows: number;
  sqlQueries: number;
  k8sReservedGBh: number;
  k8sActualGBh: number;
  k8sCpuCoreH: number;
  k8sJobs: number;
  records: number;
  byUser?: CruProjectUserBreakdown[];
  byContextType?: CruProjectContextBreakdown[];
  byConnection?: CruProjectConnectionBreakdown[];
  byModel?: CruProjectModelBreakdown[];
}

export interface CruUserRow {
  authIdentifier: string;
  memGBh: number;
  cpuH: number;
  llmUSD: number;
  sqlExecS?: number;
  sqlTotalS?: number;
  sqlQueries?: number;
  k8sReservedGBh?: number;
  k8sActualGBh?: number;
  k8sCpuCoreH?: number;
  k8sJobs?: number;
  records: number;
}

export interface CruContextTypeRow {
  type: string;
  memGBh: number;
  cpuH: number;
  records: number;
}

export interface CruIdleResource {
  id: string;
  projectKey: string;
  contextType: string;
  memGBh: number;
  cpuH: number;
}

export interface CruTopProcess {
  id: string;
  projectKey: string;
  contextType: string;
  commandName: string;
  memGBh: number;
  cpuH: number;
}

export interface CruConnectionRow {
  connection: string;
  queries: number;
  execS: number;
  totalS: number;
  rows: number;
  topProjects: { projectKey: string; execS: number }[];
  // share of wall time NOT spent in the DB engine — high = fetch/egress-bound
  fetchOverheadPct: number;
}

export interface CruLlmModelRow {
  llmId: string;
  model: string;
  llmType: string;
  connection: string;
  usd: number;
  ptok: number;
  ctok: number;
  queries: number;
  cacheHit: number;
  cacheMiss: number;
  compS: number;
}

export interface CruK8sClusterRow {
  clusterId: string;
  jobs: number;
  sparkJobs: number;
  reservedGBh: number;
}

export interface CruK8sNodeRow {
  nodeId: string;
  actualGBh: number;
  cpuCoreH: number;
  pods: number;
}

export interface CruK8sExecTypeRow {
  type: string;
  actualGBh: number;
  cpuCoreH: number;
  pods: number;
}

export interface CruK8sData {
  clusters?: CruK8sClusterRow[];
  nodes?: CruK8sNodeRow[];
  execTypes?: CruK8sExecTypeRow[];
}

export interface CruDailyRow {
  date: string;
  memGBh: number;
  cpuH: number;
  llmUSD: number;
  sqlExecS: number;
  sqlQueries: number;
  k8sGBh: number;
}

export interface CruClassTotals {
  local?: { memGBh: number; cpuH: number; records: number };
  sql?: {
    queries: number;
    execS: number;
    totalS: number;
    rows: number;
    connections: number;
    unattributed?: { queries: number; execS: number; totalS: number; rows: number };
  };
  k8s?: {
    jobs: number;
    sparkJobs: number;
    reservedGBh: number;
    actualGBh: number;
    cpuCoreH: number;
    censusSnapshots: number;
    censusPods: number;
  };
  llm?: {
    usd: number;
    ptok: number;
    ctok: number;
    queries: number;
    cacheHit: number;
    cacheMiss: number;
    records: number;
  };
}

export interface CruCostData {
  ok?: boolean;
  error?: string;
  auditDir?: string;
  span?: CruSpan;
  totals?: CruTotals;
  classTotals?: CruClassTotals;
  projects?: CruProjectRow[];
  users?: CruUserRow[];
  contextTypes?: CruContextTypeRow[];
  connections?: CruConnectionRow[];
  llmModels?: CruLlmModelRow[];
  k8s?: CruK8sData;
  idleResources?: CruIdleResource[];
  topProcesses?: CruTopProcess[];
  daily?: CruDailyRow[];
}
