import { useEffect } from 'react';
import { useDiag } from '../../context/DiagContext';
import { MemoryChart, MemoryAnalysisCard } from '../index';
import { CpuChart } from '../CpuChart';
import { ProcessUsageTable } from '../ProcessUsageTable';
import { LiveResourceChart } from '../resources/LiveResourceChart';
import { startProcessMetricsScan } from '../../state/processMetrics';
import {
  resourceSamplesStore,
  startResourcePolling,
  stopResourcePolling,
} from '../../state/resourceSamples';

/** Merged Memory + CPU page. In API mode it additionally runs the live
 * sampler that feeds the utilization strip, doughnuts and process table: one
 * SSE stream (1s server ticks) on the local host, or a 15s poll chain plus a
 * 60s `ps`+host-summary heavy tier on remote hosts. Zip mode keeps the static
 * doughnuts/table exactly as before — no sampling. */
export function ResourcesPage() {
  const { state, setParsedData } = useDiag();
  const { parsedData } = state;
  const isApi = state.dataSource === 'api';
  const { status: pollStatus } = resourceSamplesStore.use();

  useEffect(() => {
    if (!isApi) return;
    startProcessMetricsScan();
    startResourcePolling(setParsedData);
    return () => stopResourcePolling();
  }, [isApi, setParsedData]);

  const hasMemory = parsedData.memoryInfo && Object.keys(parsedData.memoryInfo).length > 0;

  return (
    <div className="page-fill gap-6">
      {isApi && pollStatus !== 'unsupported' && <LiveResourceChart />}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <CpuChart />
        {hasMemory ? (
          <MemoryChart />
        ) : (
          <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface)] p-8 text-center">
            <p className="text-[var(--text-secondary)]">No memory data available.</p>
          </div>
        )}
      </div>
      {hasMemory && <MemoryAnalysisCard />}
      <ProcessUsageTable variant="resources" />
    </div>
  );
}
