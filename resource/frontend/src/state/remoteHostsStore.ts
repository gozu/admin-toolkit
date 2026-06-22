import { createSyncStore } from './createSyncStore';
import { fetchJson } from '../utils/api';
import { setHosts } from './hostStore';
import { reloadHostKeyStatus } from './hostKeyUnlockStore';
import type { DssHost } from '../types';

// Editable remote-host rows for Settings → Remote Hosts. The API key is never
// returned; `keyStatus` reflects how it is stored at rest. All HTTP goes through
// utils/api.ts so the host header is injected and 403/409 gates are handled.

export type KeyStatus = 'encrypted' | 'plaintext' | 'none';

export interface RemoteHostRow {
  name: string;
  label: string;
  url: string;
  verifyTls: boolean;
  backupProjectKey: string;
  keyStatus: KeyStatus;
}

interface RemoteHostsState {
  rows: RemoteHostRow[];
  loading: boolean;
  error: string | null;
}

export const remoteHostsStore = createSyncStore<RemoteHostsState>({
  rows: [],
  loading: false,
  error: null,
});

/** Load the editable host list (GET /api/hosts/presets). Advanced-gated. */
export async function loadHosts(): Promise<void> {
  remoteHostsStore.patch({ loading: true, error: null });
  try {
    const res = await fetchJson<{ ok: boolean; hosts: RemoteHostRow[] }>('/api/hosts/presets');
    remoteHostsStore.patch({ rows: res.hosts ?? [], loading: false, error: null });
  } catch (e) {
    remoteHostsStore.patch({
      loading: false,
      error: e instanceof Error ? e.message : 'Failed to load remote hosts.',
    });
  }
}

/** After any create/edit/delete, refresh the three things that depend on the
 *  preset list so the rest of the app stays consistent:
 *    1. this list,
 *    2. the top-bar host picker (GET /api/hosts → hostStore),
 *    3. the host-key unlock status (a first encrypted key flips `configured`). */
export async function refreshAfterMutation(): Promise<void> {
  await Promise.all([
    loadHosts(),
    (async () => {
      try {
        const hosts = await fetchJson<DssHost[]>('/api/hosts');
        setHosts(hosts);
      } catch {
        /* leave the picker as-is if the list re-fetch fails */
      }
    })(),
    reloadHostKeyStatus(),
  ]);
}

/** React hook — full state for the Remote Hosts card. */
export function useRemoteHosts(): RemoteHostsState {
  return remoteHostsStore.use();
}
