import type { Lifecycle } from '../types';

// All scan stores are lifecycle stores. The mirror reads `lifecycle()` and
// writes the result to `parsedData[field]` — no branching by kind.
export interface RegisteredScanStore {
  field: string;
  subscribe: (listener: () => void) => () => void;
  lifecycle: () => Lifecycle;
}

const stores: RegisteredScanStore[] = [];

export function registerScanStore(entry: RegisteredScanStore): void {
  stores.push(entry);
}

export function getRegisteredScanStores(): readonly RegisteredScanStore[] {
  return stores;
}
