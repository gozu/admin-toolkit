import { useEffect, useState } from 'react';
import { useDiag } from '../context/DiagContext';
import type { DataSource } from '../types';
import { fetchJson } from '../utils/api';

interface ModeResponse {
  mode?: string;
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
      log(`Diag Parser version v${__APP_VERSION__}`);
      log('Starting data source detection');
      try {
        log('GET /api/mode');
        const data = await fetchJson<ModeResponse>('/api/mode');
        if (cancelled) return;
        if (data && data.mode === 'live') {
          log('Detected live API mode');
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
