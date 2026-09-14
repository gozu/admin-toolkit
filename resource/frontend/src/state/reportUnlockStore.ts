import { createSyncStore } from './createSyncStore';

// Device-local UI preference, independent of host/session data.
const STORAGE_KEY = 'admin-toolkit:reportUnlock';

function readHint(): boolean {
  try {
    return globalThis.localStorage?.getItem(STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

const store = createSyncStore<boolean>(readHint());

/** Toggle the report and return its new visibility to the keydown handler. */
export function toggleReport(): boolean {
  const visible = !store.get();
  store.set(visible);
  try {
    if (visible) globalThis.localStorage?.setItem(STORAGE_KEY, '1');
    else globalThis.localStorage?.removeItem(STORAGE_KEY);
  } catch {
    /* localStorage unavailable */
  }
  return visible;
}

/** React hook — is the report currently revealed on this device. */
export function useReportVisible(): boolean {
  return store.use();
}
