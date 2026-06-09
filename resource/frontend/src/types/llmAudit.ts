export type LlmAuditStatus = 'current' | 'obsolete' | 'ripoff' | 'unknown' | 'not_applicable';

export interface LlmAuditUsageAsset {
  assetType: 'recipe' | 'notebook' | 'knowledge_bank' | 'agent';
  assetName: string;
  recipeType?: string | null;
}

export interface LlmAuditRow {
  projectKey: string;
  projectName?: string;
  llmId: string;
  friendlyName?: string;
  friendlyNameShort?: string;
  type?: string;
  connection?: string | null;
  rawModel?: string | null;
  effectiveModel?: string | null;
  matchedKey?: string | null;
  status: LlmAuditStatus;
  provider?: string | null;
  family?: string | null;
  currentModel?: string | null;
  modelInputPrice?: number | null;
  modelOutputPrice?: number | null;
  currentInputPrice?: number | null;
  currentOutputPrice?: number | null;
  projectsUsing?: number;
  referencingProjects?: string[];
  usageAssets?: LlmAuditUsageAsset[];
}

export interface LlmAuditSummary {
  llmsTotal: number;
  projectsScanned: number;
  countsByStatus: Record<LlmAuditStatus, number>;
  distinctModelsByStatus: { obsolete: number; ripoff: number };
  pricingFetchedAt?: string | null;
  totalElapsedMs?: number;
}

export interface LlmAuditResponse {
  rows: LlmAuditRow[];
  summary: LlmAuditSummary;
  pricingFetchedAt?: string | null;
  events?: Array<{
    tMs: number;
    level: string;
    step: string;
    message: string;
    projectKey?: string;
  }>;
  scanErrors?: { projectKey: string; area: string; error: string }[];
  failedProjectCount?: number;
  scannedProjectCount?: number;
}
