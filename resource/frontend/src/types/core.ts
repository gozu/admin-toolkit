import type { AdoptionData } from './adoption';
import type { CodeEnv, CodeEnvCompareResult, ProvisionalCodeEnv } from './codeEnvs';
import type { ComparisonResult, ComparisonViewMode } from './comparison';
import type {
  ConnectionAuditResult,
  ConnectionCounts,
  ConnectionDetail,
  ConnectionHealthResult,
  ConnectionLocalFilesystemUsage,
  ConnectionUsageItem,
  SanityCheckMessage,
} from './connections';
import type { MailChannel } from './email';
import type { HealthScore } from './health';
import type { Cluster } from './k8s';
import type { Lifecycle, LoadingProgressState } from './lifecycle';
import type { LlmAuditResponse } from './llmAudit';
import type { LogError, LogStats } from './logs';
import type { PluginInfo } from './plugins';
import type {
  CruCostData,
  Project,
  ProjectFootprintRow,
  ProjectFootprintSummary,
  User,
  UserStats,
} from './projects';
import type {
  AuthSettings,
  CgroupSettings,
  ContainerExecDefaults,
  ContainerSettings,
  DisabledFeature,
  EnabledSettings,
  InstanceInfo,
  IntegrationSettings,
  JavaMemorySettings,
  JekSettings,
  LicenseProperties,
  MaxRunningActivities,
  ProxySettings,
  ResourceLimits,
  SecurityDefaultsSettings,
  SparkSettings,
} from './settings';
import type { DipHomeStorage, DirEntry, DirTreeData, FilesystemInfo, MemoryInfo, SystemLimits } from './system';
import type { ExecResourceConfig } from '../utils/execResources';

// Diagnostic types
export type DiagType = 'instance' | 'job' | 'fm' | 'unknown';
export type DataSource = 'zip' | 'api';

// Multi-instance host configuration. `id` is 'local' for the DSS the toolkit
// runs on, otherwise the plugin-preset name. `url` is empty for 'local'.
export interface DssHost {
  id: string;
  label: string;
  url: string;
}

export interface DssHostStatus {
  ok: boolean;
  pluginInstalled?: boolean;
  pluginVersion?: string | null;
  adminToolkitProjectExists?: boolean;
  error?: string;
}
export type DebugLevel = 'info' | 'warn' | 'error';
export type LayoutMode = 'standard' | 'ultrawide';

export interface DebugLogEntry {
  id: string;
  timestamp: string;
  message: string;
  scope?: string;
  level: DebugLevel;
}

// Extracted files map
export type ExtractedFiles = Record<string, string>;

// Full parsed data structure
export interface ParsedData {
  // Basic info
  company?: string;
  dssVersion?: string;
  pythonVersion?: string;
  diagType?: DiagType;
  lastRestartTime?: string;
  instanceInfo?: InstanceInfo;

  // System info
  cpuCores?: string;
  osInfo?: string;
  memoryInfo?: MemoryInfo;
  systemLimits?: SystemLimits;
  filesystemInfo?: FilesystemInfo[];
  /** Mount holding DIP_HOME (df -PT over the data dir) — drives the NFS and
   *  data-mount-full critical cap rules. Absent on older remote toolkits. */
  dipHomeStorage?: DipHomeStorage;

  // Settings
  enabledSettings?: EnabledSettings;
  sparkSettings?: SparkSettings;
  authSettings?: AuthSettings;
  containerSettings?: ContainerSettings;
  containerExecDefaults?: ContainerExecDefaults;
  /** Per-exec-config resource fields (flat memRequestMB/memLimitMB/cpu*) —
   *  extracted from raw settings by utils/execResources. Absent (`undefined`)
   *  ⇒ the exec-config-resources score component silently skips. */
  execResourceConfigs?: ExecResourceConfig[];
  integrationSettings?: IntegrationSettings;
  resourceLimits?: ResourceLimits;
  cgroupSettings?: CgroupSettings;
  proxySettings?: ProxySettings;
  maxRunningActivities?: MaxRunningActivities;
  jekSettings?: JekSettings;
  javaMemorySettings?: JavaMemorySettings;
  javaMemoryLimits?: JavaMemorySettings;
  disabledFeatures?: Record<string, DisabledFeature>;
  securityDefaults?: SecurityDefaultsSettings;
  ldapAuthorizedGroups?: string[];
  connectionAudit?: ConnectionAuditResult[];

  // Data collections
  connections?: ConnectionCounts;
  connectionCounts?: ConnectionCounts;
  connectionDetails?: ConnectionDetail[];
  connectionHealth?: ConnectionHealthResult[];
  connectionHealthTotal?: number | null;
  sanityCheck?: SanityCheckMessage[];
  sanityCheckMaxSeverity?: string | null;
  connectionDatasetUsages?: ConnectionUsageItem[];
  connectionLlmUsages?: ConnectionUsageItem[];
  connectionLocalFilesystemUsages?: ConnectionLocalFilesystemUsage[];
  connectionUsageTotal?: number | null;
  connectionUsageScanned?: number | null;
  connectionUsageScanErrors?: { projectKey: string; area: string; error: string }[];
  connectionUsageFailedProjectCount?: number;
  connectionUsageScannedProjectCount?: number;
  userStats?: UserStats;
  usersByProjects?: Record<string, string>;
  users?: User[];
  projects?: Project[];
  projectFootprint?: ProjectFootprintRow[];
  projectFootprintSummary?: ProjectFootprintSummary;
  projectCostData?: CruCostData;
  adoptionData?: AdoptionData;
  plugins?: string[];
  pluginDetails?: PluginInfo[];
  pluginsCount?: number;
  /** True while the deferred /api/plugins/usages scan is still in flight, so the
   *  "Projects" column shows a pending placeholder instead of "?". */
  pluginUsagesPending?: boolean;
  codeEnvs?: CodeEnv[];
  codeEnvSizes?: Record<string, number>;
  codeEnvsExpectedCount?: number;
  provisionalCodeEnvs?: ProvisionalCodeEnv[];
  codeEnvsCompare?: CodeEnvCompareResult | null;
  llmAudit?: LlmAuditResponse;
  analysisLoading?: LoadingProgressState;

  // Lifecycle (discriminated-union) fields. Every module declares one. The
  // sidebar resolver, the analysis aggregator, and the orchestrator all read
  // from these fields uniformly — no LoadingProgressState branch remains.
  summaryLoading?: Lifecycle;
  filesystemLoading?: Lifecycle;
  memoryLoading?: Lifecycle;
  cpuLoading?: Lifecycle;
  settingsLoading?: Lifecycle;
  connectionsInventoryLoading?: Lifecycle;
  connectionsHealthLoading?: Lifecycle;
  connectionUsageLoading?: Lifecycle;
  connectionsAuditLoading?: Lifecycle;
  projectCleanerLoading?: Lifecycle;
  projectFootprintLoading?: Lifecycle;
  projectComputeLoading?: Lifecycle;
  projectCostLoading?: Lifecycle;
  adoptionLoading?: Lifecycle;
  usersLoading?: Lifecycle;
  pluginsLoading?: Lifecycle;
  pluginSyncLoading?: Lifecycle;
  codeEnvCleanerLoading?: Lifecycle;
  codeEnvsLoading?: Lifecycle;
  codeEnvSizesLoading?: Lifecycle;
  codeEnvReplacementLoading?: Lifecycle;
  codeEnvsComparisonLoading?: Lifecycle;
  containerExecsLoading?: Lifecycle;
  imageCleanerLoading?: Lifecycle;
  csTemplateReplacementLoading?: Lifecycle;
  llmAuditLoading?: Lifecycle;
  k8sInsightsLoading?: Lifecycle;
  logsLoading?: Lifecycle;
  sanityCheckLoading?: Lifecycle;
  dbHealthLoading?: Lifecycle;
  reportLoading?: Lifecycle;
  // Static action page — never driven through a load ritual; declared only to
  // satisfy the registry/lifecycle contract (mirrors reportLoading/dbHealthLoading).
  feedbackLoading?: Lifecycle;
  // Agents chat loads on demand per conversation (noLoadGlyph); declared only
  // to satisfy the registry/lifecycle contract.
  agentsLoading?: Lifecycle;
  pythonVersionCounts?: Record<string, number>;
  rVersionCounts?: Record<string, number>;
  totalEnvCount?: number;
  skippedEnvCount?: number;
  clusters?: Cluster[];
  mailChannels?: MailChannel[];
  configuredMailChannel?: string;

  // License
  license?: Record<string, unknown>;
  licenseInfo?: Record<string, unknown>;
  licenseProperties?: LicenseProperties;
  hasLicenseUsage?: boolean;

  // Logs
  formattedLogErrors?: string;
  rawLogErrors?: LogError[];
  logStats?: LogStats;

  // General settings raw
  generalSettings?: Record<string, unknown>;

  // Loading state
  dataReady?: boolean;

  // Directory listing
  dirTree?: DirTreeData;
}

// Context state
export type FootprintScope = 'dss' | 'project';

export interface ApiDirTreeState {
  isLoading: boolean;
  isExpanding: boolean;
  error: string | null;
  tree: DirTreeData | null;
  expandedNodes: Map<string, DirEntry>;
  scope: FootprintScope;
  projectKey: string;
}

export interface DiagState {
  extractedFiles: ExtractedFiles;
  parsedData: ParsedData;
  activeFilter: string;
  layoutMode: LayoutMode;
  isLoading: boolean;
  error: string | null;
  diagType: DiagType;
  rootFiles: string[];
  projectFiles: string[];
  dsshome: string;
  originalFile: File | null; // Original zip file for deferred extraction
  dataSource: DataSource;
  debugLogs: DebugLogEntry[];
  apiDirTree: ApiDirTreeState;
  focusedConnectionFilter: { name?: string; type?: string } | null;
  focusedUserFilter: { login?: string } | null;
}

// Context actions
export type DiagAction =
  | { type: 'SET_LOADING'; payload: boolean }
  | { type: 'SET_ERROR'; payload: string | null }
  | { type: 'SET_EXTRACTED_FILES'; payload: ExtractedFiles }
  | { type: 'SET_PARSED_DATA'; payload: Partial<ParsedData> }
  | { type: 'SET_ACTIVE_FILTER'; payload: string }
  | { type: 'SET_LAYOUT_MODE'; payload: LayoutMode }
  | { type: 'SET_DIAG_TYPE'; payload: DiagType }
  | { type: 'SET_ROOT_FILES'; payload: string[] }
  | { type: 'SET_PROJECT_FILES'; payload: string[] }
  | { type: 'SET_DSSHOME'; payload: string }
  | { type: 'SET_ORIGINAL_FILE'; payload: File | null }
  | { type: 'SET_DATA_SOURCE'; payload: DataSource }
  | {
      type: 'ADD_DEBUG_LOG';
      payload: Omit<DebugLogEntry, 'id' | 'timestamp'> & { timestamp?: string };
    }
  | { type: 'CLEAR_DEBUG_LOGS' }
  | { type: 'UPSERT_PROVISIONAL_CODE_ENVS'; payload: ProvisionalCodeEnv[] }
  | { type: 'CLEAR_PROVISIONAL_CODE_ENVS' }
  | { type: 'APPEND_PARTIAL_CODE_ENVS'; payload: CodeEnv[] }
  | { type: 'APPEND_PARTIAL_PROJECT_FOOTPRINT'; payload: ProjectFootprintRow[] }
  | { type: 'SET_API_DIR_TREE'; payload: Partial<ApiDirTreeState> }
  | { type: 'SET_API_DIR_TREE_EXPANDED_NODE'; payload: { path: string; node: DirEntry } }
  | {
      type: 'SET_FOCUSED_CONNECTION_FILTER';
      payload: { name?: string; type?: string } | null;
    }
  | { type: 'SET_FOCUSED_USER_FILTER'; payload: { login?: string } | null }
  | { type: 'RESET' };

export type PageId =
  | 'mission-control'
  | 'summary'
  | 'filesystem'
  | 'resources'
  | 'projects'
  | 'users'
  | 'adoption'
  | 'code-envs'
  | 'code-envs-cleaner'
  | 'code-envs-comparison'
  | 'container-execs'
  | 'cs-template-replacement'
  | 'connections-inventory'
  | 'connections-insights'
  | 'connections-health'
  | 'connections-fs-migration'
  | 'logs'
  | 'sanity-check'
  | 'project-cleaner'
  | 'project-compute'
  | 'project-cost'
  | 'plugins-installed'
  | 'plugins'
  | 'report'
  | 'db-health'
  | 'image-cleaner'
  | 'llm-audit'
  | 'k8s-insights'
  | 'settings'
  | 'feedback'
  | 'agents'
  | 'agent-tuning';

export type AppMode = 'landing' | 'single' | 'comparison' | 'tools' | 'settings';

// A single diagnostic file with all its parsed data
export interface DiagFile {
  id: string;
  filename: string;
  uploadedAt: Date;
  fileSize: number;
  parsedData: ParsedData;
  extractedFiles: ExtractedFiles;
  diagType: DiagType;
  dsshome: string;
  originalFile: File | null;
  healthScore: HealthScore | null;
}

// Comparison state
export interface ComparisonState {
  before: DiagFile | null;
  after: DiagFile | null;
  result: ComparisonResult | null;
  viewMode: ComparisonViewMode;
  isProcessingBefore: boolean;
  isProcessingAfter: boolean;
}

// Extended DiagState with comparison support
export interface DiagStateWithComparison extends DiagState {
  mode: AppMode;
  activePage: PageId;
  comparison: ComparisonState;
}

// New actions for comparison
export type ComparisonAction =
  | { type: 'SET_MODE'; payload: AppMode }
  | { type: 'SET_ACTIVE_PAGE'; payload: PageId }
  | { type: 'SET_COMPARISON_FILE'; payload: { slot: 'before' | 'after'; file: DiagFile } }
  | { type: 'CLEAR_COMPARISON_FILE'; payload: 'before' | 'after' }
  | { type: 'SET_COMPARISON_RESULT'; payload: ComparisonResult }
  | { type: 'SET_COMPARISON_VIEW_MODE'; payload: ComparisonViewMode }
  | {
      type: 'SET_COMPARISON_PROCESSING';
      payload: { slot: 'before' | 'after'; isProcessing: boolean };
    }
  | { type: 'RESET_COMPARISON' };

// Combined action type
export type DiagActionWithComparison = DiagAction | ComparisonAction;
