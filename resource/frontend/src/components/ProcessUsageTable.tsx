import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { DataGrid } from './common/DataGrid';
import { RefreshControl } from './common/RefreshControl';
import {
  getProcessMetrics,
  restartProcessMetricsScan,
  startProcessMetricsScan,
  subscribeProcessMetrics,
} from '../state/processMetrics';
import type { ColumnDef } from '../utils/dataGridTypes';
import { formatKb } from '../utils/formatters';
import { aggregateByUser, displayCommand, displayUser, webappRefFromCommand } from '../utils/processUsage';
import { dssUrls } from '../utils/codeEnvUsageLinks';
import type { Lifecycle, ProcessMetric } from '../types';

/** Per-user aggregate row: both metrics joined so every column is sortable. */
interface UserRow {
  user: string;
  count: number;
  cpuPercent: number;
  memPercent: number;
  rssKb: number;
}

/**
 * Merged "usage by user" table with expandable per-PID child rows. Replaces
 * the ProcessUsageByUser bar card + ProcessMetricsTable pair: top-level rows
 * are per-user aggregates (default collapsed); the chevron on a user expands
 * that user's PIDs, and the header "Expand all" toggle shows every PID.
 * `variant` only changes the title and the default sort column. Data comes
 * from the shared processMetrics store (one `/api/host/process-metrics`
 * fetch shared by the Memory and CPU pages).
 */
export function ProcessUsageTable({ variant }: { variant: 'memory' | 'cpu' }) {
  const scan = useSyncExternalStore(subscribeProcessMetrics, getProcessMetrics, getProcessMetrics);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());

  useEffect(() => {
    startProcessMetricsScan();
  }, []);

  const userRows = useMemo<UserRow[]>(() => {
    const byRss = aggregateByUser(scan.processes, 'rssKb');
    const cpuByUser = new Map(
      aggregateByUser(scan.processes, 'cpuPercent').map((r) => [r.user, r.value]),
    );
    return byRss.map((r) => ({
      user: r.user,
      count: r.count,
      cpuPercent: cpuByUser.get(r.user) ?? 0,
      memPercent: r.share,
      rssKb: r.value,
    }));
  }, [scan.processes]);

  const processesByUser = useMemo(() => {
    const metric = variant === 'cpu' ? 'cpuPercent' : 'rssKb';
    const map = new Map<string, ProcessMetric[]>();
    for (const p of scan.processes) {
      const list = map.get(p.user) || [];
      list.push(p);
      map.set(p.user, list);
    }
    for (const list of map.values()) list.sort((a, b) => b[metric] - a[metric]);
    return map;
  }, [scan.processes, variant]);

  const allExpanded = userRows.length > 0 && expanded.size >= userRows.length;
  const toggleUser = (user: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(user)) next.delete(user);
      else next.add(user);
      return next;
    });
  const toggleAll = () =>
    setExpanded(allExpanded ? new Set() : new Set(userRows.map((r) => r.user)));

  const columns = useMemo<ColumnDef<UserRow>[]>(
    () => [
      {
        id: 'user',
        label: 'User',
        defaultSortDir: 'asc',
        render: (r) => {
          const isOpen = expanded.has(r.user);
          return (
            <button
              type="button"
              onClick={() => toggleUser(r.user)}
              title={`${r.user} — ${isOpen ? 'collapse' : 'expand'} ${r.count} process${r.count === 1 ? '' : 'es'}`}
              className="flex items-center gap-1.5 text-left text-[var(--text-primary)] hover:text-[var(--neon-cyan)]"
              aria-expanded={isOpen}
            >
              <span
                aria-hidden
                className={`inline-block text-[10px] text-[var(--text-muted)] transition-transform duration-150 ${isOpen ? 'rotate-90' : ''}`}
              >
                ▶
              </span>
              {displayUser(r.user)}
            </button>
          );
        },
        sortValue: (r) => displayUser(r.user).toLowerCase(),
      },
      {
        id: 'count',
        label: 'Processes',
        align: 'right',
        mono: true,
        render: (r) => r.count,
        sortValue: (r) => r.count,
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
    ],
    [expanded],
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

  const title = variant === 'cpu' ? 'CPU usage' : 'Memory usage';

  const headerExtra = (
    <div className="flex items-center justify-between gap-3 border-b border-[var(--border-glass)] px-4 py-2 text-xs text-[var(--text-muted)]">
      <span>
        {scan.truncated && scan.totalProcesses
          ? `Showing top ${scan.processes.length} of ${scan.totalProcesses} processes by ${variant === 'cpu' ? 'CPU' : 'memory'}.`
          : 'Live snapshot from the host process table (ps), grouped by user.'}
      </span>
      <span className="flex items-center gap-2">
        <button
          onClick={toggleAll}
          disabled={userRows.length === 0}
          className="rounded px-2 py-1 text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)] disabled:opacity-50"
        >
          {allExpanded ? 'Collapse all' : 'Expand all'}
        </button>
        <RefreshControl
          busy={scan.status === 'loading'}
          fetchedAt={scan.status === 'done' ? scan.finishedAt : null}
          onRefresh={restartProcessMetricsScan}
        />
      </span>
    </div>
  );

  return (
    <DataGrid<UserRow, ProcessMetric>
      id={variant === 'cpu' ? 'cpu-usage-table' : 'memory-usage-table'}
      title={title}
      rows={userRows}
      columns={columns}
      rowKey={(r) => r.user}
      getRowChildren={(r) => processesByUser.get(r.user) ?? []}
      expandedRowKeys={expanded}
      childRowKey={(p) => `pid-${p.pid}`}
      childRowClassName="bg-[var(--bg-glass)]"
      renderChildRow={(p) => [
        <span key="pid" className="block pl-6 font-mono tabular-nums text-[var(--text-secondary)]">
          {p.pid}
        </span>,
        ((cleaned, ref) => (
          <span
            key="cmd"
            title={p.command}
            className="block max-w-[32rem] truncate text-left font-sans text-[var(--text-secondary)]"
          >
            {ref ? (
              <a
                href={dssUrls.webapp(ref.projectKey, ref.webappId)}
                target="_blank"
                rel="noreferrer"
                className="hover:text-[var(--neon-cyan)] hover:underline"
              >
                {cleaned}
              </a>
            ) : (
              cleaned
            )}
          </span>
        ))(displayCommand(p.command, scan.dipHome), webappRefFromCommand(p.command)),
        `${p.cpuPercent.toFixed(1)}%`,
        `${p.memPercent.toFixed(1)}%`,
        formatKb(p.rssKb),
      ]}
      defaultSortColumnId={variant === 'cpu' ? 'cpu' : 'rss'}
      defaultSortDir="desc"
      lifecycle={gridLifecycle}
      headerExtra={headerExtra}
      countBadge={{ total: userRows.length }}
      emptyMessage={
        scan.status === 'error'
          ? `Could not load process metrics: ${scan.error ?? 'unknown error'}`
          : 'No process data yet.'
      }
      scroll="card"
    />
  );
}
