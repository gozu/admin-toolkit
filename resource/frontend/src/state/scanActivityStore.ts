import { createSyncStore } from './createSyncStore';
import { getSessionEpoch } from './sessionCache';

// Covers queued bootstrap work without its own module lifecycle.
export const scanActivityStore = createSyncStore(0, { sessionScoped: true });
export function beginScanActivity() {
  const epoch = getSessionEpoch();
  scanActivityStore.set(scanActivityStore.get() + 1);
  let ended = false;
  return () => {
    if (ended) return;
    ended = true;
    if (epoch === getSessionEpoch())
      scanActivityStore.set(Math.max(0, scanActivityStore.get() - 1));
  };
}
