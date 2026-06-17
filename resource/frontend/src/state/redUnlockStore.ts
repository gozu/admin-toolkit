import { createSyncStore } from './createSyncStore';
import { fetchJson } from '../utils/api';

// Advanced-action unlock state. The actual bearer token lives in an HttpOnly
// cookie (`admin_toolkit_red`, set/cleared by the backend) — JS can't read it,
// so XSS can't steal it, and it rides every same-origin request automatically.
//
// This store only mirrors the *UI* state:
//   authed    — does this browser hold a valid unlock cookie (server truth)
//   expiresAt — epoch ms the cookie expires (for display)
//   showRed   — does the user currently want the red pages shown (view pref)
// Red pages are visible iff `authed && showRed`. Hiding them keeps the cookie,
// so revealing again never re-prompts; only "Forget on this device" clears it.
//
// A non-sensitive copy of this state is mirrored to localStorage purely for a
// flash-free first paint. It holds NO token: forging `authed:true` only reveals
// UI — every advanced endpoint is still cookie-gated server-side. The server
// is reconciled on boot via GET /api/auth/red/status.

const STORAGE_KEY = 'admin-toolkit:redUnlock';

interface RedUnlockState {
  authed: boolean;
  expiresAt: number; // epoch ms; 0 = none
  showRed: boolean;
}

const LOCKED: RedUnlockState = { authed: false, expiresAt: 0, showRed: true };

function readHint(): RedUnlockState {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY);
    if (!raw) return LOCKED;
    const p = JSON.parse(raw) as Partial<RedUnlockState>;
    const expiresAt = typeof p.expiresAt === 'number' ? p.expiresAt : 0;
    return {
      authed: p.authed === true && expiresAt > Date.now(),
      expiresAt,
      showRed: p.showRed !== false, // default shown
    };
  } catch {
    return LOCKED;
  }
}

function persist(state: RedUnlockState): void {
  try {
    if (state.authed && state.expiresAt > Date.now()) {
      globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(state));
    } else {
      globalThis.localStorage?.removeItem(STORAGE_KEY);
    }
  } catch {
    /* localStorage unavailable */
  }
}

const store = createSyncStore<RedUnlockState>(readHint());

function isLive(s: RedUnlockState): boolean {
  return s.authed && s.expiresAt > Date.now();
}

function set(next: RedUnlockState): void {
  store.set(next);
  persist(next);
}

/** Modal success: the backend has set the cookie; reveal the red pages. */
export function markUnlocked(expiresAt: number): void {
  set({ authed: true, expiresAt, showRed: true });
}

/** Toolbar pill: show/hide the red pages without touching the cookie. */
export function toggleShowRed(): void {
  set({ ...store.get(), showRed: !store.get().showRed });
}

/** Server rejected an advanced call (cookie missing/expired/rotated). */
export function markLockedFromServer(): void {
  set(LOCKED);
}

/** Settings "Forget on this device": clear the cookie, then lock the UI. */
export async function forgetRed(): Promise<void> {
  try {
    await fetchJson('/api/auth/red/lock', { method: 'POST' });
  } catch {
    /* clear the UI regardless — the cookie is HttpOnly, server is source of truth */
  }
  set(LOCKED);
}

let hydrated = false;
/** Reconcile UI state with the cookie on boot (runs once). */
export async function hydrateRedStatus(): Promise<void> {
  if (hydrated) return;
  hydrated = true;
  try {
    const s = await fetchJson<{ unlocked: boolean; expiresAt: number }>('/api/auth/red/status');
    const cur = store.get();
    set({
      authed: !!s.unlocked,
      expiresAt: s.unlocked ? s.expiresAt : 0,
      showRed: cur.showRed,
    });
  } catch {
    hydrated = false; // allow a retry if the boot probe failed
  }
}

/** React hook — Sidebar visibility: authed AND the user wants them shown. */
export function useRedVisible(): boolean {
  const s = store.use();
  return isLive(s) && s.showRed;
}

/** React hook — full state for the toolbar pill and Settings. */
export function useRedState(): { authed: boolean; showRed: boolean; expiresAt: number } {
  const s = store.use();
  const live = isLive(s);
  return { authed: live, showRed: s.showRed, expiresAt: live ? s.expiresAt : 0 };
}

/** Non-hook getter — same shape as useRedState(), for non-React callers
 *  (e.g. the diagnostic-bundle builder) that need a one-shot snapshot. */
export function getRedState(): { authed: boolean; showRed: boolean; expiresAt: number } {
  const s = store.get();
  const live = isLive(s);
  return { authed: live, showRed: s.showRed, expiresAt: live ? s.expiresAt : 0 };
}
