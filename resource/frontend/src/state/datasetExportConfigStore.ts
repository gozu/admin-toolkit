import { createSyncStore } from './createSyncStore';
import { fetchJson } from '../utils/api';

// Local-scoped feature config for "Save Tables as Datasets". Empty
// configuredConnection ⇒ feature disabled (toolbar button greyed out).
// Mirrors state/dbHealthConnectionsStore.ts (sessionScoped singleton).
interface State {
  configuredConnection: string | null;
  project: string | null;
  loaded: boolean;
  loading: boolean;
  error: string | null;
}

const INITIAL: State = {
  configuredConnection: null,
  project: null,
  loaded: false,
  loading: false,
  error: null,
};

const store = createSyncStore<State>(INITIAL, { sessionScoped: true });
let inflight: Promise<void> | null = null;

async function fetchConfigOnce(): Promise<void> {
  store.patch({ loading: true, error: null });
  try {
    const data = await fetchJson<{ configuredConnection?: string; project?: string }>(
      '/api/tools/dataset-export/config',
    );
    store.patch({
      configuredConnection: data.configuredConnection || null,
      project: data.project || null,
      loaded: true,
    });
  } catch (err) {
    store.patch({
      error: err instanceof Error ? err.message : String(err),
      loaded: true,
    });
  } finally {
    store.patch({ loading: false });
  }
}

export const datasetExportConfigStore = {
  use: store.use,
  get: store.get,
  loadConfig(): Promise<void> {
    if (inflight) return inflight;
    if (store.get().loaded) return Promise.resolve();
    inflight = fetchConfigOnce().finally(() => {
      inflight = null;
    });
    return inflight;
  },
};
