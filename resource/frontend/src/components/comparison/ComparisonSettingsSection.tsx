import { motion } from 'framer-motion';
import { useState } from 'react';
import type { ParsedData, FieldDelta, ChangeType } from '../../types';
import { getSettingsComparison } from '../../utils/compareData';
import { DeltaBadge } from './DeltaBadge';
import { formatKey } from '../../utils/formatters';
import { ComparisonMemoryAnalysisCard } from './ComparisonMemoryAnalysisCard';

interface ComparisonSettingsSectionProps {
  beforeData: ParsedData;
  afterData: ParsedData;
}

interface SettingsTableProps {
  title: string;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  deltas: FieldDelta[];
  id: string;
  isSensitive?: boolean;
}

function formatValue(value: unknown): string {
  if (value === undefined || value === null) return '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'object') {
    if ('value' in (value as Record<string, unknown>)) {
      return String((value as { value: unknown }).value);
    }
    return JSON.stringify(value);
  }
  return String(value);
}

function getRowChangeType(key: string, before: Record<string, unknown>, after: Record<string, unknown>): ChangeType {
  const beforeVal = before[key];
  const afterVal = after[key];

  if (beforeVal === undefined && afterVal !== undefined) return 'added';
  if (beforeVal !== undefined && afterVal === undefined) return 'removed';
  if (JSON.stringify(beforeVal) !== JSON.stringify(afterVal)) return 'modified';
  return 'unchanged';
}

type FilterType = 'all' | 'added' | 'removed' | 'modified';

function SettingsTable({ title, before, after, deltas, id, isSensitive = false }: SettingsTableProps) {
  const hasChanges = deltas.length > 0;
  const [expanded, setExpanded] = useState(true); // Always expanded
  const [showUnchanged, setShowUnchanged] = useState(!hasChanges); // Show unchanged when no changes
  const [filter, setFilter] = useState<FilterType>('all');

  const allKeys = new Set([...Object.keys(before), ...Object.keys(after)]);
  const sortedKeys = Array.from(allKeys).sort((a, b) => {
    const aType = getRowChangeType(a, before, after);
    const bType = getRowChangeType(b, before, after);
    const order = { added: 0, removed: 1, modified: 2, unchanged: 3 };
    return order[aType] - order[bType];
  });

  const changedKeys = sortedKeys.filter((k) => getRowChangeType(k, before, after) !== 'unchanged');
  const unchangedKeys = sortedKeys.filter((k) => getRowChangeType(k, before, after) === 'unchanged');

  // Apply filter
  const filteredChangedKeys = filter === 'all'
    ? changedKeys
    : changedKeys.filter((k) => getRowChangeType(k, before, after) === filter);

  const displayKeys = showUnchanged ? [...filteredChangedKeys, ...unchangedKeys] : filteredChangedKeys;

  // Count by type for filter badges
  const addedCount = changedKeys.filter((k) => getRowChangeType(k, before, after) === 'added').length;
  const removedCount = changedKeys.filter((k) => getRowChangeType(k, before, after) === 'removed').length;
  const modifiedCount = changedKeys.filter((k) => getRowChangeType(k, before, after) === 'modified').length;

  if (allKeys.size === 0) return null;

  return (
    <motion.div
      className="chart-container"
      id={id}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className="chart-header w-full flex items-center justify-between cursor-pointer hover:bg-[var(--bg-glass-hover)] transition-colors rounded-t-lg"
      >
        <div className="flex items-center gap-2">
          <h4>{title}</h4>
          {isSensitive && (
            <span className="text-[var(--neon-amber)]" title="Security-related settings">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
              </svg>
            </span>
          )}
          {deltas.length > 0 && (
            <span className="badge badge-warning">{deltas.length} change{deltas.length !== 1 ? 's' : ''}</span>
          )}
        </div>
        <motion.svg
          className="w-5 h-5 text-[var(--text-muted)]"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          animate={{ rotate: expanded ? 0 : -90 }}
          transition={{ duration: 0.2 }}
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </motion.svg>
      </button>

      {expanded && (
        <div className="max-h-[400px] overflow-y-auto">
          {/* Filter buttons */}
          {changedKeys.length > 0 && (
            <div className="flex items-center gap-1 px-3 py-2 border-b border-[var(--border-glass)]">
              <button
                onClick={() => setFilter('all')}
                className={`px-2 py-0.5 text-xs rounded transition-colors ${
                  filter === 'all'
                    ? 'bg-[var(--neon-cyan)]/20 text-[var(--neon-cyan)]'
                    : 'text-[var(--text-muted)] hover:bg-[var(--bg-glass)]'
                }`}
              >
                All ({changedKeys.length})
              </button>
              {addedCount > 0 && (
                <button
                  onClick={() => setFilter('added')}
                  className={`px-2 py-0.5 text-xs rounded transition-colors ${
                    filter === 'added'
                      ? 'bg-[var(--neon-green)]/20 text-[var(--neon-green)]'
                      : 'text-[var(--text-muted)] hover:bg-[var(--bg-glass)]'
                  }`}
                >
                  Added ({addedCount})
                </button>
              )}
              {removedCount > 0 && (
                <button
                  onClick={() => setFilter('removed')}
                  className={`px-2 py-0.5 text-xs rounded transition-colors ${
                    filter === 'removed'
                      ? 'bg-[var(--neon-red)]/20 text-[var(--neon-red)]'
                      : 'text-[var(--text-muted)] hover:bg-[var(--bg-glass)]'
                  }`}
                >
                  Removed ({removedCount})
                </button>
              )}
              {modifiedCount > 0 && (
                <button
                  onClick={() => setFilter('modified')}
                  className={`px-2 py-0.5 text-xs rounded transition-colors ${
                    filter === 'modified'
                      ? 'bg-[var(--neon-amber)]/20 text-[var(--neon-amber)]'
                      : 'text-[var(--text-muted)] hover:bg-[var(--bg-glass)]'
                  }`}
                >
                  Modified ({modifiedCount})
                </button>
              )}
            </div>
          )}
          <table className="table-dark w-full">
            <thead>
              <tr>
                <th className="w-[30%]">Setting</th>
                <th className="w-[30%]">Before</th>
                <th className="w-[30%]">After</th>
                <th className="w-[10%]">Status</th>
              </tr>
            </thead>
            <tbody>
              {displayKeys.length === 0 ? (
                <tr>
                  <td colSpan={4} className="text-center text-[var(--text-muted)] py-4">
                    No changes detected
                  </td>
                </tr>
              ) : (
                displayKeys.map((key) => {
                  const changeType = getRowChangeType(key, before, after);
                  const beforeVal = formatValue(before[key]);
                  const afterVal = formatValue(after[key]);

                  const rowClass =
                    changeType === 'added'
                      ? 'bg-[var(--status-success-bg)]/30'
                      : changeType === 'removed'
                        ? 'bg-[var(--status-critical-bg)]/30'
                        : changeType === 'modified'
                          ? 'bg-[var(--status-warning-bg)]/30'
                          : '';

                  return (
                    <tr key={key} className={`${rowClass} hover:bg-[var(--bg-glass)] transition-colors duration-100`}>
                      <td className="font-medium text-[var(--text-primary)]">
                        {id === 'usersByProjects-table' ? key : formatKey(key)}
                      </td>
                      <td className={changeType === 'removed' ? 'text-[var(--neon-red)]' : 'text-[var(--text-muted)]'}>
                        <span className={`font-mono ${changeType === 'removed' ? 'line-through' : ''}`}>
                          {beforeVal}
                        </span>
                      </td>
                      <td className={changeType === 'added' ? 'text-[var(--neon-green)]' : changeType === 'modified' ? 'text-[var(--text-primary)] font-semibold' : 'text-[var(--text-secondary)]'}>
                        <span className="font-mono">{afterVal}</span>
                      </td>
                      <td>
                        {changeType !== 'unchanged' && <DeltaBadge changeType={changeType} />}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>

          {unchangedKeys.length > 0 && (
            <button
              onClick={() => setShowUnchanged(!showUnchanged)}
              className="w-full py-2 text-sm text-[var(--neon-cyan)] hover:text-[var(--neon-cyan-dim)] transition-colors flex items-center justify-center gap-1 border-t border-[var(--border-glass)]"
            >
              {showUnchanged ? (
                <>
                  Hide {unchangedKeys.length} unchanged
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
                  </svg>
                </>
              ) : (
                <>
                  Show {unchangedKeys.length} unchanged
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                </>
              )}
            </button>
          )}
        </div>
      )}
    </motion.div>
  );
}

export function ComparisonSettingsSection({ beforeData, afterData }: ComparisonSettingsSectionProps) {
  const settingsComparison = getSettingsComparison(beforeData, afterData);

  const totalChanges = Object.values(settingsComparison).reduce((sum, s) => sum + s.deltas.length, 0);
  const settingsWithChanges = Object.entries(settingsComparison).filter(([, v]) => v.deltas.length > 0);

  const settingsLabels: Record<string, string> = {
    enabledSettings: 'Enabled Settings',
    sparkSettings: 'Spark Settings',
    authSettings: 'Authentication Settings',
    containerSettings: 'Container Settings',
    integrationSettings: 'Integration Settings',
    resourceLimits: 'Resource Limits',
    cgroupSettings: 'CGroups Configuration',
    proxySettings: 'Proxy Configuration',
    maxRunningActivities: 'Max Running Activities',
    javaMemorySettings: 'Java Memory Settings',
    javaMemoryLimits: 'Java Memory Limits',
  };

  // Check if memory analysis can be shown (needs cgroup limit on at least one side)
  const beforeCgroupLimit = parseInt(String(beforeData.cgroupSettings?.['Memory Limit'] || '0').replace(/[^0-9]/g, '')) || 0;
  const afterCgroupLimit = parseInt(String(afterData.cgroupSettings?.['Memory Limit'] || '0').replace(/[^0-9]/g, '')) || 0;
  const hasMemoryAnalysis = beforeCgroupLimit > 0 || afterCgroupLimit > 0;

  if (Object.keys(settingsComparison).length === 0 && !hasMemoryAnalysis) {
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
        <h2 className="text-xl font-semibold text-neon-subtle">Configuration Comparison</h2>
        <div className="flex items-center gap-2">
          {totalChanges > 0 ? (
            <span className="badge badge-warning">{totalChanges} total change{totalChanges !== 1 ? 's' : ''}</span>
          ) : (
            <span className="badge badge-success">No changes</span>
          )}
          {settingsWithChanges.length > 0 && (
            <span className="text-sm text-[var(--text-muted)]">
              in {settingsWithChanges.length} section{settingsWithChanges.length !== 1 ? 's' : ''}
            </span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <ComparisonMemoryAnalysisCard beforeData={beforeData} afterData={afterData} />
        {Object.entries(settingsComparison).map(([key, { before, after, deltas }]) => {
          // Mark security-related settings as sensitive
          const sensitiveKeys = ['authSettings', 'proxySettings', 'containerSettings'];
          const isSensitive = sensitiveKeys.includes(key);

          return (
            <SettingsTable
              key={key}
              id={`${key}-comparison`}
              title={settingsLabels[key] || key}
              before={before}
              after={after}
              deltas={deltas}
              isSensitive={isSensitive}
            />
          );
        })}
      </div>
    </motion.div>
  );
}
