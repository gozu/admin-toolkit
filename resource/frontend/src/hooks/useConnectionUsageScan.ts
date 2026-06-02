import { useCallback } from 'react';
import { useDiag } from '../context/DiagContext';
import { fetchRaw } from '../utils/api';
import { parseSseStream } from '../utils/sseStream';
import {
  connectionUsageScanStore,
  getConnectionUsageScanController,
  setConnectionUsageScanController,
  type ConnectionUsageScanError,
} from '../state/connectionUsageScanStore';
import type {
  ConnectionLocalFilesystemUsage,
  ConnectionUsageItem,
  Lifecycle,
} from '../types';

export function useConnectionUsageScan() {
  const { setParsedData } = useDiag();
  const { scanning, scanned, total, error, scanErrors, failedProjectCount, scannedProjectCount } =
    connectionUsageScanStore.use();

  const scan = useCallback(async () => {
    if (connectionUsageScanStore.get().scanning) return;

    const controller = new AbortController();
    setConnectionUsageScanController(controller);

    const startedAt = new Date().toISOString();
    const runningLifecycle = (
      pct: number,
      message?: string,
    ): Lifecycle => ({
      phase: 'running',
      startedAt,
      progressPct: pct,
      message,
      subPhase: 'scan',
      updatedAt: new Date().toISOString(),
    });

    connectionUsageScanStore.patch({
      scanning: true,
      error: null,
      scanned: null,
      total: null,
      scanErrors: [],
      failedProjectCount: 0,
      scannedProjectCount: 0,
    });
    setParsedData({
      connectionDatasetUsages: [],
      connectionLlmUsages: [],
      connectionLocalFilesystemUsages: [],
      connectionUsageTotal: null,
      connectionUsageScanned: null,
      connectionUsageScanErrors: [],
      connectionUsageFailedProjectCount: 0,
      connectionUsageScannedProjectCount: 0,
      connectionUsageLoading: runningLifecycle(0, 'Discovering projects'),
    });

    let scanTotal = 0;
    try {
      const response = await fetchRaw('/api/connections/usages', { signal: controller.signal });

      if (!response.ok || !response.body) {
        const body = await response.text();
        let msg = `Scan failed: ${response.status} ${response.statusText}`;
        try { msg = (JSON.parse(body) as { error?: string }).error || msg; } catch { /* ignore */ }
        throw new Error(msg);
      }

      for await (const { event, payload } of parseSseStream(response.body)) {
        const data = payload as Record<string, unknown>;
        if (event === 'error') {
          throw new Error(String(data.error || 'Scan error'));
        } else if (event === 'init') {
          scanTotal = Number(data.total);
          connectionUsageScanStore.patch({ total: scanTotal });
          setParsedData({
            connectionUsageTotal: scanTotal,
            connectionUsageLoading: runningLifecycle(0, `Scanning ${scanTotal} projects`),
          });
        } else if (event === 'progress') {
          const n = Number(data.scanned);
          connectionUsageScanStore.patch({ scanned: n });
          const pct = scanTotal > 0 ? Math.min(99, Math.round((n / scanTotal) * 100)) : 0;
          setParsedData({
            connectionUsageScanned: n,
            connectionUsageLoading: runningLifecycle(pct, `Scanned ${n} / ${scanTotal} projects`),
          });
        } else if (event === 'done') {
          const dataset = (data.datasetUsages || []) as ConnectionUsageItem[];
          const llm = (data.llmUsages || []) as ConnectionUsageItem[];
          const fs = (data.localFilesystemUsages || []) as ConnectionLocalFilesystemUsage[];
          const scanErrors = (data.scanErrors || []) as ConnectionUsageScanError[];
          const failedProjectCount = Number(data.failedProjectCount) || 0;
          const scannedProjectCount =
            data.scannedProjectCount != null ? Number(data.scannedProjectCount) : scanTotal;
          connectionUsageScanStore.patch({
            scanned: scanTotal,
            scanErrors,
            failedProjectCount,
            scannedProjectCount,
          });
          setParsedData({
            connectionDatasetUsages: dataset,
            connectionLlmUsages: llm,
            connectionLocalFilesystemUsages: fs,
            connectionUsageScanned: scanTotal,
            connectionUsageScanErrors: scanErrors,
            connectionUsageFailedProjectCount: failedProjectCount,
            connectionUsageScannedProjectCount: scannedProjectCount,
            connectionUsageLoading: {
              phase: 'done',
              startedAt,
              finishedAt: new Date().toISOString(),
              isEmpty: dataset.length === 0 && llm.length === 0 && fs.length === 0,
              message: `Scanned ${scanTotal} projects`,
            },
          });
        }
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        setParsedData({ connectionUsageLoading: { phase: 'queued' } });
        return;
      }
      const msg = err instanceof Error ? err.message : String(err);
      connectionUsageScanStore.patch({ error: msg });
      setParsedData({
        connectionUsageLoading: {
          phase: 'error',
          startedAt,
          finishedAt: new Date().toISOString(),
          error: msg,
          progressPct: 0,
        },
      });
    } finally {
      connectionUsageScanStore.patch({ scanning: false });
      setConnectionUsageScanController(null);
    }
  }, [setParsedData]);

  const abort = useCallback(() => {
    getConnectionUsageScanController()?.abort();
    connectionUsageScanStore.patch({ scanning: false });
    setConnectionUsageScanController(null);
    setParsedData({ connectionUsageLoading: { phase: 'queued' } });
  }, [setParsedData]);

  return {
    scanning,
    scanned,
    total,
    error,
    scanErrors,
    failedProjectCount,
    scannedProjectCount,
    scan,
    abort,
  };
}
