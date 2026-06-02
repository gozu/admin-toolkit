import { useEffect, useMemo, useSyncExternalStore } from 'react';
import { DataGrid } from './common/DataGrid';
import {
  getProcessMetrics,
  restartProcessMetricsScan,
  startProcessMetricsScan,
  subscribeProcessMetrics,
} from '../state/processMetrics';
import type { ColumnDef } from '../utils/dataGridTypes';
import { formatKb } from '../utils/formatters';
import { displayUser } from '../utils/processUsage';
import type { Lifecycle, ProcessMetric } from '../types';

const COLUMNS: ColumnDef<ProcessMetric>[] = [
  {
    id: 'pid',
    label: 'PID',
    align: 'right',
    mono: true,
    width: '6rem',
    render: (r) => r.pid,
    sortValue: (r) => r.pid,
  },
  {
    id: 'command',
    label: 'Command',
    render: (r) => (
      <span title={r.command} className="block max-w-[32rem] truncate">
        {r.command}
      </span>
    ),
    sortValue: (r) => r.command.toLowerCase(),
  },
  {
    id: 'user',
    label: 'User',
    render: (r) => <span title={r.user}>{displayUser(r.user)}</span>,
    sortValue: (r) => displayUser(r.user).toLowerCase(),
  },
  {
    id: 'cpu',
    label: 'CPU %',
    align: 'right',
    mono: true,
    render: (r) => `${r.cpuPercent.toFixed(1)}%`,
    sortValue: (r) => r.cpuPercent,
  },
  {
    id: 'mem',
    label: 'Mem %',
    align: 'right',
    mono: true,
    render: (r) => `${r.memPercent.toFixed(1)}%`,
    sortValue: (r) => r.memPercent,
  },
  {
    id: 'rss',
    label: 'RSS',
    align: 'right',
    mono: true,
    render: (r) => formatKb(r.rssKb),
    sortValue: (r) => r.rssKb,
  },
];

/**
 * Per-PID CPU/memory table. `variant` only changes the title + default sort
 * column; both views show the full set of metric columns. Data comes from the
 * shared processMetrics store (one `/api/host/process-metrics` fetch shared by
 * the Memory page card and the CPU sub-page).
 *
 * `filterUser`/`onClearFilter` (optional) let a parent drill into a single
 * Linux user — e.g. clicking a bar in `ProcessUsageByUser`. When set, the
 * table shows only that user's PIDs plus a clearable chip in the header.
 */
export function ProcessMetricsTable({
  variant,
  filterUser = null,
  onClearFilter,
}: {
  variant: 'memory' | 'cpu';
  filterUser?: string | null;
  onClearFilter?: () => void;
}) {
  const scan = useSyncExternalStore(subscribeProcessMetrics, getProcessMetrics, getProcessMetrics);

  useEffect(() => {
    startProcessMetricsScan();
  }, []);

  const rows = useMemo(
    () => (filterUser ? scan.processes.filter((p) => p.user === filterUser) : scan.processes),
    [scan.processes, filterUser],
  );

  const gridLifecycle = useMemo<Lifecycle | null>(() => {
    const startedAt = scan.startedAt ?? '1970-01-01T00:00:00.000Z';
    if (scan.status === 'loading') {
      return { phase: 'running', startedAt, progressPct: 0, message: 'Reading process table', updatedAt: startedAt };
    }
    if (scan.status === 'error') {
      return {
        phase: 'error',
        startedAt,
        finishedAt: scan.finishedAt ?? startedAt,
        error: scan.error ?? 'Process metrics failed',
        progressPct: 0,
      };
    }
    return null;
  }, [scan.status, scan.startedAt, scan.finishedAt, scan.error]);

  const title = variant === 'cpu' ? 'CPU usage by PID' : 'Memory usage by PID';

  const headerExtra = (
    <div className="flex items-center justify-between gap-3 border-b border-[var(--border-glass)] px-4 py-2 text-xs text-[var(--text-muted)]">
      <span className="flex items-center gap-2">
        {scan.truncated && scan.totalProcesses
          ? `Showing top ${scan.processes.length} of ${scan.totalProcesses} processes by ${variant === 'cpu' ? 'CPU' : 'memory'}.`
          : 'Live snapshot from the host process table (ps).'}
        {filterUser && (
          <button
            onClick={onClearFilter}
            title={`Clear filter (${filterUser})`}
            className="inline-flex items-center gap-1 rounded-full bg-[var(--bg-glass)] px-2 py-0.5 text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)]"
          >
            filtered: {displayUser(filterUser)}
            <span aria-hidden>✕</span>
          </button>
        )}
      </span>
      <button
        onClick={restartProcessMetricsScan}
        disabled={scan.status === 'loading'}
        className="rounded px-2 py-1 text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)] disabled:opacity-50"
      >
        {scan.status === 'loading' ? 'Refreshing…' : 'Refresh'}
      </button>
    </div>
  );

  return (
    <DataGrid<ProcessMetric>
      id={variant === 'cpu' ? 'cpu-by-pid-table' : 'memory-by-pid-table'}
      title={title}
      rows={rows}
      columns={COLUMNS}
      rowKey={(r) => String(r.pid)}
      defaultSortColumnId={variant === 'cpu' ? 'cpu' : 'rss'}
      defaultSortDir="desc"
      lifecycle={gridLifecycle}
      headerExtra={headerExtra}
      countBadge={{ total: scan.totalProcesses ?? scan.processes.length, filtered: rows.length }}
      emptyMessage={
        scan.status === 'error'
          ? `Could not load process metrics: ${scan.error ?? 'unknown error'}`
          : 'No process data yet.'
      }
      scroll={{ maxH: '60vh' }}
    />
  );
}
