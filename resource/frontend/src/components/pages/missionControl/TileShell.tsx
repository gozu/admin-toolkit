import { motion } from 'framer-motion';
import type { ReactNode } from 'react';
import type { Lifecycle, PageId } from '../../../types';
import { TILE_VARIANTS } from './tokens';

// Shared tile chrome for the Mission Control wall: micro-caps title, domain
// accent hairline, lifecycle gate, whole-tile drill-down. Tiles never scroll —
// content is designed to fit, anything longer truncates.

export type DomainAccent = 'system' | 'connections' | 'projects' | 'compute' | 'hygiene';

const ACCENT_COLOR: Record<DomainAccent, string> = {
  system: 'var(--neon-cyan)',
  connections: 'var(--neon-purple)',
  projects: 'var(--neon-green)',
  compute: 'var(--neon-amber)',
  hygiene: 'var(--neon-magenta)',
};

function TileSkeleton() {
  return (
    <div aria-hidden className="flex h-full animate-pulse flex-col justify-center gap-2">
      <div className="h-3 w-2/5 rounded bg-[var(--bg-elevated)]" />
      <div className="h-2 w-4/5 rounded bg-[var(--bg-elevated)]" />
      <div className="h-2 w-3/5 rounded bg-[var(--bg-elevated)]" />
    </div>
  );
}

export interface TileShellProps {
  title: string;
  /** CSS grid-template-area name this tile occupies. */
  area: string;
  target: PageId;
  accent: DomainAccent;
  lifecycle: Lifecycle;
  onNavigate: (page: PageId) => void;
  /** Optional small chrome on the right of the title row (e.g. coverage). */
  titleRight?: ReactNode;
  /** Shown when lifecycle is done with isEmpty. */
  emptyText?: string;
  /**
   * For user-triggered scans (K8s, DB Health): `queued` means intentionally
   * idle, not loading — show this dim microcopy instead of a pulsing skeleton.
   */
  idleText?: string;
  /**
   * True once the tile's data source has produced something renderable.
   * While streaming (`running`), a tile with data shows live content under
   * the progress hairline instead of a skeleton — the original-page pattern.
   */
  hasData?: boolean;
  children: ReactNode;
}

export function TileShell({
  title,
  area,
  target,
  accent,
  lifecycle,
  onNavigate,
  titleRight,
  emptyText,
  idleText,
  hasData = false,
  children,
}: TileShellProps) {
  const accentColor = ACCENT_COLOR[accent];
  const phase = lifecycle.phase;
  const isIdle = phase === 'queued' && idleText !== undefined;
  const showSkeleton = !isIdle && (phase === 'queued' || phase === 'running') && !hasData;
  const isError = phase === 'error';
  const isEmpty = phase === 'done' && lifecycle.isEmpty;

  return (
    <motion.div
      variants={TILE_VARIANTS}
      role="button"
      tabIndex={0}
      aria-label={`${title} — open page`}
      onClick={() => onNavigate(target)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onNavigate(target);
        }
      }}
      className="group relative flex min-h-0 min-w-0 cursor-pointer flex-col overflow-hidden rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface)] transition-colors duration-200 hover:border-[var(--text-tertiary)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--accent)]"
      style={{
        gridArea: area,
        ...(isError
          ? { background: 'color-mix(in srgb, var(--neon-red) 5%, var(--bg-surface))' }
          : {}),
      }}
    >
      {/* Domain accent hairline */}
      <span
        aria-hidden
        className="absolute inset-x-0 top-0 h-px"
        style={{ background: `linear-gradient(90deg, ${accentColor}, transparent 75%)`, opacity: 0.7 }}
      />
      {/* Running progress hairline */}
      {phase === 'running' && (
        <span
          aria-hidden
          className="absolute left-0 top-0 z-10 h-[2px] transition-[width] duration-500"
          style={{ width: `${lifecycle.progressPct}%`, background: 'var(--neon-yellow)' }}
        />
      )}
      <div className="flex items-center justify-between gap-2 px-2.5 pb-1 pt-2">
        <span
          className="truncate text-[10px] font-semibold uppercase tracking-[0.14em] text-[var(--text-tertiary)]"
          style={{ fontFamily: 'var(--font-heading)' }}
        >
          {title}
        </span>
        {titleRight && <span className="flex-shrink-0">{titleRight}</span>}
      </div>
      <div className="relative min-h-0 flex-1 px-2.5 pb-2">
        {isIdle ? (
          <div className="flex h-full items-center text-[11px] italic text-[var(--text-tertiary)]">
            {idleText}
          </div>
        ) : showSkeleton ? (
          <TileSkeleton />
        ) : isError ? (
          <div className="flex h-full items-center text-[11px] leading-snug text-[var(--neon-red)]">
            <span className="line-clamp-3">{lifecycle.error}</span>
          </div>
        ) : isEmpty ? (
          <div className="flex h-full items-center text-[11px] text-[var(--text-tertiary)]">
            {emptyText ?? 'No data'}
          </div>
        ) : (
          children
        )}
      </div>
    </motion.div>
  );
}
