import type {
  ConnectionAuditResult,
  ConnectionDatasetUsage,
  ConnectionHealthResult,
  ConnectionLlmUsage,
  ConnectionUsageItem,
  ParsedData,
} from '../types';

/**
 * LLM mesh connection types — must mirror the set in `ConnectionUsageCard.tsx`.
 * A connection whose `type` is in this set is treated as an LLM-asset connection
 * (its dataset/usage rows count toward `llmAssetCount`).
 */
export const LLM_MESH_TYPES = new Set<string>([
  'OpenAI', 'AzureOpenAI', 'Anthropic', 'Bedrock', 'CustomLLM',
  'SnowflakeCortex', 'VertexAILLM', 'HuggingFaceLocal', 'RemoteMCP',
  'Pinecone', 'AzureAISearch', 'ElasticSearch',
  'Cohere', 'MistralAI', 'StabilityAI', 'SageMakerLLM', 'Milvus',
  'NVIDIANIMLLM', 'AzureAIFoundry', 'AzureLLM',
]);

export interface ConnectionInsightsRow {
  name: string;
  type: string;
  driver?: string;
  /** Union of projects appearing in datasetUsages + llmUsages for this connection. */
  projectCount: number;
  datasetCount: number;
  recipeCount: number;
  /** Count of LLM mesh assets (sum of `projects.length` for LLM-type usage items). */
  llmAssetCount: number;
  /** Number of local filesystem usage rows where `connection === name`. */
  fsUsageCount: number;
  auditSeverity: 'critical' | 'warning' | 'info' | null;
  auditIssues: string[];
  healthStatus: 'ok' | 'fail' | 'skipped' | null;
  healthError?: string;
}

function projectKeyOf(p: ConnectionDatasetUsage | ConnectionLlmUsage): string {
  return p.projectKey;
}

/**
 * Build per-connection insights rows by joining the slices that already live
 * in ParsedData. Pure, O(n) over connections + their per-connection rows.
 */
export function buildConnectionInsightsRows(parsedData: ParsedData): ConnectionInsightsRow[] {
  const details = parsedData.connectionDetails || [];
  if (details.length === 0) return [];

  const datasetUsages = parsedData.connectionDatasetUsages || [];
  const llmUsages = parsedData.connectionLlmUsages || [];
  const fsUsages = parsedData.connectionLocalFilesystemUsages || [];
  const audit = parsedData.connectionAudit || [];
  const health = parsedData.connectionHealth || [];

  const datasetByName = new Map<string, ConnectionUsageItem>();
  for (const u of datasetUsages) datasetByName.set(u.name, u);
  const llmByName = new Map<string, ConnectionUsageItem>();
  for (const u of llmUsages) llmByName.set(u.name, u);

  const fsCountByName = new Map<string, number>();
  for (const f of fsUsages) {
    fsCountByName.set(f.connection, (fsCountByName.get(f.connection) || 0) + 1);
  }

  const auditByName = new Map<string, ConnectionAuditResult>();
  for (const a of audit) auditByName.set(a.name, a);

  const healthByName = new Map<string, ConnectionHealthResult>();
  for (const h of health) healthByName.set(h.name, h);

  const rows: ConnectionInsightsRow[] = details.map((d) => {
    const ds = datasetByName.get(d.name);
    const llm = llmByName.get(d.name);

    // Union of project keys across dataset + LLM usage
    const projectKeys = new Set<string>();
    if (ds) {
      for (const p of ds.projects) projectKeys.add(projectKeyOf(p));
    }
    if (llm) {
      for (const p of llm.projects) projectKeys.add(projectKeyOf(p));
    }

    const datasetCount = ds?.datasetCount ?? (ds ? ds.projects.length : 0);
    const recipeCount = llm?.recipeCount ?? (llm ? llm.projects.length : 0);

    // LLM asset count: if connection type is LLM mesh, count asset references
    // from both the dataset slice (mesh dataset rows) and the LLM slice.
    let llmAssetCount = 0;
    if (LLM_MESH_TYPES.has(d.type)) {
      if (ds) llmAssetCount += ds.projects.length;
      if (llm) llmAssetCount += llm.projects.length;
    }

    const fsUsageCount = fsCountByName.get(d.name) || 0;

    const a = auditByName.get(d.name);
    const auditIssues = a?.configIssues || [];
    const auditSeverity =
      a && auditIssues.length > 0 ? a.severity : null;

    const h = healthByName.get(d.name);

    return {
      name: d.name,
      type: d.type,
      driver: d.driverClassName,
      projectCount: projectKeys.size,
      datasetCount,
      recipeCount,
      llmAssetCount,
      fsUsageCount,
      auditSeverity,
      auditIssues,
      healthStatus: h ? h.status : null,
      healthError: h?.error,
    };
  });

  return rows;
}
