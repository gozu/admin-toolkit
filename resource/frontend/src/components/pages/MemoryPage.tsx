import { useDiag } from '../../context/DiagContext';
import { MemoryChart, MemoryAnalysisCard } from '../index';
import { ProcessUsageTable } from '../ProcessUsageTable';

export function MemoryPage() {
  const { state } = useDiag();
  const { parsedData } = state;

  const hasMemory = parsedData.memoryInfo && Object.keys(parsedData.memoryInfo).length > 0;

  return (
    <div className="page-fill gap-6">
      {hasMemory ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <MemoryChart />
          <MemoryAnalysisCard />
        </div>
      ) : (
        <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface)] p-8 text-center">
          <p className="text-[var(--text-secondary)]">No memory data available.</p>
        </div>
      )}
      <ProcessUsageTable variant="memory" />
    </div>
  );
}
