import { createSyncStore } from './createSyncStore';
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
  store.patch({ loading: true, error: null });
  try {
    const data = await fetchJson<{ connections: PgConnection[]; configuredConnection?: string }>(
      '/api/tools/db-health/connections',
    );
    store.patch({
      connections: data.connections || [],
      configuredConnection: data.configuredConnection ?? null,
      loaded: true,
    });
  } catch (err) {
    store.patch({
      error: err instanceof Error ? err.message : String(err),
      loaded: true,
    });
  } finally {
    store.patch({ loading: false });
  }
}

async function fetchDetailsOnce(connection: string): Promise<void> {
  const q = encodeURIComponent(connection);
  patchDetail(connection, {
    loading: true,
    error: null,
    warnings: [],
  });
  try {
    const overview = await fetchJson<DbOverview>(`/api/tools/db-health/overview?connection=${q}`);
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
    patchDetail(connection, {
      overview,
      tables,
      perProject,
      warnings,
      loaded: true,
    });
  } catch (err) {
    patchDetail(connection, {
      error: err instanceof Error ? err.message : String(err),
      loaded: true,
    });
  } finally {
    patchDetail(connection, { loading: false });
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
    connectionsInflight = fetchConnectionsOnce().finally(() => {
      connectionsInflight = null;
    });
    return connectionsInflight;
  },
  loadDetails(connection: string, opts: { force?: boolean } = {}): Promise<void> {
    if (!connection) return Promise.resolve();
    const existing = detailInflightByConnection.get(connection);
    if (existing) return existing;
    const slot = store.get().detailsByConnection[connection];
    if (slot?.loaded && !opts.force) return Promise.resolve();
    const promise = fetchDetailsOnce(connection).finally(() => {
      detailInflightByConnection.delete(connection);
    });
    detailInflightByConnection.set(connection, promise);
    return promise;
  },
  async loadDefaultConfiguredDetails(): Promise<void> {
    await this.load();
    const { configuredConnection } = store.get();
    if (!configuredConnection) return;
    await this.loadDetails(configuredConnection);
  },
};
