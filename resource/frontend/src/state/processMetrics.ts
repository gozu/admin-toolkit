import { fetchJson } from '../utils/api';
import { createSyncStore } from './createSyncStore';
import { registerScanStore } from './scanStoreRegistry';
import type { Lifecycle, ProcessMetric } from '../types';

export interface ProcessMetricsState {
  processes: ProcessMetric[];
  totalProcesses: number | null;
  truncated: boolean;
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
  error?: string;
}

const INITIAL_STATE: ProcessMetricsState = {
  processes: [],
  totalProcesses: null,
  truncated: false,
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

async function runLoad() {
  _controller?.abort();
  const controller = new AbortController();
  _controller = controller;

  store.set({ ...INITIAL_STATE, status: 'loading', startedAt: new Date().toISOString() });

  try {
    const data = await fetchJson<ProcessMetricsResponse>('/api/host/process-metrics', {
      signal: controller.signal,
    });
    if (!data.ok) throw new Error(data.error || 'Process metrics unavailable');
    const processes = data.processes || [];
    store.patch({
      status: 'done',
      processes,
      totalProcesses: data.totalProcesses ?? processes.length,
      truncated: Boolean(data.truncated),
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

/** Retry button — abort any in-flight load, reset state, and start fresh. */
export function restartProcessMetricsScan(): void {
  _controller?.abort();
  _controller = null;
  void runLoad();
}
