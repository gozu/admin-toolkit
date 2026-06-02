import type { Lifecycle, LoadingProgressState, ParsedData } from '../types';
import type { LifecycleFieldName } from './moduleRegistry';
import { aggregateLifecycles } from './lifecycleAggregator';

/**
 * Aggregate per-module lifecycles into a single analysis-wide Lifecycle.
 *
 * Pure: reads from ParsedData and the supplied field list. Composes child
 * timestamps; never invents a new timestamp at render time. The terminal
 * messages ("Analysis complete" / "One or more modules failed") are
 * normalized at the aggregate boundary so consumers don't have to.
 */
export function deriveAnalysisLifecycle(
  parsedData: ParsedData,
  fields: readonly LifecycleFieldName[],
  sessionStartedAt: string,
): Lifecycle {
  // An expected-but-absent field reads as queued (not skipped): with the
  // orchestrator gone, skipping absent fields would let the aggregate flash
  // "Analysis complete" at t=0 before any module has been written. Defaulting
  // to queued keeps the global indicator pending until every field is terminal.
  // Matches pageLifecycle.resolveLifecycle's absent→queued behavior.
  const states: Lifecycle[] = [];
  for (const f of fields) states.push(parsedData[f] ?? { phase: 'queued' });
  if (states.length === 0) {
    return { phase: 'queued', startedAt: sessionStartedAt };
  }

  const agg = aggregateLifecycles(states);
  switch (agg.phase) {
    case 'running':
      return { ...agg, message: agg.message || 'Analysis in progress' };
    case 'done':
      return { ...agg, message: agg.message || 'Analysis complete' };
    case 'error':
      return { ...agg, error: 'One or more modules failed' };
    case 'queued':
      return agg.startedAt ? agg : { phase: 'queued', startedAt: sessionStartedAt };
  }
}

// Convert a Lifecycle back into the legacy LoadingProgressState shape so the
// existing parsedData.analysisLoading consumers (HealthScoreCard,
// CodeEnvsPage, ConnectionsChart) continue to render unchanged.
export function lifecycleToLoadingProgress(lc: Lifecycle): LoadingProgressState {
  switch (lc.phase) {
    case 'queued':
      return {
        active: false,
        progressPct: 0,
        phase: 'queued',
        message: 'Analysis queued',
        startedAt: lc.startedAt,
        updatedAt: lc.startedAt,
      };
    case 'running':
      return {
        active: true,
        progressPct: lc.progressPct,
        phase: lc.subPhase,
        message: lc.message,
        startedAt: lc.startedAt,
        updatedAt: lc.updatedAt,
      };
    case 'done':
      return {
        active: false,
        progressPct: 100,
        phase: 'done',
        message: lc.message || 'Analysis complete',
        startedAt: lc.startedAt,
        updatedAt: lc.finishedAt,
      };
    case 'error':
      return {
        active: false,
        progressPct: lc.progressPct,
        phase: 'error',
        message: lc.error,
        error: lc.error,
        startedAt: lc.startedAt,
        updatedAt: lc.finishedAt,
      };
  }
}
