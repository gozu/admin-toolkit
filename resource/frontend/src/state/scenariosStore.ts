import { createModuleScanStore } from './createModuleScanStore';
import type { ScenarioRow, ScenariosResult } from '../types';

type ScenariosEvent =
  | { type: 'error'; error: string }
  | { type: 'inventory'; projectsToScan: number }
  | { type: 'project'; scenarios: ScenarioRow[]; scanned: number }
  | {
      type: 'done';
      projectsScanned: number;
      failedProjects: { projectKey: string; error: string }[];
    };

const EMPTY: ScenariosResult = {
  scenarios: [],
  projectsToScan: 0,
  projectsScanned: 0,
  failedProjects: [],
};

export const scenariosScan = createModuleScanStore<ScenariosResult, ScenariosEvent>({
  loadingField: 'scenariosLoading',
  streamEndpoint: '/api/scenarios/scan',
  parseEvent: (event, payload) => {
    const data = payload as Record<string, unknown>;
    switch (event) {
      case 'error':
        return { type: 'error', error: String(data.error || 'Scenario scan failed') };
      case 'inventory':
        return { type: 'inventory', projectsToScan: Number(data.projectsToScan) || 0 };
      case 'project':
        return {
          type: 'project',
          scenarios: (data.scenarios as ScenarioRow[]) ?? [],
          scanned: Number(data.scanned) || 0,
        };
      case 'done':
        return {
          type: 'done',
          projectsScanned: Number(data.projectsScanned) || 0,
          failedProjects:
            (data.failedProjects as { projectKey: string; error: string }[]) ?? [],
        };
      default:
        return null;
    }
  },
  reduce: (state, ev) => {
    switch (ev.type) {
      case 'error':
        return { error: ev.error };
      case 'inventory':
        return {
          data: { ...EMPTY, projectsToScan: ev.projectsToScan },
          total: ev.projectsToScan,
          scanPhase: 'scanning',
          scanMessage: `Scanning ${ev.projectsToScan} project${ev.projectsToScan === 1 ? '' : 's'} for scenarios…`,
          progressPct: 2,
        };
      case 'project': {
        const current = state.data ?? EMPTY;
        const next: ScenariosResult = {
          ...current,
          scenarios: ev.scenarios.length
            ? [...current.scenarios, ...ev.scenarios]
            : current.scenarios,
          projectsScanned: ev.scanned,
        };
        const total = current.projectsToScan || state.total || 0;
        return {
          data: next,
          scanPhase: 'scanning',
          scanMessage: `Scanned ${ev.scanned} of ${total || ev.scanned} projects · ${next.scenarios.length} scenario${next.scenarios.length === 1 ? '' : 's'}`,
          progressPct: total > 0 ? Math.min(99, 2 + (ev.scanned / total) * 97) : 50,
        };
      }
      case 'done': {
        const current = state.data ?? EMPTY;
        return {
          data: {
            ...current,
            projectsScanned: ev.projectsScanned,
            failedProjects: ev.failedProjects,
          },
          scanPhase: 'complete',
          scanMessage: `${current.scenarios.length} scenario${current.scenarios.length === 1 ? '' : 's'} across ${ev.projectsScanned} projects`,
          progressPct: 100,
        };
      }
    }
  },
});
