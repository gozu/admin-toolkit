import { useDiag } from '../../context/DiagContext';
import { scanActivityStore } from '../../state/scanActivityStore';
import { overallScanStatus } from '../../utils/overallScanStatus';
import { MODULES } from '../../utils/moduleRegistry';
import { ProgressIndicator } from './ProgressIndicator';

export function OverallScanIndicator() {
  const { state, setActivePage } = useDiag();
  const backgroundWork = scanActivityStore.use();
  const directoryBusy = state.apiDirTree.isLoading || state.apiDirTree.isExpanding;
  const { busy, pending, failed } = overallScanStatus(
    state.parsedData,
    backgroundWork > 0 || directoryBusy,
    state.error,
  );
  if (state.dataSource !== 'api') return null;
  const errors = !!state.error || failed.length > 0 || !!state.apiDirTree.error;
  const label = busy ? 'Scanning' : errors ? 'Scan incomplete' : 'Scan complete';
  const color = busy
    ? 'text-[var(--neon-yellow)]'
    : errors
      ? 'text-[var(--neon-red)]'
      : 'text-[var(--neon-green)]';
  return (
    <details
      className="relative w-36 shrink-0 text-[10px]"
      data-overall-scan={busy ? 'busy' : errors ? 'error' : 'done'}
    >
      <summary
        className={`flex cursor-pointer list-none items-center gap-2 rounded px-1 py-1 ${color}`}
        aria-label={`${label}. View scan status`}
      >
        <svg
          aria-hidden
          viewBox="0 0 20 20"
          fill="none"
          className={`h-4 w-4 shrink-0 ${busy ? 'animate-spin motion-reduce:animate-none' : ''}`}
        >
          {busy ? (
            <>
              <circle cx="10" cy="10" r="7" stroke="currentColor" strokeWidth="2" opacity=".2" />
              <path
                d="M10 3a7 7 0 0 1 7 7"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </>
          ) : errors ? (
            <>
              <circle cx="10" cy="10" r="7" stroke="currentColor" strokeWidth="1.5" />
              <path
                d="M10 6v5m0 3h.01"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </>
          ) : (
            <path
              d="m4 10 4 4 8-8"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          )}
        </svg>
        <span role="status">{label}</span>
        {busy && <span className="font-mono text-[var(--text-muted)]">{pending.length || ''}</span>}
      </summary>
      <div className="absolute left-0 top-full z-50 mt-2 max-h-96 w-80 overflow-auto rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface)] p-3 shadow-xl">
        <p className="mb-3 text-[var(--text-secondary)]">
          {busy
            ? 'Waiting for every scan and enrichment to finish.'
            : errors
              ? 'Scanning stopped with errors. Some results are incomplete.'
              : 'All scheduled scans and enrichments have finished.'}
        </p>
        {state.error && <p className="mb-3 text-[var(--neon-red)]">{state.error}</p>}
        {!state.parsedData.dataReady && busy && (
          <p className="mb-3">Loading data and enrichment tails…</p>
        )}
        {directoryBusy && <p className="mb-3">Scanning filesystem…</p>}
        {backgroundWork > 0 && (
          <p className="mb-3">Background discovery: {backgroundWork} pending</p>
        )}
        {[...pending, ...failed].map(({ field, lifecycle }) => {
          const mod = MODULES.find((m) => m.lifecycle.fields.includes(field));
          return (
            <div key={field} className="mt-3">
              <button
                className="mb-1 text-[var(--text-primary)] hover:underline"
                onClick={() => mod && setActivePage(mod.id)}
              >
                {mod?.label || field}
              </button>
              <ProgressIndicator lifecycle={lifecycle} compact />
            </div>
          );
        })}
        {state.apiDirTree.error && (
          <p className="mt-3 text-[var(--neon-red)]">Filesystem: {state.apiDirTree.error}</p>
        )}
      </div>
    </details>
  );
}
