import { createSyncStore } from './createSyncStore';
import { fetchJson } from '../utils/api';

// Remote-host API-key unlock state. The actual decryption key lives in an
// HttpOnly cookie (`admin_toolkit_hostkey`, set/cleared by the backend) — JS
// can't read it, so it rides every same-origin request automatically.
//
// This store only mirrors the *UI* state:
//   configured — does any remote-host preset carry an encrypted (adkfk1$) key
//   unlocked   — does this browser hold a cookie that still decrypts them
// The unlock modal is shown when `configured && !unlocked`, or when any API
// call returns 409 remote-keys-locked.
//
// A non-sensitive copy is mirrored to localStorage purely for flash-free first
// paint. It holds NO key: forging `unlocked:true` reveals nothing — every
// remote call still fails server-side without the cookie. The server is
// reconciled on boot via GET /api/hosts/keys/status.

const STORAGE_KEY = 'admin-toolkit:hostKeyUnlock';

interface HostKeyUnlockState {
  configured: boolean;
  unlocked: boolean;
}

const INITIAL: HostKeyUnlockState = { configured: false, unlocked: false };

function readHint(): HostKeyUnlockState {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY);
    if (!raw) return INITIAL;
    const p = JSON.parse(raw) as Partial<HostKeyUnlockState>;
    return { configured: p.configured === true, unlocked: p.unlocked === true };
  } catch {
    return INITIAL;
  }
}

function persist(state: HostKeyUnlockState): void {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* localStorage unavailable */
  }
}

const store = createSyncStore<HostKeyUnlockState>(readHint());

function set(next: HostKeyUnlockState): void {
  store.set(next);
  persist(next);
}

/** Modal success: the backend has set the cookie. */
export function markUnlocked(): void {
  set({ configured: true, unlocked: true });
}

/** Server returned 409 remote-keys-locked (cookie missing/expired/wrong salt). */
export function markLockedFromServer(): void {
  set({ ...store.get(), configured: true, unlocked: false });
}

/** Settings "Forget on this device": clear the cookie, then lock the UI. */
export async function forgetHostKey(): Promise<void> {
  try {
    await fetchJson('/api/hosts/keys/lock', { method: 'POST' });
  } catch {
    /* clear the UI regardless — the cookie is HttpOnly, server is source of truth */
  }
  set({ ...store.get(), unlocked: false });
}

let hydrated = false;
/** Reconcile UI state with the cookie on boot (runs once). */
export async function hydrateHostKeyStatus(): Promise<void> {
  if (hydrated) return;
  hydrated = true;
  try {
    const s = await fetchJson<{ configured: boolean; unlocked: boolean }>(
      '/api/hosts/keys/status',
    );
    set({ configured: !!s.configured, unlocked: !!s.unlocked });
  } catch {
    hydrated = false; // allow a retry if the boot probe failed
  }
}

/** React hook — full state for Settings + the boot/locked modal trigger. */
export function useHostKeyState(): HostKeyUnlockState {
  return store.use();
}

/** Non-hook getter for non-React callers. */
export function getHostKeyState(): HostKeyUnlockState {
  return store.get();
}
