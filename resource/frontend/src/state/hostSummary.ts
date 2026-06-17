import { fetchJson } from '../utils/api';
import { createSyncStore } from './createSyncStore';
import type { ParsedData } from '../types';

// Host/memory summary fields produced by the host-metrics macro — the same
// subset of /api/overview that the on-demand /api/host/summary endpoint
// returns. Patched back into parsedData on refresh so the Memory and host
// overview cards re-render with fresh numbers.
export type HostSummaryData = Pick<
  ParsedData,
  | 'cpuCores'
  | 'osInfo'
  | 'memoryInfo'
  | 'systemLimits'
  | 'filesystemInfo'
  | 'pythonVersion'
  | 'lastRestartTime'
  | 'dssVersion'
  | 'instanceInfo'
>;

export interface HostSummaryState {
  status: 'idle' | 'loading' | 'done' | 'error';
  error: string | null;
  // Stored ISO timestamp of the last successful re-run, for the "as of" label.
  fetchedAt: string | null;
}

const INITIAL_STATE: HostSummaryState = { status: 'idle', error: null, fetchedAt: null };

const store = createSyncStore<HostSummaryState>(INITIAL_STATE, { sessionScoped: true });
let _controller: AbortController | null = null;

export function getHostSummary(): HostSummaryState {
  return store.get();
}

export function subscribeHostSummary(listener: () => void): () => void {
  return store.subscribe(listener);
}

/**
 * Re-run the host-metrics command (free -m / df -h / ulimit -a / cpuinfo) on
 * demand, bypassing the overview cache. `apply` patches the fresh fields back
 * into parsedData (callers pass `setParsedData`). Isolated re-run: hits only
 * `/api/host/summary`, never the full startup sequence.
 */
export async function refreshHostSummary(
  apply: (data: HostSummaryData) => void,
): Promise<void> {
  _controller?.abort();
  const controller = new AbortController();
  _controller = controller;
  store.patch({ status: 'loading', error: null });
  try {
    const data = await fetchJson<HostSummaryData>('/api/host/summary', {
      signal: controller.signal,
    });
    apply(data);
    store.patch({ status: 'done', fetchedAt: new Date().toISOString() });
  } catch (err) {
    if ((err as Error).name === 'AbortError') return;
    store.patch({
      status: 'error',
      error: err instanceof Error ? err.message : String(err),
    });
  } finally {
    if (_controller === controller) _controller = null;
  }
}
