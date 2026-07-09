import { useEffect, useMemo } from 'react';
import { useDiag } from '../context/DiagContext';
import { MODULES } from '../utils/moduleRegistry';
import {
  resolveModuleAvailability,
  type ModuleAvailabilityInputs,
} from '../utils/pageAvailability';
import { clusterAvailabilityStore } from '../state/clusterAvailabilityStore';
import { imageCleanerDetectScan } from '../state/imageCleanerStore';
import { dbHealthConnectionsStore } from '../state/dbHealthConnectionsStore';
import { getActiveHostId } from '../state/hostStore';
import type { PageId } from '../types';

// Per-host sticky seed: once a module settles as unavailable it is remembered,
// so the next session's sidebar starts correct instead of popping the module
// out mid-startup. Settled signals always win over the seed (both directions).
const STORAGE_PREFIX = 'admin-toolkit:hiddenModules:';

function loadSeed(hostId: string): ReadonlySet<PageId> {
  try {
    const raw = localStorage.getItem(STORAGE_PREFIX + hostId);
    if (!raw) return new Set();
    const arr = JSON.parse(raw) as unknown;
    return new Set(Array.isArray(arr) ? (arr as PageId[]) : []);
  } catch {
    return new Set();
  }
}

/** Pages hidden from the nav surfaces (Sidebar, ⌘K) because their module is
 * definitively not applicable on this host. PageRouter never consumes this —
 * an open page is never yanked away; deep links keep working. */
export function useModuleAvailability(): ReadonlySet<PageId> {
  const { state } = useDiag();
  const { parsedData } = state;
  const isApi = state.dataSource === 'api';
  const cluster = clusterAvailabilityStore.use();
  const detect = imageCleanerDetectScan.store.use();
  const db = dbHealthConnectionsStore.use();
  const hostId = getActiveHostId();

  const inputs: ModuleAvailabilityInputs = useMemo(
    () => ({
      clusters: parsedData.clusters,
      clusterCount: cluster.count,
      clusterCountLoaded: cluster.loaded,
      containerExecDefaults: parsedData.containerExecDefaults,
      registryDetectSettled: !detect.loading && detect.data != null,
      registryProvider: detect.data?.provider ?? null,
      llmAuditLoading: parsedData.llmAuditLoading,
      dbHealthLoaded: db.loaded,
      dbHealthErrored: db.error != null,
      dbHealthConfiguredConnection: db.configuredConnection,
    }),
    [
      parsedData.clusters,
      parsedData.containerExecDefaults,
      parsedData.llmAuditLoading,
      cluster.count,
      cluster.loaded,
      detect.loading,
      detect.data,
      db.loaded,
      db.error,
      db.configuredConnection,
    ],
  );

  const { hidden, persistPayload } = useMemo(() => {
    // The seed only serves API sessions: a zip import describes some other
    // instance's data and must neither read nor write the host's memory.
    const seed = isApi ? loadSeed(hostId) : new Set<PageId>();
    const hiddenSet = new Set<PageId>();
    const persist: PageId[] = [];
    for (const mod of MODULES) {
      if (mod.availability === 'always') continue;
      const availability = resolveModuleAvailability(mod.availability, inputs);
      if (availability === 'unavailable') {
        hiddenSet.add(mod.id);
        persist.push(mod.id);
      } else if (availability === 'unknown' && seed.has(mod.id)) {
        // Not settled yet this session — trust last session's verdict.
        hiddenSet.add(mod.id);
        persist.push(mod.id);
      }
    }
    return { hidden: hiddenSet, persistPayload: JSON.stringify(persist.sort()) };
  }, [isApi, hostId, inputs]);

  useEffect(() => {
    if (!isApi) return;
    try {
      localStorage.setItem(STORAGE_PREFIX + hostId, persistPayload);
    } catch {
      /* localStorage unavailable — gating still works, just not sticky */
    }
  }, [isApi, hostId, persistPayload]);

  return hidden;
}
