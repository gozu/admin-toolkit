import { motion, AnimatePresence } from 'framer-motion';
import { useEffect, useState } from 'react';
import type { HealthScore, HealthIssue, HealthSeverity, HealthCategoryScore, HealthCategory } from '../types';
import { HEALTH_FACTOR_CONTROLS, type HealthFactorKey, type HealthFactorToggles } from '../hooks/useHealthScore';

interface HealthScoreCardProps {
  healthScore: HealthScore;
  /** While true, hide the real score and show a rolling "calculating" effect. */
  calculating?: boolean;
}

/** Rapidly-cycling random 2-digit number shown while the score is being computed. */
function RollingScore({ className }: { className?: string }) {
  const [n, setN] = useState(() => Math.floor(10 + Math.random() * 90));
  useEffect(() => {
    const id = setInterval(() => setN(Math.floor(10 + Math.random() * 90)), 60);
    return () => clearInterval(id);
  }, []);
  return <span className={className}>{n}</span>;
}

interface HealthIssuesPanelProps {
  healthScore: HealthScore;
  healthFactorToggles: HealthFactorToggles;
  onToggleHealthFactor: (factor: HealthFactorKey) => void;
}

const severityConfig: Record<HealthSeverity, { bg: string; text: string; border: string; icon: React.ReactNode }> = {
  critical: {
    bg: 'bg-[var(--status-critical-bg)]',
    text: 'text-[var(--neon-red)]',
    border: 'border-[var(--status-critical-border)]',
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
      </svg>
    ),
  },
  warning: {
    bg: 'bg-[var(--status-warning-bg)]',
    text: 'text-[var(--neon-amber)]',
    border: 'border-[var(--status-warning-border)]',
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  },
  info: {
    bg: 'bg-[var(--status-info-bg)]',
    text: 'text-[var(--neon-cyan)]',
    border: 'border-[var(--status-info-border)]',
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  },
  good: {
    bg: 'bg-[var(--status-success-bg)]',
    text: 'text-[var(--neon-green)]',
    border: 'border-[var(--status-success-border)]',
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
      </svg>
    ),
  },
};

const statusConfig: Record<HealthScore['status'], { label: string; color: string; glowClass: string }> = {
  healthy: {
    label: 'Healthy',
    color: 'text-[var(--neon-green)]',
    glowClass: 'health-glow-green',
  },
  warning: {
    label: 'Needs Attention',
    color: 'text-[var(--neon-amber)]',
    glowClass: 'health-glow-amber',
  },
  critical: {
    label: 'Critical Issues',
    color: 'text-[var(--neon-red)]',
    glowClass: 'health-glow-red',
  },
};

const categoryExplanations: Record<HealthCategory, string> = {
  code_envs:
    'Measures per-project code environment sprawl. Each extra code environment multiplies storage, fragility, deployment time, and failure surface.',
  project_footprint:
    'Measures project storage pressure using project size distribution. Very large projects increase storage cost and operational risk.',
  system_capacity:
    'Checks runtime capacity: available memory, disk pressure, and open-files limits. Low headroom raises outage risk.',
  security_isolation:
    'Checks isolation controls (user isolation and cgroups). Weak isolation increases blast radius and resource contention risk.',
  version_currency:
    'Checks platform version currency (Python and Spark). Older versions increase security exposure and upgrade debt.',
  runtime_config:
    'Checks operational runtime settings such as Java heap sizing and disabled features.',
  version:
    'Checks platform version currency (Python and Spark). Older versions increase security exposure and upgrade debt.',
  system:
    'Legacy category kept for backward compatibility with older snapshots.',
  config:
    'Legacy category kept for backward compatibility with older snapshots.',
  security:
    'Legacy category kept for backward compatibility with older snapshots.',
  connections:
    'Checks per-connection configuration audit: fast-write, details readability, HDFS interface, and default connections (e.g. filesystem_root).',
  license:
    'License compliance signal (currently not weighted).',
  errors:
    'Runtime/parsing error signal (currently not weighted).',
};

function CategoryTooltip({ text }: { text: string }) {
  return (
    <span className="relative ml-1 inline-flex items-center group">
      <span
        tabIndex={0}
        className="inline-flex h-3.5 w-3.5 items-center justify-center rounded-full border border-[var(--border-glass)] text-[10px] font-semibold text-[var(--text-muted)] select-none"
      >
        i
      </span>
      <span className="pointer-events-none absolute left-1/2 top-full z-50 mt-1 hidden w-64 -translate-x-1/2 rounded-md border border-[var(--border-glass)] bg-[var(--bg-elevated)] px-2 py-1 text-[11px] leading-snug text-[var(--text-primary)] shadow-lg group-hover:block group-focus-within:block">
        {text}
      </span>
    </span>
  );
}

const statusColorKey: Record<HealthScore['status'], string> = { healthy: 'green', warning: 'amber', critical: 'red' };

function ScoreGauge({ score, status, calculating }: { score: number; status: HealthScore['status']; calculating?: boolean }) {
  const config = statusConfig[status];
  const circumference = 2 * Math.PI * 45;
  const strokeDashoffset = circumference - (score / 100) * circumference;
  const colorKey = statusColorKey[status];

  if (calculating) {
    return (
      <div className="relative w-36 h-36">
        <motion.svg
          className="w-full h-full"
          viewBox="0 0 100 100"
          animate={{ rotate: 360 }}
          transition={{ duration: 1.4, ease: 'linear', repeat: Infinity }}
        >
          {/* Background circle */}
          <circle cx="50" cy="50" r="45" fill="none" stroke="var(--border-glass)" strokeWidth="8" />
          {/* Neutral indeterminate arc (grey = loading per tone semantics) */}
          <circle
            cx="50"
            cy="50"
            r="45"
            fill="none"
            stroke="var(--text-muted)"
            strokeWidth="8"
            strokeLinecap="round"
            strokeDasharray={`${circumference * 0.25} ${circumference}`}
            opacity={0.5}
          />
        </motion.svg>
        {/* Center content */}
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <RollingScore className="text-4xl font-bold font-mono text-[var(--text-muted)]" />
          <span className="text-xs text-[var(--text-muted)] uppercase tracking-wider">Score</span>
        </div>
      </div>
    );
  }

  return (
    <div className="relative w-36 h-36">
      <svg className="w-full h-full transform -rotate-90" viewBox="0 0 100 100">
        <defs>
          <linearGradient id={`scoreGradient-${status}`} x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor={`var(--health-${colorKey}-start)`} />
            <stop offset="100%" stopColor={`var(--health-${colorKey}-end)`} />
          </linearGradient>
        </defs>
        {/* Background circle */}
        <circle
          cx="50"
          cy="50"
          r="45"
          fill="none"
          stroke="var(--border-glass)"
          strokeWidth="8"
        />
        {/* Score arc */}
        <motion.circle
          cx="50"
          cy="50"
          r="45"
          fill="none"
          stroke={`url(#scoreGradient-${status})`}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={circumference}
          initial={{ strokeDashoffset: circumference }}
          animate={{ strokeDashoffset }}
          transition={{ duration: 2, ease: [0.16, 1, 0.3, 1] }}
        />
      </svg>
      {/* Center content */}
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <motion.span
          className={`text-4xl font-bold font-mono ${config.color}`}
          initial={{ opacity: 0, scale: 0.5 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.5, delay: 0.3 }}
        >
          {score}
        </motion.span>
        <span className="text-xs text-[var(--text-muted)] uppercase tracking-wider">Score</span>
      </div>
    </div>
  );
}

function CategoryBar({ category, calculating }: { category: HealthCategoryScore; calculating?: boolean }) {
  const barColor = category.score >= 80
    ? 'bg-[var(--neon-green)]'
    : category.score >= 50
      ? 'bg-[var(--neon-amber)]'
      : 'bg-[var(--neon-red)]';
  const explanation = categoryExplanations[category.category] || category.label;

  if (calculating) {
    return (
      <div className="grid grid-cols-[172px_1fr_36px] items-center gap-1.5">
        <div className="text-xs text-[var(--text-secondary)] whitespace-nowrap">
          <span className="inline-flex items-center leading-tight">
            {category.label}
            <CategoryTooltip text={explanation} />
          </span>
        </div>
        <div className="h-2 bg-[var(--bg-glass)] rounded-full overflow-hidden">
          <div className="h-full w-2/5 rounded-full bg-[var(--text-muted)] opacity-30 animate-pulse" />
        </div>
        <div className="w-8 text-right">
          <RollingScore className="text-xs font-mono text-[var(--text-muted)]" />
        </div>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-[172px_1fr_36px] items-center gap-1.5">
      <div className="text-xs text-[var(--text-secondary)] whitespace-nowrap">
        <span className="inline-flex items-center leading-tight">
          {category.label}
          <CategoryTooltip text={explanation} />
        </span>
      </div>
      <div className="h-2 bg-[var(--bg-glass)] rounded-full overflow-hidden">
        <motion.div
          className={`h-full ${barColor} rounded-full`}
          initial={{ width: 0 }}
          animate={{ width: `${category.score}%` }}
          transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
        />
      </div>
      <div className="w-8 text-xs font-mono text-[var(--text-muted)] text-right">
        {Math.round(category.score)}
      </div>
    </div>
  );
}

function IssueItem({ issue, index }: { issue: HealthIssue; index: number }) {
  const [expanded, setExpanded] = useState(false);
  const config = severityConfig[issue.severity];

  return (
    <motion.div
      className={`${config.bg} border ${config.border} rounded-lg overflow-hidden`}
      initial={{ opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.3, delay: index * 0.05 }}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-3 py-2 flex items-center gap-2 text-left hover:bg-[var(--bg-glass-hover)] transition-colors"
      >
        <span className={config.text}>{config.icon}</span>
        <span className={`flex-1 text-sm font-medium ${config.text}`}>{issue.title}</span>
        <motion.svg
          className={`w-4 h-4 ${config.text}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          animate={{ rotate: expanded ? 180 : 0 }}
          transition={{ duration: 0.2 }}
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </motion.svg>
      </button>
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="px-3 pb-3 pt-1 border-t border-[var(--border-glass)]">
              <p className="text-sm text-[var(--text-secondary)] mb-2">{issue.description}</p>
              {issue.recommendation && (
                <p className="text-sm">
                  <span className="text-[var(--text-muted)]">Recommendation: </span>
                  <span className="text-[var(--text-primary)]">{issue.recommendation}</span>
                </p>
              )}
              {issue.value !== undefined && issue.threshold !== undefined && (
                <div className="mt-2 flex gap-4 text-xs font-mono">
                  <span className="text-[var(--text-muted)]">
                    Current: <span className={config.text}>{issue.value}</span>
                  </span>
                  <span className="text-[var(--text-muted)]">
                    Target: <span className="text-[var(--neon-green)]">{issue.threshold}</span>
                  </span>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

export function HealthIssuesDetectedPanel({ healthScore }: { healthScore: HealthScore }) {
  const [showAllIssues, setShowAllIssues] = useState(false);

  const displayedIssues = showAllIssues ? healthScore.issues : healthScore.issues.slice(0, 3);
  const hasMoreIssues = healthScore.issues.length > 3;

  return (
    <motion.div
      className="rounded-xl p-4"
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium uppercase tracking-wider text-[var(--text-muted)]">
          {healthScore.issues.length > 0 ? 'Issues Detected' : 'No Issues Detected'}
        </h3>
        <div className="flex items-center gap-2">
          {healthScore.criticalCount > 0 && (
            <span className="badge badge-critical">{healthScore.criticalCount} critical</span>
          )}
          {healthScore.warningCount > 0 && (
            <span className="badge badge-warning">{healthScore.warningCount} warning</span>
          )}
          {healthScore.infoCount > 0 && (
            <span className="badge badge-info">{healthScore.infoCount} info</span>
          )}
        </div>
      </div>
      {healthScore.issues.length > 0 ? (
        <div className="space-y-2">
          {displayedIssues.map((issue, idx) => (
            <IssueItem key={issue.id} issue={issue} index={idx} />
          ))}
        </div>
      ) : (
        <motion.div
          className="flex min-h-32 flex-col items-center justify-center py-6"
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.2 }}
        >
          <div className="mb-2 flex h-12 w-12 items-center justify-center rounded-full bg-[var(--status-success-bg)]">
            <svg className="h-6 w-6 text-[var(--neon-green)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <span className="text-sm text-[var(--text-muted)]">System looks healthy!</span>
        </motion.div>
      )}
      {hasMoreIssues && (
        <button
          onClick={() => setShowAllIssues(!showAllIssues)}
          className="mt-3 flex items-center justify-center gap-1 text-sm text-[var(--neon-cyan)] transition-colors hover:text-[var(--neon-cyan-dim)]"
        >
          {showAllIssues ? (
            <>
              Show less
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
              </svg>
            </>
          ) : (
            <>
              Show {healthScore.issues.length - 3} more
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </>
          )}
        </button>
      )}
    </motion.div>
  );
}

export function HealthFactorTogglesPanel({
  healthFactorToggles,
  onToggleHealthFactor,
}: {
  healthFactorToggles: HealthFactorToggles;
  onToggleHealthFactor: (factor: HealthFactorKey) => void;
}) {
  const [showControls, setShowControls] = useState(true);

  return (
    <div className="rounded-xl p-4">
      <button
        onClick={() => setShowControls(!showControls)}
        className="flex w-full items-center justify-between rounded-lg px-3 py-2 transition-colors hover:bg-[var(--bg-glass)]"
      >
        <div className="flex items-center gap-2">
          <motion.svg
            className="h-4 w-4 text-[var(--text-muted)]"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            animate={{ rotate: showControls ? 90 : 0 }}
            transition={{ duration: 0.2 }}
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </motion.svg>
          <span className="text-sm font-medium text-[var(--text-secondary)]">
            Customize health checks
          </span>
        </div>
        <span className="font-mono text-xs text-[var(--text-muted)]">
          {Object.values(healthFactorToggles).filter(Boolean).length} / {HEALTH_FACTOR_CONTROLS.length} enabled
        </span>
      </button>

      <AnimatePresence>
        {showControls && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="px-3 pb-2 pt-3">
              <div className="flex flex-wrap gap-2">
                {HEALTH_FACTOR_CONTROLS.map((control) => {
                  const enabled = healthFactorToggles[control.key];
                  return (
                    <button
                      key={control.key}
                      onClick={() => onToggleHealthFactor(control.key)}
                      className={
                        enabled
                          ? 'flex items-center gap-1.5 rounded border border-[var(--neon-cyan)]/40 bg-[var(--neon-cyan)]/10 px-2.5 py-1 text-xs text-[var(--neon-cyan)] transition-colors'
                          : 'flex items-center gap-1.5 rounded border border-[var(--border-glass)] bg-[var(--bg-glass)] px-2.5 py-1 text-xs text-[var(--text-muted)] transition-colors hover:border-[var(--border-glass-hover)]'
                      }
                    >
                      {enabled ? (
                        <svg className="w-3.5 h-3.5 flex-shrink-0 text-[var(--neon-cyan)]" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                          <rect x="3" y="3" width="18" height="18" rx="3" strokeWidth={2} />
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12l3 3 5-6" />
                        </svg>
                      ) : (
                        <svg className="w-3.5 h-3.5 flex-shrink-0 text-[var(--text-muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                          <rect x="3" y="3" width="18" height="18" rx="3" strokeWidth={2} />
                        </svg>
                      )}
                      {control.label}
                    </button>
                  );
                })}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function HealthIssuesPanel({ healthScore, healthFactorToggles, onToggleHealthFactor }: HealthIssuesPanelProps) {
  return (
    <div className="space-y-4">
      <HealthIssuesDetectedPanel healthScore={healthScore} />
      <HealthFactorTogglesPanel
        healthFactorToggles={healthFactorToggles}
        onToggleHealthFactor={onToggleHealthFactor}
      />
    </div>
  );
}

export function HealthScoreCard({ healthScore, calculating = false }: HealthScoreCardProps) {
  const config = statusConfig[healthScore.status];

  return (
    <motion.div
      id="health-score"
      className={`glass-card p-5 ${calculating ? '' : config.glowClass}`}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold text-neon-subtle">Health Score</h2>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[180px_1fr]">
        {/* Score Gauge */}
        <div className="flex flex-col items-center justify-center">
          <ScoreGauge score={healthScore.overall} status={healthScore.status} calculating={calculating} />
          <motion.div
            className={`mt-2 text-lg font-semibold ${calculating ? 'text-[var(--text-muted)]' : config.color}`}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.5 }}
          >
            {calculating ? 'Calculating…' : config.label}
          </motion.div>
        </div>

        {/* Category Breakdown */}
        <div className="flex flex-col justify-center space-y-3">
          <h3 className="text-sm font-medium text-[var(--text-muted)] uppercase tracking-wider mb-1">
            Category Scores
          </h3>
          {healthScore.categories.map((category, idx) => (
            <motion.div
              key={category.category}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.2 + idx * 0.1 }}
            >
              <CategoryBar category={category} calculating={calculating} />
            </motion.div>
          ))}
        </div>
      </div>
    </motion.div>
  );
}
