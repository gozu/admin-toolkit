/**
 * Live-mode data loader — thin orchestrator over the hooks/apiLoader/
 * modules. Owns the effect lifecycle (cancellation, reload), the phase
 * sequencing, and the cross-phase flags; all per-domain load logic lives in
 * apiLoader/* (context, lifecycle, phase1, phase2, codeEnvs, footprint,
 * secondary, finalize).
 *
 * Phase map (unchanged from the original monolith):
 *   1. /api/overview + raw settings → initial ParsedData + lifecycle tracker
 *   2. six parallel secondary fetches + GeneralSettings/ProjectStandards parsers
 *      → SET_LOADING false (UI renders) → /api/settings (backend timeouts)
 *   3. heavy gate (code-envs ∥ project-footprint ∥ llm-audit ∥ plugin-usages)
 *      + connection-health SSE + low gate (projects ∥ logs)
 *   4. users-by-projects derivation, timing table, deferred-scan autostarts,
 *      await tails (connection-health + code-env sizes) → dataReady
 */
import { useEffect, useRef } from 'react';
import { useDiag, DEFAULT_DSSHOME } from '../context/DiagContext';
import { getAppVersion } from '../state/appVersionStore';
import type { ParsedData } from '../types';
import { fetchJson } from '../utils/api';
import { useApiDirTree } from './useApiDirTree';
import { useConnectionUsageScan } from './useConnectionUsageScan';
import { createLoaderContext } from './apiLoader/context';
import { createLifecycleTracker } from './apiLoader/lifecycle';
import { loadPhase1 } from './apiLoader/phase1';
import { loadPhase2 } from './apiLoader/phase2';
import { runCodeEnvs } from './apiLoader/codeEnvs';
import { runProjectFootprint } from './apiLoader/footprint';
import {
  runConnectionHealth,
  runLlmAudit,
  runLogs,
  runPluginUsages,
  runProjects,
} from './apiLoader/secondary';
import {
  autostartDeferredScans,
  computeUsersByProjects,
  emitTimingTable,
} from './apiLoader/finalize';

export function useApiDataLoader(enabled: boolean, reloadKey = 0) {
  const { dispatch } = useDiag();
  // Reuse the dir-tree hook's memoized loadRoot (stable: useCallback([dispatch]))
  // so the autostart block can populate the global tree without duplicating its
  // fetch/abort/debug logic. Held in a ref synced each render so the long-lived
  // load effect calls the current fn without stale-closure / exhaustive-deps churn.
  const { loadRoot: loadDirTreeRoot } = useApiDirTree();
  const loadDirTreeRootRef = useRef(loadDirTreeRoot);
  // Intentional latest-value ref: synced in render so the long-lived load effect
  // (deps [enabled]) always calls the current fn without re-subscribing. The ref
  // is only *read* later inside that effect, never during render, so the
  // render-time write is safe. A useEffect sync would lag one commit.
  // eslint-disable-next-line react-hooks/refs -- latest-value ref, read only in effects
  loadDirTreeRootRef.current = loadDirTreeRoot;

  // Connections usage scan (shared by Insights / Usage / FS-Migration). Was
  // previously fired by useSessionOrchestrator; relocated here with identical
  // gating so the orchestrator could be deleted.
  const { scan: scanConnectionUsage } = useConnectionUsageScan();
  useEffect(() => {
    if (!enabled) return;
    void scanConnectionUsage();
  }, [enabled, reloadKey, scanConnectionUsage]);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const ctx = createLoaderContext(dispatch, () => cancelled);
    const { log, nowMs, fmtMs } = ctx;

    const run = async () => {
      log(`Diag Parser version v${getAppVersion() || '?'}`);
      log('Starting live data load');
      log(
        ctx.basicProjectsEnabled
          ? 'Basic /api/projects endpoint enabled (query: basicProjects=1)'
          : 'Basic /api/projects endpoint disabled by default (add query basicProjects=1 to re-enable)',
      );
      dispatch({ type: 'SET_LOADING', payload: true });
      dispatch({ type: 'SET_ERROR', payload: null });
      dispatch({ type: 'SET_DIAG_TYPE', payload: 'instance' });
      dispatch({ type: 'SET_DSSHOME', payload: DEFAULT_DSSHOME });

      try {
        const { overview, rawSettings, rawProjectStandards } = await loadPhase1(ctx);

        if (cancelled) return;

        const initialData: ParsedData = {
          ...overview,
        };
        if (overview.sparkVersion) {
          initialData.sparkSettings = {
            ...(initialData.sparkSettings || {}),
            'Spark Version': overview.sparkVersion,
          };
        }
        dispatch({ type: 'SET_PARSED_DATA', payload: initialData });
        const tracker = createLifecycleTracker(ctx, initialData);
        // Mark trivially-synchronous modules done once the overview + settings
        // load completes — they have no async work of their own, but they
        // must still pass through the queued→done ritual.
        tracker.markDone('summaryLoading', 'Overview ready');
        tracker.markDone('settingsLoading', 'Settings loaded');
        tracker.markDone(
          'filesystemLoading',
          'Filesystem ready',
          (overview.filesystemInfo?.length ?? 0) === 0,
        );
        tracker.markDone('memoryLoading', 'Memory ready');
        log('Phase 1 complete (overview + settings)');

        // Phase 2: load secondary data in parallel
        log('Phase 2 starting');
        await loadPhase2(ctx, tracker, overview, rawSettings, rawProjectStandards);
        if (cancelled) return;

        // Allow UI to render after core data is available
        dispatch({ type: 'SET_LOADING', payload: false });
        log('Core data ready, released loading state');

        // Fetch backend settings for configurable timeouts
        let beSettings: Record<string, number> = {};
        try {
          beSettings = await fetchJson<{
            current: Record<string, number>;
            defaults: Record<string, number>;
          }>('/api/settings').then((d) => d.current);
          log('Backend settings loaded');
        } catch {
          log('Backend settings fetch failed, using defaults', 'warn');
        }

        // Phase 3: heavier endpoints
        log('Phase 3 starting');
        let codeEnvsDone = false;
        let projectFootprintDone = false;
        let projectFootprintStarted = false;
        const slowHeavyTimer = setTimeout(() => {
          const waiting: string[] = [];
          if (!codeEnvsDone) waiting.push('/api/code-envs');
          if (projectFootprintStarted && !projectFootprintDone)
            waiting.push('/api/project-footprint');
          if (waiting.length > 0) {
            log(`Heavy endpoints still loading after 8000ms: ${waiting.join(', ')}`, 'warn');
          }
        }, 8000);

        // The slow code-env-sizes tail — handed over by the code-envs runner
        // so the await-tails step can join it before dataReady.
        let codeEnvSizesTracked: Promise<PromiseSettledResult<unknown>> | null = null;

        log(
          'Phase 3 strategy: launch code-envs + project-footprint + connection-health in parallel; stage llm-audit + plugin-usages behind them; defer dir-tree until Directory page is opened',
        );
        const phase3Start = nowMs();
        const heavyStart = nowMs();
        const lowStart = nowMs();
        projectFootprintStarted = true;
        const priorityGate = Promise.allSettled([
          runCodeEnvs(ctx, tracker, beSettings, {
            markCodeEnvsDone: () => {
              codeEnvsDone = true;
            },
            setSizesTracked: (tracked) => {
              codeEnvSizesTracked = tracked;
            },
          }),
          runProjectFootprint(ctx, tracker, beSettings, {
            markFootprintDone: () => {
              projectFootprintDone = true;
            },
          }),
        ]);
        // The DSS API saturates around ~40 concurrent calls (tam-global, 443
        // projects): llm-audit + plugin-usages running alongside the two
        // page-gating scans above slows all four. Staging them here unblocks
        // the Projects/Code Envs pages ~2x sooner and llm-audit itself runs
        // uncontended.
        const heavyGate = priorityGate.then(() => {
          log('Phase 3 stage 2: launching llm-audit + plugin-usages');
          return Promise.allSettled([runLlmAudit(ctx, tracker, beSettings), runPluginUsages(ctx, tracker)]);
        });
        const connectionHealthGate = runConnectionHealth(ctx, tracker);
        log('Deferring /api/dir-tree root load until after Phase 3 (background autostart)');
        const lowGate = Promise.allSettled([
          runProjects(ctx, tracker, beSettings),
          runLogs(ctx, tracker, beSettings),
        ]);

        await heavyGate;
        clearTimeout(slowHeavyTimer);
        if (cancelled) return;
        log(`Phase 3 heavy endpoints done (${fmtMs(heavyStart)})`);

        // (codeEnvReplacement is tracked alongside the main /api/code-envs fetch;
        //  codeEnvsComparison starts after code-envs settle via delayed warmup;
        //  codeEnvCleaner is owned solely by the
        //  managedFoldersScan store — autostarted below — so no mirror block is
        //  needed here anymore.)

        await lowGate;
        if (cancelled) return;
        log(`Phase 3 low-priority endpoints done (${fmtMs(lowStart)})`);
        log(`Phase 3 all endpoints done (${fmtMs(phase3Start)})`);

        // Action pages (db-health / cs-template / plugin-sync / report) are
        // `noLoadGlyph`: no sidebar glyph, excluded from the global aggregate,
        // and no startup markDone — their lifecycle field drives only in-page UI.

        computeUsersByProjects(ctx, tracker);
        emitTimingTable(ctx);
        log('Live data load completed');

        autostartDeferredScans(ctx, () => void loadDirTreeRootRef.current?.());

        // Await the slow tails (code-env sizes + connection-health) AFTER
        // kicking off the scans above — those scans depend on neither tail, so
        // gating their start behind the ~slow sizes fetch left the sidebar
        // showing static grey "queued" circles. Each tail is tracked via its
        // own lifecycle field, so it stays a visible spinner while in flight.
        const tails: Promise<unknown>[] = [connectionHealthGate];
        if (codeEnvSizesTracked) tails.push(codeEnvSizesTracked);
        log(`Awaiting ${tails.length} tail requests`);
        await Promise.allSettled(tails);
        log('Tails resolved');

        dispatch({ type: 'SET_PARSED_DATA', payload: { dataReady: true } });
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Unknown error';
        log(`Live data load failed: ${message}`, 'error');
        dispatch({ type: 'SET_ERROR', payload: `Failed to load live diagnostics: ${message}` });
      } finally {
        if (!cancelled) {
          dispatch({ type: 'SET_LOADING', payload: false });
          log('Loader finalized');
        }
      }
    };

    run();

    return () => {
      cancelled = true;
    };
  }, [dispatch, enabled, reloadKey]);
}
