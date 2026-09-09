import { getSessionEpoch, subscribeSessionEpoch } from './sessionCache';
import { createSyncStore, sessionWriter } from './createSyncStore';
import { fetchJson } from '../utils/api';

export interface PgConnection {
  name: string;
  type: string;
  host: string;
  port: number;
  db: string;
}

export interface DbOverview {
  dbSize: string;
  dbSizeBytes: number;
  version: string;
  tableCount: number;
  totalDeadTuples: number;
  totalLiveTuples: number;
  canWrite: boolean;
  queryMethod: string;
  warnings?: string[];
  driverLog?: string[];
}

export interface TableInfo {
  name: string;
  totalSize: string;
  totalSizeBytes: number;
  rowCount: number;
  deadTuples: number;
  bloatRatio: number;
  lastVacuum: string | null;
  lastAutovacuum: string | null;
  lastAnalyze: string | null;
}

export interface ProjectBreakdown {
  projectKey: string;
  sizeBytes: number;
  tableCount: number;
  rowCount: number;
}

export interface SystemBucket {
  tables: { name: string; rowCount: number; sizeBytes: number }[];
  totalBytes: number;
}

export interface PerProjectResponse {
  projects: ProjectBreakdown[];
  system: SystemBucket;
  isRuntimeDb: boolean;
  warnings?: string[];
}

export interface DbHealthDetails {
  overview: DbOverview | null;
  tables: TableInfo[];
  perProject: PerProjectResponse | null;
  warnings: string[];
}

interface DetailSlot extends DbHealthDetails {
  loading: boolean;
  error: string | null;
  loaded: boolean;
}

interface State {
  connections: PgConnection[];
  configuredConnection: string | null;
  loading: boolean;
  error: string | null;
  loaded: boolean;
  detailsByConnection: Record<string, DetailSlot>;
}

const EMPTY_DETAIL: DetailSlot = {
  overview: null,
  tables: [],
  perProject: null,
  warnings: [],
  loading: false,
  error: null,
  loaded: false,
};

const INITIAL: State = {
  connections: [],
  configuredConnection: null,
  loading: false,
  error: null,
  loaded: false,
  detailsByConnection: {},
};

const store = createSyncStore<State>(INITIAL, { sessionScoped: true });
let connectionsInflight: Promise<void> | null = null;
const detailInflightByConnection = new Map<string, Promise<void>>();

function patchDetail(connection: string, patch: Partial<DetailSlot>): void {
  const current = store.get();
  const prev = current.detailsByConnection[connection] || EMPTY_DETAIL;
  store.patch({
    detailsByConnection: {
      ...current.detailsByConnection,
      [connection]: { ...prev, ...patch },
    },
  });
}

async function fetchConnectionsOnce(): Promise<void> {
  const write = sessionWriter(store);
  write.patch({ loading: true, error: null });
  try {
    const data = await fetchJson<{ connections: PgConnection[]; configuredConnection?: string }>(
      '/api/tools/db-health/connections',
    );
    write.patch({
      connections: data.connections || [],
      configuredConnection: data.configuredConnection ?? null,
      loaded: true,
    });
  } catch (err) {
    write.patch({
      error: err instanceof Error ? err.message : String(err),
      loaded: true,
    });
  } finally {
    write.patch({ loading: false });
  }
}

async function fetchDetailsOnce(connection: string): Promise<void> {
  const write = sessionWriter(store);
  const patch = (value: Partial<DetailSlot>) => { if (write.current()) patchDetail(connection, value); };
  const q = encodeURIComponent(connection);
  patch({
    loading: true,
    error: null,
    warnings: [],
  });
  try {
    const overview = await fetchJson<DbOverview>(`/api/tools/db-health/overview?connection=${q}`);
    if (!write.current()) return;
    const [tablesResponse, perProject] = await Promise.all([
      fetchJson<{ tables: TableInfo[]; warnings?: string[] }>(
        `/api/tools/db-health/tables?connection=${q}`,
      ),
      fetchJson<PerProjectResponse>(`/api/tools/db-health/per-project?connection=${q}`),
    ]);
    const tables = tablesResponse.tables || [];
    const warnings = [
      ...(overview.warnings || []),
      ...(tablesResponse.warnings || []),
      ...(perProject.warnings || []),
    ];
    patch({
      overview,
      tables,
      perProject,
      warnings,
      loaded: true,
    });
  } catch (err) {
    patch({
      error: err instanceof Error ? err.message : String(err),
      loaded: true,
    });
  } finally {
    patch({ loading: false });
  }
}

export const dbHealthConnectionsStore = {
  use: store.use,
  get: store.get,
  getDetail(connection: string): DetailSlot {
    return store.get().detailsByConnection[connection] || EMPTY_DETAIL;
  },
  load(): Promise<void> {
    if (connectionsInflight) return connectionsInflight;
    if (store.get().loaded) return Promise.resolve();
    const epoch = getSessionEpoch();
    connectionsInflight = fetchConnectionsOnce().finally(() => {
      if (epoch === getSessionEpoch()) connectionsInflight = null;
    });
    return connectionsInflight;
  },
  loadDetails(connection: string, opts: { force?: boolean } = {}): Promise<void> {
    if (!connection) return Promise.resolve();
    const existing = detailInflightByConnection.get(connection);
    if (existing) return existing;
    const slot = store.get().detailsByConnection[connection];
    if (slot?.loaded && !opts.force) return Promise.resolve();
    const epoch = getSessionEpoch();
    const promise = fetchDetailsOnce(connection).finally(() => {
      if (epoch === getSessionEpoch()) detailInflightByConnection.delete(connection);
    });
    detailInflightByConnection.set(connection, promise);
    return promise;
  },
  async loadDefaultConfiguredDetails(): Promise<void> {
    const epoch = getSessionEpoch();
    await this.load();
    if (epoch !== getSessionEpoch()) return;
    const { configuredConnection } = store.get();
    if (!configuredConnection) return;
    await this.loadDetails(configuredConnection);
  },
};

subscribeSessionEpoch(() => { connectionsInflight = null; detailInflightByConnection.clear(); });
