export type LlmAuditStatus = 'current' | 'obsolete' | 'ripoff' | 'unknown' | 'not_applicable';

export interface LlmAuditUsageAsset {
  assetType: 'recipe' | 'notebook' | 'knowledge_bank' | 'agent';
  assetName: string;
  recipeType?: string | null;
  /** Project the asset lives in (rows are deduped by llmId, so assets span projects). */
  projectKey?: string;
}

export interface LlmAuditRow {
  projectKey: string;
  projectName?: string;
  /** All projects whose catalog exposes this LLM (rows are deduped by llmId). */
  projectKeys?: string[];
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
  /** Set when the LiteLLM pricing catalog could not be fetched (air-gapped /
   * TLS-intercepted instance) — rows still list, verdicts degrade to unknown. */
  pricingError?: string | null;
  totalElapsedMs?: number;
}

export interface LlmAuditResponse {
  rows: LlmAuditRow[];
  summary: LlmAuditSummary;
  pricingFetchedAt?: string | null;
  pricingError?: string | null;
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
