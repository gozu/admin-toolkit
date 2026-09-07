/**
 * Shared "re-run host command" affordance: a small Refresh button plus an
 * optional "as of HH:MM:SS" freshness label. Used by every host-command-backed
 * surface (CPU/Memory process tables, Memory summary, host overview, K8s audit)
 * so wording and styling stay consistent. Styled to match the original
 * ProcessUsageTable button.
 */
function formatClock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour12: false });
}

export function RefreshControl({
  busy,
  fetchedAt,
  onRefresh,
  label = 'Refresh',
  disabled = false,
  title,
}: {
  busy: boolean;
  fetchedAt?: string | null;
  onRefresh: () => void;
  label?: string;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <span className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
      {fetchedAt && (
        <span className="font-mono tabular-nums">as of {formatClock(fetchedAt)}</span>
      )}
      <button
        type="button"
        onClick={onRefresh}
        disabled={busy || disabled}
        aria-busy={busy}
        title={title}
        className="refresh-control inline-flex items-center gap-1.5 rounded px-2 py-1 text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)] disabled:opacity-50"
      >
        <svg aria-hidden="true" className="refresh-control-icon h-3.5 w-3.5 shrink-0" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M13 6a5.2 5.2 0 1 0 .1 3.6M13 2.5V6H9.5" />
        </svg>
        <span className="inline-grid text-left">
          {/* Both labels size the same grid cell so adjacent controls stay put. */}
          <span className="col-start-1 row-start-1" style={{ visibility: busy ? 'hidden' : 'visible' }} aria-hidden={busy}>{label}</span>
          <span className="col-start-1 row-start-1" style={{ visibility: busy ? 'visible' : 'hidden' }} aria-hidden={!busy}>Refreshing…</span>
        </span>
      </button>
    </span>
  );
}
