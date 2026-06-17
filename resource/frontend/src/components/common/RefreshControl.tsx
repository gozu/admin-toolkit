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
        title={title}
        className="rounded px-2 py-1 text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)] disabled:opacity-50"
      >
        {busy ? 'Refreshing…' : label}
      </button>
    </span>
  );
}
