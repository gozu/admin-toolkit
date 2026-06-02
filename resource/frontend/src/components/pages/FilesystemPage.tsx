import { useDiag } from '../../context/DiagContext';
import { ApiDirTreeSection, FilesystemChart } from '../index';

export function FilesystemPage() {
  const { state } = useDiag();
  const { parsedData } = state;

  const hasFilesystem = parsedData.filesystemInfo && parsedData.filesystemInfo.length > 0;

  return (
    <div className="page-fill">
      <div className="flex flex-col gap-6 flex-1 min-h-0">
        {hasFilesystem && (
          <div className="w-full">
            <FilesystemChart />
          </div>
        )}

        {!hasFilesystem && (
          <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface)] p-8 text-center">
            <p className="text-[var(--text-secondary)]">No filesystem data available.</p>
          </div>
        )}

        <ApiDirTreeSection />
      </div>
    </div>
  );
}
