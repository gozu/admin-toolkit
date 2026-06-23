import { useEffect, useRef } from 'react';
import { useDiag } from '../context/DiagContext';
import { codeEnvComparisonScan } from '../state/codeEnvComparisonStore';
import { csTemplateScan } from '../state/csTemplateStore';
import { dbHealthConnectionsStore } from '../state/dbHealthConnectionsStore';
import {
  imageCleanerDetectScan,
  imageCleanerReleaseDates,
  loadReleaseDate,
} from '../state/imageCleanerStore';
import { managedFoldersScan } from '../state/managedFoldersStore';
import { prefetchInactiveProjects } from '../state/inactiveProjectsCache';
import { startProcessMetricsScan } from '../state/processMetrics';
import { projectCostScan } from '../state/projectCostScan';
import { reportLlmsStore } from '../state/reportLlmsStore';
import { getSessionEpoch } from '../state/sessionCache';
import { SHARED_LOADING_FIELDS } from '../utils/moduleRegistry';
import { resolveLifecycleFromFields } from '../utils/pageLifecycle';
import type { Lifecycle, ParsedData } from '../types';

type IdleHandle = number;

const requestIdle = (cb: () => void): IdleHandle => {
  if ('requestIdleCallback' in window) {
    return window.requestIdleCallback(cb, { timeout: 2500 });
  }
  return globalThis.setTimeout(cb, 80);
};

const cancelIdle = (handle: IdleHandle): void => {
  if ('cancelIdleCallback' in window) {
    window.cancelIdleCallback(handle);
  } else {
    globalThis.clearTimeout(handle);
  }
};

const chunkPreloads = [
  () => import('../components/ToolsContainer'),
  () => import('../components/InactiveProjectCleaner'),
  () => import('../components/ImageCleaner'),
  () => import('../components/CSTemplateReplacement'),
  () => import('../components/pages/DbHealthPage'),
  () => import('../components/pages/ReportPage'),
  () => import('../components/pages/LlmAuditPage'),
];

function isTerminal(lc: Lifecycle | undefined): boolean {
  return lc?.phase === 'done' || lc?.phase === 'error';
}

// Cost/CRU (`projectCostLoading`) is the only global-aggregate field with no
// init-time starter — it loads solely on the Cost page's mount, so the global
// "Analysis complete" indicator (and anything gated on it) never resolves until
// the page is visited. Auto-start it here, but only once every OTHER aggregate
// field is terminal, so the heavy CRU/audit parse runs last and doesn't compete
// with the rest of the initial load.
const NON_COST_FIELDS = SHARED_LOADING_FIELDS.filter((f) => f !== 'projectCostLoading');

export function useDelayedPageWarmup(enabled: boolean, parsedData: ParsedData): void {
  const { dispatch } = useDiag();
  const epochRef = useRef(getSessionEpoch());
  const coreStartedRef = useRef(false);
  const codeEnvCompareStartedRef = useRef(false);

  const setLifecycle = (field: keyof ParsedData, lc: Lifecycle) => {
    dispatch({ type: 'SET_PARSED_DATA', payload: { [field]: lc } });
  };

  // Reset the per-session warmup guards when the session epoch changes (session
  // reset). Lives in an effect, not the render body, so refs are never touched
  // during render. Declared before the warmup effects below so the guards are
  // fresh before those effects re-check them on the same commit.
  useEffect(() => {
    const epoch = getSessionEpoch();
    if (epochRef.current !== epoch) {
      epochRef.current = epoch;
      coreStartedRef.current = false;
      codeEnvCompareStartedRef.current = false;
    }
  });

  useEffect(() => {
    if (!enabled || coreStartedRef.current) return;
    coreStartedRef.current = true;
    let cancelled = false;
    let idleHandle: IdleHandle | null = null;

    const runQueue = (jobs: Array<() => Promise<unknown> | unknown>) => {
      const runNext = (index: number) => {
        if (cancelled || index >= jobs.length) return;
        idleHandle = requestIdle(() => {
          Promise.resolve()
            .then(jobs[index])
            .catch(() => {
              /* each store publishes its own error state */
            });
          runNext(index + 1);
        });
      };
      runNext(0);
    };

    const warmProjectCleaner = async () => {
      const startedAt = new Date().toISOString();
      setLifecycle('projectCleanerLoading', {
        phase: 'running',
        startedAt,
        progressPct: 20,
        message: 'Prefetching project cleaner data',
        updatedAt: startedAt,
      });
      try {
        await Promise.all([prefetchInactiveProjects(), managedFoldersScan.load()]);
        setLifecycle('projectCleanerLoading', {
          phase: 'done',
          startedAt,
          finishedAt: new Date().toISOString(),
          isEmpty: false,
          message: 'Project cleaner data cached',
        });
      } catch (err) {
        setLifecycle('projectCleanerLoading', {
          phase: 'error',
          startedAt,
          finishedAt: new Date().toISOString(),
          error: err instanceof Error ? err.message : String(err),
          progressPct: 20,
        });
      }
    };

    const warmImageCleaner = async () => {
      const startedAt = new Date().toISOString();
      setLifecycle('imageCleanerLoading', {
        phase: 'running',
        startedAt,
        progressPct: 20,
        message: 'Detecting Docker registry provider',
        updatedAt: startedAt,
      });
      await imageCleanerDetectScan.load();
      const detect = imageCleanerDetectScan.store.get();
      if (detect.error) {
        setLifecycle('imageCleanerLoading', {
          phase: 'error',
          startedAt,
          finishedAt: new Date().toISOString(),
          error: detect.error,
          progressPct: 20,
        });
        return;
      }
      const provider = detect.data?.provider;
      if (!provider) {
        setLifecycle('imageCleanerLoading', {
          phase: 'done',
          startedAt,
          finishedAt: new Date().toISOString(),
          isEmpty: true,
          message: 'No registry provider detected',
        });
        return;
      }
      setLifecycle('imageCleanerLoading', {
        phase: 'running',
        startedAt,
        progressPct: 50,
        message: `Loading ${provider.toUpperCase()} release date`,
        updatedAt: new Date().toISOString(),
      });
      await loadReleaseDate(provider);
      const release = imageCleanerReleaseDates.get().byProvider[provider];
      if (release?.error) {
        setLifecycle('imageCleanerLoading', {
          phase: 'error',
          startedAt,
          finishedAt: new Date().toISOString(),
          error: release.error.message,
          progressPct: 50,
        });
        return;
      }
      setLifecycle('imageCleanerLoading', {
        phase: 'done',
        startedAt,
        finishedAt: new Date().toISOString(),
        isEmpty: !release?.info,
        message: 'Docker image cleaner bootstrap cached',
      });
    };

    const warmDbHealth = async () => {
      const startedAt = new Date().toISOString();
      setLifecycle('dbHealthLoading', {
        phase: 'running',
        startedAt,
        progressPct: 15,
        message: 'Discovering Postgres connections',
        updatedAt: startedAt,
      });
      await dbHealthConnectionsStore.load();
      const afterConnections = dbHealthConnectionsStore.get();
      if (afterConnections.error) {
        setLifecycle('dbHealthLoading', {
          phase: 'error',
          startedAt,
          finishedAt: new Date().toISOString(),
          error: afterConnections.error,
          progressPct: 15,
        });
        return;
      }
      if (!afterConnections.configuredConnection) {
        setLifecycle('dbHealthLoading', {
          phase: 'done',
          startedAt,
          finishedAt: new Date().toISOString(),
          isEmpty: true,
          message: 'DB Health not configured',
        });
        return;
      }
      setLifecycle('dbHealthLoading', {
        phase: 'running',
        startedAt,
        progressPct: 45,
        message: `Loading ${afterConnections.configuredConnection}`,
        updatedAt: new Date().toISOString(),
      });
      await dbHealthConnectionsStore.loadDefaultConfiguredDetails();
      const detail = dbHealthConnectionsStore.getDetail(afterConnections.configuredConnection);
      if (detail.error) {
        setLifecycle('dbHealthLoading', {
          phase: 'error',
          startedAt,
          finishedAt: new Date().toISOString(),
          error: detail.error,
          progressPct: 45,
        });
        return;
      }
      setLifecycle('dbHealthLoading', {
        phase: 'done',
        startedAt,
        finishedAt: new Date().toISOString(),
        isEmpty: detail.tables.length === 0,
        message: 'DB Health data cached',
      });
    };

    runQueue([
      ...chunkPreloads,
      () => reportLlmsStore.load(),
      () => dbHealthConnectionsStore.load(),
      () => csTemplateScan.load(),
      () => managedFoldersScan.load(),
      () => startProcessMetricsScan(),
      warmImageCleaner,
      warmProjectCleaner,
      warmDbHealth,
    ]);

    return () => {
      cancelled = true;
      if (idleHandle != null) cancelIdle(idleHandle);
    };
    // `setLifecycle` is a fresh closure each render (it's not memoized), so adding
    // it would re-fire this one-shot warmup queue on every render. It only wraps
    // the stable `dispatch`, which IS in the deps, so omitting it is safe.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- setLifecycle wraps dispatch (already a dep); listing it would re-run the warmup each render
  }, [dispatch, enabled]);

  useEffect(() => {
    if (!enabled || codeEnvCompareStartedRef.current) return;
    if (!isTerminal(parsedData.codeEnvsLoading)) return;
    codeEnvCompareStartedRef.current = true;
    let cancelled = false;
    const handle = requestIdle(() => {
      if (!cancelled) void codeEnvComparisonScan.load();
    });
    return () => {
      cancelled = true;
      cancelIdle(handle);
    };
  }, [enabled, parsedData.codeEnvsLoading]);

  // Deferred Cost/CRU autostart: fire once the other aggregate fields settle so
  // the global "Analysis complete" can resolve without a visit to the Cost page.
  // Guarded on the store's own `scanStarted`, so the Cost page's mount-load
  // becomes a no-op fast-path when this has already kicked it off (and vice
  // versa). One-shot per session because `scanStarted` stays true thereafter.
  const othersLc = resolveLifecycleFromFields(NON_COST_FIELDS, parsedData);
  useEffect(() => {
    if (!enabled) return;
    if (othersLc.phase !== 'done' && othersLc.phase !== 'error') return;
    if (projectCostScan.store.get().scanStarted) return;
    void projectCostScan.load();
  }, [enabled, othersLc.phase]);
}
