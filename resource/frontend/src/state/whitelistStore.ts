import { createSyncStore } from './createSyncStore';
import { fetchJson } from '../utils/api';
import { getActiveHostId } from './hostStore';
import type { FindingWhitelistEntry } from '../hooks/useHealthScore';

// Per-item finding whitelist (false-positive doctrine): whitelisted items are
// exempted inside the health-score scorers — factor score AND issue membership.
// The store is hub-global (entries carry the host they apply to); the score
// consumes only the active host's slice via activeHostWhitelist().

interface WhitelistState {
  entries: FindingWhitelistEntry[];
  loaded: boolean;
  loading: boolean;
  error: string | null;
}

export const whitelistStore = createSyncStore<WhitelistState>({
  entries: [],
  loaded: false,
  loading: false,
  error: null,
});

export async function loadWhitelist(): Promise<void> {
  if (whitelistStore.get().loading) return;
  whitelistStore.patch({ loading: true, error: null });
  try {
    const res = await fetchJson<{ entries: FindingWhitelistEntry[] }>('/api/whitelist');
    whitelistStore.patch({ entries: res.entries ?? [], loaded: true, loading: false });
  } catch (e) {
    whitelistStore.patch({ loading: false, loaded: true, error: e instanceof Error ? e.message : String(e) });
  }
}

/** Entries applying to the active host (host '*' matches everywhere). */
export function activeHostWhitelist(entries: FindingWhitelistEntry[]): FindingWhitelistEntry[] {
  const active = getActiveHostId();
  return entries.filter((e) => (e.host || 'local') === active || e.host === '*');
}

export async function addWhitelistEntry(
  rule: string,
  item: string,
  note?: string,
): Promise<void> {
  const res = await fetchJson<{ entries: FindingWhitelistEntry[] }>('/api/whitelist/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rule, item, host: getActiveHostId(), note: note || undefined }),
  });
  whitelistStore.patch({ entries: res.entries ?? [], loaded: true });
}

export async function removeWhitelistEntry(entry: FindingWhitelistEntry): Promise<void> {
  const res = await fetchJson<{ entries: FindingWhitelistEntry[] }>('/api/whitelist/remove', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rule: entry.rule, item: entry.item, host: entry.host || 'local' }),
  });
  whitelistStore.patch({ entries: res.entries ?? [], loaded: true });
}

// Load once at module init so the score has the entries as early as possible.
void loadWhitelist();
