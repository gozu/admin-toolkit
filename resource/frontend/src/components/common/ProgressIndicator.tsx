import type { Lifecycle, LoadingProgressState } from '../../types';
import { useCompletionBeat } from '../../hooks/useCompletionBeat';

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
  /** Keep the completion beat before removing an inline loading surface. */
  hideWhenDone?: boolean;
  className?: string;
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
      return lc.message || fallback || 'Queued';
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
      track: 'bg-[var(--neon-green)]/10',
      fill: 'bg-[var(--neon-green)]',
      text: 'text-[var(--neon-green)]',
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
  hideWhenDone = false,
  className = '',
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
  // Before the first measured percentage, keep the track empty. A traveling
  // segment would jump backwards when a small real percentage arrives.
  const indeterminate = tone === 'active' && progressPct <= 0 && !(lc.phase === 'running' && lc.subPhase === 'aborted');
  const displayMessage = message || lifecycleMessage(lc, '');
  // Active messages get a steady ellipsis; strip any literal trailing
  // one so we never render "Loading……".
  const messageText =
    tone === 'active' ? displayMessage.replace(/(?:\.{3}|…)\s*$/, '') : displayMessage;
  const displayPhase = phase || lifecyclePhase(lc);
  const finishing = useCompletionBeat(lc);
  if (hideWhenDone && tone === 'ready' && !finishing) return null;

  return (
    <div className={`progress-landing ${compact ? 'space-y-1' : 'space-y-2'} ${className}`} data-state={tone} data-finishing={finishing || undefined}>
      <div className={`flex items-center justify-between gap-3 text-xs ${colors.text}`}>
        <span className="min-w-0 truncate">
          {messageText}
          {tone === 'active' && <span aria-hidden>…</span>}
        </span>
        <span className="progress-readout flex h-5 shrink-0 items-center gap-1.5 font-mono">
          <svg aria-hidden className={`progress-check ${tone === 'ready' ? '' : 'invisible'}`} width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path pathLength="1" d="m3 8 3 3 7-7" />
          </svg>
          <span className="w-8 text-right">{indeterminate ? '—' : `${Math.round(progressPct)}%`}</span>
        </span>
      </div>
      <div role="progressbar" aria-label={messageText} aria-valuemin={0} aria-valuemax={100} aria-valuenow={indeterminate ? undefined : progressPct} className={`progress-track relative ${compact ? 'h-2' : 'h-3'} overflow-hidden rounded-full ${colors.track}`}>
        <div
          className={
            indeterminate
              ? `progress-fill h-full rounded-full ${colors.fill}`
              : `progress-fill h-full rounded-full ${colors.fill} transition-[width] duration-300 ease-out motion-reduce:transition-none`
          }
          style={{ width: `${progressPct}%` }}
        >
          {tone === 'active' && !indeterminate && <span aria-hidden className="progress-tip" />}
        </div>
        <span aria-hidden className="progress-finish-sweep" />
      </div>
      {!compact && displayPhase && (
        <div className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
          {displayPhase.replace(/_/g, ' ')}
        </div>
      )}
    </div>
  );
}
