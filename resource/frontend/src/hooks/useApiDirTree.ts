import { useCallback, useEffect, useRef } from 'react';
import type { DirEntry, DirTreeData, FootprintScope } from '../types';
import { fetchJson } from '../utils/api';
import { useDiag } from '../context/DiagContext';

function _readDirTreeDepth(): number {
  try {
    const raw = window.localStorage.getItem('diagparser.thresholds');
    if (raw) {
      const t = JSON.parse(raw);
      if (typeof t.dirTreeDefaultDepth === 'number') return t.dirTreeDefaultDepth;
    }
  } catch { /* use default */ }
  return 3;
}
const DEFAULT_MAX_DEPTH = _readDirTreeDepth();
const EXPAND_DEPTH = 5;
const SPECULATIVE_EXPAND_DEPTH = 3;
const SPECULATIVE_PREFETCH_LIMIT = 3;
const SPECULATIVE_COOLDOWN_MS = 400;

export type { FootprintScope };

interface DirTreeDebugError {
  kind?: string;
  path?: string;
  error?: string;
}

interface DirTreeDebugItem {
  path?: string;
  size?: number;
  humanSize?: string;
  fileCount?: number;
  reason?: string;
  fsType?: string;
  mounts?: string[];
}

interface DirTreeDebugPayload {
  totalSize?: number;
  totalFiles?: number;
  nodesVisited?: number;
  dirsVisited?: number;
  filesVisited?: number;
  entriesScanned?: number;
  symlinksSeen?: number;
  statErrors?: number;
  scanErrors?: number;
  topChildren?: DirTreeDebugItem[];
  specialMountTotals?: DirTreeDebugItem[];
  largeLeafs?: DirTreeDebugItem[];
  errors?: DirTreeDebugError[];
  dfRootUsed?: number;
  dfMountedUsed?: number;
  dfTotalUsed?: number;
  dfMountsIncluded?: DirTreeDebugItem[];
  dfMountsExcluded?: DirTreeDebugItem[];
  dfTopMountBuckets?: DirTreeDebugItem[];
  overlayUnknownBytes?: number;
  permissionDeniedPaths?: string[];
}

interface DirTreeResponse extends DirTreeData {
  debug?: DirTreeDebugPayload;
}
interface DirTreeExpandResponse {
  node?: DirEntry | null;
  debug?: DirTreeDebugPayload;
}

interface DirTreeLoadOptions {
  scope?: FootprintScope;
  projectKey?: string;
}

interface ExpandDirectoryOptions {
  priority?: 'user' | 'speculative';
  debounceMs?: number;
  suppressErrors?: boolean;
  maxDepth?: number;
}

function buildScopeQuery(scope: FootprintScope, projectKey: string): string {
  const params = new URLSearchParams();
  params.set('scope', scope);
  if (scope === 'project' && projectKey.trim()) {
    params.set('projectKey', projectKey.trim());
  }
  return params.toString();
}

function formatBytes(bytes: number | undefined): string {
  if (!bytes || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let size = bytes;
  let idx = 0;
  while (size >= 1024 && idx < units.length - 1) {
    size /= 1024;
    idx += 1;
  }
  return `${size.toFixed(2)} ${units[idx]}`;
}

export function useApiDirTree() {
  const { state: diagState, dispatch } = useDiag();
  const state = diagState.apiDirTree;
  const loadAbortRef = useRef<AbortController | null>(null);
  const expandAbortRef = useRef<AbortController | null>(null);
  const expandTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prefetchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const expandedNodesRef = useRef(state.expandedNodes);
  const activeRequestRef = useRef<{ path: string; priority: 'user' | 'speculative' } | null>(null);
  const inFlightExpandsRef = useRef(new Map<string, Promise<DirEntry | null>>());
  const lastExpandCompletedAtRef = useRef(0);

  useEffect(() => {
    expandedNodesRef.current = state.expandedNodes;
  }, [state.expandedNodes]);

  const abortLoad = useCallback(() => {
    const controller = loadAbortRef.current;
    if (!controller) return;
    controller.abort();
    loadAbortRef.current = null;
    dispatch({
      type: 'ADD_DEBUG_LOG',
      payload: { scope: 'dir-tree', level: 'warn', message: 'Aborted root directory scan request' },
    });
    dispatch({ type: 'SET_API_DIR_TREE', payload: { isLoading: false, error: null } });
  }, [dispatch]);

  useEffect(() => {
    return () => {
      if (loadAbortRef.current) {
        loadAbortRef.current.abort();
        loadAbortRef.current = null;
      }
      if (expandAbortRef.current) {
        expandAbortRef.current.abort();
        expandAbortRef.current = null;
      }
      if (expandTimerRef.current) {
        clearTimeout(expandTimerRef.current);
        expandTimerRef.current = null;
      }
      if (prefetchTimerRef.current) {
        clearTimeout(prefetchTimerRef.current);
        prefetchTimerRef.current = null;
      }
    };
  }, []);

  const loadRoot = useCallback(async (options: DirTreeLoadOptions = {}) => {
    if (loadAbortRef.current) {
      loadAbortRef.current.abort();
      loadAbortRef.current = null;
    }

    const scope: FootprintScope = options.scope || 'dss';
    const projectKey = options.projectKey || '';
    const scopeQuery = buildScopeQuery(scope, projectKey);
    const url = `/api/dir-tree?maxDepth=${DEFAULT_MAX_DEPTH}&${scopeQuery}`;
    const controller = new AbortController();
    loadAbortRef.current = controller;

    dispatch({ type: 'ADD_DEBUG_LOG', payload: { scope: 'dir-tree', level: 'info', message: `GET ${url}` } });
    dispatch({ type: 'SET_API_DIR_TREE', payload: { isLoading: true, error: null, scope, projectKey } });

    try {
      const data = await fetchJson<DirTreeResponse>(url, { signal: controller.signal });
      if (loadAbortRef.current === controller) {
        loadAbortRef.current = null;
      }
      if (data.debug) {
        const dbg = data.debug;
        dispatch({
          type: 'ADD_DEBUG_LOG',
          payload: {
            scope: 'dir-tree',
            level: 'info',
            message: [
              `Tree debug: total=${formatBytes(dbg.totalSize)} files=${(dbg.totalFiles ?? 0).toLocaleString()}`,
              `nodes=${dbg.nodesVisited ?? 0}`,
              `dirs=${dbg.dirsVisited ?? 0}`,
              `files=${dbg.filesVisited ?? 0}`,
              `scanned=${dbg.entriesScanned ?? 0}`,
              `symlinks=${dbg.symlinksSeen ?? 0}`,
              `statErr=${dbg.statErrors ?? 0}`,
              `scanErr=${dbg.scanErrors ?? 0}`,
            ].join(' | '),
          },
        });

        if (dbg.topChildren && dbg.topChildren.length > 0) {
          const top = dbg.topChildren
            .slice(0, 5)
            .map((item) => `${item.path || '?'}=${item.humanSize || formatBytes(item.size)}`)
            .join(', ');
          dispatch({
            type: 'ADD_DEBUG_LOG',
            payload: { scope: 'dir-tree', level: 'info', message: `Top children: ${top}` },
          });
        }

        if (dbg.specialMountTotals && dbg.specialMountTotals.length > 0) {
          const special = dbg.specialMountTotals
            .slice(0, 5)
            .map((item) => `${item.path || '?'}=${item.humanSize || formatBytes(item.size)}`)
            .join(', ');
          dispatch({
            type: 'ADD_DEBUG_LOG',
            payload: { scope: 'dir-tree', level: 'warn', message: `Special mounts included: ${special}` },
          });
        }

        if (typeof dbg.dfTotalUsed === 'number' && dbg.dfTotalUsed > 0) {
          dispatch({
            type: 'ADD_DEBUG_LOG',
            payload: {
              scope: 'dir-tree',
              level: 'info',
              message: `DF overlay: total=${formatBytes(dbg.dfTotalUsed)} rootfs=${formatBytes(dbg.dfRootUsed)} mounted=${formatBytes(dbg.dfMountedUsed)}`,
            },
          });
        }

        if (dbg.dfTopMountBuckets && dbg.dfTopMountBuckets.length > 0) {
          const mounts = dbg.dfTopMountBuckets
            .slice(0, 5)
            .map((item) => `${item.path || '?'}=${item.humanSize || formatBytes(item.size)}`)
            .join(', ');
          dispatch({
            type: 'ADD_DEBUG_LOG',
            payload: { scope: 'dir-tree', level: 'info', message: `DF mount buckets: ${mounts}` },
          });
        }

        if (typeof dbg.overlayUnknownBytes === 'number' && dbg.overlayUnknownBytes > 0) {
          dispatch({
            type: 'ADD_DEBUG_LOG',
            payload: {
              scope: 'dir-tree',
              level: 'warn',
              message: `Unscanned usage overlaid: ${formatBytes(dbg.overlayUnknownBytes)}`,
            },
          });
        }

        if (dbg.permissionDeniedPaths && dbg.permissionDeniedPaths.length > 0) {
          const denied = dbg.permissionDeniedPaths.slice(0, 5).join(', ');
          dispatch({
            type: 'ADD_DEBUG_LOG',
            payload: {
              scope: 'dir-tree',
              level: 'warn',
              message: `Permission denied paths: ${denied}`,
            },
          });
        }

        if (dbg.largeLeafs && dbg.largeLeafs.length > 0) {
          const large = dbg.largeLeafs
            .slice(0, 5)
            .map((item) => `${item.path || '?'}=${item.humanSize || formatBytes(item.size)}${item.reason ? ` (${item.reason})` : ''}`)
            .join(', ');
          dispatch({
            type: 'ADD_DEBUG_LOG',
            payload: { scope: 'dir-tree', level: 'warn', message: `Large leaf entries: ${large}` },
          });
        }

        if (dbg.errors && dbg.errors.length > 0) {
          const errs = dbg.errors
            .slice(0, 3)
            .map((item) => `${item.kind || 'err'} ${item.path || '?'} -> ${item.error || 'unknown'}`)
            .join(' | ');
          dispatch({
            type: 'ADD_DEBUG_LOG',
            payload: { scope: 'dir-tree', level: 'warn', message: `Tree scanner errors: ${errs}` },
          });
        }
      }
      dispatch({
        type: 'ADD_DEBUG_LOG',
        payload: {
          scope: 'dir-tree',
          level: 'info',
          message: `Loaded dir tree (${scope}) root with ${data.root?.children?.length || 0} children`,
        }
      });
      dispatch({
        type: 'SET_API_DIR_TREE',
        payload: { isLoading: false, tree: data, expandedNodes: new Map(), error: null },
      });
    } catch (err) {
      if (loadAbortRef.current === controller) {
        loadAbortRef.current = null;
      }
      if (err instanceof DOMException && err.name === 'AbortError') {
        dispatch({
          type: 'ADD_DEBUG_LOG',
          payload: { scope: 'dir-tree', level: 'info', message: `Root load canceled (${scope})` },
        });
        dispatch({ type: 'SET_API_DIR_TREE', payload: { isLoading: false, error: null } });
        return;
      }
      const message = err instanceof Error ? err.message : 'Unknown error';
      dispatch({ type: 'ADD_DEBUG_LOG', payload: { scope: 'dir-tree', level: 'error', message: `Root load failed: ${message}` } });
      dispatch({ type: 'SET_API_DIR_TREE', payload: { isLoading: false, error: message } });
    }
  }, [dispatch]);

  const abortSpeculativeWork = useCallback(() => {
    if (prefetchTimerRef.current) {
      clearTimeout(prefetchTimerRef.current);
      prefetchTimerRef.current = null;
    }
    if (activeRequestRef.current?.priority !== 'speculative') {
      return;
    }
    if (expandAbortRef.current) {
      expandAbortRef.current.abort();
      expandAbortRef.current = null;
    }
  }, []);

  const expandDirectory = useCallback((dirPath: string, options: ExpandDirectoryOptions = {}): Promise<DirEntry | null> => {
    if (!dirPath) return Promise.resolve(null);
    const priority = options.priority || 'user';
    const debounceMs = options.debounceMs ?? 0;
    const suppressErrors = options.suppressErrors ?? (priority === 'speculative');
    const maxDepth = options.maxDepth ?? (priority === 'speculative' ? SPECULATIVE_EXPAND_DEPTH : EXPAND_DEPTH);
    const cached = expandedNodesRef.current.get(dirPath);
    if (cached) {
      return Promise.resolve(cached);
    }
    const existing = inFlightExpandsRef.current.get(dirPath);
    if (existing) {
      return existing;
    }

    if (priority === 'speculative') {
      const now = Date.now();
      if (activeRequestRef.current || now - lastExpandCompletedAtRef.current < SPECULATIVE_COOLDOWN_MS) {
        return Promise.resolve(null);
      }
    } else {
      abortSpeculativeWork();
    }

    if (expandAbortRef.current && activeRequestRef.current?.priority !== 'speculative') {
      expandAbortRef.current.abort();
      expandAbortRef.current = null;
    }

    if (priority === 'user' && expandTimerRef.current) {
      clearTimeout(expandTimerRef.current);
      expandTimerRef.current = null;
    }

    const promise = new Promise<DirEntry | null>((resolve) => {
      const run = async () => {
        if (priority === 'speculative' && activeRequestRef.current) {
          resolve(null);
          return;
        }
        const scopeQuery = buildScopeQuery(state.scope, state.projectKey);
        const url = `/api/dir-tree?path=${encodeURIComponent(dirPath)}&maxDepth=${maxDepth}&${scopeQuery}`;
        const controller = new AbortController();
        expandAbortRef.current = controller;
        activeRequestRef.current = { path: dirPath, priority };

        dispatch({
          type: 'ADD_DEBUG_LOG',
          payload: {
            scope: 'dir-tree',
            level: 'info',
            message: `${priority === 'speculative' ? 'Prefetch' : 'Expand'} ${dirPath} via ${state.scope}`,
          }
        });
        dispatch({ type: 'SET_API_DIR_TREE', payload: { isExpanding: priority === 'user', error: null } });
        try {
          const data = await fetchJson<DirTreeExpandResponse>(url, { signal: controller.signal });
          if (expandAbortRef.current === controller) {
            expandAbortRef.current = null;
          }
          if (activeRequestRef.current?.path === dirPath) {
            activeRequestRef.current = null;
          }
          lastExpandCompletedAtRef.current = Date.now();
          if (data.debug) {
            const dbg = data.debug;
            dispatch({
              type: 'ADD_DEBUG_LOG',
              payload: {
                scope: 'dir-tree',
                level: 'info',
                message: `Expand debug ${dirPath}: total=${formatBytes(dbg.totalSize)} files=${(dbg.totalFiles ?? 0).toLocaleString()} scanned=${dbg.entriesScanned ?? 0} symlinks=${dbg.symlinksSeen ?? 0}`,
              },
            });
          }
          const node = data.node || null;
          if (node) {
            dispatch({
              type: 'ADD_DEBUG_LOG',
              payload: {
                scope: 'dir-tree',
                level: 'info',
                message: `${priority === 'speculative' ? 'Prefetched' : 'Expanded'} ${dirPath} (${node.children.length} children)`,
              }
            });
            dispatch({ type: 'SET_API_DIR_TREE_EXPANDED_NODE', payload: { path: dirPath, node } });
          } else {
            dispatch({ type: 'SET_API_DIR_TREE', payload: { isExpanding: false } });
          }
          resolve(node);
        } catch (err) {
          if (expandAbortRef.current === controller) {
            expandAbortRef.current = null;
          }
          if (activeRequestRef.current?.path === dirPath) {
            activeRequestRef.current = null;
          }
          lastExpandCompletedAtRef.current = Date.now();
          if (err instanceof DOMException && err.name === 'AbortError') {
            dispatch({
              type: 'ADD_DEBUG_LOG',
              payload: { scope: 'dir-tree', level: 'info', message: `${priority === 'speculative' ? 'Prefetch' : 'Expand'} canceled ${dirPath}` },
            });
            dispatch({ type: 'SET_API_DIR_TREE', payload: { isExpanding: false, error: null } });
            resolve(null);
            return;
          }
          const message = err instanceof Error ? err.message : 'Unknown error';
          dispatch({
            type: 'ADD_DEBUG_LOG',
            payload: {
              scope: 'dir-tree',
              level: suppressErrors ? 'warn' : 'error',
              message: `${priority === 'speculative' ? 'Prefetch' : 'Expand'} failed ${dirPath}: ${message}`,
            }
          });
          dispatch({ type: 'SET_API_DIR_TREE', payload: { isExpanding: false, error: suppressErrors ? null : message } });
          resolve(null);
        } finally {
          inFlightExpandsRef.current.delete(dirPath);
        }
      };

      if (debounceMs > 0) {
        expandTimerRef.current = setTimeout(() => {
          expandTimerRef.current = null;
          void run();
        }, debounceMs);
      } else {
        void run();
      }
    });
    inFlightExpandsRef.current.set(dirPath, promise);
    return promise;
  }, [abortSpeculativeWork, dispatch, state.scope, state.projectKey]);

  const schedulePrefetch = useCallback((dirPaths: string[]) => {
    if (prefetchTimerRef.current) {
      clearTimeout(prefetchTimerRef.current);
      prefetchTimerRef.current = null;
    }
    if (!state.tree || !dirPaths.length) {
      return;
    }
    prefetchTimerRef.current = setTimeout(() => {
      prefetchTimerRef.current = null;
      const nextPaths = dirPaths
        .filter((path) => !!path && !expandedNodesRef.current.has(path) && !inFlightExpandsRef.current.has(path))
        .slice(0, SPECULATIVE_PREFETCH_LIMIT);
      void nextPaths.reduce<Promise<DirEntry | null>>(
        (chain, path) => chain.then(() => expandDirectory(path, { priority: 'speculative' })),
        Promise.resolve(null),
      );
    }, SPECULATIVE_COOLDOWN_MS);
  }, [expandDirectory, state.tree]);

  return {
    state,
    loadRoot,
    abortLoad,
    expandDirectory,
    schedulePrefetch,
    cancelPrefetch: abortSpeculativeWork,
  };
}
