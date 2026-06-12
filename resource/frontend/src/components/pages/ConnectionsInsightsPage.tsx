import { ConnectionsInsightsTable } from '../connections/ConnectionsInsightsTable';
import { useDiag } from '../../context/DiagContext';
import { useConnectionUsageScan } from '../../hooks/useConnectionUsageScan';
import { resolveLifecycleById } from '../../utils/pageLifecycle';
import { Spinner } from '../common/Spinner';

export function ConnectionsInsightsPage() {
  // The scan is auto-triggered by useApiDataLoader at session start.
  // This page renders the result and exposes rescan/abort for the usage scan,
  // but the loading *banner* reflects the composite lifecycle of every
  // dependency (inventory + usage + health + audit).
  const { scanning, scan, abort } = useConnectionUsageScan();
  const { state } = useDiag();
  const pageLifecycle = resolveLifecycleById('connections-insights', state.parsedData);

  const showBanner =
    pageLifecycle.phase === 'running' ||
    pageLifecycle.phase === 'queued' ||
    pageLifecycle.phase === 'error';

  const bannerMessage =
    pageLifecycle.phase === 'error'
      ? pageLifecycle.error
      : pageLifecycle.phase === 'running'
        ? pageLifecycle.message || 'Loading insights…'
        : 'Loading insights…';

  return (
    <div className="page-fill">
      <div className="flex flex-col gap-4 flex-1 min-h-0">
        <div className="rounded-lg px-4 py-3 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
            {showBanner && (
              <>
                {pageLifecycle.phase === 'running' && <Spinner />}
                {pageLifecycle.phase === 'error' ? (
                  <span className="text-[var(--neon-red)]">
                    <span className="font-medium">Insights error:</span> {bannerMessage}
                  </span>
                ) : (
                  <span>{bannerMessage}</span>
                )}
              </>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={scan}
              disabled={scanning}
              className="px-3 py-1 rounded-md text-xs font-medium text-[var(--text-secondary)] border border-[var(--text-tertiary)]/30 hover:bg-[var(--bg-glass-hover)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {scanning ? 'Rescanning…' : 'Rescan'}
            </button>
            {scanning && (
              <button
                onClick={abort}
                className="px-3 py-1 rounded-md text-xs font-medium text-[var(--text-secondary)] border border-[var(--text-tertiary)]/30 hover:bg-[var(--bg-glass-hover)] transition-colors"
              >
                Abort
              </button>
            )}
          </div>
        </div>
        <ConnectionsInsightsTable />
      </div>
    </div>
  );
}
