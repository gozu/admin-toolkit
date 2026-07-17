/**
 * Phase-3 secondary runners: LLM audit, deferred plugin usages, the optional
 * basic projects list, log errors, and the connection-health SSE consumer.
 * Bodies moved verbatim from the old monolithic useApiDataLoader.ts.
 */
import type { ConnectionHealthResult, LlmAuditResponse } from '../../types';
import { fetchJson, fetchRaw } from '../../utils/api';
import { parseSseStream } from '../../utils/sseStream';
import { LIVE_PROGRESS_TIMEOUT_MS, type LoaderCtx } from './context';
import type { LifecycleTracker } from './lifecycle';
import type {
  LlmAuditProgressResponse,
  LogErrorsResponse,
  PluginUsagesResponse,
  ProjectsResponse,
} from './types';

const LLM_AUDIT_PHASE_LABEL: Record<string, string> = {
  pricing: 'Fetching model pricing catalog',
  connections: 'Listing LLM connections',
  catalog: 'Listing projects',
  scan: 'Scanning projects for LLMs',
  usage_scan: 'Scanning LLM usage references',
  classify: 'Classifying models',
  done: 'Finalizing',
};

export async function runLlmAudit(
  ctx: LoaderCtx,
  tracker: LifecycleTracker,
  beSettings: Record<string, number>,
): Promise<void> {
  const {
    dispatch,
    cancelled,
    log,
    timed,
    settledError,
    withTimeout,
    isAbortError,
    getErrorMessage,
    abortPendingRequest,
  } = ctx;
  // The single /api/llm-audit fetch is opaque for its whole runtime, so a
  // sidecar poll of /api/llm-audit/progress feeds the backend's summary
  // (% + phase + project counts) into the running lifecycle — that is what
  // moves the Model Audit page's progress bar. Events/rows are not consumed;
  // the since/rowsSince cursors only keep the poll payloads tiny.
  let progressActive = true;
  let progressWarned = false;
  let progressAbort: AbortController | null = null;
  let progressSince = 0;
  let progressRowsSince = 0;
  const pollProgress = async () => {
    while (!cancelled() && progressActive) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      if (cancelled() || !progressActive) break;
      try {
        progressAbort = new AbortController();
        const query = new URLSearchParams();
        query.set('since', String(progressSince));
        query.set('rowsSince', String(progressRowsSince));
        const payload = await withTimeout(
          fetchJson<LlmAuditProgressResponse>(`/api/llm-audit/progress?${query.toString()}`, {
            signal: progressAbort.signal,
          }),
          '/api/llm-audit/progress',
          LIVE_PROGRESS_TIMEOUT_MS,
        );
        if (typeof payload.next === 'number') progressSince = payload.next;
        if (typeof payload.partialRowsNext === 'number')
          progressRowsSince = payload.partialRowsNext;
        const summary = payload.summary;
        const current = tracker.data.llmAuditLoading;
        // Patch only while track() holds the running phase — a late poll
        // response must never overwrite the done/error settle.
        if (
          progressActive &&
          payload.status === 'running' &&
          summary &&
          current?.phase === 'running'
        ) {
          const phase = String(summary.phase || '');
          const total = Number(summary.projectsTotal || 0);
          const done = Number(summary.projectsDone || 0);
          const counts =
            total > 0 && (phase === 'scan' || phase === 'usage_scan')
              ? ` (${done}/${total} projects)`
              : '';
          tracker.patchLifecycle('llmAuditLoading', {
            phase: 'running',
            startedAt: current.startedAt,
            progressPct: Math.max(0, Math.min(100, Math.round(Number(summary.progressPct || 0)))),
            message: `${LLM_AUDIT_PHASE_LABEL[phase] ?? 'Auditing LLMs'}${counts}`,
            subPhase: phase || undefined,
            updatedAt: new Date().toISOString(),
          });
        }
      } catch (err) {
        if ((!progressActive || cancelled()) && isAbortError(err)) break;
        if (!progressWarned) {
          progressWarned = true;
          log(`LLM audit live progress polling unavailable: ${getErrorMessage(err)}`, 'warn');
        }
      } finally {
        progressAbort = null;
      }
    }
  };
  const progressPromise = pollProgress();
  const llmAuditRes = await tracker.track(
    'llmAuditLoading',
    timed<LlmAuditResponse>('/api/llm-audit', beSettings.fe_timeout_llm_audit ?? 620000),
    {
      startMessage: 'Starting LLM model audit',
      isEmpty: (v) => ((v as LlmAuditResponse)?.rows?.length || 0) === 0,
    },
  );
  progressActive = false;
  abortPendingRequest(progressAbort);
  await progressPromise;
  if (cancelled()) return;
  if (llmAuditRes.status === 'fulfilled' && llmAuditRes.value) {
    tracker.data = { ...tracker.data, llmAudit: llmAuditRes.value };
    dispatch({ type: 'SET_PARSED_DATA', payload: tracker.data });
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
    const rawErr = settledError(llmAuditRes);
    log(`Failed /api/llm-audit: ${rawErr}`, 'warn');
    // Parse inner error from JSON body (500 responses include structured body)
    let innerErr = rawErr;
    const jsonBodyMatch = rawErr.match(/\{[\s\S]*\}$/);
    if (jsonBodyMatch) {
      try {
        const body = JSON.parse(jsonBodyMatch[0]) as Record<string, unknown>;
        if (typeof body.error === 'string') innerErr = body.error;
      } catch {
        /* ignore */
      }
    }
    if (/SSL|CERTIFICATE|certificate.*verif|verif.*certif/i.test(innerErr)) {
      log(
        `Model Audit root cause: SSL/TLS certificate verification failed — backend cannot reach external pricing API (corporate proxy with custom CA?). LLM connections may exist but will show as "No LLMs found".`,
        'warn',
      );
    }
    log(
      `Note: llmAuditLoading failure cascades to Users — Users will show ✕ even though user data loaded fine.`,
      'warn',
    );
  }
}

// Per-plugin usage scan, split out of /api/plugins so the cheap plugin
// list renders fast in Phase 2 and the expensive get_plugin().list_usages()
// fan-out fills the "projects using" column in asynchronously here.
export async function runPluginUsages(ctx: LoaderCtx, tracker: LifecycleTracker): Promise<void> {
  const { dispatch, cancelled, log, timed, settle, settledError } = ctx;
  const usagesRes = await settle(timed<PluginUsagesResponse>('/api/plugins/usages', 620000));
  if (cancelled()) return;
  if (usagesRes.status === 'fulfilled' && usagesRes.value) {
    const byId = usagesRes.value.usagesByPlugin || {};
    const merged = (tracker.data.pluginDetails || []).map((row) =>
      byId[row.id] ? { ...row, ...byId[row.id] } : row,
    );
    tracker.data = { ...tracker.data, pluginDetails: merged, pluginUsagesPending: false };
    dispatch({ type: 'SET_PARSED_DATA', payload: tracker.data });
    log(`Loaded plugin usages (${Object.keys(byId).length} plugins)`);
  } else {
    tracker.data = { ...tracker.data, pluginUsagesPending: false };
    dispatch({ type: 'SET_PARSED_DATA', payload: tracker.data });
    log(`Failed /api/plugins/usages: ${settledError(usagesRes)}`, 'warn');
  }
}

export async function runProjects(
  ctx: LoaderCtx,
  tracker: LifecycleTracker,
  beSettings: Record<string, number>,
): Promise<void> {
  const {
    dispatch,
    cancelled,
    log,
    timed,
    settle,
    settledError,
    basicProjectsEnabled,
    recordTiming,
  } = ctx;
  const projectsRes: PromiseSettledResult<ProjectsResponse | null> = basicProjectsEnabled
    ? await settle(
        timed<ProjectsResponse>('/api/projects', beSettings.fe_timeout_projects ?? 45000),
      )
    : { status: 'fulfilled', value: null };
  if (cancelled()) return;
  if (!basicProjectsEnabled) {
    recordTiming('/api/projects', 0, 'skip');
    log('Skipped /api/projects in lean live mode');
    tracker.data = {
      ...tracker.data,
      projects: [],
    };
    dispatch({ type: 'SET_PARSED_DATA', payload: tracker.data });
  } else if (projectsRes.status === 'fulfilled' && projectsRes.value) {
    tracker.data = {
      ...tracker.data,
      projects: projectsRes.value.projects || [],
    };
    dispatch({ type: 'SET_PARSED_DATA', payload: tracker.data });
    log(`Loaded projects (${tracker.data.projects?.length || 0})`);
  } else {
    log(`Failed /api/projects: ${settledError(projectsRes)}`, 'warn');
  }
}

export async function runLogs(
  ctx: LoaderCtx,
  tracker: LifecycleTracker,
  beSettings: Record<string, number>,
): Promise<void> {
  const { dispatch, cancelled, log, timed, settledError } = ctx;
  const logsRes = await tracker.track(
    'logsLoading',
    timed<LogErrorsResponse>('/api/logs/errors', beSettings.fe_timeout_logs ?? 30000),
    {
      startMessage: 'Loading log errors',
      isEmpty: (v) => ((v as LogErrorsResponse).logStats?.['Displayed Errors'] || 0) === 0,
    },
  );
  if (cancelled()) return;
  if (logsRes.status === 'fulfilled' && logsRes.value) {
    const displayedErrors = logsRes.value.logStats?.['Displayed Errors'] || 0;
    tracker.data = {
      ...tracker.data,
      formattedLogErrors: logsRes.value.formattedLogErrors || 'No log errors found',
      rawLogErrors: logsRes.value.rawLogErrors || [],
      logStats: logsRes.value.logStats || {
        'Total Lines': 0,
        'Unique Errors': 0,
        'Displayed Errors': 0,
      },
    };
    dispatch({ type: 'SET_PARSED_DATA', payload: tracker.data });
    if (displayedErrors === 0) {
      log('Loaded /api/logs/errors but no recent errors were extracted', 'warn');
    } else {
      log(`Loaded log errors (${displayedErrors} displayed)`);
    }
  } else {
    log(`Failed /api/logs/errors: ${settledError(logsRes)}`, 'warn');
    tracker.data = {
      ...tracker.data,
      formattedLogErrors: 'Failed to load log errors (endpoint timed out or unavailable)',
      rawLogErrors: [],
      logStats: { 'Total Lines': 0, 'Unique Errors': 0, 'Displayed Errors': 0 },
    };
    dispatch({ type: 'SET_PARSED_DATA', payload: tracker.data });
  }
}

// Connection health scan — the whole SSE consumer is wrapped as one
// tracked promise: track() opens `running` at call and settles
// done/error when the stream ends or throws. Per-conn rows still stream
// into ParsedData live (no lifecycle/% writes mid-stream).
export function runConnectionHealth(
  ctx: LoaderCtx,
  tracker: LifecycleTracker,
): Promise<PromiseSettledResult<ConnectionHealthResult[]>> {
  const { dispatch, cancelled, log, nowMs, getErrorMessage } = ctx;
  return tracker.track(
    'connectionsHealthLoading',
    (async () => {
      try {
        const response = await fetchRaw('/api/connections/health');
        if (!response.ok || !response.body) {
          throw new Error(`Stream failed: ${response.status}`);
        }
        const collected: ConnectionHealthResult[] = [];
        let lastConnAt = nowMs();
        const slowConns: Array<{ name: string; durationMs: number }> = [];
        for await (const { event, payload } of parseSseStream(response.body)) {
          if (cancelled()) break;
          const data = payload as Record<string, unknown>;
          if (event === 'init') {
            lastConnAt = nowMs();
            dispatch({
              type: 'SET_PARSED_DATA',
              payload: { connectionHealthTotal: Number(data.total) || 0 },
            });
          } else if (event === 'conn') {
            const now = nowMs();
            const gapMs = Math.round(now - lastConnAt);
            lastConnAt = now;
            if (gapMs > 5000) {
              slowConns.push({
                name: String((data as { name?: unknown }).name ?? '?'),
                durationMs: gapMs,
              });
            }
            collected.push(data as unknown as ConnectionHealthResult);
            dispatch({
              type: 'SET_PARSED_DATA',
              payload: { connectionHealth: [...collected] },
            });
          }
        }
        const failedConns = collected.filter((c) => c.status === 'fail');
        const failNote =
          failedConns.length > 0
            ? ` — ${failedConns.length} failed: ${failedConns.map((c) => `${c.name}(${c.type})`).join(', ')}`
            : '';
        log(`Connection health scan done (${collected.length} connections${failNote})`);
        if (slowConns.length > 0) {
          log(
            `Connection health: ${slowConns.length} slow connection(s) >5s: ${slowConns.map((c) => `${c.name} ${Math.round(c.durationMs / 1000)}s`).join(', ')}`,
            'warn',
          );
        }
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
}
