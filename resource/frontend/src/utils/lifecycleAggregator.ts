import type { Lifecycle } from '../types';

// Pure FSM that composes a list of child Lifecycles into a single aggregate.
// Render-safe: no React, no `new Date()`, no ParsedData import. All timestamps
// in the output come from the inputs. The aggregator preserves the existing
// "running while any child still running, terminal only when *all* terminal"
// invariant from deriveAnalysisLifecycle.
//
// Precedence (ordered top-to-bottom, total):
//   - empty                                   → queued (no startedAt)
//   - single state                            → that state, unchanged
//   - any running                             → running, pct = mean, message
//                                               from slowest running child
//   - any queued (no running, not all term.)  → queued (earliest child startedAt)
//   - all terminal, any error                 → error
//   - all done                                → done
//
// Single-state passthrough preserves the original payload — timestamps,
// message, subPhase, isEmpty — so the 25 single-source modules behave exactly
// as they do today.
export function aggregateLifecycles(states: readonly Lifecycle[]): Lifecycle {
  if (states.length === 0) return { phase: 'queued' };
  if (states.length === 1) return states[0];

  const running = states.filter(
    (s): s is Extract<Lifecycle, { phase: 'running' }> => s.phase === 'running',
  );

  const contribution = (s: Lifecycle): number => {
    switch (s.phase) {
      case 'queued':
        return 0;
      case 'running':
        return s.progressPct;
      case 'done':
      case 'error':
        return 100;
    }
  };
  const mean = (xs: readonly number[]): number =>
    xs.length === 0 ? 0 : Math.round(xs.reduce((a, b) => a + b, 0) / xs.length);

  if (running.length > 0) {
    const slowest = running.reduce((a, b) => (a.progressPct <= b.progressPct ? a : b));
    return {
      phase: 'running',
      startedAt: slowest.startedAt,
      progressPct: mean(states.map(contribution)),
      message: slowest.message,
      subPhase: slowest.subPhase,
      updatedAt: slowest.updatedAt,
    };
  }

  const allTerminal = states.every((s) => s.phase === 'done' || s.phase === 'error');
  if (!allTerminal) {
    // Any queued + no running + not all terminal → composite stays queued.
    const earliestStartedAt = states
      .map((s) => (s.phase === 'queued' ? s.startedAt : undefined))
      .filter((t): t is string => !!t)
      .sort()[0];
    return earliestStartedAt
      ? { phase: 'queued', startedAt: earliestStartedAt }
      : { phase: 'queued' };
  }

  // All terminal — pick the earliest startedAt and the latest finishedAt.
  type Terminal = Extract<Lifecycle, { phase: 'done' | 'error' }>;
  const terminals = states as readonly Terminal[];
  const startedAt = terminals.map((s) => s.startedAt).sort()[0];
  const finishedAt = terminals.map((s) => s.finishedAt).sort().slice(-1)[0];
  const errors = terminals.filter(
    (s): s is Extract<Lifecycle, { phase: 'error' }> => s.phase === 'error',
  );
  if (errors.length > 0) {
    return {
      phase: 'error',
      startedAt,
      finishedAt,
      error: errors[0].error,
      progressPct: mean(states.map(contribution)),
    };
  }
  const dones = terminals as ReadonlyArray<Extract<Lifecycle, { phase: 'done' }>>;
  return {
    phase: 'done',
    startedAt,
    finishedAt,
    isEmpty: dones.every((s) => s.isEmpty),
  };
}
