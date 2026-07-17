import type { PageId } from '../types';
import { createSyncStore } from './createSyncStore';

// UI-only reveal flag for the on-demand Users deep-dive. Persisted to
// localStorage so a device that has opted in keeps it across reloads. Holds no
// secret — flipping it only shows read-only analytics pages.
const STORAGE_KEY = 'admin-toolkit:adoptionUnlock';

// Users-section pages revealed only after the deep-dive is unlocked (typing the
// keyword). Single source of truth for the nav + ⌘K gates — both filter on this
// set so a page never leaks into one surface but not the other.
export const EGG_GATED_PAGES: ReadonlySet<PageId> = new Set<PageId>(['adoption', 'user-churn']);

function readHint(): boolean {
  try {
    return globalThis.localStorage?.getItem(STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

const store = createSyncStore<boolean>(readHint());

/** Imperative reveal — callable from non-React code (the keydown handler). */
export function unlockAdoption(): void {
  if (store.get()) return;
  store.set(true);
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, '1');
  } catch {
    /* localStorage unavailable */
  }
}

/** React hook — is the deep-dive currently revealed on this device. */
export function useAdoptionVisible(): boolean {
  return store.use();
}
