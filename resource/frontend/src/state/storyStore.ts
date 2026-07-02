/**
 * storyStore — bootstrap fetches for the four Story pages (experimental).
 *
 * Module Bootstrap Contract: pages call the load*() methods from a single
 * mount effect; data lives here (sessionScoped — the global Refresh button
 * resets it). User-action refetches (Provision, Run Now, window changes) are
 * explicit methods, not mount effects.
 */
import { createSyncStore } from './createSyncStore';
import { fetchJson } from '../utils/api';

export interface StoryIngestRun {
  instance_id: string;
  source: string;
  cursor_value: string | null;
  last_run_at: string | null;
  last_status: string | null;
  last_error: string | null;
  last_rows_written: number | null;
}

export interface StoryScenarioStatus {
  exists: boolean;
  active: boolean;
  triggerHour: number | null;
  reporterVerified: boolean;
  reporterShape: 'primary' | 'fallback' | null;
  lastRun: { outcome: string | null; start: number | string | null } | null;
  error?: string;
}

export interface StoryStatus {
  configured: boolean;
  connection: string;
  alertEmail: string;
  dbOk: boolean;
  dbError?: string;
  schemaVersion: number;
  ingest: StoryIngestRun[];
  hosts: { id: string; label: string }[];
  scenario: StoryScenarioStatus;
}

export interface StoryProvisionStep {
  step: string;
  status: string;
  message?: string;
}

export interface StoryProvisionResult {
  ok: boolean;
  steps: StoryProvisionStep[];
  reporterVerified: boolean;
  reporterShape: string | null;
}

export interface StoryActivityDay {
  day: string;
  instance_id: string;
  active_users: number;
  viewing_actions: number;
  developing_actions: number;
  developing_users: number;
}

export interface StoryActivityUserRow {
  day: string;
  instance_id: string;
  login: string;
  project_key: string;
  viewing_actions: number;
  developing_actions: number;
}

export interface StoryEventCountRow {
  day: string;
  instance_id: string;
  msg_type: string;
  event_count: number;
  taxonomy: string;
}

export interface StoryLicenseLatest {
  snapshot_date: string;
  instance_id: string;
  dss_version: string | null;
  license_kind: string | null;
  expires_on: string | null;
  users_total: number | null;
  addons: string | null;
}

export interface StoryLicenseCapRow {
  snapshot_date: string;
  instance_id: string;
  profile: string;
  cap: number | null;
  used: number | null;
}

export interface StoryInventoryTrendRow {
  snapshot_date: string;
  instance_id: string;
  object_type: string;
  object_count: number;
}

export interface StoryInventoryProjectRow {
  snapshot_date: string;
  instance_id: string;
  project_key: string;
  object_type: string;
  object_count: number;
}

interface Slot<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  loaded: boolean;
}

function emptySlot<T>(): Slot<T> {
  return { data: null, loading: false, error: null, loaded: false };
}

interface ActivityPayload {
  days: StoryActivityDay[];
  users: StoryActivityUserRow[];
  windowDays: number;
}

interface EventCountsPayload {
  rows: StoryEventCountRow[];
  windowDays: number;
}

interface LicensesPayload {
  latest: StoryLicenseLatest[];
  caps: StoryLicenseCapRow[];
}

interface InventoryPayload {
  trends: StoryInventoryTrendRow[];
  latestByProject: StoryInventoryProjectRow[];
  windowDays: number;
}

interface State {
  status: Slot<StoryStatus>;
  activity: Slot<ActivityPayload>;
  activityDays: number;
  eventCounts: Slot<EventCountsPayload>;
  licenses: Slot<LicensesPayload>;
  inventory: Slot<InventoryPayload>;
  provision: { running: boolean; result: StoryProvisionResult | null; error: string | null };
  runNow: { running: boolean; polling: boolean; runId: string | null; error: string | null };
}

const INITIAL: State = {
  status: emptySlot(),
  activity: emptySlot(),
  activityDays: 30,
  eventCounts: emptySlot(),
  licenses: emptySlot(),
  inventory: emptySlot(),
  provision: { running: false, result: null, error: null },
  runNow: { running: false, polling: false, runId: null, error: null },
};

const store = createSyncStore<State>(INITIAL, { sessionScoped: true });
const inflight = new Map<string, Promise<void>>();

type SlotKey = 'status' | 'activity' | 'eventCounts' | 'licenses' | 'inventory';

function patchSlot(key: SlotKey, patch: Partial<Slot<unknown>>): void {
  const current = store.get()[key] as Slot<unknown>;
  store.patch({ [key]: { ...current, ...patch } } as unknown as Partial<State>);
}

async function fetchSlot(key: SlotKey, url: string): Promise<void> {
  patchSlot(key, { loading: true, error: null });
  try {
    const data = await fetchJson<unknown>(url);
    patchSlot(key, { data, loaded: true });
  } catch (err) {
    patchSlot(key, {
      error: err instanceof Error ? err.message : String(err),
      loaded: true,
    });
  } finally {
    patchSlot(key, { loading: false });
  }
}

function loadOnce(key: SlotKey, url: string, force = false): Promise<void> {
  const existing = inflight.get(key);
  if (existing) return existing;
  if (store.get()[key].loaded && !force) return Promise.resolve();
  const promise = fetchSlot(key, url).finally(() => {
    inflight.delete(key);
  });
  inflight.set(key, promise);
  return promise;
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export const storyStore = {
  use: store.use,
  get: store.get,

  loadStatus(force = false): Promise<void> {
    return loadOnce('status', '/api/story/status', force);
  },

  loadActivity(days?: number, force = false): Promise<void> {
    const current = store.get();
    const windowDays = days ?? current.activityDays;
    if (windowDays !== current.activityDays) {
      store.patch({ activityDays: windowDays, activity: emptySlot(), eventCounts: emptySlot() });
      force = true;
    }
    return Promise.all([
      loadOnce('activity', `/api/story/user-activity?days=${windowDays}`, force),
      loadOnce('eventCounts', `/api/story/event-counts?days=${windowDays}`, force),
    ]).then(() => undefined);
  },

  loadLicenses(force = false): Promise<void> {
    return loadOnce('licenses', '/api/story/licenses', force);
  },

  loadInventory(force = false): Promise<void> {
    return loadOnce('inventory', '/api/story/inventory', force);
  },

  async provision(): Promise<void> {
    if (store.get().provision.running) return;
    store.patch({ provision: { running: true, result: null, error: null } });
    try {
      const result = await fetchJson<StoryProvisionResult>('/api/story/provision', {
        method: 'POST',
      });
      store.patch({ provision: { running: false, result, error: null } });
    } catch (err) {
      store.patch({
        provision: {
          running: false,
          result: null,
          error: err instanceof Error ? err.message : String(err),
        },
      });
    }
    await this.loadStatus(true);
  },

  /** Fire the collection scenario, then poll status while it runs — progress
   *  is durable in story.ingest_runs, so polling /status is all we need. */
  async runNow(): Promise<void> {
    if (store.get().runNow.running || store.get().runNow.polling) return;
    store.patch({ runNow: { running: true, polling: false, runId: null, error: null } });
    let runId: string | null = null;
    try {
      const response = await fetchJson<{ ok: boolean; runId: string | null }>(
        '/api/story/run-now',
        { method: 'POST' },
      );
      runId = response.runId ?? null;
    } catch (err) {
      store.patch({
        runNow: {
          running: false,
          polling: false,
          runId: null,
          error: err instanceof Error ? err.message : String(err),
        },
      });
      return;
    }
    store.patch({ runNow: { running: false, polling: true, runId, error: null } });
    // The scheduled run typically lands within a couple of minutes; poll the
    // (30s-cached) status endpoint and stop quietly — the grid keeps updating
    // on any later manual refresh.
    for (let i = 0; i < 24; i += 1) {
      await sleep(10000);
      if (!store.get().runNow.polling) return;
      await this.loadStatus(true);
    }
    store.patch({ runNow: { ...store.get().runNow, polling: false } });
  },

  stopPolling(): void {
    store.patch({ runNow: { ...store.get().runNow, polling: false } });
  },
};
