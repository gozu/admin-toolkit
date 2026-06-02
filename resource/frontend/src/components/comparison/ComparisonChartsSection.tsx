import { useState } from 'react';
import { motion } from 'framer-motion';
import type { ParsedData, FilesystemInfo, MemoryInfo, ConnectionCounts } from '../../types';

interface ComparisonChartsSectionProps {
  beforeData: ParsedData;
  afterData: ParsedData;
}

// Memory Chart Comparison
function MemoryComparisonChart({ before, after }: { before?: MemoryInfo; after?: MemoryInfo }) {
  if (!before && !after) return null;

  const parseMemory = (str: string | undefined): number => {
    if (!str) return 0;
    const value = parseFloat(str.replace(/[^\d.]/g, ''));
    if (str.includes('T')) return value * 1024;
    if (str.includes('G')) return value;
    if (str.includes('M')) return value / 1024;
    return value;
  };

  const beforeTotal = parseMemory(before?.total);
  const beforeUsed = parseMemory(before?.used);
  const beforePct = beforeTotal > 0 ? (beforeUsed / beforeTotal) * 100 : 0;

  const afterTotal = parseMemory(after?.total);
  const afterUsed = parseMemory(after?.used);
  const afterFree = parseMemory(after?.available || after?.free);
  const afterPct = afterTotal > 0 ? (afterUsed / afterTotal) * 100 : 0;

  const usedDelta = afterUsed - beforeUsed;
  const pctDelta = afterPct - beforePct;

  const getBarColor = (pct: number) =>
    pct >= 90 ? 'bg-[var(--neon-red)]' : pct >= 70 ? 'bg-[var(--neon-amber)]' : 'bg-[var(--neon-green)]';

  return (
    <motion.div
      className="chart-container"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="chart-header">
        <h4>Memory Usage</h4>
        {pctDelta !== 0 && (
          <span className={`text-sm font-mono ${pctDelta > 0 ? 'text-[var(--neon-red)]' : 'text-[var(--neon-green)]'}`}>
            {pctDelta > 0 ? '+' : ''}{pctDelta.toFixed(1)}%
          </span>
        )}
      </div>

      <div className="p-4 space-y-4">
        {/* Before */}
        <div>
          <div className="flex items-center justify-between text-sm mb-1">
            <span className="text-[var(--text-muted)]">Before</span>
            <span className="font-mono text-[var(--text-secondary)]">
              {before?.used || '—'} / {before?.total || '—'} ({beforePct.toFixed(1)}%)
            </span>
          </div>
          <div className="h-4 bg-[var(--bg-glass)] rounded-full overflow-hidden relative">
            <motion.div
              className={`h-full ${getBarColor(beforePct)} opacity-60`}
              initial={{ width: 0 }}
              animate={{ width: `${beforePct}%` }}
              transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
            />
            {/* Threshold indicators */}
            <div className="absolute top-0 bottom-0 w-px bg-[var(--neon-amber)] opacity-60" style={{ left: '70%' }} title="70% Warning" />
            <div className="absolute top-0 bottom-0 w-px bg-[var(--neon-red)] opacity-60" style={{ left: '90%' }} title="90% Critical" />
          </div>
        </div>

        {/* After */}
        <div>
          <div className="flex items-center justify-between text-sm mb-1">
            <span className="text-[var(--text-muted)]">After</span>
            <span className="font-mono text-[var(--text-primary)]">
              {after?.used || '—'} / {after?.total || '—'} ({afterPct.toFixed(1)}%)
            </span>
          </div>
          <div className="h-4 bg-[var(--bg-glass)] rounded-full overflow-hidden relative">
            <motion.div
              className={`h-full ${getBarColor(afterPct)}`}
              initial={{ width: 0 }}
              animate={{ width: `${afterPct}%` }}
              transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1], delay: 0.1 }}
            />
            {/* Threshold indicators */}
            <div className="absolute top-0 bottom-0 w-px bg-[var(--neon-amber)] opacity-60" style={{ left: '70%' }} title="70% Warning" />
            <div className="absolute top-0 bottom-0 w-px bg-[var(--neon-red)] opacity-60" style={{ left: '90%' }} title="90% Critical" />
          </div>
        </div>

        {/* Summary */}
        <div className="flex items-center justify-around pt-2 border-t border-[var(--border-glass)] text-sm">
          <div className="text-center">
            <div className="text-[var(--text-muted)]">Used Change</div>
            <div className={`font-mono font-semibold ${usedDelta > 0 ? 'text-[var(--neon-red)]' : usedDelta < 0 ? 'text-[var(--neon-green)]' : 'text-[var(--text-muted)]'}`}>
              {usedDelta > 0 ? '+' : ''}{usedDelta.toFixed(1)} GB
            </div>
          </div>
          <div className="text-center">
            <div className="text-[var(--text-muted)]">Free (After)</div>
            <div className="font-mono font-semibold text-[var(--text-primary)]">
              {afterFree.toFixed(1)} GB
            </div>
          </div>
        </div>
      </div>
    </motion.div>
  );
}

// Filesystem Chart Comparison
function FilesystemComparisonChart({
  before,
  after,
}: {
  before?: FilesystemInfo[];
  after?: FilesystemInfo[];
}) {
  if (!before?.length && !after?.length) return null;

  const parseUsePercent = (str: string): number => {
    return parseFloat(str.replace('%', '')) || 0;
  };

  // Get unique mount points and sort by largest delta first (smart sorting)
  const mountPoints = Array.from(new Set([
    ...(before?.map((f) => f['Mounted on']) || []),
    ...(after?.map((f) => f['Mounted on']) || []),
  ]));

  const allMountPoints = mountPoints.map(mount => {
    const beforeFs = before?.find((f) => f['Mounted on'] === mount);
    const afterFs = after?.find((f) => f['Mounted on'] === mount);
    const beforePct = parseUsePercent(beforeFs?.['Use%'] || '0');
    const afterPct = parseUsePercent(afterFs?.['Use%'] || '0');
    return { mount, delta: Math.abs(afterPct - beforePct), beforePct, afterPct };
  });
  const hasChanges = allMountPoints.some(({ delta }) => delta > 0);
  const sortedMountPoints = hasChanges
    ? allMountPoints.sort((a, b) => b.delta - a.delta).slice(0, 5)
    : allMountPoints.slice(0, 10); // Show more when unchanged

  const getBarColor = (pct: number) =>
    pct >= 90 ? 'bg-[var(--neon-red)]' : pct >= 70 ? 'bg-[var(--neon-amber)]' : 'bg-[var(--neon-green)]';

  return (
    <motion.div
      className="chart-container"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="chart-header">
        <h4>Filesystem Usage</h4>
      </div>

      <div className="p-4 space-y-4 max-h-[400px] overflow-y-auto">
        {sortedMountPoints.map(({ mount, beforePct, afterPct }) => {
          const pctDelta = afterPct - beforePct;

          return (
            <div key={mount}>
              <div className="flex items-center justify-between text-sm mb-1">
                <span className="text-[var(--text-primary)] font-medium truncate" title={mount}>
                  {mount.length > 20 ? `...${mount.slice(-20)}` : mount}
                </span>
                {pctDelta !== 0 && (
                  <span className={`font-mono text-xs ${pctDelta > 0 ? 'text-[var(--neon-red)]' : 'text-[var(--neon-green)]'}`}>
                    {pctDelta > 0 ? '+' : ''}{pctDelta.toFixed(0)}%
                  </span>
                )}
              </div>
              <div className="flex gap-1 h-3">
                {/* Before bar */}
                <div className="flex-1 bg-[var(--bg-glass)] rounded overflow-hidden relative">
                  <motion.div
                    className={`h-full ${getBarColor(beforePct)} opacity-50`}
                    initial={{ width: 0 }}
                    animate={{ width: `${beforePct}%` }}
                    transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
                  />
                  {/* Threshold indicators */}
                  <div className="absolute top-0 bottom-0 w-px bg-[var(--neon-amber)] opacity-40" style={{ left: '70%' }} />
                  <div className="absolute top-0 bottom-0 w-px bg-[var(--neon-red)] opacity-40" style={{ left: '90%' }} />
                  <span className="absolute right-1 top-0 text-[10px] font-mono text-[var(--text-muted)]">
                    {beforePct.toFixed(0)}%
                  </span>
                </div>
                {/* After bar */}
                <div className="flex-1 bg-[var(--bg-glass)] rounded overflow-hidden relative">
                  <motion.div
                    className={`h-full ${getBarColor(afterPct)}`}
                    initial={{ width: 0 }}
                    animate={{ width: `${afterPct}%` }}
                    transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1], delay: 0.1 }}
                  />
                  {/* Threshold indicators */}
                  <div className="absolute top-0 bottom-0 w-px bg-[var(--neon-amber)] opacity-40" style={{ left: '70%' }} />
                  <div className="absolute top-0 bottom-0 w-px bg-[var(--neon-red)] opacity-40" style={{ left: '90%' }} />
                  <span className="absolute right-1 top-0 text-[10px] font-mono text-[var(--text-primary)]">
                    {afterPct.toFixed(0)}%
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="px-4 pb-2 flex gap-4 text-xs text-[var(--text-muted)] border-t border-[var(--border-glass)] pt-2">
        <span className="flex items-center gap-1">
          <span className="w-3 h-1.5 bg-[var(--neon-cyan)] rounded opacity-50" />
          Before
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-1.5 bg-[var(--neon-cyan)] rounded" />
          After
        </span>
        <span className="flex items-center gap-1 ml-auto">
          <span className="w-px h-2 bg-[var(--neon-amber)]" />
          70%
        </span>
        <span className="flex items-center gap-1">
          <span className="w-px h-2 bg-[var(--neon-red)]" />
          90%
        </span>
      </div>
    </motion.div>
  );
}

// Connections Chart Comparison
function ConnectionsComparisonChart({
  before,
  after,
}: {
  before?: ConnectionCounts;
  after?: ConnectionCounts;
}) {
  // Sort keys by total count (before + after) descending
  const allKeys = Array.from(new Set([
    ...Object.keys(before || {}),
    ...Object.keys(after || {}),
  ])).sort((a, b) => {
    const totalA = (before?.[a] || 0) + (after?.[a] || 0);
    const totalB = (before?.[b] || 0) + (after?.[b] || 0);
    return totalB - totalA;
  });

  // Calculate hasChanges before useState
  const hasChanges = allKeys.some(key => (before?.[key] || 0) !== (after?.[key] || 0));
  const [expanded, setExpanded] = useState(!hasChanges); // Expand when no changes

  if (!before && !after) return null;
  if (allKeys.length === 0) return null;

  const visibleKeys = expanded ? allKeys : allKeys.slice(0, 10);
  const hiddenCount = allKeys.length - 10;

  const beforeTotal = Object.values(before || {}).reduce((sum, v) => sum + v, 0);
  const afterTotal = Object.values(after || {}).reduce((sum, v) => sum + v, 0);
  const totalDelta = afterTotal - beforeTotal;

  const maxValue = Math.max(
    ...Object.values(before || {}),
    ...Object.values(after || {}),
    1
  );

  const colors = [
    'bg-[var(--neon-cyan)]',
    'bg-[var(--neon-purple)]',
    'bg-[var(--neon-green)]',
    'bg-[var(--neon-amber)]',
    'bg-[var(--neon-red)]',
  ];

  return (
    <motion.div
      className="chart-container"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="chart-header">
        <h4>Connections</h4>
        <div className="flex items-center gap-2">
          <span className="text-sm text-[var(--text-muted)]">
            {beforeTotal} → {afterTotal}
          </span>
          {totalDelta !== 0 && (
            <span className={`text-sm font-mono ${totalDelta > 0 ? 'text-[var(--neon-green)]' : 'text-[var(--neon-red)]'}`}>
              ({totalDelta > 0 ? '+' : ''}{totalDelta})
            </span>
          )}
        </div>
      </div>

      <div className="p-4 space-y-3">
        {visibleKeys.map((key, idx) => {
          const beforeVal = before?.[key] || 0;
          const afterVal = after?.[key] || 0;
          const delta = afterVal - beforeVal;
          const color = colors[idx % colors.length];

          return (
            <div key={key}>
              <div className="flex items-center justify-between text-sm mb-1">
                <span className="text-[var(--text-primary)]">{key}</span>
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[var(--text-muted)]">{beforeVal}</span>
                  <span className="text-[var(--text-muted)]">→</span>
                  <span className="font-mono text-[var(--text-primary)]">{afterVal}</span>
                  {delta !== 0 && (
                    <span className={`font-mono text-xs ${delta > 0 ? 'text-[var(--neon-green)]' : 'text-[var(--neon-red)]'}`}>
                      {delta > 0 ? '+' : ''}{delta}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex gap-1 h-3">
                <div className="flex-1 bg-[var(--bg-glass)] rounded overflow-hidden">
                  <motion.div
                    className={`h-full ${color} opacity-50`}
                    initial={{ width: 0 }}
                    animate={{ width: `${(beforeVal / maxValue) * 100}%` }}
                    transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
                  />
                </div>
                <div className="flex-1 bg-[var(--bg-glass)] rounded overflow-hidden">
                  <motion.div
                    className={`h-full ${color}`}
                    initial={{ width: 0 }}
                    animate={{ width: `${(afterVal / maxValue) * 100}%` }}
                    transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1], delay: 0.1 }}
                  />
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {hiddenCount > 0 && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full py-1.5 text-xs font-medium text-[var(--neon-cyan)] hover:text-[var(--neon-cyan-bright)] transition-colors border-t border-[var(--border-glass)]"
        >
          {expanded ? 'Show less' : `Show ${hiddenCount} more`}
        </button>
      )}

      <div className="px-4 pb-2 flex gap-4 text-xs text-[var(--text-muted)] border-t border-[var(--border-glass)] pt-2">
        <span className="flex items-center gap-1">
          <span className="w-3 h-1.5 bg-[var(--neon-cyan)] rounded opacity-50" />
          Before
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-1.5 bg-[var(--neon-cyan)] rounded" />
          After
        </span>
      </div>
    </motion.div>
  );
}

// SVG Icons for Scale Overview
const UsersIconSvg = () => (
  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
  </svg>
);

const ProjectsIconSvg = () => (
  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
  </svg>
);

const ClustersIconSvg = () => (
  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z" />
  </svg>
);

const CodeEnvsIconSvg = () => (
  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
  </svg>
);

const PluginsIconSvg = () => (
  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 10V3L4 14h7v7l9-11h-7z" />
  </svg>
);

// Scale Stats Comparison
function ScaleComparisonCard({ beforeData, afterData }: { beforeData: ParsedData; afterData: ParsedData }) {
  const stats = [
    {
      label: 'Users',
      before: beforeData.users?.length ?? 0,
      after: afterData.users?.length ?? 0,
      icon: <UsersIconSvg />,
    },
    {
      label: 'Projects',
      before: beforeData.projects?.length ?? 0,
      after: afterData.projects?.length ?? 0,
      icon: <ProjectsIconSvg />,
    },
    {
      label: 'Clusters',
      before: beforeData.clusters?.length ?? 0,
      after: afterData.clusters?.length ?? 0,
      icon: <ClustersIconSvg />,
    },
    {
      label: 'Code Envs',
      before: beforeData.codeEnvs?.length ?? 0,
      after: afterData.codeEnvs?.length ?? 0,
      icon: <CodeEnvsIconSvg />,
    },
    {
      label: 'Plugins',
      before: beforeData.plugins?.length ?? beforeData.pluginsCount ?? 0,
      after: afterData.plugins?.length ?? afterData.pluginsCount ?? 0,
      icon: <PluginsIconSvg />,
    },
  ];

  // Only show if at least one stat exists
  if (stats.every((s) => s.before === 0 && s.after === 0)) return null;

  return (
    <motion.div
      className="chart-container"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="chart-header">
        <h4>Scale Overview</h4>
      </div>

      <div className="p-4 grid grid-cols-2 sm:grid-cols-3 gap-4">
        {stats.filter((s) => s.before > 0 || s.after > 0).map((stat) => {
          const delta = stat.after - stat.before;
          return (
            <div key={stat.label} className="text-center">
              <div className="flex justify-center mb-1 text-[var(--text-muted)]">{stat.icon}</div>
              <div className="text-xs text-[var(--text-muted)] mb-1">{stat.label}</div>
              <div className="flex items-center justify-center gap-2">
                <span className="font-mono text-[var(--text-muted)]">{stat.before}</span>
                <span className="text-[var(--text-muted)]">→</span>
                <span className="font-mono text-[var(--text-primary)] font-semibold">{stat.after}</span>
              </div>
              {delta !== 0 && (
                <span className={`text-xs font-mono ${delta > 0 ? 'text-[var(--neon-green)]' : 'text-[var(--neon-red)]'}`}>
                  {delta > 0 ? '+' : ''}{delta}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </motion.div>
  );
}

export function ComparisonChartsSection({ beforeData, afterData }: ComparisonChartsSectionProps) {
  const hasMemory = beforeData.memoryInfo || afterData.memoryInfo;
  const hasFilesystem = beforeData.filesystemInfo?.length || afterData.filesystemInfo?.length;
  const hasConnections =
    Object.keys(beforeData.connectionCounts || {}).length > 0 ||
    Object.keys(afterData.connectionCounts || {}).length > 0;
  const hasScale =
    (beforeData.users?.length || afterData.users?.length ||
     beforeData.projects?.length || afterData.projects?.length);

  if (!hasMemory && !hasFilesystem && !hasConnections && !hasScale) {
    return null;
  }

  return (
    <motion.div
      className="mb-5"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold text-neon-subtle">Resource Usage Comparison</h2>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {hasMemory && (
          <MemoryComparisonChart
            before={beforeData.memoryInfo}
            after={afterData.memoryInfo}
          />
        )}
        {hasFilesystem && (
          <FilesystemComparisonChart
            before={beforeData.filesystemInfo}
            after={afterData.filesystemInfo}
          />
        )}
        {hasConnections && (
          <ConnectionsComparisonChart
            before={beforeData.connectionCounts}
            after={afterData.connectionCounts}
          />
        )}
        {hasScale && (
          <ScaleComparisonCard beforeData={beforeData} afterData={afterData} />
        )}
      </div>
    </motion.div>
  );
}
