import type { Lifecycle, LoadingProgressState } from '../../types';

type ProgressTone = 'loading' | 'active' | 'ready' | 'error';

interface ProgressIndicatorProps {
  // Preferred: explicit Lifecycle drives tone, pct, and message together.
  lifecycle?: Lifecycle | null;
  // Legacy: LoadingProgressState. Coerced into a Lifecycle internally; kept
  // for unmigrated callers and removed in the cleanup step.
  loading?: LoadingProgressState | null;
  // Legacy overrides — kept so spot callers can force a value without
  // constructing a full Lifecycle. Avoid in new code; pass a Lifecycle.
  active?: boolean;
  pct?: number;
  message?: string;
  phase?: string;
  compact?: boolean;
}

function clampPct(value: number | undefined): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, Number(value)));
}

function toneOf(lc: Lifecycle): ProgressTone {
  switch (lc.phase) {
    case 'queued':
      return 'loading';
    case 'running':
      return 'active';
    case 'done':
      return 'ready';
    case 'error':
      return 'error';
  }
}

// Coerce a legacy LoadingProgressState into a Lifecycle. Used only when the
// caller still passes the old prop; new callers should hand us a Lifecycle.
function liftLoading(s: LoadingProgressState | null | undefined): Lifecycle {
  if (!s) return { phase: 'queued' };
  if (s.error) {
    return {
      phase: 'error',
      startedAt: s.startedAt || s.updatedAt || '1970-01-01T00:00:00.000Z',
      finishedAt: s.updatedAt || s.startedAt || '1970-01-01T00:00:00.000Z',
      error: s.error,
      progressPct: s.progressPct ?? 0,
    };
  }
  if (s.active) {
    const startedAt = s.startedAt || s.updatedAt || '1970-01-01T00:00:00.000Z';
    return {
      phase: 'running',
      startedAt,
      progressPct: s.progressPct ?? 0,
      message: s.message,
      subPhase: s.phase,
      updatedAt: s.updatedAt || startedAt,
    };
  }
  if ((s.progressPct ?? 0) >= 100) {
    const startedAt = s.startedAt || s.updatedAt || '1970-01-01T00:00:00.000Z';
    return {
      phase: 'done',
      startedAt,
      finishedAt: s.updatedAt || startedAt,
      isEmpty: false,
      message: s.message,
    };
  }
  return { phase: 'queued' };
}

function lifecyclePct(lc: Lifecycle): number {
  switch (lc.phase) {
    case 'queued':
      return 0;
    case 'running':
      return clampPct(lc.progressPct);
    case 'done':
      return 100;
    case 'error':
      return clampPct(lc.progressPct);
  }
}

function lifecycleMessage(lc: Lifecycle, fallback: string): string {
  switch (lc.phase) {
    case 'queued':
      return fallback || 'Queued';
    case 'running':
      return lc.message || fallback || 'Loading…';
    case 'done':
      return lc.message || fallback || 'Ready';
    case 'error':
      return lc.error || fallback || 'Failed';
  }
}

function lifecyclePhase(lc: Lifecycle): string {
  switch (lc.phase) {
    case 'running':
      return lc.subPhase || 'running';
    case 'done':
      return 'done';
    case 'error':
      return 'error';
    default:
      return lc.phase;
  }
}

function toneClasses(tone: ProgressTone): { track: string; fill: string; text: string } {
  if (tone === 'error') {
    return {
      track: 'bg-[var(--neon-red)]/10',
      fill: 'bg-[var(--neon-red)]',
      text: 'text-[var(--neon-red)]',
    };
  }
  if (tone === 'ready') {
    return {
      track: 'bg-white/10',
      fill: 'bg-white',
      text: 'text-[var(--text-primary)]',
    };
  }
  if (tone === 'active') {
    return {
      track: 'bg-[var(--neon-yellow)]/10',
      fill: 'bg-[var(--neon-yellow)]',
      text: 'text-[var(--neon-yellow)]',
    };
  }
  return {
    track: 'bg-[var(--bg-glass)]',
    fill: 'bg-[var(--text-tertiary)]',
    text: 'text-[var(--text-secondary)]',
  };
}

export function ProgressIndicator({
  lifecycle,
  loading,
  active,
  pct,
  message,
  phase,
  compact = false,
}: ProgressIndicatorProps) {
  // Prefer an explicit Lifecycle. Fall back to a coerced LoadingProgressState
  // for legacy callers. Spot overrides (active/pct/message/phase) still let
  // callers force a value without constructing a Lifecycle.
  let lc: Lifecycle = lifecycle ?? liftLoading(loading);
  if (active !== undefined || pct !== undefined) {
    // Override branch: synthesize a transient Lifecycle that respects the
    // explicit pct/active props rather than the prop-derived one.
    const overridePct = clampPct(pct ?? lifecyclePct(lc));
    if (active === false && overridePct >= 100) {
      lc = {
        phase: 'done',
        startedAt: '1970-01-01T00:00:00.000Z',
        finishedAt: '1970-01-01T00:00:00.000Z',
        isEmpty: false,
        message,
      };
    } else if (active) {
      lc = {
        phase: 'running',
        startedAt: '1970-01-01T00:00:00.000Z',
        progressPct: overridePct,
        message,
        subPhase: phase,
        updatedAt: '1970-01-01T00:00:00.000Z',
      };
    }
  }

  const tone = toneOf(lc);
  const colors = toneClasses(tone);
  const progressPct = lifecyclePct(lc);
  // Binary spinner: a running state with no real percentage renders as an
  // indeterminate full-width pulse rather than a static 0% bar. (No progress
  // interpolation exists anymore — running == "working", nothing more.)
  const indeterminate = tone === 'active' && progressPct <= 0;
  const displayMessage = message || lifecycleMessage(lc, 'Loading…');
  const displayPhase = phase || lifecyclePhase(lc);

  return (
    <div className={compact ? 'space-y-1' : 'space-y-2'}>
      <div className={`flex items-center justify-between gap-3 text-xs ${colors.text}`}>
        <span className="min-w-0 truncate">{displayMessage}</span>
        {!indeterminate && (
          <span className="font-mono text-[var(--text-primary)]">{Math.round(progressPct)}%</span>
        )}
      </div>
      <div className={`${compact ? 'h-1.5' : 'h-2'} overflow-hidden rounded-full ${colors.track}`}>
        <div
          className={
            indeterminate
              ? `h-full w-full rounded-full ${colors.fill} animate-pulse motion-reduce:animate-none`
              : `h-full rounded-full ${colors.fill} transition-[width] duration-300 ease-out motion-reduce:transition-none${
                  tone === 'active' ? ' progress-sheen' : ''
                }`
          }
          style={indeterminate ? undefined : { width: `${progressPct}%` }}
        />
      </div>
      {!compact && displayPhase && (
        <div className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
          {displayPhase.replace(/_/g, ' ')}
        </div>
      )}
    </div>
  );
}
