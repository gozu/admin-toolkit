import { useEffect, useRef } from 'react';
import { useDiag } from '../context/DiagContext';
import { dbHealthConnectionsStore as db } from '../state/dbHealthConnectionsStore';
import { imageCleanerDetectScan, loadDefaultImageCleanerBootstrap } from '../state/imageCleanerStore';
import { managedFoldersScan } from '../state/managedFoldersStore';
import { clusterAvailabilityStore, loadClusterCount } from '../state/clusterAvailabilityStore';
import { getAutomaticK8sCluster, k8sInsightsScan, setK8sScanClusterId } from '../state/k8sInsightsStore';
import { k8sClusterHealthStore } from '../state/k8sClusterHealthStore';
import { prefetchInactiveProjects } from '../state/inactiveProjectsCache';
import { startProcessMetricsScan } from '../state/processMetrics';
import '../state/sqlPushdownScan';
import { runSanityCheck } from '../state/sanityCheckScan';
import { getSessionEpoch } from '../state/sessionCache';
import { getRegisteredScanStores } from '../state/scanStoreRegistry';
import { scanScheduler } from '../state/scanScheduler';
import { MODULE_BY_ID, SCAN_POLICIES, type LifecycleFieldName } from '../utils/moduleRegistry';
import type { Lifecycle, ParsedData } from '../types';

const terminal = (lc?: Lifecycle) => lc?.phase === 'done' || lc?.phase === 'error';

// All background starts live here; stores own request deduplication and freshness.
export function useDelayedPageWarmup(enabled: boolean, parsedData: ParsedData): void {
  const { state, dispatch } = useDiag();
  const epoch = getSessionEpoch();
  const started = useRef(new Set<string>());
  useEffect(() => { started.current.clear(); }, [epoch]);
  useEffect(() => {
    scanScheduler.enable(enabled);
    return () => scanScheduler.enable(false);
  }, [enabled, epoch]);

  useEffect(() => {
    if (!enabled) return;
    for (const entry of getRegisteredScanStores()) {
      const field = entry.field as LifecycleFieldName;
      const policy = SCAN_POLICIES[field];
      if (!entry.load || !policy || started.current.has(field)) continue;
      if (policy.after?.some((dependency) => !terminal(parsedData[dependency]))) continue;
      started.current.add(field);
      void entry.load(false, policy.priority);
    }
  }, [enabled, epoch, parsedData]);

  useEffect(() => {
    if (!enabled || started.current.has('bootstrap')) return;
    started.current.add('bootstrap');
    const publish = (payload: Partial<ParsedData>) => {
      if (epoch === getSessionEpoch()) dispatch({ type: 'SET_PARSED_DATA', payload });
    };
    const task = (key: string, cheap: boolean, run: (signal: AbortSignal) => Promise<unknown>, field?: LifecycleFieldName) => {
      void scanScheduler.enqueue(key, 10, cheap, async (signal) => {
        const startedAt = new Date().toISOString();
        if (field) publish({ [field]: { phase: 'running', startedAt, progressPct: 0, message: 'Loading', updatedAt: startedAt } });
        try {
          await run(signal);
          if (field && !signal.aborted) publish({ [field]: { phase: 'done', startedAt, finishedAt: new Date().toISOString(), isEmpty: false } });
        } catch (err) {
          if (field && !signal.aborted) publish({ [field]: { phase: 'error', startedAt, finishedAt: new Date().toISOString(), error: String(err), progressPct: 0 } });
        }
      });
    };
    task('cluster-health', true, async () => {
      await loadClusterCount();
      if (epoch !== getSessionEpoch()) return;
      if ((clusterAvailabilityStore.get().count ?? 0) > 0) await k8sClusterHealthStore.load();
      const cluster = getAutomaticK8sCluster();
      if (cluster && epoch === getSessionEpoch() && !k8sInsightsScan.store.get().scanStarted) {
        setK8sScanClusterId(cluster);
        void k8sInsightsScan.load(false, 40);
      }
    });
    // Await scan dependencies OUTSIDE a scheduler slot to avoid nested-job deadlocks.
    void managedFoldersScan.load(false, 20).then(() => {
      if (epoch === getSessionEpoch()) task('inactive-projects', false, prefetchInactiveProjects, 'projectCleanerLoading');
    });
    void imageCleanerDetectScan.load(false, 10).then(() => {
      if (epoch === getSessionEpoch()) task('image-release', true, loadDefaultImageCleanerBootstrap);
    });
    task('db-health', false, async () => {
      await db.loadDefaultConfiguredDetails();
      const { error, configuredConnection } = db.get();
      const detailError = configuredConnection && db.getDetail(configuredConnection).error;
      if (error || detailError) throw new Error(error || detailError || 'DB Health failed');
    }, 'dbHealthLoading');
    task('sanity-check', false, async (signal) => {
      const result = await runSanityCheck({ signal });
      publish({ sanityCheck: result.messages, sanityCheckMaxSeverity: result.maxSeverity });
    }, 'sanityCheckLoading');
    startProcessMetricsScan();
  }, [dispatch, enabled, epoch]);

  useEffect(() => {
    if (!enabled) return;
    const refresh = () => {
      if (document.visibilityState === 'hidden') return;
      const fields = MODULE_BY_ID[state.activePage].lifecycle.fields;
      for (const entry of getRegisteredScanStores()) {
        if (SCAN_POLICIES[entry.field as LifecycleFieldName] && fields.includes(entry.field as LifecycleFieldName)) void entry.load?.();
      }
    };
    refresh();
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refresh);
    const timer = window.setInterval(refresh, 60_000);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', refresh);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, [enabled, epoch, state.activePage]);
}
