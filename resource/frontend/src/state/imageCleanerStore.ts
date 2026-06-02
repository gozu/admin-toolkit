import { createModuleScanStore } from './createModuleScanStore';
import { createSyncStore } from './createSyncStore';
import { fetchRaw } from '../utils/api';

export type Provider = 'ecr' | 'acr' | 'gar';
type DetectSource = 'dss-config' | 'imds' | 'ipnet' | 'none';

export interface DetectResult {
  provider: Provider | null;
  registryUrl: string | null;
  source: DetectSource;
}

// Detect-provider runs once per session and joins the lifecycle aggregator
// through the scan-store mirror — see useScanStoreLoadingMirror. The page
// later writes its own scan-phase lifecycle on top; once detect completes the
// scan store stays `loading: false`, so it never clobbers later page writes.
export const imageCleanerDetectScan = createModuleScanStore<DetectResult, never>({
  loadingField: 'imageCleanerLoading',
  fallbackEndpoint: '/api/tools/image-cleaner/detect-provider',
});

export interface ReleaseInfo {
  version: string;
  releaseDate: string;
  maxCutoffDate: string;
}

export interface ErrorWithHint {
  message: string;
  hint?: string;
}

interface PerProvider {
  info: ReleaseInfo | null;
  loading: boolean;
  error: ErrorWithHint | null;
}

interface ReleaseDateState {
  byProvider: Partial<Record<Provider, PerProvider>>;
}

const releaseDateStore = createSyncStore<ReleaseDateState>(
  { byProvider: {} },
  { sessionScoped: true },
);
const inflightByProvider = new Map<Provider, Promise<void>>();

async function fetchWithHint<T>(url: string): Promise<T> {
  const resp = await fetchRaw(url);
  const text = await resp.text();
  if (!resp.ok) {
    let parsed: { error?: string; hint?: string } = {};
    try {
      parsed = JSON.parse(text) as { error?: string; hint?: string };
    } catch {
      /* not JSON */
    }
    const err = new Error(parsed.error || `${resp.status} ${resp.statusText}`) as Error & {
      hint?: string;
    };
    if (parsed.hint) err.hint = parsed.hint;
    throw err;
  }
  return JSON.parse(text) as T;
}

export function loadReleaseDate(provider: Provider): Promise<void> {
  const slot = releaseDateStore.get().byProvider[provider];
  if (slot?.info) return Promise.resolve();
  const existing = inflightByProvider.get(provider);
  if (existing) return existing;
  releaseDateStore.patch({
    byProvider: {
      ...releaseDateStore.get().byProvider,
      [provider]: { info: null, loading: true, error: null },
    },
  });
  const p = (async () => {
    try {
      const info = await fetchWithHint<ReleaseInfo>(
        `/api/tools/image-cleaner/release-date?provider=${provider}`,
      );
      releaseDateStore.patch({
        byProvider: {
          ...releaseDateStore.get().byProvider,
          [provider]: { info, loading: false, error: null },
        },
      });
    } catch (err) {
      const e = err as Error & { hint?: string };
      releaseDateStore.patch({
        byProvider: {
          ...releaseDateStore.get().byProvider,
          [provider]: {
            info: null,
            loading: false,
            error: { message: e.message, hint: e.hint },
          },
        },
      });
    } finally {
      inflightByProvider.delete(provider);
    }
  })();
  inflightByProvider.set(provider, p);
  return p;
}

export async function loadDefaultImageCleanerBootstrap(): Promise<void> {
  await imageCleanerDetectScan.load();
  const provider = imageCleanerDetectScan.store.get().data?.provider;
  if (provider) {
    await loadReleaseDate(provider);
  }
}

export const imageCleanerReleaseDates = {
  use: releaseDateStore.use,
  get: releaseDateStore.get,
};
