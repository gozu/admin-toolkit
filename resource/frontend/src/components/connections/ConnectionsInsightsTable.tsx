import { useEffect, useMemo, useState } from 'react';
import { useDiag } from '../../context/DiagContext';
import { ScanIncompleteNotice } from '../ScanIncompleteNotice';
import { DataGrid } from '../common/DataGrid';
import type { ColumnDef } from '../../utils/dataGridTypes';
import { dssUrls } from '../../utils/codeEnvUsageLinks';
import {
  buildConnectionInsightsRows,
  type ConnectionInsightsRow,
} from '../../utils/connectionInsights';

const SEVERITY_RANK: Record<'critical' | 'warning' | 'info', number> = {
  critical: 3,
  warning: 2,
  info: 1,
};

const HEALTH_RANK: Record<'ok' | 'fail' | 'skipped', number> = {
  fail: 3,
  skipped: 2,
  ok: 1,
};

function severityTextClass(sev: 'critical' | 'warning' | 'info' | null): string {
  if (sev === 'critical') return 'text-[var(--neon-red)]';
  if (sev === 'warning') return 'text-[var(--neon-yellow)]';
  return 'text-[var(--text-muted)]';
}

function healthTextClass(status: 'ok' | 'fail' | 'skipped' | null): string {
  if (status === 'ok') return 'text-[var(--neon-green)]';
  if (status === 'fail') return 'text-[var(--neon-red)]';
  return 'text-[var(--text-muted)]';
}

const LINK_CLS =
  'inline-flex items-center gap-1 px-1.5 py-0.5 rounded hover:bg-[var(--bg-glass-hover)] hover:underline cursor-pointer';

function CountCell(value: number, onClick?: () => void) {
  if (!value) return <span className="text-[var(--text-muted)]">0</span>;
  if (!onClick) return value;
  return (
    <button
      type="button"
      onClick={onClick}
      title="Click to open Usage"
      className={LINK_CLS}
    >
      {value}
    </button>
  );
}

export function ConnectionsInsightsTable() {
  const { state, setFocusedConnectionFilter, setActivePage } = useDiag();
  const { parsedData, focusedConnectionFilter } = state;

  const rows = useMemo(() => buildConnectionInsightsRows(parsedData), [parsedData]);

  // Local filter state — seeded from the context prefilter (if any), one-way.
  const [nameFilter, setNameFilter] = useState<string>(focusedConnectionFilter?.name ?? '');
  const [typeFilter, setTypeFilter] = useState<string>(focusedConnectionFilter?.type ?? '');

  // Reflect a prefilter that arrives *after* mount by adjusting state during
  // render (React's supported pattern) rather than via a setState-in-effect.
  // The mount seed above already covers the first prefilter.
  const [lastAppliedFilter, setLastAppliedFilter] = useState(focusedConnectionFilter);
  if (focusedConnectionFilter !== lastAppliedFilter) {
    setLastAppliedFilter(focusedConnectionFilter);
    if (focusedConnectionFilter) {
      if (typeof focusedConnectionFilter.name === 'string')
        setNameFilter(focusedConnectionFilter.name);
      if (typeof focusedConnectionFilter.type === 'string')
        setTypeFilter(focusedConnectionFilter.type);
    }
  }

  // Clear the one-shot context prefilter once consumed. Updating *context*
  // state in an effect is the legitimate, un-flagged case.
  // Gated on being the active page: during AnimatePresence exit this table is
  // still mounted and context-subscribed, and would otherwise eat a filter
  // that a count-click just set for the Usage page before it mounts.
  const isActivePage = state.activePage === 'connections-insights';
  useEffect(() => {
    if (focusedConnectionFilter && isActivePage) setFocusedConnectionFilter(null);
  }, [focusedConnectionFilter, isActivePage, setFocusedConnectionFilter]);

  const hasPrefilter = nameFilter !== '' || typeFilter !== '';

  const filtered = useMemo(() => {
    const n = nameFilter.trim().toLowerCase();
    const t = typeFilter.trim().toLowerCase();
    return rows.filter((r) => {
      if (n && !r.name.toLowerCase().includes(n)) return false;
      if (t && !r.type.toLowerCase().includes(t)) return false;
      return true;
    });
  }, [rows, nameFilter, typeFilter]);

  const columns = useMemo<ColumnDef<ConnectionInsightsRow>[]>(() => {
    const goToHealth = () => setActivePage('connections-health');
    const goToUsage = (name: string) => {
      setFocusedConnectionFilter({ name });
      setActivePage('connections-usage');
    };
    return [
      {
        id: 'name',
        label: 'Connection',
        mono: true,
        defaultSortDir: 'asc',
        cellClassName: 'whitespace-nowrap',
        render: (row) => (
          <a
            href={dssUrls.llmConn(row.name)}
            target="_blank"
            rel="noopener noreferrer"
            title={row.driver ? `Driver: ${row.driver}` : undefined}
            className="text-[var(--neon-cyan)] hover:underline"
          >
            {row.name}
          </a>
        ),
        sortValue: (row) => row.name.toLowerCase(),
      },
      {
        id: 'type',
        label: 'Type',
        defaultSortDir: 'asc',
        cellClassName: 'whitespace-nowrap',
        render: (row) => row.type,
        sortValue: (row) => row.type.toLowerCase(),
      },
      {
        id: 'projectCount',
        label: 'Projects',
        align: 'right',
        mono: true,
        render: (row) => CountCell(row.projectCount, () => goToUsage(row.name)),
        sortValue: (row) => row.projectCount,
      },
      {
        id: 'datasetCount',
        label: 'Datasets',
        align: 'right',
        mono: true,
        render: (row) => CountCell(row.datasetCount, () => goToUsage(row.name)),
        sortValue: (row) => row.datasetCount,
      },
      {
        id: 'recipeCount',
        label: 'Recipes',
        align: 'right',
        mono: true,
        render: (row) => CountCell(row.recipeCount, () => goToUsage(row.name)),
        sortValue: (row) => row.recipeCount,
      },
      {
        id: 'llmAssetCount',
        label: 'LLM assets',
        align: 'right',
        mono: true,
        // llm-audit has no prefilter mechanism — Usage is the closest drill target.
        render: (row) => CountCell(row.llmAssetCount, () => goToUsage(row.name)),
        sortValue: (row) => row.llmAssetCount,
      },
      {
        id: 'fsUsageCount',
        label: 'FS usages',
        align: 'right',
        mono: true,
        render: (row) => CountCell(row.fsUsageCount, () => goToUsage(row.name)),
        sortValue: (row) => row.fsUsageCount,
      },
      {
        id: 'auditSeverity',
        label: 'Audit',
        render: (row) => {
          const auditTitle =
            row.auditIssues.length > 0
              ? `${row.auditIssues.join('\n')}\n\nClick to open Health → Audit`
              : 'No audit findings';
          return (
            <button
              type="button"
              onClick={goToHealth}
              title={auditTitle}
              className={`${LINK_CLS} ${severityTextClass(row.auditSeverity)}`}
            >
              <span>{row.auditSeverity ?? '—'}</span>
              {row.auditIssues.length > 0 && (
                <span className="font-mono text-[var(--text-muted)]">
                  ({row.auditIssues.length})
                </span>
              )}
            </button>
          );
        },
        sortValue: (row) => (row.auditSeverity ? SEVERITY_RANK[row.auditSeverity] : 0),
      },
      {
        id: 'healthStatus',
        label: 'Health',
        render: (row) => {
          const healthTitle = row.healthError
            ? `${row.healthError}\n\nClick to open Health`
            : 'Click to open Health';
          return (
            <button
              type="button"
              onClick={goToHealth}
              title={healthTitle}
              className={`${LINK_CLS} ${healthTextClass(row.healthStatus)}`}
            >
              {row.healthStatus ?? '—'}
            </button>
          );
        },
        sortValue: (row) => (row.healthStatus ? HEALTH_RANK[row.healthStatus] : 0),
      },
    ];
  }, [setActivePage, setFocusedConnectionFilter]);

  const clearFilters = () => {
    setNameFilter('');
    setTypeFilter('');
  };

  if (rows.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface)] p-6 text-sm text-[var(--text-secondary)]">
        No connection details available yet. Run a connection scan from <strong>Inventory</strong>,
        <strong> Usage</strong>, or <strong>Health</strong> to populate insights.
      </div>
    );
  }

  return (
    <div className="rounded-xl overflow-hidden flex flex-col flex-1 min-h-0">
      {/* Header / filters */}
      <div className="px-4 py-3 border-b border-[var(--border-glass)]">
        <div className="flex items-center justify-between gap-3 mb-3">
          <h4 className="text-lg font-semibold text-[var(--text-primary)]">
            {filtered.length === rows.length
              ? `${rows.length} Connections`
              : `${filtered.length} of ${rows.length} Connections`}
          </h4>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="text"
            placeholder="Filter by name…"
            value={nameFilter}
            onChange={(e) => setNameFilter(e.target.value)}
            className="flex-1 min-w-[180px] px-3 py-1.5 text-sm rounded-md border border-[var(--border-default)] bg-[var(--bg-surface)] text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)]"
          />
          <input
            type="text"
            placeholder="Filter by type…"
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="flex-1 min-w-[180px] px-3 py-1.5 text-sm rounded-md border border-[var(--border-default)] bg-[var(--bg-surface)] text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)]"
          />
          {hasPrefilter && (
            <button
              type="button"
              onClick={clearFilters}
              className="px-3 py-1.5 rounded-md text-xs font-medium text-[var(--text-secondary)] border border-[var(--text-tertiary)]/30 hover:bg-[var(--bg-glass-hover)] transition-colors"
            >
              Clear filter
            </button>
          )}
        </div>
      </div>

      {/* Scan incomplete notice (self-hides when no failures) */}
      {(parsedData.connectionUsageFailedProjectCount ?? 0) > 0 && (
        <div className="px-4 pt-3">
          <ScanIncompleteNotice
            failedProjectCount={parsedData.connectionUsageFailedProjectCount}
            scannedProjectCount={parsedData.connectionUsageScannedProjectCount}
          />
        </div>
      )}

      <DataGrid
        rows={filtered}
        columns={columns}
        rowKey={(row) => row.name}
        defaultSortColumnId="projectCount"
        filtersActive={hasPrefilter}
        noMatchMessage="No connections match the current filters."
        scroll="card"
      />
    </div>
  );
}
