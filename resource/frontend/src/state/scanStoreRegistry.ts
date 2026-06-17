import type { Lifecycle } from '../types';

// All scan stores are lifecycle stores. The mirror reads `lifecycle()` and
// writes the result to `parsedData[field]` — no branching by kind.
export interface RegisteredScanStore {
  field: string;
  subscribe: (listener: () => void) => () => void;
  lifecycle: () => Lifecycle;
  // Optional compact snapshot of the store's ScanState (phase/progress/error/
  // timings + a small data summary). Consumed by the diagnostic-bundle builder;
  // stores that don't provide it are simply omitted from the data summary.
  snapshot?: () => unknown;
}

const stores: RegisteredScanStore[] = [];

export function registerScanStore(entry: RegisteredScanStore): void {
  stores.push(entry);
}

export function getRegisteredScanStores(): readonly RegisteredScanStore[] {
  return stores;
}
