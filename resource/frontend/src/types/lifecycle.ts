export interface LoadingProgressState {
  active: boolean;
  progressPct: number;
  phase?: string;
  message?: string;
  startedAt?: string;
  updatedAt?: string;
  error?: string | null;
}

/**
 * Lifecycle is the discriminated-union replacement for LoadingProgressState.
 *
 * The four phases are *structurally* distinct so that "not asked yet" cannot
 * be confused with "done":
 *   - queued:  scheduled to run but not started (the default for any field that
 *              hasn't been written yet — `startedAt` is optional so a derived
 *              composite or pre-orchestrator boot tick can return queued
 *              without inventing a timestamp).
 *   - running: actively progressing, may carry a percentage and sub-phase
 *   - done:    finished successfully (the only path to a completed glyph)
 *   - error:   finished with a failure
 *
 * Timestamps must be set when lifecycle changes; never inside render-time
 * resolvers.
 */
export type Lifecycle =
  | { phase: 'queued'; startedAt?: string }
  | {
      phase: 'running';
      startedAt: string;
      progressPct: number;
      message?: string;
      subPhase?: string;
      updatedAt: string;
    }
  | {
      phase: 'done';
      startedAt: string;
      finishedAt: string;
      isEmpty: boolean;
      message?: string;
    }
  | {
      phase: 'error';
      startedAt: string;
      finishedAt: string;
      error: string;
      progressPct: number;
    };

export function isTerminal(l: Lifecycle): boolean {
  return l.phase === 'done' || l.phase === 'error';
}

export function isActive(l: Lifecycle): boolean {
  return l.phase === 'queued' || l.phase === 'running';
}
