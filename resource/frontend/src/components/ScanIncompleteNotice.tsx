import { useMemo } from 'react';
import { RichPopover } from './common/RichPopover';

export interface ScanErrorEntry {
  projectKey: string;
  area: string;
  error: string;
}

interface ScanIncompleteNoticeProps {
  failedProjectCount?: number;
  scannedProjectCount?: number;
  /** Per-project scan errors. When present the count becomes clickable. */
  scanErrors?: ScanErrorEntry[];
  className?: string;
}

interface FailedProject {
  projectKey: string;
  errors: ScanErrorEntry[];
}

function groupByProject(scanErrors: ScanErrorEntry[]): FailedProject[] {
  const buckets = new Map<string, ScanErrorEntry[]>();
  for (const entry of scanErrors) {
    const key = entry?.projectKey || '(unknown project)';
    const bucket = buckets.get(key);
    if (bucket) bucket.push(entry);
    else buckets.set(key, [entry]);
  }
  return [...buckets.entries()]
    .map(([projectKey, errors]) => ({ projectKey, errors }))
    .sort((a, b) => a.projectKey.localeCompare(b.projectKey));
}

function FailedProjectList({
  projects,
  failedProjectCount,
}: {
  projects: FailedProject[];
  failedProjectCount: number;
}) {
  const truncated = failedProjectCount > projects.length;
  return (
    <div className="space-y-1.5">
      <div className="text-xs font-semibold text-[var(--text-primary)]">
        Projects that failed to scan{' '}
        {truncated ? `(${projects.length} of ${failedProjectCount})` : `(${projects.length})`}
      </div>
      <ul className="max-h-64 overflow-y-auto space-y-1.5 pr-1">
        {projects.map((project) => (
          <li key={project.projectKey} className="leading-snug">
            <div className="text-xs font-mono text-[var(--text-secondary)] break-all">
              {project.projectKey}
            </div>
            {project.errors.map((entry, i) => (
              <div
                key={`${entry.area}-${i}`}
                className="text-[11px] text-[var(--text-muted)] break-words"
                title={entry.error}
              >
                {entry.area ? `${entry.area}: ` : ''}
                {entry.error || 'unknown error'}
              </div>
            ))}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Small reusable inline banner shown when a project scan completed with
 * per-project failures. Renders nothing when there were no failures. When
 * `scanErrors` is supplied the failure count is a popover trigger listing the
 * projects that failed and why.
 */
export function ScanIncompleteNotice({
  failedProjectCount,
  scannedProjectCount,
  scanErrors,
  className = '',
}: ScanIncompleteNoticeProps) {
  const projects = useMemo(() => groupByProject(scanErrors || []), [scanErrors]);

  if (!failedProjectCount) return null;

  const countLabel = <>{failedProjectCount}</>;

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
        {projects.length > 0 ? (
          <RichPopover
            width={360}
            ariaLabel={`Show the ${projects.length} projects that failed to scan`}
            content={
              <FailedProjectList projects={projects} failedProjectCount={failedProjectCount} />
            }
          >
            <span className="underline decoration-dotted underline-offset-2 transition-colors hover:text-[var(--text-primary)]">
              {countLabel}
            </span>
          </RichPopover>
        ) : (
          countLabel
        )}{' '}
        of {scannedProjectCount ?? '?'} projects failed to scan — results may be incomplete.
      </span>
    </div>
  );
}
