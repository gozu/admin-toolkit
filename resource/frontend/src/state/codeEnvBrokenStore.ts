import { createModuleScanStore } from './createModuleScanStore';
import type { BrokenEnvRow, CodeEnvBrokenResult } from '../types';

type BrokenScanEvent =
  | { type: 'error'; error: string }
  | { type: 'init'; total: number; sizesAvailable: boolean }
  | { type: 'env'; row: BrokenEnvRow }
  | { type: 'done'; total: number; failed: number; ok: number; indeterminate: number };

const EMPTY: CodeEnvBrokenResult = {
  rows: [],
  indeterminate: [],
  okCount: 0,
  total: 0,
  sizesAvailable: false,
};

export const codeEnvBrokenScan = createModuleScanStore<CodeEnvBrokenResult, BrokenScanEvent>({
  loadingField: 'codeEnvsBrokenLoading',
  streamEndpoint: '/api/code-envs/broken/scan',
  parseEvent: (event, payload) => {
    const data = payload as Record<string, unknown>;
    switch (event) {
      case 'error':
        return { type: 'error', error: String(data.error || 'Code env build scan failed') };
      case 'init':
        return {
          type: 'init',
          total: Number(data.total) || 0,
          sizesAvailable: Boolean(data.sizesAvailable),
        };
      case 'env':
        return { type: 'env', row: data as unknown as BrokenEnvRow };
      case 'done':
        return {
          type: 'done',
          total: Number(data.total) || 0,
          failed: Number(data.failed) || 0,
          ok: Number(data.ok) || 0,
          indeterminate: Number(data.indeterminate) || 0,
        };
      default:
        return null;
    }
  },
  reduce: (state, ev) => {
    switch (ev.type) {
      case 'error':
        return { error: ev.error };
      case 'init':
        return {
          data: { ...EMPTY, total: ev.total, sizesAvailable: ev.sizesAvailable },
          total: ev.total,
          scanPhase: 'scanning',
          scanMessage: `Reading build logs for ${ev.total} environment${ev.total === 1 ? '' : 's'}…`,
          progressPct: 2,
        };
      case 'env': {
        const current = state.data ?? EMPTY;
        // Healthy envs are counted, not retained — only failures and
        // indeterminate rows carry detail the page renders.
        const next: CodeEnvBrokenResult =
          ev.row.status === 'FAILED'
            ? { ...current, rows: [...current.rows, ev.row] }
            : ev.row.status === 'OK'
              ? { ...current, okCount: current.okCount + 1 }
              : { ...current, indeterminate: [...current.indeterminate, ev.row] };
        const scanned = next.rows.length + next.okCount + next.indeterminate.length;
        const total = next.total || state.total || 0;
        return {
          data: next,
          scanPhase: 'scanning',
          scanMessage: `Scanned ${scanned} of ${total || scanned}`,
          progressPct: total > 0 ? Math.min(99, (scanned / total) * 100) : 50,
        };
      }
      case 'done': {
        const current = state.data ?? EMPTY;
        return {
          data: { ...current, total: ev.total },
          scanPhase: 'complete',
          scanMessage: `${ev.total} environment${ev.total === 1 ? '' : 's'} scanned · ${ev.failed} broken`,
          progressPct: 100,
        };
      }
    }
  },
});
