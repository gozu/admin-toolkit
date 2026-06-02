import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fetchJson } from '../../utils/api';
import { useDiag } from '../../context/DiagContext';
import type { Lifecycle } from '../../types';
import {
  dbHealthConnectionsStore,
  type DbOverview,
  type ProjectBreakdown,
  type TableInfo,
} from '../../state/dbHealthConnectionsStore';

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const SMALL_PROJECT_THRESHOLD = 5 * 1024; // 5 KB

function relativeTime(iso: string | null): string {
  if (!iso) return 'Never';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return 'Never';
  const diff = Date.now() - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'Just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return d.toLocaleDateString();
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'kB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

function bloatColor(ratio: number): string {
  if (ratio >= 0.5) return 'var(--neon-red, #ef4444)';
  if (ratio >= 0.2) return 'var(--neon-yellow, #eab308)';
  return 'var(--neon-green, #22c55e)';
}

function vacuumAgeColor(iso: string | null): string {
  if (!iso) return 'var(--neon-red, #ef4444)';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return 'var(--neon-red, #ef4444)';
  const days = (Date.now() - d.getTime()) / 86400000;
  if (days > 7) return 'var(--neon-yellow, #eab308)';
  return 'var(--text-secondary)';
}

type SortKey = 'name' | 'totalSizeBytes' | 'rowCount' | 'deadTuples' | 'bloatRatio' | 'lastVacuum' | 'lastAutovacuum' | 'lastAnalyze';
type SortDir = 'asc' | 'desc';

/* ------------------------------------------------------------------ */
/*  Table Row                                                          */
/* ------------------------------------------------------------------ */

function TableRow({ t, overview, actionLoading, handleAction }: {
  t: TableInfo;
  overview: DbOverview | null;
  actionLoading: Record<string, string>;
  handleAction: (action: 'vacuum' | 'analyze', tableName: string) => void;
}) {
  return (
    <tr className="border-b border-[var(--border-default)]/30 hover:bg-[var(--bg-glass-hover)]">
      <td className="py-1.5 pr-4 font-mono text-xs text-[var(--text-primary)]">{t.name}</td>
      <td className="py-1.5 pr-4 text-right text-[var(--text-secondary)]">{t.totalSize}</td>
      <td className="py-1.5 pr-4 text-right text-[var(--text-secondary)]">{(t.rowCount ?? 0).toLocaleString()}</td>
      <td className="py-1.5 pr-4 text-right" style={{ color: (t.deadTuples ?? 0) > 1000 ? 'var(--neon-yellow)' : 'var(--text-secondary)' }}>
        {(t.deadTuples ?? 0).toLocaleString()}
      </td>
      <td className="py-1.5 pr-4 text-right font-mono" style={{ color: bloatColor(t.bloatRatio ?? 0) }}>
        {((t.bloatRatio ?? 0) * 100).toFixed(1)}%
      </td>
      <td className="py-1.5 pr-4 text-xs" style={{ color: vacuumAgeColor(t.lastVacuum) }}>
        {relativeTime(t.lastVacuum)}
      </td>
      <td className="py-1.5 pr-4 text-xs" style={{ color: vacuumAgeColor(t.lastAutovacuum) }}>
        {relativeTime(t.lastAutovacuum)}
      </td>
      <td className="py-1.5 pr-4 text-xs text-[var(--text-secondary)]">
        {relativeTime(t.lastAnalyze)}
      </td>
      <td className="py-1.5">
        <div className="flex gap-1">
          <button
            onClick={() => handleAction('vacuum', t.name)}
            disabled={!overview?.canWrite || !!actionLoading[`vacuum-${t.name}`]}
            title={!overview?.canWrite ? 'No write access on this connection' : `Run VACUUM on ${t.name} — reclaims disk space from dead tuples`}
            className="px-2 py-0.5 text-xs rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] border border-[var(--border-default)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-30 disabled:cursor-not-allowed inline-flex items-center gap-1"
          >
            {actionLoading[`vacuum-${t.name}`] ? <><span className="w-3 h-3 border border-current border-t-transparent rounded-full animate-spin" />Running</> : 'Vacuum'}
          </button>
          <button
            onClick={() => handleAction('analyze', t.name)}
            disabled={!overview?.canWrite || !!actionLoading[`analyze-${t.name}`]}
            title={!overview?.canWrite ? 'No write access on this connection' : 'Runs PostgreSQL ANALYZE to update query planner statistics for faster queries'}
            className="px-2 py-0.5 text-xs rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] border border-[var(--border-default)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-30 disabled:cursor-not-allowed inline-flex items-center gap-1"
          >
            {actionLoading[`analyze-${t.name}`] ? <><span className="w-3 h-3 border border-current border-t-transparent rounded-full animate-spin" />Running</> : 'Optimize'}
          </button>
        </div>
      </td>
    </tr>
  );
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function DbHealthPage() {
  const { dispatch } = useDiag();
  const setLifecycle = useCallback(
    (lc: Lifecycle) => {
      dispatch({ type: 'SET_PARSED_DATA', payload: { dbHealthLoading: lc } });
    },
    [dispatch],
  );

  // Connection state — sourced from the session-scoped store so revisits reattach.
  const {
    connections,
    configuredConnection,
    loading: connLoading,
    error: connError,
    loaded: connLoaded,
    detailsByConnection,
  } = dbHealthConnectionsStore.use();
  const [selectedConn, setSelectedConn] = useState<string>('');

  const selectedDetail = selectedConn ? detailsByConnection[selectedConn] : undefined;
  const overview = selectedDetail?.overview ?? null;
  const tables = selectedDetail?.tables ?? [];
  const perProject = selectedDetail?.perProject ?? null;
  const dataLoading = selectedDetail?.loading ?? false;
  const dataError = selectedDetail?.error ?? null;
  const warnings = selectedDetail?.warnings ?? [];

  // Action state
  const [actionLoading, setActionLoading] = useState<Record<string, string>>({});

  // Sort state
  const [sortKey, setSortKey] = useState<SortKey>('totalSizeBytes');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  // Bootstrap: fire the cached connections fetch on first visit. The store
  // dedupes inflight + already-loaded calls, so this is cheap on remount.
  useEffect(() => {
    void dbHealthConnectionsStore.load();
  }, []);

  // Pick a default selected connection once the store reports loaded data.
  // Only auto-select when a connection is configured in plugin settings; if it
  // is blank, DB Health is disabled (see the not-configured branch below).
  useEffect(() => {
    if (!connLoaded || connections.length === 0 || selectedConn) return;
    if (!configuredConnection) return;
    const match = connections.find((c) => c.name === configuredConnection);
    setSelectedConn(match ? match.name : configuredConnection);
  }, [connLoaded, connections, configuredConnection, selectedConn]);

  // Mirror the store's bootstrap state into the page's lifecycle field. The
  // per-connection fetch below overwrites this once the user picks a
  // connection — we only need running / error / empty here.
  const connLifecycleStartedRef = useRef<string | null>(null);
  useEffect(() => {
    if (connLoading) {
      if (!connLifecycleStartedRef.current) {
        connLifecycleStartedRef.current = new Date().toISOString();
      }
      setLifecycle({
        phase: 'running',
        startedAt: connLifecycleStartedRef.current,
        progressPct: 10,
        message: 'Discovering Postgres connections',
        updatedAt: connLifecycleStartedRef.current,
      });
      return;
    }
    if (!connLoaded) return;
    const startedAt = connLifecycleStartedRef.current || new Date().toISOString();
    if (connError) {
      setLifecycle({
        phase: 'error',
        startedAt,
        finishedAt: new Date().toISOString(),
        error: connError,
        progressPct: 10,
      });
      return;
    }
    if (!configuredConnection) {
      setLifecycle({
        phase: 'done',
        startedAt,
        finishedAt: new Date().toISOString(),
        isEmpty: true,
        message: 'DB Health not configured',
      });
      return;
    }
    if (connections.length === 0) {
      setLifecycle({
        phase: 'done',
        startedAt,
        finishedAt: new Date().toISOString(),
        isEmpty: true,
        message: 'No Postgres connections found',
      });
    }
    // When connections exist the per-connection effect below takes over.
  }, [connLoading, connLoaded, connError, connections.length, configuredConnection, setLifecycle]);

  // Load data when connection changes. The store dedupes inflight requests and
  // reuses warmed details for the configured default connection.
  useEffect(() => {
    if (!selectedConn) return;
    const startedAt = new Date().toISOString();
    setLifecycle({
      phase: 'running',
      startedAt,
      progressPct: 25,
      message: `Loading ${selectedConn}`,
      updatedAt: startedAt,
    });

    dbHealthConnectionsStore.loadDetails(selectedConn)
      .then(() => {
        const detail = dbHealthConnectionsStore.getDetail(selectedConn);
        if (detail.error) {
          setLifecycle({
            phase: 'error',
            startedAt,
            finishedAt: new Date().toISOString(),
            error: detail.error,
            progressPct: 25,
          });
          return;
        }
        setLifecycle({
          phase: 'done',
          startedAt,
          finishedAt: new Date().toISOString(),
          isEmpty: detail.tables.length === 0,
        });
      })
      .catch((err) => {
        setLifecycle({
          phase: 'error',
          startedAt,
          finishedAt: new Date().toISOString(),
          error: String(err),
          progressPct: 25,
        });
      })
  }, [selectedConn, setLifecycle]);

  // Sort tables
  const sortedTables = useMemo(() => {
    const sorted = [...tables].sort((a, b) => {
      let av: string | number = a[sortKey] ?? '';
      let bv: string | number = b[sortKey] ?? '';
      if (typeof av === 'string' && typeof bv === 'string') {
        return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
      }
      av = Number(av) || 0;
      bv = Number(bv) || 0;
      return sortDir === 'asc' ? av - bv : bv - av;
    });
    return sorted;
  }, [tables, sortKey, sortDir]);

  const handleSort = useCallback((key: SortKey) => {
    setSortKey((prev) => {
      if (prev === key) {
        setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
        return prev;
      }
      setSortDir('desc');
      return key;
    });
  }, []);

  // Actions
  const handleAction = useCallback(async (action: 'vacuum' | 'analyze', tableName: string) => {
    if (!selectedConn) return;

    const key = `${action}-${tableName}`;
    setActionLoading((prev) => ({ ...prev, [key]: action }));
    try {
      const payload: Record<string, string> = { connection: selectedConn, table: tableName };
      const res = await fetchJson<{ success?: boolean; error?: string }>(
        `/api/tools/db-health/${action}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        },
      );
      if (res.error) {
        alert(`${action.toUpperCase()} failed: ${res.error}`);
      } else {
        await dbHealthConnectionsStore.loadDetails(selectedConn, { force: true });
      }
    } catch (err) {
      alert(`${action.toUpperCase()} failed: ${String(err)}`);
    } finally {
      setActionLoading((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
    }
  }, [selectedConn]);

  // Per-project: split into main (>=5KB) and small (<5KB)
  const projectGroups = useMemo(() => {
    if (!perProject) return { main: [] as ProjectBreakdown[], small: [] as ProjectBreakdown[] };
    const sorted = [...perProject.projects].sort((a, b) => b.sizeBytes - a.sizeBytes);
    const main: ProjectBreakdown[] = [];
    const small: ProjectBreakdown[] = [];
    for (const p of sorted) {
      if (p.sizeBytes < SMALL_PROJECT_THRESHOLD) small.push(p);
      else main.push(p);
    }
    return { main, small };
  }, [perProject]);

  // Per-project bar widths (based on main projects + system only)
  const maxProjectSize = useMemo(() => {
    if (!perProject) return 1;
    const allSizes = [
      ...projectGroups.main.map((p) => p.sizeBytes),
      perProject.system?.totalBytes ?? 0,
    ];
    return Math.max(...allSizes, 1);
  }, [perProject, projectGroups.main]);

  /* ---------------------------------------------------------------- */
  /*  Render                                                           */
  /* ---------------------------------------------------------------- */

  const sortIcon = (key: SortKey) => {
    if (sortKey !== key) return <span className="opacity-30 ml-1">{'\u21C5'}</span>;
    return <span className="ml-1">{sortDir === 'asc' ? '\u25B2' : '\u25BC'}</span>;
  };

  const colTips: Record<string, string> = {
    name: 'PostgreSQL table name',
    totalSizeBytes: 'Total disk space including indexes and TOAST data',
    rowCount: 'Estimated number of live rows (from pg_stat_user_tables)',
    deadTuples: 'Rows marked for deletion but not yet reclaimed by VACUUM',
    bloatRatio: 'Dead tuples as a percentage of total tuples — high values indicate VACUUM is needed',
    lastVacuum: 'Last time a manual VACUUM was run on this table',
    lastAutovacuum: 'Last time PostgreSQL autovacuum processed this table',
    lastAnalyze: 'Last time table statistics were updated (PostgreSQL ANALYZE)',
  };

  // Group tables into folders
  const SMALL_THRESHOLD = 5000;
  const tableGroups = useMemo(() => {
    const system: TableInfo[] = [];
    const small: TableInfo[] = [];
    const main: TableInfo[] = [];
    for (const t of sortedTables) {
      if (t.name.startsWith('pg_') || t.name.startsWith('sql_') || t.name.startsWith('information_schema')) {
        system.push(t);
      } else if (t.rowCount < SMALL_THRESHOLD && t.totalSizeBytes < 65536) {
        small.push(t);
      } else {
        main.push(t);
      }
    }
    return { main, system, small };
  }, [sortedTables]);

  // Collapsed state for folders
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({ system: true, small: true, smallProjects: true });
  const toggleGroup = useCallback((group: string) => {
    setCollapsedGroups((prev) => ({ ...prev, [group]: !prev[group] }));
  }, []);

  // Bulk vacuum + optimize all
  const [bulkRunning, setBulkRunning] = useState(false);
  const [bulkProgress, setBulkProgress] = useState('');
  const handleBulkAction = useCallback(async () => {
    if (!selectedConn || !tables.length) return;
    setBulkRunning(true);
    const total = tables.length;
    let done = 0;
    for (const t of tables) {
      setBulkProgress(`${done + 1}/${total}: ${t.name}`);
      try {
        const payload: Record<string, string> = { connection: selectedConn, table: t.name };
        await fetchJson(`/api/tools/db-health/vacuum`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        await fetchJson(`/api/tools/db-health/analyze`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      } catch { /* continue on error */ }
      done++;
    }
    setBulkProgress('');
    setBulkRunning(false);
    await dbHealthConnectionsStore.loadDetails(selectedConn, { force: true });
  }, [selectedConn, tables]);

  // Not configured: DB Health is disabled until an admin sets a RuntimeDB
  // connection in plugin settings. Show guidance instead of the selector/data.
  if (connLoaded && !configuredConnection) {
    return (
      <div className="w-full py-4">
        <div className="glass-card mx-auto mt-12 max-w-xl p-8 text-center">
          <h3 className="mb-2 text-lg font-semibold text-[var(--text-primary)]">DB Health isn’t configured</h3>
          <p className="text-sm text-[var(--text-secondary)]">
            Set a PostgreSQL (RuntimeDB) connection in the Admin Toolkit plugin
            settings to enable it.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full py-4 flex flex-col gap-4">
      {/* Connection Selector */}
      <div className="glass-card p-4">
        <div className="flex items-center gap-3 flex-wrap">
          <label className="text-sm font-medium text-[var(--text-secondary)]">PostgreSQL Connection</label>
          {connLoading ? (
            <div className="w-5 h-5 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin" />
          ) : connError ? (
            <span className="text-sm text-[var(--neon-red)]">Failed to load connections: {connError}</span>
          ) : connections.length === 0 ? (
            <span className="text-sm text-[var(--text-secondary)]">No PostgreSQL connections found</span>
          ) : (
            <select
              value={selectedConn}
              onChange={(e) => setSelectedConn(e.target.value)}
              className="bg-[var(--bg-glass)] border border-[var(--border-default)] rounded px-3 py-1.5 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--accent)]"
            >
              {connections.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name} ({c.host || 'localhost'}:{c.port || 5432}/{c.db})
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      {/* Warnings Banner */}
      {warnings.length > 0 && (
        <div className="glass-card p-3 border border-[var(--neon-yellow)] text-sm text-[var(--neon-yellow)]">
          <strong>Warnings:</strong>
          <ul className="list-disc ml-5 mt-1">
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Loading / Error states */}
      {dataLoading && (
        <div className="flex items-center justify-center py-12">
          <div className="w-6 h-6 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin" />
          <span className="ml-3 text-[var(--text-secondary)] text-sm">Querying database...</span>
        </div>
      )}

      {dataError && !dataLoading && (
        <div className="glass-card p-6 border border-[var(--neon-red)] text-center">
          <p className="text-[var(--neon-red)] text-sm">{dataError}</p>
        </div>
      )}

      {/* Summary Cards */}
      {overview && !dataLoading && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <div className="glass-card p-4 text-center">
            <div className="text-xs uppercase tracking-wider text-[var(--text-secondary)] mb-1">Database Size</div>
            <div className="text-2xl font-bold text-[var(--text-primary)]">{overview.dbSize}</div>
          </div>
          <div className="glass-card p-4 text-center">
            <div className="text-xs uppercase tracking-wider text-[var(--text-secondary)] mb-1">Tables</div>
            <div className="text-2xl font-bold text-[var(--text-primary)]">{overview.tableCount}</div>
          </div>
          <div className="glass-card p-4 text-center">
            <div className="text-xs uppercase tracking-wider text-[var(--text-secondary)] mb-1">PG Version</div>
            <div className="text-2xl font-bold text-[var(--text-primary)]">{overview.version}</div>
          </div>
          <div className="glass-card p-4 text-center">
            <div className="text-xs uppercase tracking-wider text-[var(--text-secondary)] mb-1">Dead Tuples</div>
            <div className="text-2xl font-bold" style={{ color: (overview.totalDeadTuples ?? 0) > 10000 ? 'var(--neon-red)' : 'var(--text-primary)' }}>
              {(overview.totalDeadTuples ?? 0).toLocaleString()}
            </div>
            {(overview.totalLiveTuples ?? 0) > 0 && (
              <div className="text-xs text-[var(--text-secondary)] mt-0.5">
                {(((overview.totalDeadTuples ?? 0) / ((overview.totalLiveTuples ?? 0) + (overview.totalDeadTuples ?? 0))) * 100).toFixed(1)}% bloat
              </div>
            )}
          </div>
        </div>
      )}

      {/* Tables Table */}
      {tables.length > 0 && !dataLoading && (
        <div className="glass-card p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-[var(--text-secondary)]">Table Breakdown</h3>
            {overview?.canWrite && (
              <button
                onClick={handleBulkAction}
                disabled={bulkRunning}
                title="Run VACUUM followed by ANALYZE on every table"
                className="px-3 py-1 text-xs rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] border border-[var(--border-default)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5"
              >
                {bulkRunning ? (
                  <><span className="w-3 h-3 border border-current border-t-transparent rounded-full animate-spin" />{bulkProgress}</>
                ) : 'Vacuum & Optimize All'}
              </button>
            )}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--border-default)] text-left text-xs uppercase tracking-wider text-[var(--text-secondary)]">
                  <th className="pb-2 pr-4 cursor-pointer select-none" onClick={() => handleSort('name')} title={colTips.name}>
                    Table {sortIcon('name')}
                  </th>
                  <th className="pb-2 pr-4 cursor-pointer select-none text-right" onClick={() => handleSort('totalSizeBytes')} title={colTips.totalSizeBytes}>
                    Size {sortIcon('totalSizeBytes')}
                  </th>
                  <th className="pb-2 pr-4 cursor-pointer select-none text-right" onClick={() => handleSort('rowCount')} title={colTips.rowCount}>
                    Rows {sortIcon('rowCount')}
                  </th>
                  <th className="pb-2 pr-4 cursor-pointer select-none text-right" onClick={() => handleSort('deadTuples')} title={colTips.deadTuples}>
                    Dead Tuples {sortIcon('deadTuples')}
                  </th>
                  <th className="pb-2 pr-4 cursor-pointer select-none text-right" onClick={() => handleSort('bloatRatio')} title={colTips.bloatRatio}>
                    Bloat % {sortIcon('bloatRatio')}
                  </th>
                  <th className="pb-2 pr-4 cursor-pointer select-none" onClick={() => handleSort('lastVacuum')} title={colTips.lastVacuum}>
                    Vacuum {sortIcon('lastVacuum')}
                  </th>
                  <th className="pb-2 pr-4 cursor-pointer select-none" onClick={() => handleSort('lastAutovacuum')} title={colTips.lastAutovacuum}>
                    Autovacuum {sortIcon('lastAutovacuum')}
                  </th>
                  <th className="pb-2 pr-4 cursor-pointer select-none" onClick={() => handleSort('lastAnalyze')} title={colTips.lastAnalyze}>
                    Optimized {sortIcon('lastAnalyze')}
                  </th>
                  <th className="pb-2" title="Run VACUUM or Optimize (PostgreSQL ANALYZE) on this table">Actions</th>
                </tr>
              </thead>
              <tbody>
                {/* Main tables */}
                {tableGroups.main.map((t) => (
                  <TableRow key={t.name} t={t} overview={overview} actionLoading={actionLoading} handleAction={handleAction} />
                ))}
                {/* System tables folder */}
                {tableGroups.system.length > 0 && (
                  <>
                    <tr className="cursor-pointer hover:bg-[var(--bg-glass-hover)]" onClick={() => toggleGroup('system')}>
                      <td colSpan={9} className="py-2 text-xs font-medium text-[var(--text-secondary)]">
                        <span className="mr-1">{collapsedGroups.system ? '\u25B6' : '\u25BC'}</span>
                        System Tables ({tableGroups.system.length})
                      </td>
                    </tr>
                    {!collapsedGroups.system && tableGroups.system.map((t) => (
                      <TableRow key={t.name} t={t} overview={overview} actionLoading={actionLoading} handleAction={handleAction} />
                    ))}
                  </>
                )}
                {/* Small tables folder */}
                {tableGroups.small.length > 0 && (
                  <>
                    <tr className="cursor-pointer hover:bg-[var(--bg-glass-hover)]" onClick={() => toggleGroup('small')}>
                      <td colSpan={9} className="py-2 text-xs font-medium text-[var(--text-secondary)]">
                        <span className="mr-1">{collapsedGroups.small ? '\u25B6' : '\u25BC'}</span>
                        Small Tables &lt;5k rows ({tableGroups.small.length})
                      </td>
                    </tr>
                    {!collapsedGroups.small && tableGroups.small.map((t) => (
                      <TableRow key={t.name} t={t} overview={overview} actionLoading={actionLoading} handleAction={handleAction} />
                    ))}
                  </>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Per-Project Breakdown */}
      {perProject && !dataLoading && (
        <div className="glass-card p-4">
          <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-3">Per-Project Space Usage</h3>
          {!perProject.isRuntimeDb ? (
            <p className="text-sm text-[var(--text-secondary)]">
              Per-project breakdown is only available for RuntimeDB connections.
              All tables are shown in the table breakdown above.
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              {/* System bucket first */}
              {perProject.system && (
                <ProjectBar
                  label="System / Shared"
                  size={formatBytes(perProject.system.totalBytes)}
                  sizeBytes={perProject.system.totalBytes}
                  tableCount={perProject.system.tables.length}
                  rowCount={perProject.system.tables.reduce((s, t) => s + t.rowCount, 0)}
                  maxBytes={maxProjectSize}
                  isSystem
                />
              )}
              {/* Main projects (>=5KB) */}
              {projectGroups.main.map((p) => (
                <ProjectBar
                  key={p.projectKey}
                  label={p.projectKey}
                  size={formatBytes(p.sizeBytes)}
                  sizeBytes={p.sizeBytes}
                  tableCount={p.tableCount}
                  rowCount={p.rowCount}
                  maxBytes={maxProjectSize}
                />
              ))}
              {/* Small projects (<5KB) — collapsed by default */}
              {projectGroups.small.length > 0 && (
                <>
                  <button
                    onClick={() => toggleGroup('smallProjects')}
                    className="flex items-center gap-1 text-xs font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors py-1"
                  >
                    <span>{collapsedGroups.smallProjects ? '\u25B6' : '\u25BC'}</span>
                    Small Projects &lt;5 kB ({projectGroups.small.length})
                  </button>
                  {!collapsedGroups.smallProjects && projectGroups.small.map((p) => (
                    <ProjectBar
                      key={p.projectKey}
                      label={p.projectKey}
                      size={formatBytes(p.sizeBytes)}
                      sizeBytes={p.sizeBytes}
                      tableCount={p.tableCount}
                      rowCount={p.rowCount}
                      maxBytes={maxProjectSize}
                    />
                  ))}
                </>
              )}
              {perProject.projects.length === 0 && (
                <p className="text-sm text-[var(--text-secondary)]">
                  No project-attributed data found. All tables are in the System / Shared bucket.
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Sub-components                                                     */
/* ------------------------------------------------------------------ */

function ProjectBar({
  label,
  size,
  sizeBytes,
  tableCount,
  rowCount,
  maxBytes,
  isSystem,
}: {
  label: string;
  size: string;
  sizeBytes: number;
  tableCount: number;
  rowCount: number;
  maxBytes: number;
  isSystem?: boolean;
}) {
  const pct = Math.max((sizeBytes / maxBytes) * 100, 2);
  return (
    <div className="flex items-center gap-3">
      <div className="w-36 shrink-0 text-xs font-mono text-[var(--text-primary)] truncate" title={label}>
        {label}
      </div>
      <div className="flex-1 h-5 bg-[var(--bg-glass)] rounded overflow-hidden">
        <div
          className="h-full rounded transition-all duration-300"
          style={{
            width: `${pct}%`,
            backgroundColor: isSystem ? 'var(--text-secondary)' : 'var(--accent)',
            opacity: isSystem ? 0.5 : 0.8,
          }}
        />
      </div>
      <div className="w-48 shrink-0 text-xs text-[var(--text-secondary)] text-right">
        {size} &middot; {tableCount} tables &middot; {(rowCount ?? 0).toLocaleString()} rows
      </div>
    </div>
  );
}
