/**
 * Per-session cache for heavy per-card fetches.
 *
 * On the first call for a given key, runs the fetcher and caches the result.
 * On subsequent calls within the same session epoch, returns the cached value.
 * Concurrent callers for the same key await the same in-flight promise.
 *
 * Epoch is bumped by `bumpSessionEpoch()` (called by the global Refresh button),
 * which invalidates all cached entries.
 */

let _epoch = 0;
const _cache: Map<string, { epoch: number; value: unknown }> = new Map();
const _inflight: Map<string, Promise<unknown>> = new Map();
const _listeners: Set<() => void> = new Set();

export function getSessionEpoch(): number {
  return _epoch;
}

export function bumpSessionEpoch(): number {
  _epoch += 1;
  _cache.clear();
  _inflight.clear();
  _listeners.forEach((l) => l());
  return _epoch;
}

export function subscribeSessionEpoch(listener: () => void): () => void {
  _listeners.add(listener);
  return () => {
    _listeners.delete(listener);
  };
}

export async function fetchWithSessionCache<T>(key: string, fetcher: () => Promise<T>): Promise<T> {
  const cached = _cache.get(key);
  if (cached && cached.epoch === _epoch) {
    return cached.value as T;
  }
  const inflight = _inflight.get(key);
  if (inflight) {
    return inflight as Promise<T>;
  }
  const promise = (async () => {
    try {
      const value = await fetcher();
      _cache.set(key, { epoch: _epoch, value });
      return value;
    } finally {
      _inflight.delete(key);
    }
  })();
  _inflight.set(key, promise);
  return promise;
}

export function peekSessionCache<T>(key: string): T | undefined {
  const cached = _cache.get(key);
  if (cached && cached.epoch === _epoch) return cached.value as T;
  return undefined;
}

export function invalidateSessionKey(key: string): void {
  _cache.delete(key);
  _inflight.delete(key);
}
