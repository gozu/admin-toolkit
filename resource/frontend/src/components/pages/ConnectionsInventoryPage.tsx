import { useDiag } from '../../context/DiagContext';
import { ConnectionsChart } from '../index';

export function ConnectionsInventoryPage() {
  const { state } = useDiag();
  const { parsedData } = state;

  const hasConnections =
    (parsedData.connections && Object.keys(parsedData.connections).length > 0) ||
    (parsedData.connectionCounts && Object.keys(parsedData.connectionCounts).length > 0);

  return (
    <div className="w-full py-4 flex flex-col gap-4">
      {hasConnections ? (
        <ConnectionsChart />
      ) : (
        <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface)] p-8 text-center">
          <p className="text-[var(--text-secondary)]">No connection data available.</p>
        </div>
      )}
    </div>
  );
}
