import { motion, AnimatePresence } from 'framer-motion';
import { useState } from 'react';
import type { HealthScore, HealthIssue, HealthCategoryScore, HealthCategory } from '../../types';
import { compareHealthIssues } from '../../utils/compareData';

interface ComparisonHealthSectionProps {
  beforeHealth: HealthScore | null;
  afterHealth: HealthScore | null;
  healthChange: number;
  dynamicTitle?: string;
}

const statusConfig: Record<HealthScore['status'], { label: string; color: string; glowClass: string }> = {
  healthy: {
    label: 'Healthy',
    color: 'text-[var(--neon-green)]',
    glowClass: 'shadow-[0_0_15px_rgba(0,255,136,0.2)]',
  },
  warning: {
    label: 'Needs Attention',
    color: 'text-[var(--neon-amber)]',
    glowClass: 'shadow-[0_0_15px_rgba(255,184,0,0.2)]',
  },
  critical: {
    label: 'Critical Issues',
    color: 'text-[var(--neon-red)]',
    glowClass: 'shadow-[0_0_15px_rgba(255,51,102,0.2)]',
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

function ScoreGauge({ score, status, label }: { score: number; status: HealthScore['status']; label: string }) {
  const config = statusConfig[status];
  const circumference = 2 * Math.PI * 40;
  const strokeDashoffset = circumference - (score / 100) * circumference;

  const gradientColors = {
    healthy: { start: '#00ff88', end: '#00f5ff' },
    warning: { start: '#ffb800', end: '#ff8800' },
    critical: { start: '#ff3366', end: '#ff0066' },
  };

  const colors = gradientColors[status];
  const gradientId = `scoreGradient-${label}-${status}`;

  return (
    <div className="flex flex-col items-center">
      <div className="relative w-28 h-28">
        <svg className="w-full h-full transform -rotate-90" viewBox="0 0 100 100">
          <defs>
            <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stopColor={colors.start} />
              <stop offset="100%" stopColor={colors.end} />
            </linearGradient>
          </defs>
          <circle
            cx="50"
            cy="50"
            r="40"
            fill="none"
            stroke="var(--border-glass)"
            strokeWidth="8"
          />
          <motion.circle
            cx="50"
            cy="50"
            r="40"
            fill="none"
            stroke={`url(#${gradientId})`}
            strokeWidth="8"
            strokeLinecap="round"
            strokeDasharray={circumference}
            initial={{ strokeDashoffset: circumference }}
            animate={{ strokeDashoffset }}
            transition={{ duration: 1, ease: [0.16, 1, 0.3, 1] }}
          />
          {/* Target threshold line at 80 (healthy threshold) */}
          <line
            x1="50"
            y1="6"
            x2="50"
            y2="14"
            stroke="var(--text-muted)"
            strokeWidth="2"
            strokeDasharray="2,2"
            opacity="0.6"
            transform={`rotate(${(80 / 100) * 360}, 50, 50)`}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <motion.span
            className={`text-2xl font-bold font-mono ${config.color}`}
            initial={{ opacity: 0, scale: 0.5 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.5, delay: 0.3 }}
          >
            {score}
          </motion.span>
        </div>
      </div>
      <span className="mt-2 text-xs text-[var(--text-muted)] uppercase tracking-wider">{label}</span>
      <span className={`text-sm font-medium ${config.color}`}>{config.label}</span>
    </div>
  );
}

function CategoryComparisonBar({ beforeCat, afterCat }: { beforeCat?: HealthCategoryScore; afterCat?: HealthCategoryScore }) {
  const beforeScore = beforeCat?.score ?? 0;
  const afterScore = afterCat?.score ?? 0;
  const label = beforeCat?.label ?? afterCat?.label ?? 'Unknown';
  const category = beforeCat?.category ?? afterCat?.category;
  const delta = afterScore - beforeScore;
  const explanation = category ? categoryExplanations[category] : label;

  const getBarColor = (score: number) =>
    score >= 80
      ? 'bg-[var(--neon-green)]'
      : score >= 50
        ? 'bg-[var(--neon-amber)]'
        : 'bg-[var(--neon-red)]';

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-[var(--text-secondary)] inline-flex items-center">
          {label}
          <CategoryTooltip text={explanation} />
        </span>
        <div className="flex items-center gap-2">
          <span className="text-[var(--text-muted)] font-mono">{Math.round(beforeScore)}</span>
          <span className="text-[var(--text-muted)]">→</span>
          <span className="text-[var(--text-primary)] font-mono">{Math.round(afterScore)}</span>
          {delta !== 0 && (
            <span className={`font-mono text-xs ${delta > 0 ? 'text-[var(--neon-green)]' : 'text-[var(--neon-red)]'}`}>
              {delta > 0 ? '+' : ''}{Math.round(delta)}
            </span>
          )}
        </div>
      </div>
      <div className="flex gap-1 h-2">
        <div className="w-28 bg-[var(--bg-glass)] rounded-full overflow-hidden">
          <motion.div
            className={`h-full ${getBarColor(beforeScore)} rounded-full opacity-50`}
            initial={{ width: 0 }}
            animate={{ width: `${beforeScore}%` }}
            transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
          />
        </div>
        <div className="w-28 bg-[var(--bg-glass)] rounded-full overflow-hidden">
          <motion.div
            className={`h-full ${getBarColor(afterScore)} rounded-full`}
            initial={{ width: 0 }}
            animate={{ width: `${afterScore}%` }}
            transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1], delay: 0.1 }}
          />
        </div>
      </div>
    </div>
  );
}

function IssueItem({ issue, type, defaultExpanded = false }: { issue: HealthIssue; type: 'new' | 'resolved' | 'persisting'; defaultExpanded?: boolean }) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  const typeConfig = {
    new: {
      bg: 'bg-[var(--status-critical-bg)]',
      border: 'border-[var(--status-critical-border)]',
      text: 'text-[var(--neon-red)]',
      label: 'New',
    },
    resolved: {
      bg: 'bg-[var(--status-success-bg)]',
      border: 'border-[var(--status-success-border)]',
      text: 'text-[var(--neon-green)]',
      label: 'Resolved',
    },
    persisting: {
      bg: 'bg-[var(--bg-glass)]',
      border: 'border-[var(--border-glass)]',
      text: 'text-[var(--text-muted)]',
      label: 'Persisting',
    },
  };

  const config = typeConfig[type];

  return (
    <div className={`${config.bg} border ${config.border} rounded-lg overflow-hidden`}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-3 py-2 flex items-center gap-2 text-left hover:bg-[var(--bg-glass-hover)] transition-colors"
      >
        <span className={`text-xs px-1.5 py-0.5 rounded ${config.bg} ${config.text} border ${config.border}`}>
          {config.label}
        </span>
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
              <p className="text-sm text-[var(--text-secondary)]">{issue.description}</p>
              {issue.recommendation && (
                <p className="text-sm mt-2">
                  <span className="text-[var(--text-muted)]">Recommendation: </span>
                  <span className="text-[var(--text-primary)]">{issue.recommendation}</span>
                </p>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function ComparisonHealthSection({ beforeHealth, afterHealth, healthChange, dynamicTitle }: ComparisonHealthSectionProps) {
  const [showAllIssues, setShowAllIssues] = useState(false);

  if (!beforeHealth || !afterHealth) {
    return null;
  }

  const { newIssues, resolvedIssues, persistingIssues } = compareHealthIssues(
    beforeHealth.issues,
    afterHealth.issues
  );

  const changeDirection = healthChange > 0 ? 'improvement' : healthChange < 0 ? 'regression' : 'neutral';
  const changeColor =
    changeDirection === 'improvement'
      ? 'text-[var(--neon-green)]'
      : changeDirection === 'regression'
        ? 'text-[var(--neon-red)]'
        : 'text-[var(--text-muted)]';

  // Build category map for comparison
  const beforeCats = new Map(beforeHealth.categories.map((c) => [c.category, c]));
  const afterCats = new Map(afterHealth.categories.map((c) => [c.category, c]));
  const allCategories = new Set([...beforeCats.keys(), ...afterCats.keys()]);

  const allIssues = [
    ...newIssues.map((i) => ({ issue: i, type: 'new' as const })),
    ...resolvedIssues.map((i) => ({ issue: i, type: 'resolved' as const })),
    ...persistingIssues.map((i) => ({ issue: i, type: 'persisting' as const })),
  ];

  const displayedIssues = showAllIssues ? allIssues : allIssues.slice(0, 5);
  const hasMoreIssues = allIssues.length > 5;

  return (
    <motion.div
      className="glass-card p-5 mb-5"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <h2 className="text-xl font-semibold text-neon-subtle">Health Score</h2>
          {dynamicTitle && (
            <span className={`text-sm font-medium px-2 py-0.5 rounded ${
              healthChange > 0 ? 'bg-[var(--status-success-bg)] text-[var(--neon-green)]' :
              healthChange < 0 ? 'bg-[var(--status-critical-bg)] text-[var(--neon-red)]' :
              'bg-[var(--bg-glass)] text-[var(--text-muted)]'
            }`}>
              {dynamicTitle}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {newIssues.length > 0 && (
            <span className="badge badge-critical">{newIssues.length} new issue{newIssues.length !== 1 ? 's' : ''}</span>
          )}
          {resolvedIssues.length > 0 && (
            <span className="badge badge-success">{resolvedIssues.length} resolved</span>
          )}
          {healthChange !== 0 && (
            <span className={`text-lg font-bold font-mono ${changeColor}`}>
              {healthChange > 0 ? '+' : ''}{healthChange}
            </span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Score Gauges - Side by Side */}
        <div className="flex items-center justify-center gap-8">
          <ScoreGauge score={beforeHealth.overall} status={beforeHealth.status} label="Before" />
          <div className="flex flex-col items-center">
            <svg className="w-8 h-8 text-[var(--text-muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14 5l7 7m0 0l-7 7m7-7H3" />
            </svg>
            {healthChange !== 0 && (
              <span className={`text-xl font-bold font-mono mt-1 ${changeColor}`}>
                {healthChange > 0 ? '+' : ''}{healthChange}
              </span>
            )}
          </div>
          <ScoreGauge score={afterHealth.overall} status={afterHealth.status} label="After" />
        </div>

        {/* Category Comparison */}
        <div className="flex flex-col justify-center space-y-3">
          <div className="flex items-center justify-between mb-1">
            <h3 className="text-sm font-medium text-[var(--text-muted)] uppercase tracking-wider">
              Category Scores
            </h3>
            <div className="flex gap-2 text-xs text-[var(--text-muted)]">
              <span className="flex items-center gap-1">
                <span className="w-3 h-1.5 bg-[var(--neon-cyan)] rounded opacity-50" />
                Before
              </span>
              <span className="flex items-center gap-1">
                <span className="w-3 h-1.5 bg-[var(--neon-cyan)] rounded" />
                After
              </span>
            </div>
          </div>
          {Array.from(allCategories).map((cat) => (
            <CategoryComparisonBar
              key={cat}
              beforeCat={beforeCats.get(cat)}
              afterCat={afterCats.get(cat)}
            />
          ))}
        </div>

        {/* Issues Diff */}
        <div className="flex flex-col">
          <h3 className="text-sm font-medium text-[var(--text-muted)] uppercase tracking-wider mb-3">
            Issues Changes
          </h3>
          {allIssues.length > 0 ? (
            <>
              {/* Issue summary line */}
              <div className="flex flex-wrap items-center gap-2 mb-2 text-xs">
                {newIssues.length > 0 && (
                  <span className="text-[var(--neon-red)]">{newIssues.length} new</span>
                )}
                {newIssues.length > 0 && (resolvedIssues.length > 0 || persistingIssues.length > 0) && (
                  <span className="text-[var(--text-muted)]">•</span>
                )}
                {resolvedIssues.length > 0 && (
                  <span className="text-[var(--neon-green)]">{resolvedIssues.length} resolved</span>
                )}
                {resolvedIssues.length > 0 && persistingIssues.length > 0 && (
                  <span className="text-[var(--text-muted)]">•</span>
                )}
                {persistingIssues.length > 0 && (
                  <span className="text-[var(--text-muted)]">{persistingIssues.length} persisting</span>
                )}
              </div>
              <div className="space-y-2 max-h-[280px] overflow-y-auto pr-1">
                {displayedIssues.map(({ issue, type }) => (
                  <IssueItem key={`${type}-${issue.id}`} issue={issue} type={type} defaultExpanded={type === 'new'} />
                ))}
              </div>
            </>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center py-6">
              <div className="w-12 h-12 rounded-full bg-[var(--status-info-bg)] flex items-center justify-center mb-2">
                <svg className="w-6 h-6 text-[var(--neon-cyan)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <span className="text-sm text-[var(--text-muted)]">No issue changes</span>
            </div>
          )}
          {hasMoreIssues && (
            <button
              onClick={() => setShowAllIssues(!showAllIssues)}
              className="mt-2 text-sm text-[var(--neon-cyan)] hover:text-[var(--neon-cyan-dim)] transition-colors flex items-center gap-1 justify-center"
            >
              {showAllIssues ? (
                <>
                  Show less
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
                  </svg>
                </>
              ) : (
                <>
                  Show {allIssues.length - 5} more
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                </>
              )}
            </button>
          )}
        </div>
      </div>
    </motion.div>
  );
}
