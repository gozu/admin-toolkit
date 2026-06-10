/**
 * Phase-3 project-footprint runner: the main /api/project-footprint fetch
 * with its live progress poll (partial rows + bench events). Bodies moved
 * verbatim from the old monolithic useApiDataLoader.ts; the replay machinery
 * now comes from the shared progressReplay module.
 */
import type { ProjectFootprintRow } from '../../types';
import { fetchJson } from '../../utils/api';
import { LIVE_PROGRESS_TIMEOUT_MS, type LoaderCtx } from './context';
import type { LifecycleTracker } from './lifecycle';
import { createProgressReplay } from './progressReplay';
import type { ProjectFootprintProgressResponse, ProjectFootprintResponse } from './types';

export interface FootprintRunnerHooks {
  /** Flips the orchestrator's slow-heavy-warning flag. */
  markFootprintDone: () => void;
}

export async function runProjectFootprint(
  ctx: LoaderCtx,
  tracker: LifecycleTracker,
  beSettings: Record<string, number>,
  hooks: FootprintRunnerHooks,
): Promise<void> {
  const {
    dispatch,
    cancelled,
    log,
    cleanToken,
    benchMs,
    benchSummaryLine,
    benchStepLine,
    getErrorMessage,
    isAbortError,
    abortPendingRequest,
    withTimeout,
    timed,
    settledError,
  } = ctx;
  const { track } = tracker;

  let projectFootprintProgressActive = true;
  let projectFootprintProgressAbortController: AbortController | null = null;
  // Use a sentinel so the first poll only syncs run id (status=replaced) instead of replaying stale events.
  let projectFootprintProgressRunId: string | undefined = '__pending__';
  let projectFootprintProgressCursor = 0;
  let projectFootprintProgressWarned = false;
  const projectFootprintProgressPath = '/api/project-footprint/progress';
  const replay = createProgressReplay(ctx, tracker, 'pjft');

  let projectFootprintRowsSince = 0;
  const pollProjectFootprintProgress = async () => {
    while (!cancelled() && projectFootprintProgressActive) {
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
          replay.reset();
          continue;
        }
        const nextCursor =
          typeof payload.next === 'number' ? payload.next : projectFootprintProgressCursor;
        projectFootprintProgressCursor = nextCursor;
        // Rows-only poll: stream partial rows + events; no % math /
        // lifecycle writes (the glyph is track()'s binary spinner).
        if (Array.isArray(payload.events) && payload.events.length > 0) {
          replay.replay(payload.events);
        }
        if (Array.isArray(payload.partialRows) && payload.partialRows.length > 0) {
          const rows = payload.partialRows as unknown as ProjectFootprintRow[];
          dispatch({ type: 'APPEND_PARTIAL_PROJECT_FOOTPRINT', payload: rows });
        }
        if (typeof payload.partialRowsNext === 'number') {
          projectFootprintRowsSince = payload.partialRowsNext;
        }
      } catch (err) {
        if ((!projectFootprintProgressActive || cancelled()) && isAbortError(err)) {
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
    timed<ProjectFootprintResponse>(
      '/api/project-footprint',
      beSettings.fe_timeout_project_footprint ?? 620000,
    ),
    {
      startMessage: 'Starting project analysis',
      isEmpty: (v) => ((v as ProjectFootprintResponse)?.projects || []).length === 0,
    },
  );
  projectFootprintProgressActive = false;
  abortPendingRequest(projectFootprintProgressAbortController);
  await projectFootprintProgressPromise;
  hooks.markFootprintDone();
  if (cancelled()) return;
  if (projectFootprintRes.status === 'fulfilled' && projectFootprintRes.value) {
    tracker.data = {
      ...tracker.data,
      projectFootprint: projectFootprintRes.value.projects || [],
      projectFootprintSummary: projectFootprintRes.value.summary,
    };
    dispatch({ type: 'SET_PARSED_DATA', payload: tracker.data });
    log(`Loaded project footprint (${tracker.data.projectFootprint?.length || 0} projects)`);
    const benchmark = (projectFootprintRes.value.summary as Record<string, unknown> | undefined)
      ?.benchmark as
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
          `rows=${tracker.data.projectFootprint?.length || 0}`,
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
      replay.replay(benchmark.events || []);
    }
  } else {
    log(`Failed /api/project-footprint: ${settledError(projectFootprintRes)}`, 'warn');
  }
}
