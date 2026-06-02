import { createModuleScanStore } from './createModuleScanStore';
import type { K8sInsightsScanResult } from '../types';

type K8sEvent =
  | { type: 'error'; error: string }
  | { type: 'init'; clusterId: string; totalProbes: number }
  | { type: 'probe'; name: string; ok: boolean; error: string | null; durationMs: number }
  | { type: 'done'; result: K8sInsightsScanResult };

let totalProbes = 0;
let probesSeen = 0;
let pendingClusterId = '';

export function setK8sScanClusterId(id: string): void {
  pendingClusterId = id;
}

export const k8sInsightsScan = createModuleScanStore<K8sInsightsScanResult, K8sEvent>({
  loadingField: 'k8sInsightsLoading',
  streamEndpoint: () => {
    const qs = pendingClusterId
      ? `?clusterId=${encodeURIComponent(pendingClusterId)}`
      : '';
    return `/api/k8s-insights/stream${qs}`;
  },
  parseEvent: (event, payload) => {
    const data = payload as Record<string, unknown>;
    switch (event) {
      case 'error':
        return { type: 'error', error: String(data.error || 'K8S Insights scan failed') };
      case 'init':
        return {
          type: 'init',
          clusterId: String(data.clusterId || ''),
          totalProbes: Number(data.totalProbes) || 0,
        };
      case 'probe':
        return {
          type: 'probe',
          name: String(data.name || ''),
          ok: Boolean(data.ok),
          error: data.error == null ? null : String(data.error),
          durationMs: Number(data.durationMs) || 0,
        };
      case 'done':
        return { type: 'done', result: data as unknown as K8sInsightsScanResult };
      default:
        return null;
    }
  },
  reduce: (_state, ev) => {
    switch (ev.type) {
      case 'error':
        return { error: ev.error };
      case 'init':
        totalProbes = ev.totalProbes;
        probesSeen = 0;
        return {
          scanPhase: 'probing',
          scanMessage: `Probing cluster ${ev.clusterId || '(auto)'}…`,
          progressPct: 2,
        };
      case 'probe': {
        probesSeen += 1;
        const pct = totalProbes > 0
          ? Math.min(95, Math.max(5, (probesSeen / totalProbes) * 90))
          : 50;
        const labelOk = ev.ok ? 'ok' : 'fail';
        return {
          scanPhase: 'probing',
          scanMessage: `${ev.name} (${labelOk}, ${ev.durationMs}ms) ${probesSeen}/${totalProbes}`,
          progressPct: pct,
        };
      }
      case 'done':
        return {
          data: ev.result,
          scanPhase: 'complete',
          scanMessage: ev.result.ok
            ? `Audit complete: ${ev.result.findingsCount} finding(s).`
            : `Audit failed: ${ev.result.error || 'unknown error'}`,
          progressPct: 100,
        };
    }
  },
});
