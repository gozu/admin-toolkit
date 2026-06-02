import { fetchRaw } from '../utils/api';
import { parseSseStream } from '../utils/sseStream';
import { createSyncStore } from './createSyncStore';
import { registerScanStore } from './scanStoreRegistry';
import type { Lifecycle, SqlPushdownOwnerGroup } from '../types';

export interface SqlPushdownScanState {
  total: number | null;
  scanned: number | null;
  ownerGroups: SqlPushdownOwnerGroup[];
  status: 'idle' | 'scanning' | 'done' | 'error';
  error: string | null;
  elapsedMs: number | null;
  // Stored timestamps so lifecycle resolution stays pure at render time.
  startedAt: string | null;
  finishedAt: string | null;
}

const INITIAL_STATE: SqlPushdownScanState = {
  total: null,
  scanned: null,
  ownerGroups: [],
  status: 'idle',
  error: null,
  elapsedMs: null,
  startedAt: null,
  finishedAt: null,
};

const store = createSyncStore<SqlPushdownScanState>(INITIAL_STATE, { sessionScoped: true });
let _controller: AbortController | null = null;

function progressPctOf(s: SqlPushdownScanState): number {
  if (!s.total || s.total <= 0) return s.status === 'done' ? 100 : 0;
  const scanned = Math.max(0, s.scanned ?? 0);
  const pct = Math.round((scanned / s.total) * 100);
  if (s.status === 'done') return 100;
  return Math.max(0, Math.min(99, pct));
}

function sqlPushdownLifecycle(): Lifecycle {
  const s = store.get();
  if (s.status === 'idle') return { phase: 'queued' };
  const startedAt = s.startedAt || '1970-01-01T00:00:00.000Z';
  if (s.status === 'scanning') {
    return {
      phase: 'running',
      startedAt,
      progressPct: progressPctOf(s),
      message: s.total
        ? `Scanned ${s.scanned ?? 0} / ${s.total} projects`
        : 'Discovering projects',
      subPhase: 'scanning',
      updatedAt: startedAt,
    };
  }
  const finishedAt = s.finishedAt || startedAt;
  if (s.status === 'error') {
    return {
      phase: 'error',
      startedAt,
      finishedAt,
      error: s.error || 'Scan failed',
      progressPct: progressPctOf(s),
    };
  }
  // done
  return {
    phase: 'done',
    startedAt,
    finishedAt,
    isEmpty: s.ownerGroups.length === 0,
  };
}

registerScanStore({
  field: 'projectComputeLoading',
  subscribe: store.subscribe,
  lifecycle: sqlPushdownLifecycle,
});

export function getSqlPushdownScan(): SqlPushdownScanState {
  return store.get();
}

export function subscribeSqlPushdownScan(listener: () => void): () => void {
  return store.subscribe(listener);
}

async function runScan() {
  _controller?.abort();
  const controller = new AbortController();
  _controller = controller;

  store.set({ ...INITIAL_STATE, status: 'scanning', startedAt: new Date().toISOString() });

  try {
    const response = await fetchRaw('/api/projects/sql_pushdown_audit', { signal: controller.signal });
    if (!response.ok || !response.body) {
      const body = await response.text();
      let msg = `Scan failed: ${response.status} ${response.statusText}`;
      try {
        msg = (JSON.parse(body) as { error?: string }).error || msg;
      } catch {
        /* ignore */
      }
      throw new Error(msg);
    }

    for await (const { event, payload } of parseSseStream(response.body)) {
      const data = payload as Record<string, unknown>;
      if (event === 'error') {
        throw new Error(String(data.error || 'Scan error'));
      } else if (event === 'init') {
        store.patch({ total: Number(data.total) });
      } else if (event === 'progress') {
        store.patch({ scanned: Number(data.scanned) });
      } else if (event === 'done') {
        store.patch({
          status: 'done',
          ownerGroups: (data.ownerGroups || []) as SqlPushdownOwnerGroup[],
          scanned: store.get().total,
          elapsedMs: Number(data.total_ms) || null,
          finishedAt: new Date().toISOString(),
        });
      }
    }
  } catch (err) {
    if ((err as Error).name === 'AbortError') return;
    store.patch({
      status: 'error',
      error: err instanceof Error ? err.message : String(err),
      finishedAt: new Date().toISOString(),
    });
  } finally {
    if (_controller === controller) {
      _controller = null;
    }
  }
}

/** Idempotent — only starts a scan if one has never run (or isn't running). */
export function startSqlPushdownScan(): void {
  const status = store.get().status;
  if (status === 'scanning' || status === 'done') return;
  void runScan();
}

/** Retry button — abort any in-flight scan, reset state, and start fresh. */
export function restartSqlPushdownScan(): void {
  _controller?.abort();
  _controller = null;
  void runScan();
}
