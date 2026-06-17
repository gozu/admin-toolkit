import { createModuleScanStore } from './createModuleScanStore';
import type { CruCostData } from '../types';

type CruEvent =
  | { type: 'error'; error: string }
  | { type: 'init'; cached: boolean; message: string }
  | { type: 'done'; result: CruCostData };

export const projectCostScan = createModuleScanStore<CruCostData, CruEvent>({
  loadingField: 'projectCostLoading',
  streamEndpoint: '/api/cru/stream',
  fallbackEndpoint: '/api/cru',
  parseEvent: (event, payload) => {
    const data = payload as Record<string, unknown>;
    switch (event) {
      case 'error':
        return { type: 'error', error: String(data.error || 'CRU audit parse failed') };
      case 'init':
        return {
          type: 'init',
          cached: Boolean(data.cached),
          message: String(data.message || 'Parsing host audit logs…'),
        };
      case 'done':
        return { type: 'done', result: data as unknown as CruCostData };
      default:
        return null;
    }
  },
  reduce: (_state, ev) => {
    switch (ev.type) {
      case 'error':
        return { error: ev.error };
      case 'init':
        return {
          scanPhase: ev.cached ? 'cached' : 'parsing',
          scanMessage: ev.message,
          // Coarse, indeterminate: one blocking macro call, no per-file progress.
          progressPct: ev.cached ? 90 : 5,
        };
      case 'done':
        return {
          data: ev.result,
          scanPhase: 'complete',
          scanMessage: 'Compute usage parsed.',
          progressPct: 100,
        };
    }
  },
});
