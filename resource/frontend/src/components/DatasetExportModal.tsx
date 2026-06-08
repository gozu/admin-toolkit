import { useEffect, useState } from 'react';
import { Modal } from './Modal';
import { ProgressIndicator } from './common/ProgressIndicator';
import { collectTablesForDataset } from '../utils/exportTables';
import { fetchJson } from '../utils/api';

interface SaveResult {
  name: string;
  datasetName: string;
  status: 'created' | 'overwritten' | 'error';
  rows: number;
  error?: string;
}

interface SaveResponse {
  project: string;
  connection: string;
  results: SaveResult[];
}

interface DatasetExportModalProps {
  isOpen: boolean;
  onClose: () => void;
  connection: string;
}

type Phase = 'saving' | 'done' | 'error' | 'empty';

// Per-row status → the shared ProgressIndicator color semantics
// (white = completed-neutral, red = failure). No progress bar per row.
function statusDotClass(status: SaveResult['status']): string {
  return status === 'error' ? 'bg-[var(--neon-red)]' : 'bg-white';
}

function statusLabel(status: SaveResult['status']): string {
  if (status === 'created') return 'Created';
  if (status === 'overwritten') return 'Overwritten';
  return 'Error';
}

export function DatasetExportModal({ isOpen, onClose, connection }: DatasetExportModalProps) {
  const [phase, setPhase] = useState<Phase>('saving');
  const [response, setResponse] = useState<SaveResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;

    // All state updates live in this nested async runner so none sit
    // synchronously in the effect body (react-hooks/set-state-in-effect).
    const run = async () => {
      setResponse(null);
      setError(null);

      const tables = collectTablesForDataset();
      if (!tables.length) {
        if (!cancelled) setPhase('empty');
        return;
      }

      if (!cancelled) setPhase('saving');
      try {
        const data = await fetchJson<SaveResponse>('/api/tools/dataset-export/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tables }),
        });
        if (cancelled) return;
        setResponse(data);
        setPhase('done');
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setPhase('error');
      }
    };
    void run();

    return () => {
      cancelled = true;
    };
  }, [isOpen]);

  const results = response?.results ?? [];
  const okCount = results.filter((r) => r.status !== 'error').length;
  const errCount = results.length - okCount;

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Save Tables as Datasets">
      <div className="space-y-4">
        {phase === 'empty' && (
          <p className="text-sm text-[var(--text-muted)]">No tables on this page to save.</p>
        )}

        {phase === 'saving' && (
          <ProgressIndicator
            active
            message={
              connection
                ? `Saving tables as datasets → ${connection}…`
                : 'Saving tables as datasets…'
            }
          />
        )}

        {phase === 'error' && (
          <div className="space-y-1">
            <p className="text-sm font-medium text-[var(--neon-red)]">Could not save tables.</p>
            {error && <p className="text-xs text-[var(--neon-red)] break-words">{error}</p>}
          </div>
        )}

        {phase === 'done' && response && (
          <>
            <div className="text-sm text-[var(--text-secondary)]">
              Saved{' '}
              <span className="font-medium text-[var(--text-primary)]">{okCount}</span>
              {okCount === 1 ? ' dataset' : ' datasets'}
              {errCount > 0 && (
                <>
                  {' '}·{' '}
                  <span className="font-medium text-[var(--neon-red)]">{errCount}</span> failed
                </>
              )}{' '}
              to project{' '}
              <span className="font-mono text-[var(--text-primary)]">{response.project}</span>{' '}
              on connection{' '}
              <span className="font-mono text-[var(--text-primary)]">{response.connection}</span>.
            </div>

            <ul className="space-y-1.5">
              {results.map((r, i) => (
                <li
                  key={`${r.datasetName}-${i}`}
                  className="flex items-center gap-3 rounded-lg border border-[var(--border-default)] bg-[var(--bg-glass)] px-3 py-2"
                >
                  <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${statusDotClass(r.status)}`} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-2">
                      <span className="font-mono text-sm text-[var(--text-primary)] truncate">
                        {r.datasetName}
                      </span>
                      {r.name && r.name !== r.datasetName && (
                        <span className="text-xs text-[var(--text-muted)] truncate">({r.name})</span>
                      )}
                    </div>
                    {r.status === 'error' && r.error && (
                      <p className="text-xs text-[var(--neon-red)] break-words">{r.error}</p>
                    )}
                  </div>
                  <div className="shrink-0 text-right">
                    <div className="text-xs font-medium text-[var(--text-secondary)]">
                      {statusLabel(r.status)}
                    </div>
                    {r.status !== 'error' && (
                      <div className="text-[10px] text-[var(--text-muted)]">
                        {r.rows} {r.rows === 1 ? 'row' : 'rows'}
                      </div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </Modal>
  );
}
