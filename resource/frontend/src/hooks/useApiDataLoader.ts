import { useEffect, useRef } from 'react';
import { useDiag, DEFAULT_DSSHOME } from '../context/DiagContext';
import { GeneralSettingsParser } from '../parsers/GeneralSettingsParser';
import { JavaMemoryParser } from '../parsers/JavaMemoryParser';
import { ProjectStandardsParser } from '../parsers/ProjectStandardsParser';
import type {
  ParsedData,
  ConnectionCounts,
  ConnectionDetail,
  User,
  Project,
  MailChannel,
  CodeEnv,
  ProvisionalCodeEnv,
  ProjectFootprintRow,
  PluginInfo,
  ConnectionHealthResult,
  LlmAuditResponse,
  ConnectionAuditResult,
  Lifecycle,
} from '../types';
import { fetchJson, fetchRaw, fetchText } from '../utils/api';
import { useApiDirTree } from './useApiDirTree';
import { useConnectionUsageScan } from './useConnectionUsageScan';
import { SHARED_LOADING_FIELDS, type LifecycleFieldName } from '../utils/moduleRegistry';
import {
  deriveAnalysisLifecycle,
  lifecycleToLoadingProgress,
} from '../utils/analysisLifecycle';
import { parseSseStream } from '../utils/sseStream';
import { containerExecsScan } from '../state/containerExecsStore';
import { startSqlPushdownScan } from '../state/sqlPushdownScan';
import { runSanityCheck } from '../state/sanityCheckScan';
import { imageCleanerDetectScan } from '../state/imageCleanerStore';
import { managedFoldersScan } from '../state/managedFoldersStore';

interface OverviewResponse extends Partial<ParsedData> {
  sparkVersion?: string;
}

interface ConnectionsResponse {
  connections?: ConnectionCounts;
  connectionDetails?: ConnectionDetail[];
}

interface UsersResponse {
  userStats?: Record<string, string | number>;
  users?: User[];
}

interface ProjectsResponse {
  projects?: Project[];
}

interface ProjectFootprintResponse {
  projects?: ParsedData['projectFootprint'];
  summary?: ParsedData['projectFootprintSummary'];
}

interface CodeEnvsResponse {
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
      events?: Array<{
        tMs?: number;
        level?: 'info' | 'warn' | 'error';
        step?: string;
        projectKey?: string;
        message?: string;
        elapsedMs?: number;
      }>;
    };
  };
}

interface CodeEnvsProgressResponse {
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
  events?: Array<{
    tMs?: number;
    level?: 'info' | 'warn' | 'error';
    step?: string;
    projectKey?: string;
    message?: string;
    elapsedMs?: number;
  }>;
  partialRows?: Array<Record<string, unknown>>;
  partialRowsNext?: number;
}

interface ProjectFootprintProgressResponse {
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
  events?: Array<{
    tMs?: number;
    level?: 'info' | 'warn' | 'error';
    step?: string;
    projectKey?: string;
    message?: string;
    elapsedMs?: number;
  }>;
  partialRows?: Array<Record<string, unknown>>;
  partialRowsNext?: number;
}

interface PluginsResponse {
  plugins?: string[];
  pluginDetails?: PluginInfo[];
  pluginsCount?: number;
}

type PluginUsageFields = Pick<
  PluginInfo,
  'projectsUsingCount' | 'projectsUsing' | 'missingTypes' | 'usagesError'
>;

interface PluginUsagesResponse {
  usagesByPlugin?: Record<string, PluginUsageFields>;
}

interface MailChannelsResponse {
  channels?: MailChannel[];
  configuredMailChannel?: string;
}

interface LogErrorsResponse {
  formattedLogErrors?: string;
  rawLogErrors?: ParsedData['rawLogErrors'];
  logStats?: ParsedData['logStats'];
}

export function useApiDataLoader(enabled: boolean, reloadKey = 0) {
  const { dispatch } = useDiag();
  const LIVE_PROGRESS_TIMEOUT_MS = 120000;
  // Reuse the dir-tree hook's memoized loadRoot (stable: useCallback([dispatch]))
  // so the autostart block can populate the global tree without duplicating its
  // fetch/abort/debug logic. Held in a ref synced each render so the long-lived
  // load effect calls the current fn without stale-closure / exhaustive-deps churn.
  const { loadRoot: loadDirTreeRoot } = useApiDirTree();
  const loadDirTreeRootRef = useRef(loadDirTreeRoot);
  loadDirTreeRootRef.current = loadDirTreeRoot;

  // Connections usage scan (shared by Insights / Usage / FS-Migration). Was
  // previously fired by useSessionOrchestrator; relocated here with identical
  // gating so the orchestrator could be deleted.
  const { scan: scanConnectionUsage } = useConnectionUsageScan();
  useEffect(() => {
    if (!enabled) return;
    void scanConnectionUsage();
  }, [enabled, reloadKey, scanConnectionUsage]);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const log = (message: string, level: 'info' | 'warn' | 'error' = 'info') => {
      dispatch({ type: 'ADD_DEBUG_LOG', payload: { message, scope: 'api-loader', level } });
    };
    const nowMs = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());
    const fmtMs = (start: number) => `${Math.round(nowMs() - start)}ms`;
    const getErrorMessage = (err: unknown) => (err instanceof Error ? err.message : String(err));
    const isAbortError = (err: unknown) => {
      if (err instanceof DOMException && err.name === 'AbortError') return true;
      if (err instanceof Error && err.name === 'AbortError') return true;
      const message = getErrorMessage(err);
      return /aborted|aborterror/i.test(message);
    };
    const abortPendingRequest = (controller: unknown) => {
      if (
        controller &&
        typeof controller === 'object' &&
        'abort' in controller &&
        typeof controller.abort === 'function'
      ) {
        controller.abort();
      }
    };
    type BenchEventLike = {
      tMs?: number;
      level?: 'info' | 'warn' | 'error';
      step?: string;
      projectKey?: string;
      message?: string;
      elapsedMs?: number;
    };
    const cleanToken = (value: unknown): string => {
      const raw = value == null ? '' : String(value);
      const compact = raw.replace(/\s+/g, ' ').trim();
      if (!compact) return '-';
      return compact.replace(/;/g, ',');
    };
    const benchMs = (value: unknown): string => {
      if (typeof value === 'number' && Number.isFinite(value)) return `${value.toFixed(2)}ms`;
      return '-';
    };
    const benchEventLine = (code: 'ce' | 'pjft', event: BenchEventLike): string => {
      const t = benchMs(event.tMs);
      const key = cleanToken(event.projectKey);
      const step = cleanToken(event.step);
      const message = cleanToken(event.message);
      const elapsed = benchMs(event.elapsedMs);
      if (elapsed !== '-' && message !== '-' && message !== step) {
        return `bench;${code};${t};${key};${step};${elapsed};${message}`;
      }
      if (elapsed !== '-') {
        return `bench;${code};${t};${key};${step};${elapsed}`;
      }
      if (message !== '-' && message !== step) {
        return `bench;${code};${t};${key};${step};${message}`;
      }
      return `bench;${code};${t};${key};${step}`;
    };
    const benchSummaryLine = (code: 'ce' | 'pjft', parts: string[]): string =>
      `bench;${code};summary;${parts.join(';')}`;
    const benchStepLine = (
      code: 'ce' | 'pjft',
      kind: 'step' | 'api',
      name: string,
      calls: number,
      elapsedMs: number,
      qps: number,
    ): string =>
      `bench;${code};${kind};${cleanToken(name)};calls=${calls};elapsed=${benchMs(elapsedMs)};qps=${Number(qps || 0).toFixed(2)}`;
    const shouldLogProgressEvent = (event: {
      level?: 'info' | 'warn' | 'error';
      step?: string;
      projectKey?: string;
    }): boolean => {
      // Always show warnings and errors (including per-project ones)
      const level = event.level === 'warn' || event.level === 'error' ? event.level : 'info';
      if (level !== 'info') return true;
      // Suppress all per-project/per-env info-level events
      if (event.projectKey) return false;
      // Suppress per-env usage check lines (too spammy: 238 lines)
      const step = (event.step || '').replace(/[-\s]/g, '_').toLowerCase();
      if (step === 'code_env_usage_check') return false;
      return true;
    };
    const basicProjectsEnabled = (() => {
      if (typeof window === 'undefined') return false;
      try {
        const query = new URLSearchParams(window.location.search);
        return query.get('basicProjects') === '1';
      } catch {
        return false;
      }
    })();
    const withTimeout = <T>(promise: Promise<T>, label: string, ms: number): Promise<T> =>
      new Promise<T>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
        promise.then(
          (value) => {
            clearTimeout(timer);
            resolve(value);
          },
          (error) => {
            clearTimeout(timer);
            reject(error);
          },
        );
      });

    const endpointTimings: Array<{ label: string; durationMs: number; status: 'ok' | 'fail' | 'skip' }> = [];
    const recordTiming = (label: string, durationMs: number, status: 'ok' | 'fail' | 'skip' = 'ok') => {
      endpointTimings.push({ label, durationMs: Math.round(durationMs), status });
    };

    const run = async () => {
      log(`Diag Parser version v${__APP_VERSION__}`);
      log('Starting live data load');
      log(
        basicProjectsEnabled
          ? 'Basic /api/projects endpoint enabled (query: basicProjects=1)'
          : 'Basic /api/projects endpoint disabled by default (add query basicProjects=1 to re-enable)',
      );
      dispatch({ type: 'SET_LOADING', payload: true });
      dispatch({ type: 'SET_ERROR', payload: null });
      dispatch({ type: 'SET_DIAG_TYPE', payload: 'instance' });
      dispatch({ type: 'SET_DSSHOME', payload: DEFAULT_DSSHOME });

      try {
        const overviewStart = nowMs();
        const overviewStartTs = new Date().toISOString().slice(11, 19);
        log('GET /api/overview');
        const overview = await fetchJson<OverviewResponse>('/api/overview');
        log(`GET /api/overview OK (${fmtMs(overviewStart)}) [${overviewStartTs}→${new Date().toISOString().slice(11, 19)}]`);
        recordTiming('/api/overview', nowMs() - overviewStart);
        let rawSettings: Record<string, unknown> = {};
        let rawProjectStandards: Record<string, unknown> = {};
        {
          const settingsStart = nowMs();
          const settingsStartTs = new Date().toISOString().slice(11, 19);
          log('GET /api/settings/raw');
          const psStart = nowMs();
          log('GET /api/project-standards/raw');
          const [settingsRes, psRes] = await Promise.allSettled([
            fetchJson<Record<string, unknown>>('/api/settings/raw'),
            fetchJson<Record<string, unknown>>('/api/project-standards/raw'),
          ]);
          if (settingsRes.status === 'fulfilled') {
            rawSettings = settingsRes.value;
            log(`GET /api/settings/raw OK (${fmtMs(settingsStart)}) [${settingsStartTs}→${new Date().toISOString().slice(11, 19)}]`);
            recordTiming('/api/settings/raw', nowMs() - settingsStart);
          } else {
            log('GET /api/settings/raw failed, continuing with defaults', 'warn');
            rawSettings = {};
          }
          if (psRes.status === 'fulfilled') {
            rawProjectStandards = psRes.value;
            log(`GET /api/project-standards/raw OK (${fmtMs(psStart)})`);
            recordTiming('/api/project-standards/raw', nowMs() - psStart);
          } else {
            log('GET /api/project-standards/raw failed, defaulting to NONE modes', 'warn');
            rawProjectStandards = {};
          }
        }

        if (cancelled) return;

        let currentParsedData: ParsedData = {
          ...overview,
        };

        if (overview.sparkVersion) {
          currentParsedData.sparkSettings = {
            ...(currentParsedData.sparkSettings || {}),
            'Spark Version': overview.sparkVersion,
          };
        }

        dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
        const sessionStartedAt = new Date().toISOString();
        const patchLifecycle = (field: LifecycleFieldName, value: Lifecycle) => {
          currentParsedData = { ...currentParsedData, [field]: value };
          dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
          updateAnalysisLoading();
        };
        const markDone = (field: LifecycleFieldName, message?: string, isEmpty = false) => {
          const startedAt =
            (currentParsedData[field] as Lifecycle | undefined)?.phase === 'queued' ||
            (currentParsedData[field] as Lifecycle | undefined)?.phase === 'running'
              ? // re-use the queued/running startedAt where possible
                (currentParsedData[field] as { startedAt?: string }).startedAt ||
                sessionStartedAt
              : sessionStartedAt;
          patchLifecycle(field, {
            phase: 'done',
            startedAt,
            finishedAt: new Date().toISOString(),
            isEmpty,
            message,
          });
        };
        const markRunning = (field: LifecycleFieldName, message?: string) => {
          const now = new Date().toISOString();
          patchLifecycle(field, {
            phase: 'running',
            startedAt: now,
            progressPct: 0,
            message,
            updatedAt: now,
          });
        };
        const markError = (field: LifecycleFieldName, error: string) => {
          const now = new Date().toISOString();
          const startedAt =
            (currentParsedData[field] as { startedAt?: string } | undefined)?.startedAt ||
            now;
          patchLifecycle(field, {
            phase: 'error',
            startedAt,
            finishedAt: now,
            error,
            progressPct: 0,
          });
        };
        const updateAnalysisLoading = () => {
          const lc = deriveAnalysisLifecycle(currentParsedData, SHARED_LOADING_FIELDS, sessionStartedAt);
          const next = lifecycleToLoadingProgress(lc);
          currentParsedData = { ...currentParsedData, analysisLoading: next };
          dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
        };
        // The single chokepoint that ties a fetch's Lifecycle to its promise:
        // ONE place opens `running` (at call), ONE place settles done/error (on
        // resolve/reject). Because .then(onFulfilled, onRejected) covers both
        // outcomes, a started-but-never-finished glyph is impossible. Returns a
        // settled result so existing `if (res.status === 'fulfilled')` branches
        // stay. An aborted promise (reload/unmount teardown) is a no-op — no red
        // glyph. `swallow` turns a non-fatal tail rejection into done(empty).
        type TrackField = LifecycleFieldName | readonly LifecycleFieldName[];
        interface TrackOpts {
          startMessage?: string;
          doneMessage?: (value: unknown) => string;
          isEmpty?: (value: unknown) => boolean;
          swallow?: boolean;
        }
        const track = <T>(
          field: TrackField,
          promise: Promise<T>,
          opts: TrackOpts = {},
        ): Promise<PromiseSettledResult<T>> => {
          const fields = (Array.isArray(field) ? field : [field]) as LifecycleFieldName[];
          for (const f of fields) markRunning(f, opts.startMessage);
          return promise.then(
            (value) => {
              for (const f of fields)
                markDone(f, opts.doneMessage?.(value), opts.isEmpty?.(value) ?? false);
              return { status: 'fulfilled', value } as PromiseSettledResult<T>;
            },
            (reason) => {
              if (isAbortError(reason))
                return { status: 'rejected', reason } as PromiseSettledResult<T>;
              const message = getErrorMessage(reason);
              for (const f of fields) {
                if (opts.swallow) markDone(f, message, true);
                else markError(f, message);
              }
              return { status: 'rejected', reason } as PromiseSettledResult<T>;
            },
          );
        };
        // Mark trivially-synchronous modules done once the overview + settings
        // load completes — they have no async work of their own, but they
        // must still pass through the queued→done ritual.
        markDone('summaryLoading', 'Overview ready');
        markDone('settingsLoading', 'Settings loaded');
        markDone('filesystemLoading', 'Filesystem ready', (overview.filesystemInfo?.length ?? 0) === 0);
        markDone('memoryLoading', 'Memory ready');
        log('Phase 1 complete (overview + settings)');

        // Phase 2: load secondary data in parallel
        log('Phase 2 starting');
        const timedFetch = <T>(label: string, promise: Promise<T>): Promise<T> => {
          const s = nowMs();
          return promise.then(
            (v) => { recordTiming(label, nowMs() - s); return v; },
            (e) => { recordTiming(label, nowMs() - s, 'fail'); throw e; },
          );
        };
        // Glyph-bearing Phase-2 members are tracked individually (markRunning at
        // call, done/error on settle); the bare members (plugins/java-memory/
        // mail-channels) drive no sidebar glyph. All six fetches fire eagerly
        // here, so they still run in parallel.
        const connectionsTracked = track(
          'connectionsInventoryLoading',
          timedFetch('/api/connections', fetchJson<ConnectionsResponse>('/api/connections')),
          {
            startMessage: 'Loading connections',
            doneMessage: (v) =>
              `Loaded ${Object.keys((v as ConnectionsResponse).connections || {}).length} connection types`,
            isEmpty: (v) => Object.keys((v as ConnectionsResponse).connections || {}).length === 0,
          },
        );
        const usersTracked = track(
          'usersLoading',
          timedFetch('/api/users', fetchJson<UsersResponse>('/api/users')),
          {
            startMessage: 'Loading users',
            doneMessage: (v) => `${(v as UsersResponse).users?.length || 0} users`,
            isEmpty: (v) => ((v as UsersResponse).users?.length || 0) === 0,
          },
        );
        const connectionAuditTracked = track(
          'connectionsAuditLoading',
          timedFetch(
            '/api/connections/audit',
            fetchJson<{ connections: ConnectionAuditResult[]; summary: Record<string, number> }>(
              '/api/connections/audit',
            ),
          ),
          {
            startMessage: 'Auditing connections',
            doneMessage: (v) =>
              `${((v as { connections?: ConnectionAuditResult[] }).connections || []).length} findings`,
            isEmpty: (v) =>
              ((v as { connections?: ConnectionAuditResult[] }).connections || []).length === 0,
          },
        );
        const pluginsTracked = track(
          'pluginsLoading',
          timedFetch('/api/plugins', fetchJson<PluginsResponse>('/api/plugins')),
          {
            startMessage: 'Loading installed plugins',
            doneMessage: (v) => `${(v as PluginsResponse).pluginsCount || 0} plugins`,
            isEmpty: (v) => ((v as PluginsResponse).pluginsCount || 0) === 0,
          },
        );
        const javaMemoryBare = timedFetch('/api/java-memory', fetchText('/api/java-memory'));
        const mailChannelsBare = timedFetch(
          '/api/mail-channels',
          fetchJson<MailChannelsResponse>('/api/mail-channels'),
        );

        // track() never rejects → Promise.all is safe and unwraps to the inner
        // settled result, so `if (res.status === 'fulfilled')` branches stay.
        const [connectionsRes, usersRes, connectionAuditRes, pluginsRes] = await Promise.all([
          connectionsTracked,
          usersTracked,
          connectionAuditTracked,
          pluginsTracked,
        ]);
        const [javaMemoryRes, mailChannelsRes] = await Promise.allSettled([
          javaMemoryBare,
          mailChannelsBare,
        ]);

        if (cancelled) return;

        if (connectionsRes.status === 'fulfilled') {
          currentParsedData = {
            ...currentParsedData,
            connections: connectionsRes.value.connections || {},
            connectionCounts: connectionsRes.value.connections || {},
            connectionDetails: connectionsRes.value.connectionDetails || [],
          };
          dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
          log(
            `Loaded connections (${Object.keys(currentParsedData.connections || {}).length} types)`,
          );
        } else {
          log(`Failed /api/connections: ${getErrorMessage(connectionsRes.reason)}`, 'warn');
        }

        if (usersRes.status === 'fulfilled') {
          currentParsedData = {
            ...currentParsedData,
            userStats: usersRes.value.userStats || {},
            users: usersRes.value.users || [],
          };
          dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
          log(`Loaded users (${currentParsedData.users?.length || 0})`);
        } else {
          log(`Failed /api/users: ${getErrorMessage(usersRes.reason)}`, 'warn');
        }

        if (pluginsRes.status === 'fulfilled') {
          currentParsedData = {
            ...currentParsedData,
            plugins: pluginsRes.value.plugins || [],
            pluginDetails: pluginsRes.value.pluginDetails || [],
            pluginsCount: pluginsRes.value.pluginsCount || 0,
            // Usage counts arrive later via the deferred /api/plugins/usages scan.
            pluginUsagesPending: (pluginsRes.value.pluginDetails?.length || 0) > 0,
          };
          dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
          log(`Loaded plugins (${currentParsedData.pluginsCount || 0})`);
        } else {
          log(`Failed /api/plugins: ${getErrorMessage(pluginsRes.reason)}`, 'warn');
        }

        if (javaMemoryRes.status === 'fulfilled') {
          const parser = new JavaMemoryParser();
          const result = parser.parse(javaMemoryRes.value, 'env-default.sh');
          currentParsedData = {
            ...currentParsedData,
            javaMemorySettings: result.javaMemorySettings || {},
            javaMemoryLimits: result.javaMemorySettings || {},
            dssVersion: result.dssVersion || overview.dssVersion,
          };
          dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
          log('Loaded Java memory settings');
        } else {
          log(`Failed /api/java-memory: ${getErrorMessage(javaMemoryRes.reason)}`, 'warn');
        }

        if (mailChannelsRes.status === 'fulfilled') {
          currentParsedData = {
            ...currentParsedData,
            mailChannels: mailChannelsRes.value.channels || [],
            configuredMailChannel: mailChannelsRes.value.configuredMailChannel,
          };
          dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
          log(`Loaded mail channels (${currentParsedData.mailChannels?.length || 0})`);
        } else {
          log(`Failed /api/mail-channels: ${getErrorMessage(mailChannelsRes.reason)}`, 'warn');
        }

        if (connectionAuditRes.status === 'fulfilled') {
          const auditFindings = connectionAuditRes.value.connections || [];
          currentParsedData = {
            ...currentParsedData,
            connectionAudit: auditFindings,
          };
          dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
          log(`Loaded connection audit (${auditFindings.length} findings)`);
        } else {
          log(`Failed /api/connections/audit: ${getErrorMessage(connectionAuditRes.reason)}`, 'warn');
        }

        // Apply general settings parser after we have memory and java data
        const settingsParser = new GeneralSettingsParser();
        settingsParser.setExternalData({
          sparkSettings: currentParsedData.sparkSettings,
          memoryInfo: currentParsedData.memoryInfo,
          javaMemorySettings: currentParsedData.javaMemorySettings,
          resourceLimits: currentParsedData.resourceLimits,
        });
        const settingsResult = settingsParser.parse(
          JSON.stringify(rawSettings),
          'general-settings.json',
        );

        currentParsedData = {
          ...currentParsedData,
          generalSettings: settingsResult.generalSettings || {},
          enabledSettings: settingsResult.enabledSettings || {},
          sparkSettings: {
            ...(currentParsedData.sparkSettings || {}),
            ...(settingsResult.sparkSettings || {}),
          },
          maxRunningActivities: settingsResult.maxRunningActivities || {},
          jekSettings: settingsResult.jekSettings || {},
          authSettings: settingsResult.authSettings || {},
          containerSettings: settingsResult.containerSettings || {},
          integrationSettings: settingsResult.integrationSettings || {},
          resourceLimits: settingsResult.resourceLimits || {},
          cgroupSettings: settingsResult.cgroupSettings || {},
          proxySettings: settingsResult.proxySettings || {},
          disabledFeatures: settingsResult.disabledFeatures || {},
          securityDefaults: settingsResult.securityDefaults || {},
          ldapAuthorizedGroups: settingsResult.ldapAuthorizedGroups || [],
        };
        dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
        log('Applied GeneralSettings parser');

        // Derive containerExecDefaults from project-standards (modes) +
        // GeneralSettings (executionConfigsCount). On failure the parser
        // yields userCodeMode='NONE', visualRecipesMode='NONE' so the card
        // cleanly falls back to "local backend for execution".
        const containerSettingsRaw = (rawSettings as { containerSettings?: { executionConfigs?: unknown[] } })
          .containerSettings;
        const executionConfigsCount = Array.isArray(containerSettingsRaw?.executionConfigs)
          ? containerSettingsRaw!.executionConfigs!.length
          : 0;
        const projectStandardsResult = new ProjectStandardsParser().parse(
          JSON.stringify(rawProjectStandards),
          'project-standards.json',
        );
        currentParsedData = {
          ...currentParsedData,
          containerExecDefaults: {
            executionConfigsCount,
            userCodeMode: projectStandardsResult.userCodeMode,
            visualRecipesMode: projectStandardsResult.visualRecipesMode,
          },
        };
        dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
        log(`Applied ProjectStandards parser (configs=${executionConfigsCount}, userCode=${projectStandardsResult.userCodeMode}, visualRecipes=${projectStandardsResult.visualRecipesMode})`);

        // Allow UI to render after core data is available
        dispatch({ type: 'SET_LOADING', payload: false });
        log('Core data ready, released loading state');

        // Fetch backend settings for configurable timeouts
        let beSettings: Record<string, number> = {};
        try {
          beSettings = await fetchJson<{ current: Record<string, number>; defaults: Record<string, number> }>('/api/settings').then((d) => d.current);
          log('Backend settings loaded');
        } catch { log('Backend settings fetch failed, using defaults', 'warn'); }

        // Phase 3: heavier endpoints
        log('Phase 3 starting');
        const timed = <T>(path: string, timeoutMs: number): Promise<T> => {
          const started = nowMs();
          const startTs = new Date().toISOString().slice(11, 19);
          log(`GET ${path}`);
          return withTimeout(fetchJson<T>(path), path, timeoutMs).then(
            (value) => {
              log(`GET ${path} OK (${fmtMs(started)}) [${startTs}→${new Date().toISOString().slice(11, 19)}]`);
              recordTiming(path, nowMs() - started);
              return value;
            },
            (err) => {
              log(`GET ${path} FAIL (${fmtMs(started)}) [${startTs}→${new Date().toISOString().slice(11, 19)}]`);
              recordTiming(path, nowMs() - started, 'fail');
              throw err;
            },
          );
        };
        const settle = async <T>(promise: Promise<T>): Promise<PromiseSettledResult<T>> => {
          try {
            const value = await promise;
            return { status: 'fulfilled', value };
          } catch (reason) {
            return { status: 'rejected', reason };
          }
        };
        const settledError = (result: PromiseSettledResult<unknown>) =>
          result.status === 'rejected' ? getErrorMessage(result.reason) : 'no payload';

        let codeEnvsDone = false;
        let projectFootprintDone = false;
        let projectFootprintStarted = false;
        const slowHeavyTimer = setTimeout(() => {
          const waiting: string[] = [];
          if (!codeEnvsDone) waiting.push('/api/code-envs');
          if (projectFootprintStarted && !projectFootprintDone)
            waiting.push('/api/project-footprint');
          if (waiting.length > 0) {
            log(`Heavy endpoints still loading after 8000ms: ${waiting.join(', ')}`, 'warn');
          }
        }, 8000);

        // Code-env sizes load as a slow tail tracked through `codeEnvSizesLoading`
        // (swallow:true) so the Code Envs page stays `running` — and the global
        // "Analysis complete" is withheld — until the ~slow /api/code-envs/sizes
        // request lands, without a sizes failure turning the page red. Held in a
        // closure ref so the await-tails step can join it before dataReady.
        let codeEnvSizesTracked: Promise<PromiseSettledResult<unknown>> | null = null;
        const loadCodeEnvSizes = () => {
          const sizesStart = nowMs();
          codeEnvSizesTracked = track(
            'codeEnvSizesLoading',
            fetchJson<{ sizes: Record<string, number> }>('/api/code-envs/sizes')
              .then((r) => {
                if (r?.sizes && typeof r.sizes === 'object') {
                  dispatch({ type: 'SET_PARSED_DATA', payload: { codeEnvSizes: r.sizes } });
                  log(
                    `Loaded /api/code-envs/sizes (${Object.keys(r.sizes).length} entries, ${fmtMs(sizesStart)})`,
                  );
                } else {
                  log(
                    `/api/code-envs/sizes returned no sizes object (${fmtMs(sizesStart)})`,
                    'warn',
                  );
                }
                log('Pre-warming /api/dir-tree after global footprint');
                fetchJson('/api/dir-tree?maxDepth=3&scope=dss').catch(() => { /* pre-warm optional */ });
                return r;
              })
              .catch((err: unknown) => {
                const name = err instanceof Error ? err.name : typeof err;
                const status = (err as { status?: number } | null)?.status;
                const raw = err instanceof Error ? err.message : String(err);
                const msg = raw.length > 200 ? raw.slice(0, 200) + '…' : raw;
                log(
                  `Failed /api/code-envs/sizes (${fmtMs(sizesStart)}): ${name}${status ? ` status=${status}` : ''} — ${msg}`,
                  'warn',
                );
                throw err;
              }),
            { startMessage: 'Loading code-env sizes', swallow: true },
          );
        };

        const codeEnvFields: LifecycleFieldName[] = [
          'codeEnvsLoading',
          'codeEnvReplacementLoading',
        ];

        const runCodeEnvs = async () => {
          dispatch({ type: 'CLEAR_PROVISIONAL_CODE_ENVS' });
          currentParsedData = {
            ...currentParsedData,
            codeEnvsExpectedCount: undefined,
          };
          dispatch({ type: 'SET_PARSED_DATA', payload: { codeEnvsExpectedCount: undefined } });
          let codeEnvsProgressActive = true;
          // Use a sentinel so the first poll returns only the current run id (status=replaced),
          // avoiding replay of stale events from previous runs.
          let codeEnvsProgressRunId: string | undefined = '__pending__';
          let codeEnvsProgressCursor = 0;
          let codeEnvsProgressEventsSeen = 0;
          let codeEnvsProgressWarned = false;
          let codeEnvsProgressAbortController: AbortController | null = null;
          let codeEnvsUsageScanTotal: number | null = null;
          const codeEnvsProgressPath = '/api/code-envs/progress';
          let codeEnvsProgressPathLogged = false;
          const seenCodeEnvEventKeys = new Set<string>();
          const progressEventKey = (event: {
            tMs?: number;
            step?: string;
            projectKey?: string;
            message?: string;
            elapsedMs?: number;
          }) =>
            `${event.tMs ?? ''}|${event.step ?? ''}|${event.projectKey ?? ''}|${event.message ?? ''}|${event.elapsedMs ?? ''}`;
          const setExpectedCodeEnvCount = (nextCount: number | null | undefined) => {
            const normalized =
              typeof nextCount === 'number' && Number.isFinite(nextCount) && nextCount >= 0
                ? Math.floor(nextCount)
                : undefined;
            if (currentParsedData.codeEnvsExpectedCount === normalized) return;
            currentParsedData = {
              ...currentParsedData,
              codeEnvsExpectedCount: normalized,
            };
            dispatch({ type: 'SET_PARSED_DATA', payload: { codeEnvsExpectedCount: normalized } });
          };
          const parseUsageCheckMessage = (message: string) => {
            const match = message.match(/^\[(\d+)\/(\d+)\]\s+(.+?)\s+[\u2014\u2013-]\s+(.+)$/u);
            if (!match) return null;
            const scanIndex = Number.parseInt(match[1], 10);
            const scanTotal = Number.parseInt(match[2], 10);
            const name = match[3].trim();
            const status = match[4].trim();
            const isSkipped = /skipped/i.test(status);
            const usageMatch = status.match(/(\d+)\s+usage\(s\)/i);
            const usageCount = /unused/i.test(status)
              ? 0
              : usageMatch
                ? Number.parseInt(usageMatch[1], 10)
                : NaN;
            return {
              scanIndex: Number.isFinite(scanIndex) ? scanIndex : undefined,
              scanTotal: Number.isFinite(scanTotal) ? scanTotal : undefined,
              name,
              status,
              isSkipped,
              usageCount: Number.isFinite(usageCount) ? Math.max(0, usageCount) : null,
            };
          };
          const toProvisionalRow = (parsed: {
            scanIndex?: number;
            scanTotal?: number;
            name: string;
            status: string;
            isSkipped: boolean;
            usageCount: number | null;
          }): ProvisionalCodeEnv | null => {
            if (!parsed.name) return null;
            if (parsed.isSkipped) {
              return {
                name: parsed.name,
                usageCount: -1,
                statusLabel: parsed.status,
                isSkipped: true,
                scanIndex: parsed.scanIndex,
                scanTotal: parsed.scanTotal,
                updatedAt: new Date().toISOString(),
              };
            }
            if (parsed.usageCount == null) return null;
            return {
              name: parsed.name,
              usageCount: parsed.usageCount,
              statusLabel: parsed.status,
              scanIndex: parsed.scanIndex,
              scanTotal: parsed.scanTotal,
              updatedAt: new Date().toISOString(),
            };
          };
          const replayCodeEnvProgressEvents = (events: Array<BenchEventLike>) => {
            const provisionalRows: ProvisionalCodeEnv[] = [];
            events.forEach((event) => {
              const key = progressEventKey(event);
              if (seenCodeEnvEventKeys.has(key)) return;
              seenCodeEnvEventKeys.add(key);
              const normalizedStep = String(event.step || '')
                .trim()
                .toLowerCase();
              if (normalizedStep === 'code_env_usage_scan_start') {
                const startMatch = String(event.message || '').match(/checking\s+(\d+)\s+code envs/i);
                const scannedTotal = startMatch ? Number.parseInt(startMatch[1], 10) : NaN;
                if (Number.isFinite(scannedTotal) && scannedTotal > 0) {
                  codeEnvsUsageScanTotal = scannedTotal;
                }
              }
              if (normalizedStep === 'code_env_usage_check') {
                const parsed = parseUsageCheckMessage(String(event.message || '').trim());
                if (parsed) {
                  if (typeof parsed.scanTotal === 'number' && parsed.scanTotal > 0) {
                    codeEnvsUsageScanTotal = parsed.scanTotal;
                  }
                  const provisional = toProvisionalRow(parsed);
                  if (provisional) provisionalRows.push(provisional);
                }
              }
              if (shouldLogProgressEvent(event)) {
                codeEnvsProgressEventsSeen += 1;
                const eventLevel =
                  event.level === 'warn' || event.level === 'error' ? event.level : 'info';
                log(benchEventLine('ce', event), eventLevel);
              }
            });
            if (codeEnvsUsageScanTotal != null) {
              const expectedFromScan = Math.max(0, codeEnvsUsageScanTotal);
              setExpectedCodeEnvCount(expectedFromScan);
            }
            if (provisionalRows.length > 0) {
              dispatch({ type: 'UPSERT_PROVISIONAL_CODE_ENVS', payload: provisionalRows });
            }
          };

          let codeEnvsRowsSince = 0;
          const codeEnvsPartialBuffer: CodeEnv[] = [];
          const pollCodeEnvProgress = async () => {
            while (!cancelled && codeEnvsProgressActive) {
              try {
                const query = new URLSearchParams();
                query.set('since', String(codeEnvsProgressCursor));
                query.set('rowsSince', String(codeEnvsRowsSince));
                if (codeEnvsProgressRunId) {
                  query.set('runId', codeEnvsProgressRunId);
                }
                codeEnvsProgressAbortController = new AbortController();
                if (!codeEnvsProgressPathLogged) {
                  log(`bench;ce;progress;path=${codeEnvsProgressPath}`);
                  codeEnvsProgressPathLogged = true;
                }
                const payload = await withTimeout(
                  fetchJson<CodeEnvsProgressResponse>(
                    `${codeEnvsProgressPath}?${query.toString()}`,
                    { signal: codeEnvsProgressAbortController.signal },
                  ),
                  codeEnvsProgressPath,
                  LIVE_PROGRESS_TIMEOUT_MS,
                );
                // Rows-only poll: keep the expected-count signal that feeds the
                // provisional-row table; no % math / lifecycle writes (the glyph
                // is the binary spinner driven by track()).
                const progressSummary = payload.summary || {};
                const envDetailsTotal = Number(progressSummary.envDetailsTotal || 0);
                if (envDetailsTotal > 0) {
                  setExpectedCodeEnvCount(envDetailsTotal);
                }
                if (payload.runId && payload.runId !== codeEnvsProgressRunId) {
                  codeEnvsProgressRunId = payload.runId;
                  codeEnvsProgressCursor = 0;
                  codeEnvsRowsSince = 0;
                  codeEnvsPartialBuffer.length = 0;
                  codeEnvsUsageScanTotal = null;
                  seenCodeEnvEventKeys.clear();
                  codeEnvsProgressEventsSeen = 0;
                  setExpectedCodeEnvCount(undefined);
                  dispatch({ type: 'CLEAR_PROVISIONAL_CODE_ENVS' });
                  continue;
                }
                const nextCursor =
                  typeof payload.next === 'number' ? payload.next : codeEnvsProgressCursor;
                if (Array.isArray(payload.events) && payload.events.length > 0) {
                  replayCodeEnvProgressEvents(payload.events);
                }
                codeEnvsProgressCursor = nextCursor;
                if (Array.isArray(payload.partialRows) && payload.partialRows.length > 0) {
                  const rows = payload.partialRows as unknown as CodeEnv[];
                  codeEnvsPartialBuffer.push(...rows);
                  dispatch({ type: 'APPEND_PARTIAL_CODE_ENVS', payload: rows });
                }
                if (typeof payload.partialRowsNext === 'number') {
                  codeEnvsRowsSince = payload.partialRowsNext;
                }
                if (payload.status === 'error' && payload.error) {
                  log(`bench;ce;progress-error;${cleanToken(payload.error)}`, 'error');
                }
              } catch (err) {
                if ((!codeEnvsProgressActive || cancelled) && isAbortError(err)) {
                  break;
                }
                if (!codeEnvsProgressWarned) {
                  codeEnvsProgressWarned = true;
                  log(
                    `Code env live progress polling unavailable: ${getErrorMessage(err)}`,
                    'warn',
                  );
                }
              } finally {
                codeEnvsProgressAbortController = null;
              }
              if (!codeEnvsProgressActive) break;
              await new Promise((resolve) => setTimeout(resolve, 1000));
            }
          };

          const codeEnvsProgressPromise = pollCodeEnvProgress();
          const codeEnvsRes = await track(
            codeEnvFields,
            timed<CodeEnvsResponse>('/api/code-envs', beSettings.fe_timeout_code_envs ?? 620000),
            {
              startMessage: 'Starting code env analysis',
              isEmpty: (v) => ((v as CodeEnvsResponse)?.codeEnvs || []).length === 0,
            },
          );
          codeEnvsProgressActive = false;
          abortPendingRequest(codeEnvsProgressAbortController);
          await codeEnvsProgressPromise;
          codeEnvsDone = true;
          if (cancelled) return;
          if (codeEnvsRes.status === 'fulfilled' && codeEnvsRes.value) {
            currentParsedData = {
              ...currentParsedData,
              codeEnvs: codeEnvsRes.value.codeEnvs || [],
              codeEnvsExpectedCount: (codeEnvsRes.value.codeEnvs || []).length,
              pythonVersionCounts: codeEnvsRes.value.pythonVersionCounts || {},
              rVersionCounts: codeEnvsRes.value.rVersionCounts || {},
              totalEnvCount: codeEnvsRes.value.totalEnvCount,
              skippedEnvCount: codeEnvsRes.value.skippedEnvCount,
            };
            dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
            dispatch({ type: 'CLEAR_PROVISIONAL_CODE_ENVS' });
            loadCodeEnvSizes();
            log(`Loaded code envs (${currentParsedData.codeEnvs?.length || 0})`);
            const benchmark = codeEnvsRes.value.summary?.benchmark;
            if (benchmark?.enabled) {
              log(
                benchSummaryLine('ce', [
                  `limit=${benchmark.projectLimit ?? '?'}`,
                  `selection=${cleanToken(benchmark.projectSelection ?? 'n/a')}`,
                  `elapsed=${benchMs(benchmark.totalElapsedMs)}`,
                  `timeout=${benchmark.timeoutMs ?? 0}ms`,
                  `timedOut=${Boolean(benchmark.timedOut)}`,
                  `selectedProjects=${benchmark.selectedProjectCount ?? '?'}`,
                  `selectedEnvKeys=${benchmark.selectedEnvKeyCount ?? '?'}`,
                ]),
              );
              const slowSteps = (benchmark.steps || [])
                .filter((step) => typeof step.elapsedMs === 'number')
                .sort((a, b) => (b.elapsedMs || 0) - (a.elapsedMs || 0))
                .slice(0, 8);
              slowSteps.forEach((step) => {
                log(
                  benchStepLine(
                    'ce',
                    'step',
                    step.name || 'unknown',
                    step.calls ?? 0,
                    step.elapsedMs ?? 0,
                    step.qps ?? 0,
                  ),
                );
              });
              const slowOps = (benchmark.apiCalls || [])
                .filter((op) => typeof op.elapsedMs === 'number')
                .sort((a, b) => (b.elapsedMs || 0) - (a.elapsedMs || 0))
                .slice(0, 8);
              slowOps.forEach((op) => {
                log(
                  benchStepLine(
                    'ce',
                    'api',
                    op.operation || 'unknown',
                    op.calls ?? 0,
                    op.elapsedMs ?? 0,
                    op.qps ?? 0,
                  ),
                );
              });
              if (codeEnvsProgressEventsSeen > 0) {
                log(`bench;ce;progress-events;count=${codeEnvsProgressEventsSeen}`);
              } else {
                replayCodeEnvProgressEvents(benchmark.events || []);
              }
            }
          } else {
            if (codeEnvsPartialBuffer.length > 0) {
              currentParsedData = {
                ...currentParsedData,
                codeEnvs: codeEnvsPartialBuffer,
                codeEnvsExpectedCount: codeEnvsPartialBuffer.length,
              };
              dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
              dispatch({ type: 'CLEAR_PROVISIONAL_CODE_ENVS' });
              // Recovered envs from the progress stream → override track()'s
              // error with done for the code-env-derived fields.
              for (const f of codeEnvFields)
                markDone(
                  f,
                  `Code env analysis completed (${codeEnvsPartialBuffer.length} envs from progress)`,
                  false,
                );
              loadCodeEnvSizes();
              log(`Failed /api/code-envs but recovered ${codeEnvsPartialBuffer.length} envs from progress`, 'warn');
            } else {
              dispatch({ type: 'CLEAR_PROVISIONAL_CODE_ENVS' });
              // track() marked the code-env fields error; loadCodeEnvSizes() is
              // never called, so mark its lifecycle done(empty) lest it hang
              // queued and block the global "Analysis complete" aggregate.
              markDone('codeEnvSizesLoading', 'No code envs', true);
            }
            log(`Failed /api/code-envs: ${settledError(codeEnvsRes)}`, 'warn');
          }
        };

        const runProjectFootprint = async () => {
          let projectFootprintProgressActive = true;
          let projectFootprintProgressAbortController: AbortController | null = null;
          // Use a sentinel so the first poll only syncs run id (status=replaced) instead of replaying stale events.
          let projectFootprintProgressRunId: string | undefined = '__pending__';
          let projectFootprintProgressCursor = 0;
          let projectFootprintProgressWarned = false;
          const projectFootprintProgressPath = '/api/project-footprint/progress';
          const seenProjectProgressEventKeys = new Set<string>();
          let projectUsageScanTotal: number | null = null;
          const projectProgressEventKey = (event: {
            tMs?: number;
            step?: string;
            projectKey?: string;
            message?: string;
            elapsedMs?: number;
          }) =>
            `${event.tMs ?? ''}|${event.step ?? ''}|${event.projectKey ?? ''}|${event.message ?? ''}|${event.elapsedMs ?? ''}`;
          const setExpectedCodeEnvCountFromProject = (nextCount: number | null | undefined) => {
            const normalized =
              typeof nextCount === 'number' && Number.isFinite(nextCount) && nextCount >= 0
                ? Math.floor(nextCount)
                : undefined;
            if (currentParsedData.codeEnvsExpectedCount === normalized) return;
            currentParsedData = {
              ...currentParsedData,
              codeEnvsExpectedCount: normalized,
            };
            dispatch({ type: 'SET_PARSED_DATA', payload: { codeEnvsExpectedCount: normalized } });
          };
          const parseProjectUsageCheckMessage = (message: string) => {
            const match = message.match(/^\[(\d+)\/(\d+)\]\s+(.+?)\s+[\u2014\u2013-]\s+(.+)$/u);
            if (!match) return null;
            const scanIndex = Number.parseInt(match[1], 10);
            const scanTotal = Number.parseInt(match[2], 10);
            const name = match[3].trim();
            const status = match[4].trim();
            const isSkipped = /skipped/i.test(status);
            const usageMatch = status.match(/(\d+)\s+usage\(s\)/i);
            const usageCount = /unused/i.test(status)
              ? 0
              : usageMatch
                ? Number.parseInt(usageMatch[1], 10)
                : NaN;
            return {
              scanIndex: Number.isFinite(scanIndex) ? scanIndex : undefined,
              scanTotal: Number.isFinite(scanTotal) ? scanTotal : undefined,
              name,
              status,
              isSkipped,
              usageCount: Number.isFinite(usageCount) ? Math.max(0, usageCount) : null,
            };
          };
          const toProjectProvisionalRow = (parsed: {
            scanIndex?: number;
            scanTotal?: number;
            name: string;
            status: string;
            isSkipped: boolean;
            usageCount: number | null;
          }): ProvisionalCodeEnv | null => {
            if (!parsed.name) return null;
            if (parsed.isSkipped) {
              return {
                name: parsed.name,
                usageCount: -1,
                statusLabel: parsed.status,
                isSkipped: true,
                scanIndex: parsed.scanIndex,
                scanTotal: parsed.scanTotal,
                updatedAt: new Date().toISOString(),
              };
            }
            if (parsed.usageCount == null) return null;
            return {
              name: parsed.name,
              usageCount: parsed.usageCount,
              statusLabel: parsed.status,
              scanIndex: parsed.scanIndex,
              scanTotal: parsed.scanTotal,
              updatedAt: new Date().toISOString(),
            };
          };
          const replayProjectProgressEvents = (events: Array<BenchEventLike>) => {
            const provisionalRows: ProvisionalCodeEnv[] = [];
            events.forEach((event) => {
              const key = projectProgressEventKey(event);
              if (seenProjectProgressEventKeys.has(key)) return;
              seenProjectProgressEventKeys.add(key);
              const normalizedStep = String(event.step || '')
                .trim()
                .toLowerCase();
              if (normalizedStep === 'code_env_usage_scan_start') {
                const startMatch = String(event.message || '').match(/checking\s+(\d+)\s+code envs/i);
                const scannedTotal = startMatch ? Number.parseInt(startMatch[1], 10) : NaN;
                if (Number.isFinite(scannedTotal) && scannedTotal > 0) {
                  projectUsageScanTotal = scannedTotal;
                }
              }
              if (normalizedStep === 'code_env_usage_check') {
                const parsed = parseProjectUsageCheckMessage(String(event.message || '').trim());
                if (parsed) {
                  if (typeof parsed.scanTotal === 'number' && parsed.scanTotal > 0) {
                    projectUsageScanTotal = parsed.scanTotal;
                  }
                  const provisional = toProjectProvisionalRow(parsed);
                  if (provisional) provisionalRows.push(provisional);
                }
              }
              if (shouldLogProgressEvent(event)) {
                const eventLevel =
                  event.level === 'warn' || event.level === 'error' ? event.level : 'info';
                log(benchEventLine('pjft', event), eventLevel);
              }
            });
            if (projectUsageScanTotal != null) {
              const expectedFromScan = Math.max(0, projectUsageScanTotal);
              setExpectedCodeEnvCountFromProject(expectedFromScan);
            }
            if (provisionalRows.length > 0) {
              dispatch({ type: 'UPSERT_PROVISIONAL_CODE_ENVS', payload: provisionalRows });
            }
          };

          let projectFootprintRowsSince = 0;
          const pollProjectFootprintProgress = async () => {
            while (!cancelled && projectFootprintProgressActive) {
              try {
                const query = new URLSearchParams();
                query.set('since', String(projectFootprintProgressCursor));
                query.set('rowsSince', String(projectFootprintRowsSince));
                if (projectFootprintProgressRunId) {
                  query.set('runId', projectFootprintProgressRunId);
                }
                projectFootprintProgressAbortController = new AbortController();
                const payload = await withTimeout(
                  fetchJson<ProjectFootprintProgressResponse>(
                    `${projectFootprintProgressPath}?${query.toString()}`,
                    { signal: projectFootprintProgressAbortController.signal },
                  ),
                  projectFootprintProgressPath,
                  LIVE_PROGRESS_TIMEOUT_MS,
                );
                if (payload.runId && payload.runId !== projectFootprintProgressRunId) {
                  projectFootprintProgressRunId = payload.runId;
                  projectFootprintProgressCursor = 0;
                  projectFootprintRowsSince = 0;
                  seenProjectProgressEventKeys.clear();
                  projectUsageScanTotal = null;
                  continue;
                }
                const nextCursor =
                  typeof payload.next === 'number' ? payload.next : projectFootprintProgressCursor;
                projectFootprintProgressCursor = nextCursor;
                // Rows-only poll: stream partial rows + events; no % math /
                // lifecycle writes (the glyph is track()'s binary spinner).
                if (Array.isArray(payload.events) && payload.events.length > 0) {
                  replayProjectProgressEvents(payload.events);
                }
                if (Array.isArray(payload.partialRows) && payload.partialRows.length > 0) {
                  const rows = payload.partialRows as unknown as ProjectFootprintRow[];
                  dispatch({ type: 'APPEND_PARTIAL_PROJECT_FOOTPRINT', payload: rows });
                }
                if (typeof payload.partialRowsNext === 'number') {
                  projectFootprintRowsSince = payload.partialRowsNext;
                }
              } catch (err) {
                if ((!projectFootprintProgressActive || cancelled) && isAbortError(err)) {
                  break;
                }
                if (!projectFootprintProgressWarned) {
                  projectFootprintProgressWarned = true;
                  log(
                    `Project footprint live progress polling unavailable: ${getErrorMessage(err)}`,
                    'warn',
                  );
                }
              } finally {
                projectFootprintProgressAbortController = null;
              }
              if (!projectFootprintProgressActive) break;
              await new Promise((resolve) => setTimeout(resolve, 1000));
            }
          };

          const projectFootprintProgressPromise = pollProjectFootprintProgress();
          const projectFootprintRes = await track(
            'projectFootprintLoading',
            timed<ProjectFootprintResponse>('/api/project-footprint', beSettings.fe_timeout_project_footprint ?? 620000),
            {
              startMessage: 'Starting project analysis',
              isEmpty: (v) => ((v as ProjectFootprintResponse)?.projects || []).length === 0,
            },
          );
          projectFootprintProgressActive = false;
          abortPendingRequest(projectFootprintProgressAbortController);
          await projectFootprintProgressPromise;
          projectFootprintDone = true;
          if (cancelled) return;
          if (projectFootprintRes.status === 'fulfilled' && projectFootprintRes.value) {
            currentParsedData = {
              ...currentParsedData,
              projectFootprint: projectFootprintRes.value.projects || [],
              projectFootprintSummary: projectFootprintRes.value.summary,
            };
            dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
            log(
              `Loaded project footprint (${currentParsedData.projectFootprint?.length || 0} projects)`,
            );
            const benchmark = (
              projectFootprintRes.value.summary as Record<string, unknown> | undefined
            )?.benchmark as
              | {
                  enabled?: boolean;
                  projectLimit?: number;
                  projectSelection?: string;
                  timeoutMs?: number;
                  timedOut?: boolean;
                  totalElapsedMs?: number;
                  steps?: Array<{
                    name?: string;
                    elapsedMs?: number;
                    qps?: number;
                    calls?: number;
                  }>;
                  apiCalls?: Array<{
                    operation?: string;
                    elapsedMs?: number;
                    qps?: number;
                    calls?: number;
                  }>;
                  events?: Array<{
                    tMs?: number;
                    level?: 'info' | 'warn' | 'error';
                    step?: string;
                    projectKey?: string;
                    message?: string;
                    elapsedMs?: number;
                  }>;
                }
              | undefined;
            if (benchmark?.enabled) {
              log(
                benchSummaryLine('pjft', [
                  `limit=${benchmark.projectLimit ?? '?'}`,
                  `selection=${cleanToken(benchmark.projectSelection ?? 'n/a')}`,
                  `elapsed=${benchMs(benchmark.totalElapsedMs)}`,
                  `timeout=${benchmark.timeoutMs ?? 0}ms`,
                  `timedOut=${Boolean(benchmark.timedOut)}`,
                  `rows=${currentParsedData.projectFootprint?.length || 0}`,
                ]),
              );
              const slowStep = (benchmark.steps || [])
                .filter((step) => typeof step.elapsedMs === 'number')
                .sort((a, b) => (b.elapsedMs || 0) - (a.elapsedMs || 0))
                .slice(0, 3);
              slowStep.forEach((step) => {
                log(
                  benchStepLine(
                    'pjft',
                    'step',
                    step.name || 'unknown',
                    step.calls ?? 0,
                    step.elapsedMs ?? 0,
                    step.qps ?? 0,
                  ),
                );
              });
              const slowOps = (benchmark.apiCalls || [])
                .filter((op) => typeof op.elapsedMs === 'number')
                .sort((a, b) => (b.elapsedMs || 0) - (a.elapsedMs || 0))
                .slice(0, 5);
              slowOps.forEach((op) => {
                log(
                  benchStepLine(
                    'pjft',
                    'api',
                    op.operation || 'unknown',
                    op.calls ?? 0,
                    op.elapsedMs ?? 0,
                    op.qps ?? 0,
                  ),
                );
              });
              replayProjectProgressEvents(benchmark.events || []);
            }
          } else {
            log(`Failed /api/project-footprint: ${settledError(projectFootprintRes)}`, 'warn');
          }
        };

        const runLlmAudit = async () => {
          // The /api/llm-audit/progress poll streamed no rows (only a % the
          // binary spinner no longer shows), so it's gone — track() drives the
          // single fetch's running → done/error glyph.
          const llmAuditRes = await track(
            'llmAuditLoading',
            timed<LlmAuditResponse>('/api/llm-audit', beSettings.fe_timeout_llm_audit ?? 620000),
            {
              startMessage: 'Starting LLM model audit',
              isEmpty: (v) => ((v as LlmAuditResponse)?.rows?.length || 0) === 0,
            },
          );
          if (cancelled) return;
          if (llmAuditRes.status === 'fulfilled' && llmAuditRes.value) {
            currentParsedData = { ...currentParsedData, llmAudit: llmAuditRes.value };
            dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
            const summary = llmAuditRes.value.summary || {
              countsByStatus: {},
              distinctModelsByStatus: { obsolete: 0, ripoff: 0 },
              llmsTotal: 0,
              projectsScanned: 0,
            };
            const c = (summary as { countsByStatus?: Record<string, number> }).countsByStatus || {};
            log(
              `Loaded LLM audit: ${llmAuditRes.value.rows?.length || 0} profile(s) — ` +
                `${c.ripoff || 0} overpriced, ${c.obsolete || 0} obsolete, ${c.unknown || 0} unknown`,
            );
          } else {
            log(`Failed /api/llm-audit: ${settledError(llmAuditRes)}`, 'warn');
          }
        };

        // Per-plugin usage scan, split out of /api/plugins so the cheap plugin
        // list renders fast in Phase 2 and the expensive get_plugin().list_usages()
        // fan-out fills the "projects using" column in asynchronously here.
        const runPluginUsages = async () => {
          const usagesRes = await settle(
            timed<PluginUsagesResponse>('/api/plugins/usages', 620000),
          );
          if (cancelled) return;
          if (usagesRes.status === 'fulfilled' && usagesRes.value) {
            const byId = usagesRes.value.usagesByPlugin || {};
            const merged = (currentParsedData.pluginDetails || []).map((row) =>
              byId[row.id] ? { ...row, ...byId[row.id] } : row,
            );
            currentParsedData = { ...currentParsedData, pluginDetails: merged, pluginUsagesPending: false };
            dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
            log(`Loaded plugin usages (${Object.keys(byId).length} plugins)`);
          } else {
            currentParsedData = { ...currentParsedData, pluginUsagesPending: false };
            dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
            log(`Failed /api/plugins/usages: ${settledError(usagesRes)}`, 'warn');
          }
        };

        const runProjects = async () => {
          const projectsRes: PromiseSettledResult<ProjectsResponse | null> = basicProjectsEnabled
            ? await settle(timed<ProjectsResponse>('/api/projects', beSettings.fe_timeout_projects ?? 45000))
            : { status: 'fulfilled', value: null };
          if (cancelled) return;
          if (!basicProjectsEnabled) {
            recordTiming('/api/projects', 0, 'skip');
            log('Skipped /api/projects in lean live mode');
            currentParsedData = {
              ...currentParsedData,
              projects: [],
            };
            dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
          } else if (projectsRes.status === 'fulfilled' && projectsRes.value) {
            currentParsedData = {
              ...currentParsedData,
              projects: projectsRes.value.projects || [],
            };
            dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
            log(`Loaded projects (${currentParsedData.projects?.length || 0})`);
          } else {
            log(`Failed /api/projects: ${settledError(projectsRes)}`, 'warn');
          }
        };

        const runLogs = async () => {
          const logsRes = await track(
            'logsLoading',
            timed<LogErrorsResponse>('/api/logs/errors', beSettings.fe_timeout_logs ?? 30000),
            {
              startMessage: 'Loading log errors',
              isEmpty: (v) => ((v as LogErrorsResponse).logStats?.['Displayed Errors'] || 0) === 0,
            },
          );
          if (cancelled) return;
          if (logsRes.status === 'fulfilled' && logsRes.value) {
            const displayedErrors = logsRes.value.logStats?.['Displayed Errors'] || 0;
            currentParsedData = {
              ...currentParsedData,
              formattedLogErrors: logsRes.value.formattedLogErrors || 'No log errors found',
              rawLogErrors: logsRes.value.rawLogErrors || [],
              logStats: logsRes.value.logStats || {
                'Total Lines': 0,
                'Unique Errors': 0,
                'Displayed Errors': 0,
              },
            };
            dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
            if (displayedErrors === 0) {
              log('Loaded /api/logs/errors but no recent errors were extracted', 'warn');
            } else {
              log(`Loaded log errors (${displayedErrors} displayed)`);
            }
          } else {
            log(`Failed /api/logs/errors: ${settledError(logsRes)}`, 'warn');
            currentParsedData = {
              ...currentParsedData,
              formattedLogErrors: 'Failed to load log errors (endpoint timed out or unavailable)',
              rawLogErrors: [],
              logStats: { 'Total Lines': 0, 'Unique Errors': 0, 'Displayed Errors': 0 },
            };
            dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
          }
        };

        // Connection health scan — the whole SSE consumer is wrapped as one
        // tracked promise: track() opens `running` at call and settles
        // done/error when the stream ends or throws. Per-conn rows still stream
        // into ParsedData live (no lifecycle/% writes mid-stream).
        const runConnectionHealth = () =>
          track(
            'connectionsHealthLoading',
            (async () => {
              try {
                const response = await fetchRaw('/api/connections/health');
                if (!response.ok || !response.body) {
                  throw new Error(`Stream failed: ${response.status}`);
                }
                const collected: ConnectionHealthResult[] = [];
                for await (const { event, payload } of parseSseStream(response.body)) {
                  if (cancelled) break;
                  const data = payload as Record<string, unknown>;
                  if (event === 'init') {
                    dispatch({
                      type: 'SET_PARSED_DATA',
                      payload: { connectionHealthTotal: Number(data.total) || 0 },
                    });
                  } else if (event === 'conn') {
                    collected.push(data as unknown as ConnectionHealthResult);
                    dispatch({
                      type: 'SET_PARSED_DATA',
                      payload: { connectionHealth: [...collected] },
                    });
                  }
                }
                log(`Connection health scan done (${collected.length} connections)`);
                return collected;
              } catch (err) {
                log(`Connection health scan failed: ${getErrorMessage(err)}`, 'warn');
                throw err;
              }
            })(),
            {
              startMessage: 'Probing connection health',
              isEmpty: (v) => (v as ConnectionHealthResult[]).length === 0,
            },
          );

        log(
          'Phase 3 strategy: launch code-envs + project-footprint + connection-health in parallel; defer dir-tree until Directory page is opened',
        );
        const phase3Start = nowMs();
        const heavyStart = nowMs();
        const lowStart = nowMs();
        projectFootprintStarted = true;
        const heavyGate = Promise.allSettled([
          runCodeEnvs(),
          runProjectFootprint(),
          runLlmAudit(),
          runPluginUsages(),
        ]);
        const connectionHealthGate = runConnectionHealth();
        log('Deferring /api/dir-tree root load until after Phase 3 (background autostart)');
        const lowGate = Promise.allSettled([runProjects(), runLogs()]);

        await heavyGate;
        clearTimeout(slowHeavyTimer);
        if (cancelled) return;
        log(`Phase 3 heavy endpoints done (${fmtMs(heavyStart)})`);

        // (codeEnvReplacement is tracked alongside the main /api/code-envs fetch;
        //  codeEnvsComparison starts after code-envs settle via delayed warmup;
        //  codeEnvCleaner is owned solely by the
        //  managedFoldersScan store — autostarted below — so no mirror block is
        //  needed here anymore.)

        await lowGate;
        if (cancelled) return;
        log(`Phase 3 low-priority endpoints done (${fmtMs(lowStart)})`);
        log(`Phase 3 all endpoints done (${fmtMs(phase3Start)})`);

        // Action pages (db-health / cs-template / plugin-sync / report) are
        // `noLoadGlyph`: no sidebar glyph, excluded from the global aggregate,
        // and no startup markDone — their lifecycle field drives only in-page UI.

        // Compute users by project count
        if (currentParsedData.projects?.length && currentParsedData.users?.length) {
          const userEmailMap: Record<string, string> = {};
          currentParsedData.users.forEach((u) => {
            userEmailMap[u.login] = u.email || u.login;
          });

          const projectCounts: Record<string, number> = {};
          currentParsedData.projects.forEach((p) => {
            projectCounts[p.owner] = (projectCounts[p.owner] || 0) + 1;
          });

          const usersByProjects: Record<string, string> = {};
          Object.entries(projectCounts)
            .sort(([, a], [, b]) => b - a)
            .forEach(([login, count]) => {
              const email = userEmailMap[login] || login;
              usersByProjects[email] = String(count);
            });

          if (Object.keys(usersByProjects).length > 0) {
            currentParsedData = {
              ...currentParsedData,
              usersByProjects,
            };
            dispatch({ type: 'SET_PARSED_DATA', payload: currentParsedData });
            log(`Computed users-by-projects (${Object.keys(usersByProjects).length} users)`);
          }
        }
        // Emit timing summary table
        if (endpointTimings.length > 0) {
          const rows = endpointTimings.map((t) => {
            const dur = t.durationMs >= 1000 ? `${(t.durationMs / 1000).toFixed(1)}s` : `${t.durationMs}ms`;
            const flag = t.status === 'fail' ? ' FAIL' : t.status === 'skip' ? ' SKIP' : '';
            return `${t.label}|${dur}${flag}`;
          });
          log(`TIMING_TABLE:${rows.join(';;')}`);
        }
        log('Live data load completed');

        // Auto-start scans for pages that previously waited for first user visit.
        // Fire-and-forget: each store manages its own state, errors, and cancellation.
        log(
          'Auto-starting deferred page scans (container execs, SQL pushdown, sanity check, image-cleaner detect, managed folders, dir-tree)',
        );
        void containerExecsScan.load();
        void imageCleanerDetectScan.load();
        // managedFoldersScan owns codeEnvCleanerLoading (a code-envs aggregate
        // field). Autostart it so that field reaches `done` honestly via its
        // scan store — and the global "Analysis complete" can resolve — without
        // requiring a visit to the Code Envs page.
        void managedFoldersScan.load();
        // Dir-tree loads in the background only — it deliberately does NOT join the
        // "Analysis complete" aggregator (cold scans can exceed 40s on large hosts).
        void loadDirTreeRootRef.current?.();
        startSqlPushdownScan();
        const sanityStartedAt = new Date().toISOString();
        dispatch({
          type: 'SET_PARSED_DATA',
          payload: {
            sanityCheckLoading: {
              phase: 'running',
              startedAt: sanityStartedAt,
              progressPct: 0,
              message: 'Running sanity check',
              updatedAt: sanityStartedAt,
            },
          },
        });
        runSanityCheck()
          .then((result) => {
            if (cancelled) return;
            dispatch({
              type: 'SET_PARSED_DATA',
              payload: {
                sanityCheck: result.messages,
                sanityCheckMaxSeverity: result.maxSeverity,
                sanityCheckLoading: {
                  phase: 'done',
                  startedAt: sanityStartedAt,
                  finishedAt: new Date().toISOString(),
                  isEmpty: result.messages.length === 0,
                  message: `${result.messages.length} message(s)`,
                },
              },
            });
            log(`Auto sanity check completed (${result.messages.length} messages)`);
          })
          .catch((err) => {
            const msg = getErrorMessage(err);
            dispatch({
              type: 'SET_PARSED_DATA',
              payload: {
                sanityCheckLoading: {
                  phase: 'error',
                  startedAt: sanityStartedAt,
                  finishedAt: new Date().toISOString(),
                  error: msg,
                  progressPct: 0,
                },
              },
            });
            log(`Auto sanity check failed: ${msg}`, 'warn');
          });

        // Await the slow tails (code-env sizes + connection-health) AFTER
        // kicking off the scans above — those scans depend on neither tail, so
        // gating their start behind the ~slow sizes fetch left the sidebar
        // showing static grey "queued" circles. Each tail is tracked via its
        // own lifecycle field, so it stays a visible spinner while in flight.
        const tails: Promise<unknown>[] = [connectionHealthGate];
        if (codeEnvSizesTracked) tails.push(codeEnvSizesTracked);
        log(`Awaiting ${tails.length} tail requests`);
        await Promise.allSettled(tails);
        log('Tails resolved');

        dispatch({ type: 'SET_PARSED_DATA', payload: { dataReady: true } });
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Unknown error';
        log(`Live data load failed: ${message}`, 'error');
        dispatch({ type: 'SET_ERROR', payload: `Failed to load live diagnostics: ${message}` });
      } finally {
        if (!cancelled) {
          dispatch({ type: 'SET_LOADING', payload: false });
          log('Loader finalized');
        }
      }
    };

    run();

    return () => {
      cancelled = true;
    };
  }, [dispatch, enabled, reloadKey]);
}
