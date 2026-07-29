import { useEffect, useState } from 'react';
import { useDiag } from '../context/DiagContext';
import { appVersionStore, backendFreshnessStore } from '../state/appVersionStore';
import type { DataSource } from '../types';
import { fetchJson } from '../utils/api';

interface ModeResponse {
  mode?: string;
  /** Installed plugin version, read live from the DSS API. */
  version?: string;
  /** Version the running backend process was built from (added in 0.4.797). */
  runningVersion?: string;
  backendStale?: boolean;
}

type LogFn = (message: string, level?: 'info' | 'warn' | 'error') => void;

/**
 * Decide whether the webapp's Python backend is running pre-upgrade code.
 *
 * Two tells, and the second is the one that catches the case that actually
 * bites: a backend old enough to predate `runningVersion` can't report a
 * mismatch, but its silence is itself conclusive. This frontend only ships
 * alongside a backend that answers with the field, and the frontend is served
 * from the *installed* plugin — so a live backend that omits it is necessarily
 * older than what is installed. Missing field ⇒ stale.
 */
function recordBackendFreshness(data: ModeResponse, log: LogFn): void {
  const installedVersion = String(data.version || '');
  const runningVersion = String(data.runningVersion || '');
  const stale = runningVersion
    ? Boolean(data.backendStale) || (!!installedVersion && installedVersion !== runningVersion)
    : true;

  backendFreshnessStore.set({ checked: true, stale, installedVersion, runningVersion });

  if (!stale) return;
  log(
    `Webapp backend is stale: running ${runningVersion || 'a pre-0.4.797 build'}, ` +
      `installed ${installedVersion || 'unknown'} — restart the webapp backend`,
    'warn',
  );
}

export function useDataSource() {
  const { dispatch } = useDiag();
  const [isDetecting, setIsDetecting] = useState(true);
  const [source, setSource] = useState<DataSource>('api');

  useEffect(() => {
    let cancelled = false;
    const log = (message: string, level: 'info' | 'warn' | 'error' = 'info') => {
      dispatch({ type: 'ADD_DEBUG_LOG', payload: { message, scope: 'datasource', level } });
    };

    const detect = async () => {
      log('Starting data source detection');
      try {
        log('GET /api/mode');
        const data = await fetchJson<ModeResponse>('/api/mode');
        if (data?.version) {
          appVersionStore.set(String(data.version));
          log(`Admin Toolkit plugin v${data.version}`);
        }
        if (cancelled) return;
        if (data && data.mode === 'live') {
          log('Detected live API mode');
          recordBackendFreshness(data, log);
          dispatch({ type: 'SET_DATA_SOURCE', payload: 'api' });
          dispatch({ type: 'SET_MODE', payload: 'single' });
          setSource('api');
          setIsDetecting(false);
          return;
        }
        log(`Unexpected /api/mode payload: ${JSON.stringify(data || {})}`, 'warn');
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        log(`/api/mode unavailable (${message}), falling back to ZIP mode`, 'warn');
      }

      if (!cancelled) {
        dispatch({ type: 'SET_DATA_SOURCE', payload: 'api' });
        setSource('api');
        setIsDetecting(false);
        log('API mode detection failed, defaulting to API mode');
      }
    };

    detect();

    return () => {
      cancelled = true;
    };
  }, [dispatch]);

  return { isDetecting, dataSource: source };
}
