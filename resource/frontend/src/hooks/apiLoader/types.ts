/**
 * Response shapes for the live-mode API loader. Moved verbatim from the old
 * monolithic useApiDataLoader.ts — one interface per endpoint payload.
 */
import type {
  ParsedData,
  ConnectionCounts,
  ConnectionDetail,
  User,
  Project,
  MailChannel,
  PluginInfo,
} from '../../types';

export interface OverviewResponse extends Partial<ParsedData> {
  sparkVersion?: string;
}

export interface ConnectionsResponse {
  connections?: ConnectionCounts;
  connectionDetails?: ConnectionDetail[];
}

export interface UsersResponse {
  userStats?: Record<string, string | number>;
  users?: User[];
}

export interface ProjectsResponse {
  projects?: Project[];
}

export interface ProjectFootprintResponse {
  projects?: ParsedData['projectFootprint'];
  summary?: ParsedData['projectFootprintSummary'];
}

export interface BenchEventLike {
  tMs?: number;
  level?: 'info' | 'warn' | 'error';
  step?: string;
  projectKey?: string;
  message?: string;
  elapsedMs?: number;
}

export interface CodeEnvsResponse {
  codeEnvs?: ParsedData['codeEnvs'];
  pythonVersionCounts?: Record<string, number>;
  rVersionCounts?: Record<string, number>;
  totalEnvCount?: number;
  skippedEnvCount?: number;
  summary?: {
    benchmark?: {
      enabled?: boolean;
      projectLimit?: number;
      projectSelection?: string;
      timeoutMs?: number;
      timedOut?: boolean;
      timeoutAtStep?: string | null;
      totalElapsedMs?: number;
      remainingMs?: number;
      selectedProjectCount?: number;
      selectedEnvKeyCount?: number;
      steps?: Array<{ name?: string; elapsedMs?: number; qps?: number; calls?: number }>;
      apiCalls?: Array<{ operation?: string; elapsedMs?: number; qps?: number; calls?: number }>;
      events?: BenchEventLike[];
    };
  };
}

export interface CodeEnvsProgressResponse {
  runId?: string;
  status?: string;
  error?: string | null;
  droppedUntil?: number;
  next?: number;
  summary?: {
    progressPct?: number;
    phase?: string;
    selectedProjects?: number;
    projectUsageDone?: number;
    envDetailsTotal?: number;
    envDetailsDone?: number;
    timedOut?: boolean;
    timeoutAtStep?: string | null;
    totalElapsedMs?: number;
    remainingMs?: number;
  };
  events?: BenchEventLike[];
  partialRows?: Array<Record<string, unknown>>;
  partialRowsNext?: number;
}

export interface ProjectFootprintProgressResponse {
  runId?: string;
  status?: string;
  error?: string | null;
  droppedUntil?: number;
  next?: number;
  summary?: {
    progressPct?: number;
    phase?: string;
    selectedProjects?: number;
    projectFootprintDone?: number;
    projectUsageDone?: number;
    projectAggregateDone?: number;
    timedOut?: boolean;
    timeoutAtStep?: string | null;
    totalElapsedMs?: number;
    remainingMs?: number;
  };
  events?: BenchEventLike[];
  partialRows?: Array<Record<string, unknown>>;
  partialRowsNext?: number;
}

export interface LlmAuditProgressResponse {
  runId?: string;
  status?: string;
  error?: string | null;
  next?: number;
  summary?: {
    progressPct?: number;
    phase?: string;
    projectsTotal?: number;
    projectsDone?: number;
    llmRowsTotal?: number;
    totalElapsedMs?: number;
  };
  events?: BenchEventLike[];
  partialRows?: Array<Record<string, unknown>>;
  partialRowsNext?: number;
}

export interface PluginsResponse {
  plugins?: string[];
  pluginDetails?: PluginInfo[];
  pluginsCount?: number;
}

export type PluginUsageFields = Pick<
  PluginInfo,
  'projectsUsingCount' | 'projectsUsing' | 'missingTypes' | 'usagesError'
>;

export interface PluginUsagesResponse {
  usagesByPlugin?: Record<string, PluginUsageFields>;
}

export interface MailChannelsResponse {
  channels?: MailChannel[];
  configuredMailChannel?: string;
}

export interface LogErrorsResponse {
  formattedLogErrors?: string;
  rawLogErrors?: ParsedData['rawLogErrors'];
  logStats?: ParsedData['logStats'];
}
