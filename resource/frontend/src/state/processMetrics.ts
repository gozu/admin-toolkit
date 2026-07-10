import { fetchJson } from '../utils/api';
import { createSyncStore } from './createSyncStore';
import { registerScanStore } from './scanStoreRegistry';
import type { Lifecycle, ProcessMetric } from '../types';

export interface ProcessMetricsState {
  processes: ProcessMetric[];
  totalProcesses: number | null;
  truncated: boolean;
  // Target host's DIP_HOME, used to strip boilerplate from command lines.
  dipHome: string | null;
  status: 'idle' | 'loading' | 'done' | 'error';
  error: string | null;
  // Stored timestamps so lifecycle resolution stays pure at render time.
  startedAt: string | null;
  finishedAt: string | null;
}

interface ProcessMetricsResponse {
  ok: boolean;
  processes?: ProcessMetric[];
  totalProcesses?: number;
  truncated?: boolean;
  dipHome?: string;
  error?: string;
}

const INITIAL_STATE: ProcessMetricsState = {
  processes: [],
  totalProcesses: null,
  truncated: false,
  dipHome: null,
  status: 'idle',
  error: null,
  startedAt: null,
  finishedAt: null,
};

const store = createSyncStore<ProcessMetricsState>(INITIAL_STATE, { sessionScoped: true });
let _controller: AbortController | null = null;

function processMetricsLifecycle(): Lifecycle {
  const s = store.get();
  if (s.status === 'idle') return { phase: 'queued' };
  const startedAt = s.startedAt || '1970-01-01T00:00:00.000Z';
  if (s.status === 'loading') {
    return {
      phase: 'running',
      startedAt,
      progressPct: 0,
      message: 'Reading process table',
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
      error: s.error || 'Process metrics failed',
      progressPct: 0,
    };
  }
  // done
  return { phase: 'done', startedAt, finishedAt, isEmpty: s.processes.length === 0 };
}

registerScanStore({
  field: 'cpuLoading',
  subscribe: store.subscribe,
  lifecycle: processMetricsLifecycle,
});

export function getProcessMetrics(): ProcessMetricsState {
  return store.get();
}

export function subscribeProcessMetrics(listener: () => void): () => void {
  return store.subscribe(listener);
}

async function runLoad(fresh = false) {
  _controller?.abort();
  const controller = new AbortController();
  _controller = controller;

  // Keep the previous snapshot on screen while `ps` re-runs — refreshes
  // (manual button or the Resources page's periodic tier) must not blank the
  // table/doughnuts; DataGrid shows a compact progress row alongside the rows.
  const prev = store.get();
  store.set({
    ...INITIAL_STATE,
    processes: prev.processes,
    totalProcesses: prev.totalProcesses,
    truncated: prev.truncated,
    dipHome: prev.dipHome,
    status: 'loading',
    startedAt: new Date().toISOString(),
  });

  try {
    // `?fresh=1` bypasses the backend's process-metrics cache so an explicit
    // Refresh re-runs `ps` instead of returning the (up to 10-min) cached snapshot.
    const data = await fetchJson<ProcessMetricsResponse>(
      fresh ? '/api/host/process-metrics?fresh=1' : '/api/host/process-metrics',
      { signal: controller.signal },
    );
    if (!data.ok) throw new Error(data.error || 'Process metrics unavailable');
    const processes = data.processes || [];
    store.patch({
      status: 'done',
      processes,
      totalProcesses: data.totalProcesses ?? processes.length,
      truncated: Boolean(data.truncated),
      dipHome: data.dipHome ?? null,
      finishedAt: new Date().toISOString(),
    });
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

/** Idempotent — only loads once (or if a prior load isn't running/done). */
export function startProcessMetricsScan(): void {
  const status = store.get().status;
  if (status === 'loading' || status === 'done') return;
  void runLoad();
}

/** Refresh button — abort any in-flight load and re-run `ps` (cache-bypass). */
export function restartProcessMetricsScan(): void {
  _controller?.abort();
  _controller = null;
  void runLoad(true);
}

/** One `processes` frame from /api/host/resource-stream (local host). */
export interface StreamedProcessSnapshot {
  ok?: boolean;
  processes?: ProcessMetric[];
  totalProcesses?: number;
  truncated?: boolean;
  dipHome?: string;
}

/** Patch a streamed snapshot into the store WITHOUT the loading round-trip —
 * status never leaves 'done', so the table updates in place instead of
 * mounting/unmounting a progress row every second. Supersedes any in-flight
 * macro run (its stale result must not land after fresher streamed data). */
export function applyStreamedProcessSnapshot(payload: StreamedProcessSnapshot): void {
  if (!payload || payload.ok === false || !Array.isArray(payload.processes)) return;
  _controller?.abort();
  _controller = null;
  const prev = store.get();
  const now = new Date().toISOString();
  store.set({
    ...prev,
    status: 'done',
    error: null,
    processes: payload.processes,
    totalProcesses: payload.totalProcesses ?? payload.processes.length,
    truncated: Boolean(payload.truncated),
    dipHome: payload.dipHome ?? prev.dipHome,
    startedAt: prev.startedAt ?? now,
    finishedAt: now,
  });
}
