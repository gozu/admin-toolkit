/**
 * Phase-3 code-envs runner: the main /api/code-envs fetch with its live
 * progress poll (provisional rows + bench events), partial-row recovery on
 * failure, and the slow code-env-sizes tail. Bodies moved verbatim from the
 * old monolithic useApiDataLoader.ts; the replay machinery now comes from
 * the shared progressReplay module.
 */
import type { CodeEnv } from '../../types';
import { fetchJson } from '../../utils/api';
import type { LifecycleFieldName } from '../../utils/moduleRegistry';
import { LIVE_PROGRESS_TIMEOUT_MS, type LoaderCtx } from './context';
import type { LifecycleTracker } from './lifecycle';
import { createProgressReplay } from './progressReplay';
import type { CodeEnvsProgressResponse, CodeEnvsResponse } from './types';

export interface CodeEnvsRunnerHooks {
  /** Flips the orchestrator's slow-heavy-warning flag. */
  markCodeEnvsDone: () => void;
  /** Hands the slow sizes-tail promise to the orchestrator so the
   * await-tails step can join it before dataReady. */
  setSizesTracked: (tracked: Promise<PromiseSettledResult<unknown>>) => void;
}

const codeEnvFields: LifecycleFieldName[] = ['codeEnvsLoading', 'codeEnvReplacementLoading'];

export async function runCodeEnvs(
  ctx: LoaderCtx,
  tracker: LifecycleTracker,
  beSettings: Record<string, number>,
  hooks: CodeEnvsRunnerHooks,
): Promise<void> {
  const {
    dispatch,
    cancelled,
    log,
    nowMs,
    fmtMs,
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
  const { track, markDone } = tracker;

  // Code-env sizes load as a slow tail tracked through `codeEnvSizesLoading`
  // (swallow:true) so the Code Envs page stays `running` — and the global
  // "Analysis complete" is withheld — until the ~slow /api/code-envs/sizes
  // request lands, without a sizes failure turning the page red. Handed to
  // the orchestrator so the await-tails step can join it before dataReady.
  const loadCodeEnvSizes = () => {
    const sizesStart = nowMs();
    hooks.setSizesTracked(
      track(
        'codeEnvSizesLoading',
        fetchJson<{ sizes: Record<string, number> }>('/api/code-envs/sizes')
          .then((r) => {
            if (r?.sizes && typeof r.sizes === 'object') {
              dispatch({ type: 'SET_PARSED_DATA', payload: { codeEnvSizes: r.sizes } });
              log(
                `Loaded /api/code-envs/sizes (${Object.keys(r.sizes).length} entries, ${fmtMs(sizesStart)})`,
              );
            } else {
              log(`/api/code-envs/sizes returned no sizes object (${fmtMs(sizesStart)})`, 'warn');
            }
            log('Pre-warming /api/dir-tree after global footprint');
            fetchJson('/api/dir-tree?maxDepth=3&scope=dss').catch(() => {
              /* pre-warm optional */
            });
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
      ),
    );
  };

  dispatch({ type: 'CLEAR_PROVISIONAL_CODE_ENVS' });
  tracker.data = {
    ...tracker.data,
    codeEnvsExpectedCount: undefined,
  };
  dispatch({ type: 'SET_PARSED_DATA', payload: { codeEnvsExpectedCount: undefined } });
  let codeEnvsProgressActive = true;
  // Use a sentinel so the first poll returns only the current run id (status=replaced),
  // avoiding replay of stale events from previous runs.
  let codeEnvsProgressRunId: string | undefined = '__pending__';
  let codeEnvsProgressCursor = 0;
  let codeEnvsProgressWarned = false;
  let codeEnvsProgressAbortController: AbortController | null = null;
  const codeEnvsProgressPath = '/api/code-envs/progress';
  let codeEnvsProgressPathLogged = false;
  const replay = createProgressReplay(ctx, tracker, 'ce');

  let codeEnvsRowsSince = 0;
  const codeEnvsPartialBuffer: CodeEnv[] = [];
  const pollCodeEnvProgress = async () => {
    while (!cancelled() && codeEnvsProgressActive) {
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
          fetchJson<CodeEnvsProgressResponse>(`${codeEnvsProgressPath}?${query.toString()}`, {
            signal: codeEnvsProgressAbortController.signal,
          }),
          codeEnvsProgressPath,
          LIVE_PROGRESS_TIMEOUT_MS,
        );
        // Rows-only poll: keep the expected-count signal that feeds the
        // provisional-row table; no % math / lifecycle writes (the glyph
        // is the binary spinner driven by track()).
        const progressSummary = payload.summary || {};
        const envDetailsTotal = Number(progressSummary.envDetailsTotal || 0);
        if (envDetailsTotal > 0) {
          replay.setExpectedCodeEnvCount(envDetailsTotal);
        }
        if (payload.runId && payload.runId !== codeEnvsProgressRunId) {
          codeEnvsProgressRunId = payload.runId;
          codeEnvsProgressCursor = 0;
          codeEnvsRowsSince = 0;
          codeEnvsPartialBuffer.length = 0;
          replay.reset();
          replay.setExpectedCodeEnvCount(undefined);
          dispatch({ type: 'CLEAR_PROVISIONAL_CODE_ENVS' });
          continue;
        }
        const nextCursor = typeof payload.next === 'number' ? payload.next : codeEnvsProgressCursor;
        if (Array.isArray(payload.events) && payload.events.length > 0) {
          replay.replay(payload.events);
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
        if ((!codeEnvsProgressActive || cancelled()) && isAbortError(err)) {
          break;
        }
        if (!codeEnvsProgressWarned) {
          codeEnvsProgressWarned = true;
          log(`Code env live progress polling unavailable: ${getErrorMessage(err)}`, 'warn');
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
  hooks.markCodeEnvsDone();
  if (cancelled()) return;
  if (codeEnvsRes.status === 'fulfilled' && codeEnvsRes.value) {
    tracker.data = {
      ...tracker.data,
      codeEnvs: codeEnvsRes.value.codeEnvs || [],
      codeEnvsExpectedCount: (codeEnvsRes.value.codeEnvs || []).length,
      pythonVersionCounts: codeEnvsRes.value.pythonVersionCounts || {},
      rVersionCounts: codeEnvsRes.value.rVersionCounts || {},
      totalEnvCount: codeEnvsRes.value.totalEnvCount,
      skippedEnvCount: codeEnvsRes.value.skippedEnvCount,
    };
    dispatch({ type: 'SET_PARSED_DATA', payload: tracker.data });
    dispatch({ type: 'CLEAR_PROVISIONAL_CODE_ENVS' });
    loadCodeEnvSizes();
    log(`Loaded code envs (${tracker.data.codeEnvs?.length || 0})`);
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
      if (replay.eventsSeen() > 0) {
        log(`bench;ce;progress-events;count=${replay.eventsSeen()}`);
      } else {
        replay.replay(benchmark.events || []);
      }
    }
  } else {
    if (codeEnvsPartialBuffer.length > 0) {
      tracker.data = {
        ...tracker.data,
        codeEnvs: codeEnvsPartialBuffer,
        codeEnvsExpectedCount: codeEnvsPartialBuffer.length,
      };
      dispatch({ type: 'SET_PARSED_DATA', payload: tracker.data });
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
      log(
        `Failed /api/code-envs but recovered ${codeEnvsPartialBuffer.length} envs from progress`,
        'warn',
      );
    } else {
      dispatch({ type: 'CLEAR_PROVISIONAL_CODE_ENVS' });
      // track() marked the code-env fields error; loadCodeEnvSizes() is
      // never called, so mark its lifecycle done(empty) lest it hang
      // queued and block the global "Analysis complete" aggregate.
      markDone('codeEnvSizesLoading', 'No code envs', true);
    }
    log(`Failed /api/code-envs: ${settledError(codeEnvsRes)}`, 'warn');
  }
}
