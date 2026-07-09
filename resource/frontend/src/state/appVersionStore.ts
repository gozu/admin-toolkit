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
