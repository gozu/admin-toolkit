import { fetchJson } from '../utils/api';
import { createModuleScanStore } from './createModuleScanStore';
import type {
  AppInstanceAttribution,
  AppInstanceRow,
  AppInstancesResult,
  AppRecipeRow,
  AppTemplateRow,
} from '../types';

type AppInstancesEvent =
  | { type: 'error'; error: string }
  | {
      type: 'inventory';
      apps: AppTemplateRow[];
      instances: AppInstanceRow[];
      attribution: AppInstanceAttribution;
      projectsToScan: number;
    }
  | { type: 'project'; recipes: AppRecipeRow[]; scanned: number }
  | {
      type: 'done';
      projectsScanned: number;
      keepInstanceOn: number;
      orphans: number | null;
      orphanKeys: string[];
      attachedKeys: string[];
      failedProjects: { projectKey: string; error: string }[];
    };

const EMPTY: AppInstancesResult = {
  apps: [],
  instances: [],
  recipes: [],
  attribution: { available: false },
  projectsToScan: 0,
  projectsScanned: 0,
  failedProjects: [],
  orphans: null,
};

export const appInstancesScan = createModuleScanStore<AppInstancesResult, AppInstancesEvent>({
  loadingField: 'appInstancesLoading',
  streamEndpoint: '/api/app-instances/scan',
  parseEvent: (event, payload) => {
    const data = payload as Record<string, unknown>;
    switch (event) {
      case 'error':
        return { type: 'error', error: String(data.error || 'App instance scan failed') };
      case 'inventory':
        return {
          type: 'inventory',
          apps: (data.apps as AppTemplateRow[]) ?? [],
          instances: (data.instances as AppInstanceRow[]) ?? [],
          attribution: (data.attribution as AppInstanceAttribution) ?? { available: false },
          projectsToScan: Number(data.projectsToScan) || 0,
        };
      case 'project':
        return {
          type: 'project',
          recipes: (data.recipes as AppRecipeRow[]) ?? [],
          scanned: Number(data.scanned) || 0,
        };
      case 'done':
        return {
          type: 'done',
          projectsScanned: Number(data.projectsScanned) || 0,
          keepInstanceOn: Number(data.keepInstanceOn) || 0,
          orphans: data.orphans === null || data.orphans === undefined ? null : Number(data.orphans),
          orphanKeys: (data.orphanKeys as string[]) ?? [],
          attachedKeys: (data.attachedKeys as string[]) ?? [],
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
      case 'inventory': {
        // The cheap half (two API calls + the macro) lands whole, so the
        // template and instance tables are usable while the recipe sweep runs.
        const instanceCount = ev.instances.length;
        return {
          data: {
            ...EMPTY,
            apps: ev.apps,
            instances: ev.instances,
            attribution: ev.attribution,
            projectsToScan: ev.projectsToScan,
          },
          total: ev.projectsToScan,
          scanPhase: 'scanning',
          scanMessage: `${instanceCount} app instance${instanceCount === 1 ? '' : 's'} found · scanning ${ev.projectsToScan} project${ev.projectsToScan === 1 ? '' : 's'} for App recipes…`,
          progressPct: 5,
        };
      }
      case 'project': {
        const current = state.data ?? EMPTY;
        const next: AppInstancesResult = {
          ...current,
          recipes: ev.recipes.length ? [...current.recipes, ...ev.recipes] : current.recipes,
          projectsScanned: ev.scanned,
        };
        const total = current.projectsToScan || state.total || 0;
        return {
          data: next,
          scanPhase: 'scanning',
          scanMessage: `Scanned ${ev.scanned} of ${total || ev.scanned} projects · ${next.recipes.length} App recipe${next.recipes.length === 1 ? '' : 's'}`,
          // Reserve the first 5% for the inventory phase already reported above.
          progressPct: total > 0 ? Math.min(99, 5 + (ev.scanned / total) * 94) : 50,
        };
      }
      case 'done': {
        const current = state.data ?? EMPTY;
        const on = ev.keepInstanceOn;
        // Orphan verdicts can only be settled once the recipe sweep is in, so
        // they arrive here and are folded back onto rows the inventory event
        // already delivered. Rows in neither list keep `orphan: null`
        // (undetermined) — never a default of false.
        const orphanSet = new Set(ev.orphanKeys);
        const attachedSet = new Set(ev.attachedKeys);
        return {
          data: {
            ...current,
            projectsScanned: ev.projectsScanned,
            failedProjects: ev.failedProjects,
            orphans: ev.orphans,
            instances: current.instances.map((instance) =>
              orphanSet.has(instance.projectKey)
                ? { ...instance, orphan: true }
                : attachedSet.has(instance.projectKey)
                  ? { ...instance, orphan: false }
                  : instance,
            ),
          },
          scanPhase: 'complete',
          scanMessage: `${current.instances.length} instance${current.instances.length === 1 ? '' : 's'} · ${on} recipe${on === 1 ? '' : 's'} keeping instances`,
          progressPct: 100,
        };
      }
    }
  },
});

/** Flip one App recipe's `keepInstance` (advanced-gated server-side) and patch
 *  the scanned row in place, so the table reflects the new state without
 *  re-running the whole sweep. Existing instance projects are untouched — this
 *  only stops future runs from adding more. */
export async function setKeepInstance(
  recipe: AppRecipeRow,
  keepInstance: boolean,
): Promise<void> {
  await fetchJson<{ ok: boolean }>('/api/app-instances/keep-instance', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      projectKey: recipe.projectKey,
      recipeName: recipe.name,
      keepInstance,
    }),
  });
  const current = appInstancesScan.store.get().data;
  if (!current) return;
  appInstancesScan.store.patch({
    data: {
      ...current,
      recipes: current.recipes.map((row: AppRecipeRow) =>
        row.fullId === recipe.fullId ? { ...row, keepInstance, error: null } : row,
      ),
    },
  });
}
