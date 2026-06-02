import { useCallback, useMemo, useReducer } from 'react';
import type { ReactNode } from 'react';
import type {
  ExtractedFiles,
  ParsedData,
  DiagType,
  AppMode,
  PageId,
  ComparisonResult,
  ComparisonViewMode,
  DiagFile,
  DataSource,
  DebugLevel,
  LayoutMode,
} from '../types';
import {
  DiagContext,
  diagReducer,
  buildInitialState,
  loadLayoutMode,
  LAYOUT_MODE_STORAGE_KEY,
  type DiagContextValue,
} from './DiagContext';

export function DiagProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(diagReducer, undefined, () =>
    buildInitialState(loadLayoutMode()),
  );

  // Stable callbacks — dispatch from useReducer is identity-stable
  const setLoading = useCallback((loading: boolean) => dispatch({ type: 'SET_LOADING', payload: loading }), [dispatch]);
  const setError = useCallback((error: string | null) => dispatch({ type: 'SET_ERROR', payload: error }), [dispatch]);
  const setExtractedFiles = useCallback((files: ExtractedFiles) => dispatch({ type: 'SET_EXTRACTED_FILES', payload: files }), [dispatch]);
  const setParsedData = useCallback((data: Partial<ParsedData>) => dispatch({ type: 'SET_PARSED_DATA', payload: data }), [dispatch]);
  const setActiveFilter = useCallback((filter: string) => dispatch({ type: 'SET_ACTIVE_FILTER', payload: filter }), [dispatch]);
  const setLayoutMode = useCallback((mode: LayoutMode) => {
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(LAYOUT_MODE_STORAGE_KEY, mode);
    }
    dispatch({ type: 'SET_LAYOUT_MODE', payload: mode });
  }, [dispatch]);
  const setDiagType = useCallback((type: DiagType) => dispatch({ type: 'SET_DIAG_TYPE', payload: type }), [dispatch]);
  const setRootFiles = useCallback((files: string[]) => dispatch({ type: 'SET_ROOT_FILES', payload: files }), [dispatch]);
  const setProjectFiles = useCallback((files: string[]) => dispatch({ type: 'SET_PROJECT_FILES', payload: files }), [dispatch]);
  const setDsshome = useCallback((path: string) => dispatch({ type: 'SET_DSSHOME', payload: path }), [dispatch]);
  const setOriginalFile = useCallback((file: File | null) => dispatch({ type: 'SET_ORIGINAL_FILE', payload: file }), [dispatch]);
  const setDataSource = useCallback((source: DataSource) => dispatch({ type: 'SET_DATA_SOURCE', payload: source }), [dispatch]);
  const addDebugLog = useCallback((message: string, scope?: string, level: DebugLevel = 'info') =>
    dispatch({ type: 'ADD_DEBUG_LOG', payload: { message, scope, level } }), [dispatch]);
  const clearDebugLogs = useCallback(() => dispatch({ type: 'CLEAR_DEBUG_LOGS' }), [dispatch]);
  const setFocusedConnectionFilter = useCallback(
    (filter: { name?: string; type?: string } | null) =>
      dispatch({ type: 'SET_FOCUSED_CONNECTION_FILTER', payload: filter }),
    [dispatch],
  );
  const reset = useCallback(() => dispatch({ type: 'RESET' }), [dispatch]);
  const setMode = useCallback((mode: AppMode) => dispatch({ type: 'SET_MODE', payload: mode }), [dispatch]);
  const setActivePage = useCallback((page: PageId) => dispatch({ type: 'SET_ACTIVE_PAGE', payload: page }), [dispatch]);
  const setComparisonFile = useCallback((slot: 'before' | 'after', file: DiagFile) =>
    dispatch({ type: 'SET_COMPARISON_FILE', payload: { slot, file } }), [dispatch]);
  const clearComparisonFile = useCallback((slot: 'before' | 'after') => dispatch({ type: 'CLEAR_COMPARISON_FILE', payload: slot }), [dispatch]);
  const setComparisonResult = useCallback((result: ComparisonResult) => dispatch({ type: 'SET_COMPARISON_RESULT', payload: result }), [dispatch]);
  const setComparisonViewMode = useCallback((mode: ComparisonViewMode) => dispatch({ type: 'SET_COMPARISON_VIEW_MODE', payload: mode }), [dispatch]);
  const setComparisonProcessing = useCallback((slot: 'before' | 'after', isProcessing: boolean) =>
    dispatch({ type: 'SET_COMPARISON_PROCESSING', payload: { slot, isProcessing } }), [dispatch]);
  const resetComparison = useCallback(() => dispatch({ type: 'RESET_COMPARISON' }), [dispatch]);

  const value = useMemo<DiagContextValue>(() => ({
    state, dispatch,
    setLoading, setError, setExtractedFiles, setParsedData, setActiveFilter, setLayoutMode,
    setDiagType, setRootFiles, setProjectFiles, setDsshome, setOriginalFile, setDataSource,
    addDebugLog, clearDebugLogs, setFocusedConnectionFilter, reset,
    setMode, setActivePage, setComparisonFile, clearComparisonFile, setComparisonResult,
    setComparisonViewMode, setComparisonProcessing, resetComparison,
  }), [
    state, dispatch,
    setLoading, setError, setExtractedFiles, setParsedData, setActiveFilter, setLayoutMode,
    setDiagType, setRootFiles, setProjectFiles, setDsshome, setOriginalFile, setDataSource,
    addDebugLog, clearDebugLogs, setFocusedConnectionFilter, reset,
    setMode, setActivePage, setComparisonFile, clearComparisonFile, setComparisonResult,
    setComparisonViewMode, setComparisonProcessing, resetComparison,
  ]);

  return <DiagContext.Provider value={value}>{children}</DiagContext.Provider>;
}
