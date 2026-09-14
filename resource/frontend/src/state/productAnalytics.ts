import { createSyncStore } from './createSyncStore';
import { getActiveHostId } from './hostStore';
import { subscribeSessionEpoch } from './sessionCache';
import { fetchJson } from '../utils/api';
import { createUsageTracker, usageUuid, type UsageEventName, type UsageProperties } from '../utils/usageEvents';

export interface UsageConfig {
  enabled: boolean;
  configured: boolean;
  available?: boolean;
  audience?: 'customer' | 'internal';
}
// Installation-wide preference, independent of the selected managed DSS host.
export const usageConfigStore = createSyncStore<UsageConfig | null>(null);
const trackerStore = createSyncStore<ReturnType<typeof createUsageTracker> | null>(null, { sessionScoped: true });
subscribeSessionEpoch(() => {
  // Ignore late lifecycle snapshots from the previous host/session while React
  // is committing the new session. Already queued events retain their old host.
  trackerStore.set(createUsageTracker(captureUsage, usageUuid, Date.now()));
});
interface PendingEvent {
  uuid: string;
  event: UsageEventName;
  timestamp: string;
  host_id: string;
  properties: UsageProperties;
}
let pending: PendingEvent[] = [];
let timer: ReturnType<typeof setTimeout> | undefined;
let configPromise: Promise<void> | null = null;
let started = false;
let sending = false;
let browserId = '';
let sessionId = '';

function ensureIdentity() {
  if (browserId) return;
  try {
    const stored = localStorage.getItem('admin-toolkit:usage-browser');
    browserId = stored && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(stored) ? stored : usageUuid();
    localStorage.setItem('admin-toolkit:usage-browser', browserId);
  } catch {
    browserId = usageUuid();
  }
  // One page-load session; no identity or page URLs in the identifier.
  sessionId = usageUuid();
}

export async function refreshUsageConfig(): Promise<void> {
  if (configPromise) return configPromise;
  configPromise = (async () => {
    try {
      const config = await fetchJson<UsageConfig>('/api/usage/config', { headers: { 'X-DSS-Host-Id': 'local' } });
      usageConfigStore.set(config);
      if (!config.enabled || !config.configured) pending = [];
    } catch {
      pending = [];
      usageConfigStore.set({ enabled: false, configured: false, available: false });
    }
  })().finally(() => { configPromise = null; });
  return configPromise;
}

export async function updateUsageEnabled(enabled: boolean): Promise<void> {
  const config = await fetchJson<UsageConfig>('/api/usage/config', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-DSS-Host-Id': 'local' },
    body: JSON.stringify({ enabled }),
  });
  usageConfigStore.set(config);
  pending = [];
}

export function captureUsage(event: UsageEventName, properties: UsageProperties = {}): void {
  if (!started) return;
  const config = usageConfigStore.get();
  if (config && (!config.enabled || !config.configured)) return;
  // Bound memory while configuration or the network is unavailable.
  if (pending.length >= 200) return;
  pending.push({ uuid: usageUuid(), event, properties,
    timestamp: new Date().toISOString(), host_id: getActiveHostId() });
  if (event === 'adtk_webapp_opened' || event === 'adtk_module_opened') {
    pending.push({ uuid: usageUuid(), event: 'adtk_activity', properties,
      timestamp: new Date().toISOString(), host_id: getActiveHostId() });
  }
  if (!timer) timer = setTimeout(() => { timer = undefined; void flushUsage(); }, 1500);
}

export async function flushUsage(keepalive = false): Promise<void> {
  if (sending || !pending.length) return;
  const config = usageConfigStore.get();
  if (!config?.enabled || !config.configured) return;
  ensureIdentity();
  const batch = pending.splice(0, 50);
  sending = true;
  try {
    const result = await fetchJson<{ enabled: boolean }>('/api/usage/events', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-DSS-Host-Id': 'local' },
      body: JSON.stringify({ browser_id: browserId, session_id: sessionId, events: batch }),
      keepalive, signal: AbortSignal.timeout(5000),
    });
    if (!result.enabled) { pending = []; usageConfigStore.set({ ...config, enabled: false }); }
  } catch {
    // Best effort: do not retain browsing activity on disk or retry indefinitely.
  } finally {
    sending = false;
    if (pending.length && !timer) timer = setTimeout(() => { timer = undefined; void flushUsage(); }, 1500);
  }
}

export function usageTracker() {
  let tracker = trackerStore.get();
  if (!tracker) { tracker = createUsageTracker(captureUsage); trackerStore.set(tracker); }
  return tracker;
}

export function startProductAnalytics(): void {
  if (started || typeof window === 'undefined') return;
  started = true;
  void refreshUsageConfig().then(() => { void flushUsage(); });
  captureUsage('adtk_webapp_opened');
  window.addEventListener('pagehide', () => { void flushUsage(true); });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') void flushUsage(true);
    else void refreshUsageConfig();
  });
}
