import { useState, useRef, useCallback, useEffect } from 'react';
import { fetchJson } from '../utils/api';
import { hostStore } from '../state/hostStore';
import { useDiag } from '../context/DiagContext';
import type { Lifecycle, PluginCompareRow } from '../types';

type RowStatus = 'match' | 'minor' | 'major' | 'missing' | 'remote-only';

interface DeployResult {
  pluginId: string;
  label: string;
  ok: boolean;
  error?: string;
}

interface DeployProgress {
  completed: number;
  total: number;
  results: DeployResult[];
}

function compareVersions(local: string | null, remote: string | null): RowStatus {
  if (!remote) return 'missing';
  if (!local) return 'remote-only';
  if (local === remote) return 'match';
  const [lMajor, lMinor] = local.split('.').map(Number);
  const [rMajor, rMinor] = remote.split('.').map(Number);
  if (isNaN(lMajor) || isNaN(rMajor)) return local === remote ? 'match' : 'major';
  if (lMajor !== rMajor) return 'major';
  if (lMinor !== rMinor) return 'minor';
  return 'minor';
}

const STATUS_COLORS: Record<RowStatus, string> = {
  match: 'bg-[var(--success)]/10 text-[var(--success)]',
  minor: 'bg-yellow-500/10 text-yellow-400',
  major: 'bg-[var(--neon-red)]/10 text-[var(--neon-red)]',
  missing: 'bg-[var(--neon-red)]/10 text-[var(--neon-red)]',
  'remote-only': 'bg-orange-500/10 text-orange-400',
};

const STATUS_LABELS: Record<RowStatus, string> = {
  match: 'Match',
  minor: 'Minor drift',
  major: 'Major drift',
  missing: 'Missing',
  'remote-only': 'Remote only',
};

type DisplayRow = PluginCompareRow & { status?: RowStatus };

export function PluginComparator() {
  const { hosts } = hostStore.use();
  const { state, dispatch } = useDiag();
  const pluginDetails = state.parsedData.pluginDetails;

  const setSyncLifecycle = useCallback(
    (lc: Lifecycle) => {
      dispatch({ type: 'SET_PARSED_DATA', payload: { pluginSyncLoading: lc } });
    },
    [dispatch],
  );
  const remoteHosts = hosts.filter((h) => h.id !== 'local');
  const [targetHostId, setTargetHostId] = useState('');
  const [rows, setRows] = useState<PluginCompareRow[]>([]);
  const [compared, setCompared] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<RowStatus | 'all'>('all');
  const [deploying, setDeploying] = useState(false);
  const [deployProgress, setDeployProgress] = useState<DeployProgress | null>(null);
  const [concurrency, setConcurrency] = useState(1);
  const [deployingIds, setDeployingIds] = useState<Set<string>>(new Set());
  const abortRef = useRef(false);

  useEffect(() => {
    setCompared(false);
    setRows([]);
    setFilter('all');
    setDeployProgress(null);
  }, [targetHostId]);

  const compare = useCallback(async () => {
    if (!targetHostId) return;
    setLoading(true);
    setError(null);
    setRows([]);
    const startedAt = new Date().toISOString();
    setSyncLifecycle({
      phase: 'running',
      startedAt,
      progressPct: 0,
      message: 'Comparing plugins',
      subPhase: 'compare',
      updatedAt: startedAt,
    });
    try {
      const data = await fetchJson<{ rows: PluginCompareRow[] }>(
        '/api/tools/plugins/compare',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ targetHostId }),
        },
      );
      setRows(data.rows);
      setCompared(true);
      setSyncLifecycle({
        phase: 'done',
        startedAt,
        finishedAt: new Date().toISOString(),
        isEmpty: data.rows.length === 0,
        message: `Compared ${data.rows.length} plugins`,
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      setSyncLifecycle({
        phase: 'error',
        startedAt,
        finishedAt: new Date().toISOString(),
        error: msg,
        progressPct: 0,
      });
    } finally {
      setLoading(false);
    }
  }, [targetHostId, setSyncLifecycle]);

  const baseRows: PluginCompareRow[] = compared
    ? rows
    : (pluginDetails || []).map((p) => ({
        id: p.id,
        label: p.label || p.id,
        localVersion: p.installedVersion ?? null,
        remoteVersion: null,
        isDev: !!p.isDev,
      }));

  const enrichedRows: DisplayRow[] = compared
    ? baseRows.map((row) => ({
        ...row,
        status: compareVersions(row.localVersion, row.remoteVersion),
      }))
    : baseRows;

  const filteredRows =
    compared && filter !== 'all'
      ? enrichedRows.filter((r) => r.status === filter)
      : enrichedRows;

  const counts = compared
    ? enrichedRows.reduce(
        (acc, r) => {
          if (r.status) acc[r.status] = (acc[r.status] || 0) + 1;
          return acc;
        },
        {} as Record<RowStatus, number>,
      )
    : ({} as Record<RowStatus, number>);

  const missingOnRemote = compared
    ? enrichedRows.filter((r) => r.status === 'missing')
    : [];

  /** Deploy a single plugin — used by both per-row buttons and batch deploy */
  const deploySinglePlugin = useCallback(
    async (pluginId: string): Promise<DeployResult> => {
      const row = rows.find((r) => r.id === pluginId);
      const label = row?.label || pluginId;
      try {
        await fetchJson('/api/tools/plugins/deploy-one', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ targetHostId, pluginId }),
        });
        return { pluginId, label, ok: true };
      } catch (err) {
        return { pluginId, label, ok: false, error: err instanceof Error ? err.message : String(err) };
      }
    },
    [targetHostId, rows],
  );

  /** Deploy a single plugin from a per-row button */
  const deployOneRow = useCallback(
    async (pluginId: string) => {
      setDeployingIds((prev) => new Set(prev).add(pluginId));
      const result = await deploySinglePlugin(pluginId);
      setDeployingIds((prev) => {
        const next = new Set(prev);
        next.delete(pluginId);
        return next;
      });
      // Show result briefly, then re-compare to refresh
      if (result.ok) {
        await compare();
      } else {
        setError(`Failed to deploy ${result.label}: ${result.error}`);
      }
    },
    [deploySinglePlugin, compare],
  );

  /** Batch deploy all missing plugins with concurrency control */
  const deployMissing = async () => {
    if (missingOnRemote.length === 0) return;
    abortRef.current = false;
    setDeploying(true);
    const progress: DeployProgress = { completed: 0, total: missingOnRemote.length, results: [] };
    setDeployProgress({ ...progress });
    const startedAt = new Date().toISOString();
    setSyncLifecycle({
      phase: 'running',
      startedAt,
      progressPct: 0,
      message: `Deploying ${missingOnRemote.length} plugins`,
      subPhase: 'deploy',
      updatedAt: startedAt,
    });

    const queue = [...missingOnRemote];
    let idx = 0;

    const worker = async () => {
      while (idx < queue.length) {
        if (abortRef.current) return;
        const row = queue[idx++];
        setDeployingIds((prev) => new Set(prev).add(row.id));
        const result = await deploySinglePlugin(row.id);
        setDeployingIds((prev) => {
          const next = new Set(prev);
          next.delete(row.id);
          return next;
        });
        progress.completed++;
        progress.results.push(result);
        setDeployProgress({ ...progress, results: [...progress.results] });
        setSyncLifecycle({
          phase: 'running',
          startedAt,
          progressPct: Math.round((progress.completed / progress.total) * 100),
          message: `Deployed ${progress.completed} / ${progress.total}`,
          subPhase: 'deploy',
          updatedAt: new Date().toISOString(),
        });
      }
    };

    const workers = Array.from({ length: Math.min(concurrency, queue.length) }, () => worker());
    await Promise.all(workers);

    setDeploying(false);
    const anyFailed = progress.results.some((r) => !r.ok);
    setSyncLifecycle(
      anyFailed
        ? {
            phase: 'error',
            startedAt,
            finishedAt: new Date().toISOString(),
            error: `${progress.results.filter((r) => !r.ok).length} of ${progress.total} failed`,
            progressPct: 100,
          }
        : {
            phase: 'done',
            startedAt,
            finishedAt: new Date().toISOString(),
            isEmpty: false,
            message: `Deployed ${progress.completed} plugins`,
          },
    );
    if (!abortRef.current) await compare();
  };

  return (
    <div className="space-y-4">
      {/* Host picker */}
      <section className="glass-card p-4 space-y-3">
        <h3 className="text-lg font-semibold text-[var(--text-primary)]">Plugin Comparator</h3>
        <p className="text-sm text-[var(--text-muted)]">
          Compare plugins installed on this Design node against a configured remote host.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-3 items-end">
          <label className="text-sm text-[var(--text-secondary)]">
            Compare against
            <select
              value={targetHostId}
              onChange={(e) => setTargetHostId(e.target.value)}
              className="mt-1 w-full input-glass"
              disabled={remoteHosts.length === 0}
            >
              <option value="">
                {remoteHosts.length === 0
                  ? 'No remote hosts configured — add a remote-dss-host preset in plugin settings'
                  : 'Choose a host…'}
              </option>
              {remoteHosts.map((h) => (
                <option key={h.id} value={h.id}>
                  {h.label}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={compare}
            disabled={loading || !targetHostId}
            className="px-4 py-2 rounded btn-primary disabled:opacity-50 h-[38px]"
          >
            {loading ? 'Comparing...' : 'Compare'}
          </button>
        </div>
      </section>

      {error && (
        <section className="glass-card p-3 border border-[var(--status-critical-border)] text-[var(--neon-red)] text-sm">
          {error}
        </section>
      )}

      {/* Results */}
      {enrichedRows.length > 0 && (
        <section className="glass-card p-4 space-y-3">
          {!compared && (
            <p className="text-xs text-[var(--text-tertiary)]">
              Pick a host and click <span className="text-[var(--text-secondary)]">Compare</span> to
              see drift against a remote node.
            </p>
          )}
          {/* Filter pills + deploy/abort button */}
          {compared && (
            <div className="flex flex-wrap items-center gap-2">
              <button
                onClick={() => setFilter('all')}
                className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                  filter === 'all'
                    ? 'bg-[var(--accent-muted)] text-[var(--accent)]'
                    : 'bg-[var(--bg-glass)] text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)]'
                }`}
              >
                All ({enrichedRows.length})
              </button>
              {(['match', 'minor', 'major', 'missing', 'remote-only'] as RowStatus[]).map((s) =>
                (counts[s] || 0) > 0 ? (
                  <button
                    key={s}
                    onClick={() => setFilter(s)}
                    className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                      filter === s
                        ? 'bg-[var(--accent-muted)] text-[var(--accent)]'
                        : 'bg-[var(--bg-glass)] text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)]'
                    }`}
                  >
                    {STATUS_LABELS[s]} ({counts[s]})
                  </button>
                ) : null,
              )}

              {/* Concurrency slider + Deploy/Abort button */}
              {missingOnRemote.length > 0 && (
                <div className="ml-auto flex items-center gap-3">
                  <label className="flex items-center gap-1.5 text-xs text-[var(--text-tertiary)]">
                    <span className="whitespace-nowrap">Parallel:</span>
                    <input
                      type="range"
                      min={1}
                      max={5}
                      value={concurrency}
                      onChange={(e) => setConcurrency(Number(e.target.value))}
                      disabled={deploying}
                      className="w-16 h-1 accent-[var(--accent)]"
                    />
                    <span className="font-mono w-3 text-center">{concurrency}</span>
                  </label>
                  {deploying ? (
                    <button
                      onClick={() => { abortRef.current = true; }}
                      className="px-3 py-1 rounded text-xs font-medium bg-[var(--neon-red)]/20 text-[var(--neon-red)] hover:bg-[var(--neon-red)]/30 transition-colors"
                    >
                      Abort
                    </button>
                  ) : (
                    <button
                      onClick={deployMissing}
                      disabled={loading}
                      className="px-3 py-1 rounded text-xs font-medium btn-primary disabled:opacity-50"
                    >
                      Deploy Missing ({missingOnRemote.length})
                    </button>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Deploy progress */}
          {compared && deployProgress && (
            <div className="space-y-2">
              {deploying && (
                <div className="space-y-1.5">
                  <div className="text-sm text-[var(--text-secondary)]">
                    Deploying... ({deployProgress.completed}/{deployProgress.total})
                  </div>
                  <div className="h-2 rounded-full bg-[var(--bg-glass)] overflow-hidden">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-[var(--neon-cyan)] to-[var(--accent)] transition-all duration-300"
                      style={{ width: `${(deployProgress.completed / deployProgress.total) * 100}%` }}
                    />
                  </div>
                </div>
              )}
              {!deploying && deployProgress.results.length > 0 && (() => {
                const okCount = deployProgress.results.filter((r) => r.ok).length;
                const failCount = deployProgress.results.length - okCount;
                return (
                  <div className="space-y-1.5">
                    <div className={`text-sm font-medium ${failCount > 0 ? 'text-[var(--neon-red)]' : 'text-[var(--success)]'}`}>
                      Deployed {okCount}/{deployProgress.results.length} plugins successfully
                      {failCount > 0 && ` (${failCount} failed)`}
                    </div>
                    {deployProgress.results.filter((r) => !r.ok).map((r) => (
                      <div key={r.pluginId} className="text-xs text-[var(--neon-red)]">
                        {r.label}: {r.error}
                      </div>
                    ))}
                  </div>
                );
              })()}
            </div>
          )}

          {/* Table */}
          <div className="overflow-auto max-h-[60vh] border border-[var(--border-glass)] rounded-lg">
            <table className="table-dark w-full">
              <thead className="sticky top-0 bg-[var(--bg-surface)] z-10">
                <tr>
                  <th className="text-left">Plugin Name</th>
                  <th className="text-left">Design Node</th>
                  {compared && <th className="text-left">Automation Node</th>}
                  {compared && <th className="text-left">Status</th>}
                </tr>
              </thead>
              <tbody>
                {filteredRows.map((row) => {
                  const isRowDeploying = deployingIds.has(row.id);
                  return (
                    <tr key={row.id} className="hover:bg-[var(--bg-glass)]">
                      <td className="text-[var(--text-primary)]">
                        {row.label || row.id}
                        {row.isDev && (
                          <span className="ml-2 px-1.5 py-0.5 text-[10px] font-mono rounded bg-[var(--accent-muted)] text-[var(--accent)]">
                            DEV
                          </span>
                        )}
                      </td>
                      <td className="font-mono text-sm text-[var(--text-secondary)]">
                        {row.localVersion || '\u2014'}
                      </td>
                      {compared && (
                        <td className="font-mono text-sm text-[var(--text-secondary)]">
                          {row.remoteVersion || '\u2014'}
                        </td>
                      )}
                      {compared && row.status && (
                        <td>
                          <div className="flex items-center gap-1.5">
                            <span
                              className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${STATUS_COLORS[row.status]}`}
                            >
                              {STATUS_LABELS[row.status]}
                            </span>
                            {row.status === 'missing' && (
                              <button
                                onClick={() => deployOneRow(row.id)}
                                disabled={isRowDeploying || deploying}
                                title={`Deploy ${row.label || row.id} to remote`}
                                className="p-0.5 rounded text-[var(--text-tertiary)] hover:text-[var(--accent)] hover:bg-[var(--accent-muted)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                              >
                                {isRowDeploying ? (
                                  <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                                    <path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M7.76 7.76L4.93 4.93" />
                                  </svg>
                                ) : (
                                  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                                    <path d="M12 5v14m-7-7l7 7 7-7" />
                                  </svg>
                                )}
                              </button>
                            )}
                          </div>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
