import { useEffect, useRef } from 'react';
import { useDiag } from '../context/DiagContext';
import { getRegisteredScanStores } from '../state/scanStoreRegistry';
import { SHARED_LOADING_FIELDS } from '../utils/moduleRegistry';
import {
  deriveAnalysisLifecycle,
  lifecycleToLoadingProgress,
} from '../utils/analysisLifecycle';

export function useScanStoreLoadingMirror(): void {
  const { state, dispatch } = useDiag();
  // Capture parsedData via ref so the apply closure always sees the latest
  // snapshot when recomputing the analysis aggregate, without re-subscribing.
  const parsedRef = useRef(state.parsedData);
  const sessionStartedAtRef = useRef(new Date().toISOString());

  // Keep the ref synced via effect (writing during render is a React anti-pattern).
  // Also recompute the global aggregate over the *whole* latest parsedData on
  // every change: the loader writes analysisLoading from its own (necessarily
  // partial) currentParsedData view, and scan-store / usage / sanity
  // completions arrive via independent dispatches that don't pass through the
  // loader. Recomputing here makes "Analysis complete" correct no matter which
  // writer last touched state. Guarded so it can't loop — the aggregate is a
  // pure function of the lifecycle fields, never of analysisLoading itself.
  useEffect(() => {
    parsedRef.current = state.parsedData;
    const agg = deriveAnalysisLifecycle(
      state.parsedData,
      SHARED_LOADING_FIELDS,
      sessionStartedAtRef.current,
    );
    const next = lifecycleToLoadingProgress(agg);
    const prev = state.parsedData.analysisLoading;
    if (
      !prev ||
      prev.active !== next.active ||
      prev.progressPct !== next.progressPct ||
      prev.phase !== next.phase ||
      prev.message !== next.message ||
      prev.error !== next.error
    ) {
      dispatch({ type: 'SET_PARSED_DATA', payload: { analysisLoading: next } });
    }
  }, [state.parsedData, dispatch]);

  useEffect(() => {
    const stores = getRegisteredScanStores();
    const unsubs = stores.map((entry) => {
      const apply = () => {
        const value = entry.lifecycle();
        const nextParsed = { ...parsedRef.current, [entry.field]: value };
        const aggLifecycle = deriveAnalysisLifecycle(
          nextParsed,
          SHARED_LOADING_FIELDS,
          sessionStartedAtRef.current,
        );
        dispatch({
          type: 'SET_PARSED_DATA',
          payload: {
            [entry.field]: value,
            analysisLoading: lifecycleToLoadingProgress(aggLifecycle),
          },
        });
      };
      apply();
      return entry.subscribe(apply);
    });
    return () => {
      unsubs.forEach((u) => u());
    };
  }, [dispatch]);
}
