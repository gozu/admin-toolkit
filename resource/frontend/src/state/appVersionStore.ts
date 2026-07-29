import { createSyncStore } from './createSyncStore';

// Version of the plugin serving this webapp, reported by /api/mode at boot.
// Fetched at runtime (not baked in via a vite define) so version-only bumps
// leave the committed resource/dist/ bundle byte-identical across deploys.
// Not session-scoped: this is the local webapp's own version, independent of
// which remote host is selected.
export const appVersionStore = createSyncStore<string>('');

export function getAppVersion(): string {
  return appVersionStore.get();
}

export function useAppVersion(): string {
  return appVersionStore.use();
}

export interface BackendFreshness {
  /** False until /api/mode has answered — nothing is claimed before that. */
  checked: boolean;
  /** The webapp's Python backend is running code from a different release than the installed plugin. */
  stale: boolean;
  /** Installed plugin version (what this frontend was served from). */
  installedVersion: string;
  /** Version the running backend was built from; '' when it is too old to report one. */
  runningVersion: string;
}

// DSS does not restart webapp backends when a plugin is updated, so an upgrade
// leaves the new frontend talking to the previous release's Python until an
// admin restarts it by hand. /api/mode reports both versions; this store holds
// the verdict for the app-wide gate. Not session-scoped: it describes the local
// webapp process, not the selected host.
export const backendFreshnessStore = createSyncStore<BackendFreshness>({
  checked: false,
  stale: false,
  installedVersion: '',
  runningVersion: '',
});

export function useBackendFreshness(): BackendFreshness {
  return backendFreshnessStore.use();
}
