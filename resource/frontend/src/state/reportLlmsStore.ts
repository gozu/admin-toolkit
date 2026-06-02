import { createSyncStore } from './createSyncStore';
import { registerScanStore } from './scanStoreRegistry';
import { fetchJson } from '../utils/api';
import type { Lifecycle, LlmOption } from '../types';

interface ReportLlmsState {
  llms: LlmOption[];
  loading: boolean;
  error: string | null;
  loaded: boolean;
  startedAt: string | null;
  finishedAt: string | null;
}

const INITIAL: ReportLlmsState = {
  llms: [],
  loading: false,
  error: null,
  loaded: false,
  startedAt: null,
  finishedAt: null,
};

const store = createSyncStore<ReportLlmsState>(INITIAL, { sessionScoped: true });
let inflight: Promise<void> | null = null;

async function fetchOnce(): Promise<void> {
  const startedAt = new Date().toISOString();
  store.patch({
    loading: true,
    error: null,
    startedAt,
    finishedAt: null,
  });
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 20_000);
  try {
    const data = await fetchJson<{ llms: LlmOption[]; error?: string }>('/api/llms', {
      signal: controller.signal,
    });
    store.patch({
      llms: data.llms || [],
      error: data.error || null,
      loaded: true,
      finishedAt: new Date().toISOString(),
    });
  } catch (err) {
    const msg =
      err instanceof DOMException && err.name === 'AbortError'
        ? 'Timed out loading models (20s). Retry when DSS is responsive.'
        : err instanceof Error
          ? err.message
          : String(err);
    store.patch({
      error: msg,
      loaded: true,
      finishedAt: new Date().toISOString(),
    });
  } finally {
    clearTimeout(timeoutId);
    store.patch({ loading: false });
  }
}

function lifecycle(): Lifecycle {
  const s = store.get();
  if (!s.startedAt && !s.loaded) return { phase: 'queued' };
  const startedAt = s.startedAt || new Date(0).toISOString();
  if (s.loading) {
    return {
      phase: 'running',
      startedAt,
      progressPct: 25,
      message: 'Loading report LLMs',
      updatedAt: startedAt,
    };
  }
  const finishedAt = s.finishedAt || startedAt;
  if (s.error) {
    return {
      phase: 'error',
      startedAt,
      finishedAt,
      error: s.error,
      progressPct: 25,
    };
  }
  return {
    phase: 'done',
    startedAt,
    finishedAt,
    isEmpty: s.llms.length === 0,
    message: 'Report LLMs loaded',
  };
}

registerScanStore({
  field: 'reportLoading',
  subscribe: store.subscribe,
  lifecycle,
});

export const reportLlmsStore = {
  use: store.use,
  get: store.get,
  load(force = false): Promise<void> {
    if (inflight) return inflight;
    if (store.get().loaded && !force) return Promise.resolve();
    inflight = fetchOnce().finally(() => {
      inflight = null;
    });
    return inflight;
  },
};
