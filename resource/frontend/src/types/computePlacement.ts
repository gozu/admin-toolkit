import type { ContainerExecConfig } from './containerExecs';

export type ComputePlacementKind = 'local' | 'container' | 'spark';

export type ComputePlacementObjectType = 'PROJECT' | 'RECIPE' | 'WEBAPP' | 'ML_TASK' | 'NOTEBOOK';

export interface ComputePlacementRow {
  id: string;
  projectKey: string;
  projectName: string;
  objectType: ComputePlacementObjectType;
  objectId: string;
  objectName: string;
  objectKind: string;
  surface: string;
  rawPath: string;
  /** Declared on the object: INHERIT | NONE | EXPLICIT_CONTAINER | '' (kernel/engine rows). */
  containerMode: string;
  containerConf?: string | null;
  effectiveConf?: string | null;
  /** object | project | instance | kernel | engine */
  resolvedFrom: string;
  placement: ComputePlacementKind;
  configType?: string | null;
  clusterId?: string | null;
  clusterSource?: 'project' | 'instance' | null;
  owner: string;
  ownerSource: 'object' | 'project';
  ownerEmail: string;
  ownerDisplayName: string;
  migratable: boolean;
  migrateBlocker?: string | null;
  extra: {
    recipeType?: string;
    engineType?: string;
    webappType?: string;
    analysisId?: string;
    mlTaskId?: string;
    taskType?: string;
    kernel?: string;
  };
}

export interface ComputePlacementDefault {
  containerMode: string;
  containerConf?: string | null;
  effectiveConf?: string | null;
  resolvedFrom: string;
}

export interface ComputePlacementProject {
  projectKey: string;
  projectName: string;
  owner: string;
  ownerEmail: string;
  defaults: Record<'project_code_default' | 'project_visual_default' | 'project_webapp_default', ComputePlacementDefault>;
  cluster: {
    clusterMode: string;
    clusterId?: string | null;
    effectiveClusterId?: string | null;
    clusterSource?: 'project' | 'instance' | null;
  };
  objectCount: number;
  localCount: number;
  containerCount: number;
  sparkCount: number;
}

export interface ComputePlacementCluster {
  id: string;
  name: string;
  type: string;
  architecture: string;
  state: string;
}

export interface ComputePlacementSummary {
  projectCount: number;
  scannedProjectCount: number;
  rowCount: number;
  objectRowCount: number;
  projectDefaultRowCount: number;
  byPlacement: Record<string, number>;
  byObjectType: Record<string, number>;
  byConfig: Record<string, number>;
  byCluster: Record<string, number>;
  localCount: number;
  containerCount: number;
  sparkCount: number;
  migratableCount: number;
  localOwnerCount: number;
  projectsWithLocalCount: number;
  projectsLocalByDefault: number;
}

export interface ComputePlacementScanResult {
  rows: ComputePlacementRow[];
  projects: ComputePlacementProject[];
  configs: ContainerExecConfig[];
  configNames: string[];
  configTypes: Record<string, string>;
  clusters: ComputePlacementCluster[];
  globalDefaultConfig?: string | null;
  globalDefaultClusterId?: string | null;
  summary: ComputePlacementSummary;
  scanErrors: { projectKey: string; area: string; error: string }[];
  failedProjectCount: number;
  scannedProjectCount: number;
  warnings: string[];
  timedOut: boolean;
  elapsedMs: number;
}

export type ComputeMigrationStrategy = 'objects' | 'project-defaults';

export interface ComputeMigrationOp {
  kind: 'object-explicit' | 'object-inherit' | 'object-unchanged' | 'project-default' | 'project-cluster';
  rowId?: string | null;
  projectKey: string;
  objectType?: string;
  objectId?: string;
  objectName?: string;
  objectKind?: string;
  surface?: string;
  rawPath?: string;
  from: string;
  to: string;
  status: 'planned' | 'updated' | 'failed' | 'unchanged';
  error?: string;
  note?: string;
  diag?: Record<string, unknown>;
}

export interface ComputeMigrationResult {
  dryRun: boolean;
  strategy: ComputeMigrationStrategy;
  targetConfig: string;
  clusterId?: string | null;
  scanCached?: boolean;
  matchedRows: number;
  plannedOps: number;
  updatedOps: number;
  failedOps: number;
  unchangedOps: number;
  results: ComputeMigrationOp[];
}
