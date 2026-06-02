import { motion } from 'framer-motion';
import { useMemo, useSyncExternalStore } from 'react';
import { getProcessMetrics, subscribeProcessMetrics } from '../state/processMetrics';
import { formatKb } from '../utils/formatters';
import { aggregateByUser, displayUser } from '../utils/processUsage';

/**
 * Ranked horizontal-bar card grouping the shared process snapshot by Linux
 * user. Reads the same `processMetrics` store as `ProcessMetricsTable` (no
 * extra fetch); clicking a bar drills the table below to that user.
 *
 *   - memory: bar = Σ RSS per user, label = formatted RSS + % of host RAM
 *   - cpu:    bar = Σ CPU% per user (may exceed 100 across cores)
 *
 * Selection is owned by the page (single source of truth), so the highlighted
 * bar and the table's clearable chip stay in sync.
 */
export function ProcessUsageByUser({
  variant,
  selectedUser,
  onSelectUser,
}: {
  variant: 'memory' | 'cpu';
  selectedUser: string | null;
  onSelectUser: (user: string | null) => void;
}) {
  const scan = useSyncExternalStore(subscribeProcessMetrics, getProcessMetrics, getProcessMetrics);

  const rows = useMemo(
    () => aggregateByUser(scan.processes, variant === 'cpu' ? 'cpuPercent' : 'rssKb'),
    [scan.processes, variant],
  );

  // Loading/empty/error states are surfaced by the table below; the bars just
  // sit out until there's data to rank.
  if (rows.length === 0) return null;

  const maxValue = rows[0].value || 1;
  const barColor = variant === 'cpu' ? 'var(--neon-amber)' : 'var(--neon-cyan)';
  const title = variant === 'cpu' ? 'CPU by user' : 'Memory by user';

  return (
    <motion.div
      className="chart-container"
      id={variant === 'cpu' ? 'cpu-by-user' : 'memory-by-user'}
      initial={{ opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '-50px' }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="chart-header">
        <div className="flex items-center gap-2">
          <h4>{title}</h4>
          <span className="badge badge-info font-mono">{rows.length} users</span>
        </div>
      </div>

      <div className="flex flex-col gap-0.5 px-4 py-3 max-h-[22rem] overflow-y-auto">
        {rows.map((row) => {
          const selected = row.user === selectedUser;
          const valueLabel =
            variant === 'cpu'
              ? `${row.value.toFixed(1)}%`
              : `${formatKb(row.value)} · ${row.share.toFixed(0)}%`;
          return (
            <button
              key={row.user}
              type="button"
              onClick={() => onSelectUser(selected ? null : row.user)}
              title={`${row.user} — ${row.count} process${row.count === 1 ? '' : 'es'}`}
              className={`grid grid-cols-[7rem_minmax(0,1fr)_auto] items-center gap-3 rounded px-2 py-1 text-left text-sm transition-colors hover:bg-[var(--bg-glass)] ${
                selected ? 'bg-[var(--bg-glass)]' : ''
              }`}
            >
              <span className="truncate text-[var(--text-secondary)]">{displayUser(row.user)}</span>
              <span className="h-2 overflow-hidden rounded-full bg-[var(--border-glass)]">
                <motion.span
                  className="block h-full rounded-full"
                  style={{ backgroundColor: barColor }}
                  initial={{ width: 0 }}
                  animate={{ width: `${Math.min((row.value / maxValue) * 100, 100)}%` }}
                  transition={{ duration: 0.3, ease: 'easeOut' }}
                />
              </span>
              <span className="tabular-nums font-mono text-[var(--text-muted)] whitespace-nowrap">
                {valueLabel}
              </span>
            </button>
          );
        })}
      </div>
    </motion.div>
  );
}
