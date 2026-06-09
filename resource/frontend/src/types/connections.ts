// Connection counts
export type ConnectionCounts = Record<string, number>;

// Connection detail with optional driver info
export interface ConnectionDetail {
  name: string;
  type: string;
  driverClassName?: string;
}

// Connection health-test result (streamed via SSE)
export interface ConnectionHealthResult {
  name: string;
  type: string;
  status: 'ok' | 'fail' | 'skipped';
  error?: string;
}

export interface SanityCheckMessage {
  severity: 'ERROR' | 'WARNING' | 'INFO' | 'SUCCESS';
  code: string;
  title: string;
  details: string;
  message: string;
  extraInfoSummary?: string | null;
  extraInfoDetails?: string | null;
}

// Connection usage mapping (from /api/connections/usages SSE)
export interface ConnectionDatasetUsage {
  projectKey: string;
  projectName: string;
  datasetName: string;
  datasetType: string;
}

export interface ConnectionLlmUsage {
  projectKey: string;
  projectName: string;
  recipeName: string;
  recipeType: string;
  llmId: string;
}

export interface ConnectionLocalFilesystemUsage {
  owner: string;
  ownerEmail?: string;
  projectKey: string;
  projectName: string;
  objectType: 'dataset' | 'folder' | string;
  objectId: string;
  objectName: string;
  objectSubtype?: string;
  connection: string;
  path?: string;
}

export interface ConnectionUsageItem {
  name: string;
  type: string;
  projects: ConnectionDatasetUsage[] | ConnectionLlmUsage[];
  projectCount: number;
  datasetCount?: number;
  recipeCount?: number;
}

// Per-connection configuration audit result (from /api/connections/audit)
export interface ConnectionAuditResult {
  name: string;
  type: string;
  configIssues: string[];
  severity: 'critical' | 'warning' | 'info';
}
