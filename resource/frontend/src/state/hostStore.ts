import { createSyncStore } from './createSyncStore';
import { bumpSessionEpoch } from './sessionCache';
import type { DssHost } from '../types';

const LOCAL_HOST: DssHost = { id: 'local', label: 'Local DSS', url: '' };
const STORAGE_KEY = 'admin-toolkit:activeHostId';

interface HostState {
  hosts: DssHost[];
  activeId: string;
  loaded: boolean;
}

function readPersistedId(): string {
  try {
    return globalThis.localStorage?.getItem(STORAGE_KEY) || 'local';
  } catch {
    return 'local';
  }
}

function persistId(id: string): void {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, id);
  } catch {
    /* localStorage unavailable */
  }
}

export const hostStore = createSyncStore<HostState>({
  hosts: [LOCAL_HOST],
  activeId: readPersistedId(),
  loaded: false,
});

export function getActiveHostId(): string {
  return hostStore.get().activeId;
}

export function getActiveHost(): DssHost {
  const { hosts, activeId } = hostStore.get();
  return hosts.find((h) => h.id === activeId) ?? LOCAL_HOST;
}

export function setHosts(hosts: DssHost[]): void {
  const normalized = hosts.length === 0 || hosts[0].id !== 'local'
    ? [LOCAL_HOST, ...hosts.filter((h) => h.id !== 'local')]
    : hosts;
  const { activeId } = hostStore.get();
  const stillValid = normalized.some((h) => h.id === activeId);
  hostStore.patch({
    hosts: normalized,
    activeId: stillValid ? activeId : 'local',
    loaded: true,
  });
}

export function setActiveHost(id: string): void {
  const { hosts, activeId } = hostStore.get();
  if (id === activeId) return;
  if (!hosts.some((h) => h.id === id)) return;
  bumpSessionEpoch();
  hostStore.patch({ activeId: id });
  persistId(id);
}
