import { useSyncExternalStore } from 'react';
import { useDiag } from '../../context/DiagContext';
import {
  resourceSamplesStore,
  startResourcePolling,
  stopResourcePolling,
} from '../../state/resourceSamples';
import { getProcessMetrics, subscribeProcessMetrics } from '../../state/processMetrics';

/**
 * Resources-page counterpart of `RefreshControl` for the live sampler: the
 * "as of HH:MM:SS" label always shows the last successful update (never
 * blanks while a refresh is in flight — updates land in place every second),
 * and the single button is a whole-page Stop/Start toggle for the sampler
 * (stream on local, poll chain on remote). Same span/button styling as
 * RefreshControl so the two controls read as one family.
 */
function formatClock(epochMs: number): string {
  return new Date(epochMs).toLocaleTimeString([], { hour12: false });
}

export function LiveRefreshToggle() {
  const { setParsedData } = useDiag();
  const { status, samples } = resourceSamplesStore.use();
  const scan = useSyncExternalStore(subscribeProcessMetrics, getProcessMetrics, getProcessMetrics);

  const lastSampleMs = samples.length > 0 ? samples[samples.length - 1].ts * 1000 : 0;
  const lastScanMs = scan.finishedAt ? new Date(scan.finishedAt).getTime() : 0;
  const asOfMs = Math.max(lastSampleMs, lastScanMs);
  const running = status === 'polling' || status === 'paused';

  return (
    <span className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
      {asOfMs > 0 && (
        <span className="font-mono tabular-nums">as of {formatClock(asOfMs)}</span>
      )}
      <button
        type="button"
        onClick={() => {
          if (running) stopResourcePolling();
          else startResourcePolling(setParsedData);
        }}
        title={running ? 'Stop live sampling on this page' : 'Resume live sampling'}
        className="rounded px-2 py-1 text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)]"
      >
        {running ? 'Stop' : 'Start'}
      </button>
    </span>
  );
}
