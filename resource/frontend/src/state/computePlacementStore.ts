import { createModuleScanStore } from './createModuleScanStore';
import type { ComputePlacementScanResult } from '../types';

type ComputePlacementEvent =
  | { type: 'error'; error: string }
  | { type: 'init'; total: number; cached: boolean }
  | { type: 'progress'; scanned: number; total: number; projectKey: string }
  | { type: 'done'; result: ComputePlacementScanResult };

export const computePlacementScan = createModuleScanStore<ComputePlacementScanResult, ComputePlacementEvent>({
  loadingField: 'computePlacementLoading',
  streamEndpoint: '/api/compute-placement/stream',
  fallbackEndpoint: '/api/compute-placement',
  parseEvent: (event, payload) => {
    const data = payload as Record<string, unknown>;
    switch (event) {
      case 'error':
        return { type: 'error', error: String(data.error || 'Compute placement scan failed') };
      case 'init':
        return { type: 'init', total: Number(data.total) || 0, cached: Boolean(data.cached) };
      case 'progress':
        return {
          type: 'progress',
          scanned: Number(data.scanned) || 0,
          total: Number(data.total) || 0,
          projectKey: String(data.projectKey || ''),
        };
      case 'done':
        return { type: 'done', result: data as unknown as ComputePlacementScanResult };
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
          total: ev.total,
          scanPhase: ev.cached ? 'cached' : 'scanning_projects',
          scanMessage: ev.cached
            ? 'Loading cached compute placement…'
            : `Resolving compute placement across ${ev.total.toLocaleString()} projects…`,
          progressPct: ev.cached ? 90 : 2,
        };
      case 'progress': {
        const total = ev.total || state.total || 0;
        const pct = total > 0 ? Math.min(99, Math.max(2, (ev.scanned / total) * 100)) : 50;
        return {
          scanPhase: 'scanning_projects',
          scanMessage: ev.projectKey
            ? `Scanned ${ev.scanned}/${total}: ${ev.projectKey}`
            : `Scanned ${ev.scanned}/${total} projects`,
          progressPct: pct,
        };
      }
      case 'done':
        return {
          data: ev.result,
          scanPhase: 'complete',
          scanMessage: 'Compute placement scan complete.',
          progressPct: 100,
        };
    }
  },
});
