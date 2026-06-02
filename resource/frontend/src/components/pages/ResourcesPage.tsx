import { useDiag } from '../../context/DiagContext';
import { FilesystemChart, MemoryChart, MemoryAnalysisCard } from '../index';

export function ResourcesPage() {
  const { state } = useDiag();
  const { parsedData } = state;

  const hasFilesystem = parsedData.filesystemInfo && parsedData.filesystemInfo.length > 0;
  const hasMemory = parsedData.memoryInfo && Object.keys(parsedData.memoryInfo).length > 0;

  return (
    <div className="w-full py-4">
      <div className="space-y-6">
        {hasFilesystem && (
          <div className="w-full">
            <FilesystemChart />
          </div>
        )}

        {hasMemory && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <MemoryChart />
            <MemoryAnalysisCard />
          </div>
        )}

        {!hasFilesystem && !hasMemory && (
          <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface)] p-8 text-center">
            <p className="text-[var(--text-secondary)]">No resource data available.</p>
          </div>
        )}
      </div>
    </div>
  );
}
