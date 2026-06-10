import { useState } from 'react';
import { fetchJson, ApiRequestError } from '../utils/api';
import { DataGrid } from './common/DataGrid';
import type { ColumnDef } from '../utils/dataGridTypes';

interface BenchLevel {
  concurrency: number;
  calls: number;
  errors: number;
  errorSample: string | null;
  seconds: number;
  callsPerSec: number;
  medianMs: number | null;
}

interface BenchResult {
  levels: BenchLevel[];
  peakCallsPerSec: number;
  kneeConcurrency: number;
  recommended: Record<string, number>;
  applied: Record<string, number>;
  persisted: Record<string, number>;
  persistError: string | null;
  projectsProbed: number;
  elapsedSeconds: number;
}

const LEVEL_COLUMNS: ColumnDef<BenchLevel>[] = [
  { id: 'concurrency', label: 'Concurrency', render: (lv) => lv.concurrency, align: 'right', mono: true },
  { id: 'callsPerSec', label: 'Calls/s', render: (lv) => lv.callsPerSec, align: 'right', mono: true },
  { id: 'medianMs', label: 'Median ms', render: (lv) => lv.medianMs ?? '—', align: 'right', mono: true },
  {
    id: 'errors',
    label: 'Errors',
    render: (lv) => (lv.errors > 0 ? `${lv.errors} (${lv.errorSample ?? ''})` : 0),
    align: 'right',
    mono: true,
  },
];

interface SettingsUpdateResult {
  updated: Record<string, number>;
  persisted: Record<string, number>;
  persistError: string | null;
}

/**
 * Settings action: benchmark the active DSS host's API concurrency ceiling
 * (sweep of parallel metadata probes), find the throughput knee, and tune the
 * scan worker pools from measurement instead of guesses. Apply writes the
 * recommendation to the live backend AND to the saved plugin settings so it
 * survives backend restarts.
 */
export function PerfAutoTuneCard() {
  const [running, setRunning] = useState(false);
  const [applying, setApplying] = useState(false);
  const [result, setResult] = useState<BenchResult | null>(null);
  const [appliedMsg, setAppliedMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setRunning(true);
    setError(null);
    setResult(null);
    setAppliedMsg(null);
    try {
      const res = await fetchJson<BenchResult>('/api/settings/benchmark', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      setResult(res);
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  const apply = async () => {
    if (!result) return;
    setApplying(true);
    setError(null);
    try {
      const res = await fetchJson<SettingsUpdateResult>('/api/settings/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...result.recommended, persist: true }),
      });
      if (res.persistError) {
        setAppliedMsg(`Applied to the running backend, but saving to plugin settings failed: ${res.persistError}`);
      } else {
        setAppliedMsg('Applied and saved to plugin settings — survives backend restarts.');
      }
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setApplying(false);
    }
  };

  const recommendedWorkers = result?.recommended['parallel_workers_default'];

  return (
    <section className="glass-card p-4 space-y-3">
      <div>
        <h3 className="text-lg font-semibold text-[var(--text-primary)]">Performance Auto-Tune</h3>
        <p className="text-sm text-[var(--text-muted)]">
          Benchmarks how many concurrent API calls the active DSS host can serve before throughput
          flattens, then sizes the scan worker pools from that measurement. Takes ~15–30 seconds and
          fires a few hundred lightweight metadata reads.
        </p>
      </div>

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => void run()}
          disabled={running || applying}
          className="px-3 py-1.5 rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-sm text-[var(--text-primary)] transition-colors disabled:opacity-50"
        >
          {running ? 'Benchmarking…' : 'Run benchmark'}
        </button>
        {result && !appliedMsg && (
          <button
            type="button"
            onClick={() => void apply()}
            disabled={applying || running}
            className="px-3 py-1.5 rounded bg-[var(--accent)]/20 text-[var(--accent)] hover:bg-[var(--accent)]/30 text-sm transition-colors disabled:opacity-50"
          >
            {applying ? 'Applying…' : `Apply ${recommendedWorkers} workers`}
          </button>
        )}
      </div>

      {error && <p className="text-sm text-[var(--neon-red)]">{error}</p>}
      {appliedMsg && <p className="text-sm text-[var(--text-secondary)]">{appliedMsg}</p>}

      {result && (
        <div className="space-y-2">
          <p className="text-sm text-[var(--text-secondary)]">
            Throughput flattens at{' '}
            <span className="font-mono text-[var(--text-primary)]">{result.kneeConcurrency}</span>{' '}
            concurrent calls (peak{' '}
            <span className="font-mono text-[var(--text-primary)]">{result.peakCallsPerSec}</span>{' '}
            calls/s) → recommended{' '}
            <span className="font-mono text-[var(--text-primary)]">{recommendedWorkers}</span>{' '}
            workers per scan (two heavy scans run at a time).
          </p>
          <details className="text-xs text-[var(--text-muted)]">
            <summary className="cursor-pointer select-none">Sweep detail</summary>
            <div className="mt-2">
              <DataGrid
                rows={result.levels}
                columns={LEVEL_COLUMNS}
                rowKey={(lv) => String(lv.concurrency)}
                fit
              />
            </div>
          </details>
        </div>
      )}
    </section>
  );
}
