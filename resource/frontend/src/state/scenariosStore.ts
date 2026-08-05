import { createModuleScanStore } from './createModuleScanStore';
import type { ScenarioChainIssue, ScenarioRow, ScenariosResult } from '../types';

type ScenariosEvent =
  | { type: 'error'; error: string }
  | { type: 'inventory'; projectsToScan: number; serverTz: string | null; usersChecked: boolean }
  | { type: 'project'; scenarios: ScenarioRow[]; scanned: number }
  | {
      type: 'done';
      projectsScanned: number;
      failedProjects: { projectKey: string; error: string }[];
      chainIssues: ScenarioChainIssue[] | null;
    };

const EMPTY: ScenariosResult = {
  scenarios: [],
  projectsToScan: 0,
  projectsScanned: 0,
  failedProjects: [],
  serverTz: null,
  usersChecked: false,
  chainIssues: null,
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
        return {
          type: 'inventory',
          projectsToScan: Number(data.projectsToScan) || 0,
          serverTz: typeof data.serverTz === 'string' ? data.serverTz : null,
          usersChecked: data.usersChecked === true,
        };
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
          chainIssues: Array.isArray(data.chainIssues)
            ? (data.chainIssues as ScenarioChainIssue[])
            : null,
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
          data: {
            ...EMPTY,
            projectsToScan: ev.projectsToScan,
            serverTz: ev.serverTz,
            usersChecked: ev.usersChecked,
          },
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
        // Chain verdicts need the whole estate, so they arrive here and are
        // folded back onto rows already delivered. A scenario with several
        // bad follow triggers keeps the worst verdict (missing > dormant);
        // an incomplete sweep ships null and every row stays undetermined.
        const issueByKey = new Map<string, { kind: 'missing' | 'dormant'; target: string }>();
        for (const issue of ev.chainIssues ?? []) {
          const key = `${issue.projectKey}/${issue.id}`;
          const existing = issueByKey.get(key);
          if (!existing || (existing.kind === 'dormant' && issue.kind === 'missing')) {
            issueByKey.set(key, {
              kind: issue.kind,
              target: `${issue.targetProjectKey}.${issue.targetScenarioId}`,
            });
          }
        }
        return {
          data: {
            ...current,
            projectsScanned: ev.projectsScanned,
            failedProjects: ev.failedProjects,
            chainIssues: ev.chainIssues,
            scenarios: current.scenarios.map((row) => ({
              ...row,
              chainIssue:
                ev.chainIssues === null
                  ? null
                  : (issueByKey.get(`${row.projectKey}/${row.id}`) ?? null),
            })),
          },
          scanPhase: 'complete',
          scanMessage: `${current.scenarios.length} scenario${current.scenarios.length === 1 ? '' : 's'} across ${ev.projectsScanned} projects`,
          progressPct: 100,
        };
      }
    }
  },
});
