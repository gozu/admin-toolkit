import { createModuleScanStore } from './createModuleScanStore';
import type { ContainerExecScanResult } from '../types';

type ContainerExecEvent =
  | { type: 'error'; error: string }
  | { type: 'init'; total: number; cached: boolean }
  | { type: 'progress'; scanned: number; projectKey: string }
  | { type: 'done'; result: ContainerExecScanResult };

let totalProjects = 0;

export const containerExecsScan = createModuleScanStore<ContainerExecScanResult, ContainerExecEvent>({
  loadingField: 'containerExecsLoading',
  streamEndpoint: '/api/container-execs/stream',
  fallbackEndpoint: '/api/container-execs',
  parseEvent: (event, payload) => {
    const data = payload as Record<string, unknown>;
    switch (event) {
      case 'error':
        return { type: 'error', error: String(data.error || 'Container exec scan failed') };
      case 'init':
        return { type: 'init', total: Number(data.total) || 0, cached: Boolean(data.cached) };
      case 'progress':
        return {
          type: 'progress',
          scanned: Number(data.scanned) || 0,
          projectKey: String(data.projectKey || ''),
        };
      case 'done':
        return { type: 'done', result: data as unknown as ContainerExecScanResult };
      default:
        return null;
    }
  },
  reduce: (_state, ev) => {
    switch (ev.type) {
      case 'error':
        return { error: ev.error };
      case 'init':
        totalProjects = ev.total;
        return {
          scanPhase: ev.cached ? 'cached' : 'scanning_projects',
          scanMessage: ev.cached
            ? 'Loading cached container execution results...'
            : `Scanning ${ev.total.toLocaleString()} projects...`,
          progressPct: ev.cached ? 90 : 2,
        };
      case 'progress': {
        const pct = totalProjects > 0
          ? Math.min(99, Math.max(2, (ev.scanned / totalProjects) * 100))
          : 50;
        return {
          scanPhase: 'scanning_projects',
          scanMessage: ev.projectKey
            ? `Scanned ${ev.scanned}/${totalProjects}: ${ev.projectKey}`
            : `Scanned ${ev.scanned}/${totalProjects} projects`,
          progressPct: pct,
        };
      }
      case 'done':
        return {
          data: ev.result,
          scanPhase: 'complete',
          scanMessage: 'Container execution scan complete.',
          progressPct: 100,
        };
    }
  },
});
