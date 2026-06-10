/**
 * Phase-3 secondary runners: LLM audit, deferred plugin usages, the optional
 * basic projects list, log errors, and the connection-health SSE consumer.
 * Bodies moved verbatim from the old monolithic useApiDataLoader.ts.
 */
import type { ConnectionHealthResult, LlmAuditResponse } from '../../types';
import { fetchRaw } from '../../utils/api';
import { parseSseStream } from '../../utils/sseStream';
import type { LoaderCtx } from './context';
import type { LifecycleTracker } from './lifecycle';
import type { LogErrorsResponse, PluginUsagesResponse, ProjectsResponse } from './types';

export async function runLlmAudit(
  ctx: LoaderCtx,
  tracker: LifecycleTracker,
  beSettings: Record<string, number>,
): Promise<void> {
  const { dispatch, cancelled, log, timed, settledError } = ctx;
  // The /api/llm-audit/progress poll streamed no rows (only a % the
  // binary spinner no longer shows), so it's gone — track() drives the
  // single fetch's running → done/error glyph.
  const llmAuditRes = await tracker.track(
    'llmAuditLoading',
    timed<LlmAuditResponse>('/api/llm-audit', beSettings.fe_timeout_llm_audit ?? 620000),
    {
      startMessage: 'Starting LLM model audit',
      isEmpty: (v) => ((v as LlmAuditResponse)?.rows?.length || 0) === 0,
    },
  );
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
