import { useDiag } from '../../context/DiagContext';
import { LocalFilesystemMigrationCard } from '../LocalFilesystemMigrationCard';

export function ConnectionsFsMigrationPage() {
  const { state } = useDiag();
  const { parsedData } = state;

  const hasConnections =
    (parsedData.connections && Object.keys(parsedData.connections).length > 0) ||
    (parsedData.connectionCounts && Object.keys(parsedData.connectionCounts).length > 0);

  return (
    <div className="page-fill gap-4">
      {hasConnections ? (
        <LocalFilesystemMigrationCard />
      ) : (
        <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface)] p-8 text-center">
          <p className="text-[var(--text-secondary)]">No connection data available.</p>
        </div>
      )}
    </div>
  );
}
