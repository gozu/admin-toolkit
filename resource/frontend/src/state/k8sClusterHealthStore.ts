import { subscribeSessionEpoch } from './sessionCache';
import { fetchJson } from '../utils/api';
import { createSyncStore, sessionWriter } from './createSyncStore';
import type { K8sClusterHealthResult } from '../types';

interface K8sClusterHealthState {
  data: K8sClusterHealthResult | null;
  loading: boolean;
  error: string | null;
}

const initial: K8sClusterHealthState = { data: null, loading: false, error: null };

const store = createSyncStore<K8sClusterHealthState>(initial, { sessionScoped: true });

let inflight: Promise<void> | null = null;

async function load(force = false): Promise<void> {
  const cur = store.get();
  if (!force && (cur.data || cur.loading)) return;
  if (inflight) return inflight;
  const write = sessionWriter(store);
  store.patch({ loading: true, error: null });
  inflight = (async () => {
    try {
      const result = await fetchJson<K8sClusterHealthResult>('/api/k8s-insights/clusters/health');
      write.set({ data: result, loading: false, error: null });
    } catch (err) {
      write.set({
        data: null,
        loading: false,
        error: err instanceof Error ? err.message : String(err),
      });
    } finally {
      if (write.current()) inflight = null;
    }
  })();
  return inflight;
}

export const k8sClusterHealthStore = {
  use: store.use,
  load,
  refresh: () => load(true),
};

subscribeSessionEpoch(() => { inflight = null; });
