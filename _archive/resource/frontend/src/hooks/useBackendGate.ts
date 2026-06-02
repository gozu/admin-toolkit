import { useEffect, useState } from 'react';
import { fetchJson } from '../utils/api';

interface BackendStatus {
  sql_connection_configured: boolean;
  sql_connection_healthy: boolean | null;
  instance_has_compatible_sql: boolean | null;
  table_prefix: string | null;
  effective_backend: 'sql' | 'sqlite' | 'unconfigured';
  connection_name: string | null;
  sqlite_exists: boolean;
  sqlite_has_data: boolean;
  migration_running: boolean;
  error?: string;
}

interface BackendGate {
  /** True while the status check is in-flight */
  isLoading: boolean;
  /** Compatible SQL exists but none configured — block the app */
  needsSetup: boolean;
  /** Running on SQLite fallback — show warning banner */
  isSqliteFallback: boolean;
  /** Ready to proceed to normal app */
  isReady: boolean;
  /** Raw status for debug/settings */
  status: BackendStatus | null;
  error: string | null;
}

export function useBackendGate(): BackendGate {
  const [status, setStatus] = useState<BackendStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchJson<BackendStatus>('/api/tracking/backend-status')
      .then((data) => {
        if (!cancelled) {
          setStatus(data);
          setIsLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err?.message ?? 'Failed to check backend status');
          setIsLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, []);

  if (isLoading || !status) {
    return { isLoading: true, needsSetup: false, isSqliteFallback: false, isReady: false, status: null, error };
  }

  // SQL connections exist on instance but none configured → block
  const needsSetup = !status.sql_connection_configured && status.instance_has_compatible_sql === true;

  // SQLite fallback (no SQL on instance)
  const isSqliteFallback = status.effective_backend === 'sqlite';

  const isReady = !needsSetup;

  return { isLoading: false, needsSetup, isSqliteFallback, isReady, status, error };
}
