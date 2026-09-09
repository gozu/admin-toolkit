import { createModuleScanStore } from './createModuleScanStore';
import type { SqlPushdownOwnerGroup } from '../types';

export interface SqlPushdownScanState {
  total: number | null;
  scanned: number | null;
  ownerGroups: SqlPushdownOwnerGroup[];
  status: 'idle' | 'scanning' | 'done' | 'error';
  error: string | null;
  elapsedMs: number | null;
  scanErrors?: { projectKey: string; area: string; error: string }[];
  failedProjectCount?: number;
  scannedProjectCount?: number;
  // Stored timestamps so lifecycle resolution stays pure at render time.
  startedAt: string | null;
  finishedAt: string | null;
}

const INITIAL_STATE: SqlPushdownScanState = {
  total: null,
  scanned: null,
  ownerGroups: [],
  status: 'idle',
  error: null,
  elapsedMs: null,
  startedAt: null,
  finishedAt: null,
};

type Event = { event: string; data: Record<string, unknown> };
const scan = createModuleScanStore<Partial<SqlPushdownScanState>, Event>({
  loadingField: 'projectComputeLoading',
  streamEndpoint: '/api/projects/sql_pushdown_audit',
  parseEvent: (event, data) => ({ event, data: data as Record<string, unknown> }),
  reduce: (state, { event, data }) => {
    if (event === 'error') return { error: String(data.error || 'Scan failed') };
    if (event === 'init') return { total: Number(data.total), scanPhase: 'scanning' };
    if (event === 'progress') return {
      data: { ...state.data, scanned: Number(data.scanned) },
      progressPct: state.total ? Math.min(99, Number(data.scanned) / state.total * 100) : 0,
      scanMessage: `Scanned ${data.scanned} / ${state.total ?? '?'} projects`,
    };
    if (event === 'done') return {
      progressPct: 100, scanPhase: 'complete',
      data: {
        ownerGroups: (data.ownerGroups || []) as SqlPushdownOwnerGroup[],
        scanned: state.total, elapsedMs: Number(data.total_ms) || null,
        scanErrors: (data.scanErrors || []) as SqlPushdownScanState['scanErrors'],
        failedProjectCount: Number(data.failedProjectCount) || 0,
        scannedProjectCount: Number(data.scannedProjectCount) || 0,
      },
    };
    return {};
  },
});
let previous: ReturnType<typeof scan.store.get> | undefined;
let snapshot = INITIAL_STATE;
export function getSqlPushdownScan(): SqlPushdownScanState {
  const s = scan.store.get();
  if (previous !== s) {
    previous = s;
    snapshot = { ...INITIAL_STATE, ...s.data, total: s.total ?? null,
      status: s.loading ? 'scanning' : s.error ? 'error' : s.scanStarted ? 'done' : 'idle',
      error: s.error, startedAt: s.startedAt, finishedAt: s.finishedAt };
  }
  return snapshot;
}
export const subscribeSqlPushdownScan = scan.store.subscribe;
export const startSqlPushdownScan = (priority = 0) => scan.load(false, priority);
export const restartSqlPushdownScan = () => { void scan.load(true); };
