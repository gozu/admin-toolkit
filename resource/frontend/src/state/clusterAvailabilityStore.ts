import { fetchJson } from '../utils/api';
import { createSyncStore } from './createSyncStore';

// Cheap K8s presence signal for module availability gating (API mode only —
// zip mode gates on parsedData.clusters instead). Loaded once per session via
// the delayed warmup queue; never joins the lifecycle aggregate.

export interface ClusterAvailabilityState {
  /** Clusters registered in DSS; null = unknown (endpoint failed / old backend). */
  count: number | null;
  loaded: boolean;
}

export const clusterAvailabilityStore = createSyncStore<ClusterAvailabilityState>(
  { count: null, loaded: false },
  { sessionScoped: true },
);

let _inflight: Promise<void> | null = null;

export function loadClusterCount(): Promise<void> {
  if (clusterAvailabilityStore.get().loaded) return Promise.resolve();
  if (_inflight) return _inflight;
  _inflight = (async () => {
    try {
      const data = await fetchJson<{ count: number | null }>('/api/k8s-insights/cluster-count');
      clusterAvailabilityStore.patch({
        count: typeof data.count === 'number' ? data.count : null,
        loaded: true,
      });
    } catch {
      // Unknown — availability gating treats null as "keep visible".
      clusterAvailabilityStore.patch({ count: null, loaded: true });
    } finally {
      _inflight = null;
    }
  })();
  return _inflight;
}
