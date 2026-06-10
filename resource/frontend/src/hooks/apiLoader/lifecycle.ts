/**
 * Lifecycle tracker for the live-mode API loader: owns the accumulating
 * ParsedData snapshot and the queued→running→done/error ritual for every
 * sidebar-glyph-bearing module. Bodies moved verbatim from the old
 * monolithic useApiDataLoader.ts.
 */
import type { Lifecycle, ParsedData } from '../../types';
import { SHARED_LOADING_FIELDS, type LifecycleFieldName } from '../../utils/moduleRegistry';
import { deriveAnalysisLifecycle, lifecycleToLoadingProgress } from '../../utils/analysisLifecycle';
import type { LoaderCtx } from './context';

export type TrackField = LifecycleFieldName | readonly LifecycleFieldName[];

export interface TrackOpts {
  startMessage?: string;
  doneMessage?: (value: unknown) => string;
  isEmpty?: (value: unknown) => boolean;
  swallow?: boolean;
}

export interface LifecycleTracker {
  /** The accumulating ParsedData snapshot. Setting it does NOT dispatch —
   * call sites keep their explicit SET_PARSED_DATA dispatches, exactly as
   * the old closure variable worked. */
  get data(): ParsedData;
  set data(next: ParsedData);
  patchLifecycle: (field: LifecycleFieldName, value: Lifecycle) => void;
  markDone: (field: LifecycleFieldName, message?: string, isEmpty?: boolean) => void;
  markRunning: (field: LifecycleFieldName, message?: string) => void;
  markError: (field: LifecycleFieldName, error: string) => void;
  updateAnalysisLoading: () => void;
  track: <T>(
    field: TrackField,
    promise: Promise<T>,
    opts?: TrackOpts,
  ) => Promise<PromiseSettledResult<T>>;
}

export function createLifecycleTracker(ctx: LoaderCtx, initialData: ParsedData): LifecycleTracker {
  const { dispatch, isAbortError, getErrorMessage } = ctx;
  let currentParsedData = initialData;
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
          (currentParsedData[field] as { startedAt?: string }).startedAt || sessionStartedAt
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
      (currentParsedData[field] as { startedAt?: string } | undefined)?.startedAt || now;
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
        if (isAbortError(reason)) return { status: 'rejected', reason } as PromiseSettledResult<T>;
        const message = getErrorMessage(reason);
        for (const f of fields) {
          if (opts.swallow) markDone(f, message, true);
          else markError(f, message);
        }
        return { status: 'rejected', reason } as PromiseSettledResult<T>;
      },
    );
  };

  return {
    get data() {
      return currentParsedData;
    },
    set data(next: ParsedData) {
      currentParsedData = next;
    },
    patchLifecycle,
    markDone,
    markRunning,
    markError,
    updateAnalysisLoading,
    track,
  };
}
