interface ScanIncompleteNoticeProps {
  failedProjectCount?: number;
  scannedProjectCount?: number;
  className?: string;
}

/**
 * Small reusable inline banner shown when a project scan completed with
 * per-project failures. Renders nothing when there were no failures.
 */
export function ScanIncompleteNotice({
  failedProjectCount,
  scannedProjectCount,
  className = '',
}: ScanIncompleteNoticeProps) {
  if (!failedProjectCount) return null;

  return (
    <div
      className={`flex items-center gap-1.5 px-2.5 py-1 rounded
                 bg-[var(--status-warning-bg)] border border-[var(--status-warning-border)]
                 text-[var(--neon-amber)] text-sm font-medium font-mono ${className}`}
    >
      <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
          d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
        />
      </svg>
      <span>
        {failedProjectCount} of {scannedProjectCount ?? '?'} projects failed to scan — results may
        be incomplete.
      </span>
    </div>
  );
}
