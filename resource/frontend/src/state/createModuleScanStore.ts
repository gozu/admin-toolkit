import { fetchJson, fetchRaw } from '../utils/api';
import { parseSseStream } from '../utils/sseStream';
import { createSyncStore, type SyncStore } from './createSyncStore';
import { registerScanStore } from './scanStoreRegistry';
import type { Lifecycle, ParsedData } from '../types';

export interface ScanState<TData> {
  data: TData | null;
  loading: boolean;
  progressPct: number;
  scanPhase: string;
  scanMessage: string;
  error: string | null;
  scanStarted: boolean;
  // Stored timestamps so render-time lifecycle derivation never calls Date.now().
  startedAt: string | null;
  finishedAt: string | null;
  // Optional total-items count published by some scans on 'init'. Stored so
  // progressPct can be derived without inferring from already-emitted rows.
  total?: number | null;
}

export interface ModuleScanStore<TData> {
  store: SyncStore<ScanState<TData>>;
  use: () => ScanState<TData>;
  load: (force?: boolean) => Promise<void>;
  lifecycle: () => Lifecycle;
  abort: () => void;
}

export interface CreateModuleScanStoreOptions<TData, TEvent> {
  loadingField: keyof ParsedData & `${string}Loading`;
  streamEndpoint?: string | (() => string);
  fallbackEndpoint?: string;
  parseEvent?: (event: string, payload: unknown) => TEvent | null;
  reduce?: (state: ScanState<TData>, ev: TEvent) => Partial<ScanState<TData>>;
}

// A compact, bounded description of a scan's data payload — enough to tell what
// loaded without serializing potentially-large arrays into the diag bundle.
function summarizeScanData(data: unknown): unknown {
  if (data == null) return null;
  if (Array.isArray(data)) return { kind: 'array', length: data.length };
  if (typeof data === 'object') {
    const summary: Record<string, unknown> = { kind: 'object' };
    for (const [k, v] of Object.entries(data as Record<string, unknown>)) {
      summary[k] = Array.isArray(v) ? `array(${v.length})` : typeof v;
    }
    return summary;
  }
  return { kind: typeof data };
}

export function createModuleScanStore<TData, TEvent>(
  opts: CreateModuleScanStoreOptions<TData, TEvent>,
): ModuleScanStore<TData> {
  const initial: ScanState<TData> = {
    data: null,
    loading: false,
    progressPct: 0,
    scanPhase: '',
    scanMessage: '',
    error: null,
    scanStarted: false,
    startedAt: null,
    finishedAt: null,
    total: null,
  };
  const store = createSyncStore<ScanState<TData>>(initial, { sessionScoped: true });
  let inflight: Promise<void> | null = null;
  let currentController: AbortController | null = null;

  if (!opts.streamEndpoint && !opts.fallbackEndpoint) {
    throw new Error(
      `createModuleScanStore('${opts.loadingField}'): at least one of streamEndpoint or fallbackEndpoint must be set.`,
    );
  }

  function resolveStreamEndpoint(): string | undefined {
    if (typeof opts.streamEndpoint === 'function') return opts.streamEndpoint();
    return opts.streamEndpoint;
  }

  async function runScan(): Promise<void> {
    const controller = new AbortController();
    currentController = controller;
    store.patch({
      loading: true,
      error: null,
      scanStarted: true,
      progressPct: 0,
      scanPhase: 'starting',
      startedAt: new Date().toISOString(),
      finishedAt: null,
    });
    try {
      const endpoint = resolveStreamEndpoint();
      if (!endpoint) {
        const data = await fetchJson<TData>(opts.fallbackEndpoint!);
        store.patch({ data, progressPct: 100, scanPhase: 'complete' });
        return;
      }
      const response = await fetchRaw(endpoint, { signal: controller.signal });
      if (!response.ok || !response.body) {
        if (opts.fallbackEndpoint) {
          const data = await fetchJson<TData>(opts.fallbackEndpoint);
          store.patch({ data, progressPct: 100, scanPhase: 'cached' });
          return;
        }
        throw new Error(`Stream failed: ${response.status} ${response.statusText}`);
      }
      if (!opts.parseEvent || !opts.reduce) {
        throw new Error(
          `createModuleScanStore('${opts.loadingField}'): streamEndpoint requires parseEvent + reduce.`,
        );
      }
      for await (const { event, payload } of parseSseStream(response.body)) {
        const ev = opts.parseEvent(event, payload);
        if (!ev) continue;
        const patch = opts.reduce(store.get(), ev);
        store.patch(patch);
      }
    } catch (err) {
      const aborted = controller.signal.aborted
        || (err instanceof DOMException && err.name === 'AbortError');
      store.patch({
        error: aborted ? null : (err instanceof Error ? err.message : String(err)),
        scanPhase: aborted ? 'aborted' : store.get().scanPhase,
        scanMessage: aborted ? 'Scan aborted.' : store.get().scanMessage,
      });
    } finally {
      if (currentController === controller) currentController = null;
      store.patch({ loading: false, finishedAt: new Date().toISOString() });
    }
  }

  function abort(): void {
    if (currentController) currentController.abort();
  }

  function load(force = false): Promise<void> {
    if (inflight) return inflight;
    const s = store.get();
    if (s.scanStarted && s.data && !force) return Promise.resolve();
    inflight = runScan().finally(() => {
      inflight = null;
    });
    return inflight;
  }

  function lifecycle(): Lifecycle {
    const s = store.get();
    if (!s.scanStarted) return { phase: 'queued' };
    const startedAt = s.startedAt || new Date(0).toISOString();
    if (s.loading) {
      return {
        phase: 'running',
        startedAt,
        progressPct: s.progressPct,
        message: s.scanMessage || undefined,
        subPhase: s.scanPhase || undefined,
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
        progressPct: s.progressPct,
      };
    }
    return {
      phase: 'done',
      startedAt,
      finishedAt,
      isEmpty: s.data == null,
      message: s.scanMessage || undefined,
    };
  }

  registerScanStore({
    field: opts.loadingField,
    subscribe: store.subscribe,
    lifecycle,
    rawData: () => store.get().data,
    snapshot: () => {
      const s = store.get();
      return {
        loading: s.loading,
        scanStarted: s.scanStarted,
        progressPct: s.progressPct,
        scanPhase: s.scanPhase,
        scanMessage: s.scanMessage,
        error: s.error,
        startedAt: s.startedAt,
        finishedAt: s.finishedAt,
        total: s.total ?? null,
        data: summarizeScanData(s.data),
      };
    },
  });

  return {
    store,
    use: store.use,
    load,
    lifecycle,
    abort,
  };
}
