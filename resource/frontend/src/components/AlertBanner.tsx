import type { ReactNode } from 'react';
import { motion } from 'framer-motion';
import type { DetectedIssue, IssueSeverity } from '../hooks/useIssueDetection';

interface AlertBannerProps {
  issues: DetectedIssue[];
  criticalCount: number;
  warningCount: number;
  onScrollTo: (target: string) => void;
  ultraWide?: boolean;
}

const severityConfig: Record<IssueSeverity, { bg: string; text: string; border: string }> = {
  critical: {
    bg: 'bg-[var(--status-critical-bg)]',
    text: 'text-[var(--neon-red)]',
    border: 'border border-[var(--status-critical-border)]',
  },
  warning: {
    bg: 'bg-[var(--status-warning-bg)]',
    text: 'text-[var(--neon-amber)]',
    border: 'border border-[var(--status-warning-border)]',
  },
  info: {
    bg: 'bg-[var(--status-info-bg)]',
    text: 'text-[var(--neon-cyan)]',
    border: 'border border-[var(--status-info-border)]',
  },
};

const severityIcons: Record<IssueSeverity, ReactNode> = {
  critical: (
    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
      />
    </svg>
  ),
  warning: (
    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
      />
    </svg>
  ),
  info: (
    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
      />
    </svg>
  ),
};

export function AlertBanner({
  issues,
  criticalCount,
  warningCount,
  onScrollTo,
}: AlertBannerProps) {
  if (issues.length === 0) return null;

  // Show only top issues (max 5 to avoid overflow)
  const displayedIssues = issues.slice(0, 5);
  const remainingCount = issues.length - displayedIssues.length;

  // Determine banner style based on worst severity
  const bannerStyle =
    criticalCount > 0
      ? 'bg-[var(--status-critical-bg)] border-[var(--status-critical-border)]'
      : warningCount > 0
        ? 'bg-[var(--status-warning-bg)] border-[var(--status-warning-border)]'
        : 'bg-[var(--status-info-bg)] border-[var(--status-info-border)]';

  const labelStyle =
    criticalCount > 0
      ? 'text-[var(--neon-red)]'
      : warningCount > 0
        ? 'text-[var(--neon-amber)]'
        : 'text-[var(--neon-cyan)]';

  return (
    <motion.div
      className={`${bannerStyle} border-b`}
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      <div className="py-2 flex flex-wrap items-center gap-2">
        <span className={`text-sm font-medium ${labelStyle} mr-1`}>
          {criticalCount > 0 ? 'Issues detected:' : warningCount > 0 ? 'Warnings:' : 'Info:'}
        </span>

        {displayedIssues.map((issue, idx) => {
          const config = severityConfig[issue.severity];
          return (
            <motion.button
              key={issue.id}
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 0.2, delay: idx * 0.05 }}
              onClick={() => issue.scrollTarget && onScrollTo(issue.scrollTarget)}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full
                         ${config.bg} ${config.text} ${config.border} text-sm font-medium font-mono
                         hover:opacity-80 transition-all duration-150`}
              title={issue.description}
            >
              {severityIcons[issue.severity]}
              <span className="max-w-[200px] truncate">{issue.title}</span>
              {issue.scrollTarget && (
                <svg
                  className="w-3 h-3 opacity-60"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M9 5l7 7-7 7"
                  />
                </svg>
              )}
            </motion.button>
          );
        })}

        {remainingCount > 0 && (
          <span className="text-sm text-[var(--text-muted)] font-mono">
            +{remainingCount} more
          </span>
        )}
      </div>
    </motion.div>
  );
}
