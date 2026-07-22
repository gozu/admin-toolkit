import { useEffect, useSyncExternalStore } from 'react';
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

/** Merged Memory + CPU page. One SSE stream per host feeds the utilization
 * strip, doughnuts and process table: 1s /proc ticks locally; macro ticks on
 * remote hosts at the user-picked period (default 1s, Live usage header). */
export function ResourcesPage() {
  const { state } = useDiag();
  const { parsedData } = state;
  const pollStatus = useSyncExternalStore(
    resourceSamplesStore.subscribe,
    () => resourceSamplesStore.get().status,
    () => resourceSamplesStore.get().status,
  );

  useEffect(() => {
    startProcessMetricsScan();
    startResourcePolling();
    return () => stopResourcePolling();
  }, []);

  const hasMemory = parsedData.memoryInfo && Object.keys(parsedData.memoryInfo).length > 0;

  return (
    <div className="page-fill gap-6">
      {pollStatus !== 'unsupported' && <LiveResourceChart />}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <CpuChart />
        <MemoryChart />
      </div>
      {hasMemory && <MemoryAnalysisCard />}
      <ProcessUsageTable variant="resources" />
    </div>
  );
}
