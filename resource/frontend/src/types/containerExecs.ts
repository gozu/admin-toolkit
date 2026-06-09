export interface ContainerExecConfig {
  name: string;
  type?: string;
  usableBy?: string;
  workloadType?: string;
  dockerNetwork?: string;
  kubernetesNamespace?: string;
  repositoryURL?: string;
  baseImageType?: string;
  prePushMode?: string;
  nodeSelector?: string;
}

export interface ContainerExecUsageRow {
  id: string;
  projectKey?: string;
  projectName?: string;
  objectType: string;
  objectId: string;
  objectName: string;
  objectSubtype?: string;
  surface: string;
  surfaceLabel: string;
  rawPath: string;
  containerMode: string;
  containerConf?: string | null;
  effectiveContainerConf?: string | null;
  projectConfig?: string | null;
  inheritedFrom?: string | null;
  overrideLevel?: 'project' | 'job' | '';
  recipeType?: string;
  analysisId?: string;
  mlTaskId?: string;
  writable: boolean;
  replacementSupported: boolean;
  notes?: string;
}

export interface ContainerExecProjectRow {
  projectKey: string;
  projectName: string;
  projectOverrides: ContainerExecUsageRow[];
  jobOverrides: ContainerExecUsageRow[];
}

export interface ContainerExecScanResult {
  configs: ContainerExecConfig[];
  usageRows: ContainerExecUsageRow[];
  projectRows?: ContainerExecProjectRow[];
  summary: {
    configCount: number;
    usageCount: number;
    explicitUsageCount: number;
    inheritedUsageCount: number;
    replacementSupportedCount: number;
    byConfig?: Record<string, number>;
    byObjectType?: Record<string, number>;
    byMode?: Record<string, number>;
    projectCount?: number;
    projectUsageCount?: number;
    projectOverrideCount?: number;
    projectOverrideRowCount?: number;
    jobOverrideCount?: number;
  };
  nonCarrierCounts: Record<string, number>;
  events?: Array<{
    tMs: number;
    level: string;
    step: string;
    message: string;
    projectKey?: string;
  }>;
  timedOut?: boolean;
  elapsedMs?: number;
  configNames?: string[];
  globalDefaultConfig?: string | null;
  scanErrors?: { projectKey: string; area: string; error: string }[];
  failedProjectCount?: number;
  scannedProjectCount?: number;
}

export interface ContainerExecReplaceResult {
  dryRun: boolean;
  sourceConfig: string;
  targetConfig: string;
  scanCached?: boolean;
  matchedRows: number;
  updatedRows: number;
  skippedRows: number;
  failedRows: number;
  results: Array<{
    rowId?: string;
    projectKey?: string;
    objectType?: string;
    objectId?: string;
    objectName?: string;
    surface?: string;
    rawPath?: string;
    from?: string;
    to?: string;
    status: 'planned' | 'updated' | 'skipped' | 'failed';
    error?: string;
    diag?: Record<string, unknown>;
  }>;
}
