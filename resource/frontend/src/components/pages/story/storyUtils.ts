/** Non-component helpers for the Story pages (kept out of storyShared.tsx so
 *  react-refresh sees component-only files). */
import type { Lifecycle } from '../../../types';

interface SlotLike {
  loading: boolean;
  error: string | null;
  loaded: boolean;
}

const EPOCH = new Date(0).toISOString();

/** Bridge a storyStore slot onto the DataGrid/ProgressIndicator Lifecycle. */
export function slotLifecycle(slot: SlotLike, isEmpty = false): Lifecycle {
  if (slot.loading) {
    return { phase: 'running', startedAt: EPOCH, progressPct: 50, updatedAt: EPOCH };
  }
  if (slot.error) {
    return { phase: 'error', startedAt: EPOCH, finishedAt: EPOCH, error: slot.error, progressPct: 0 };
  }
  if (slot.loaded) {
    return { phase: 'done', startedAt: EPOCH, finishedAt: EPOCH, isEmpty };
  }
  return { phase: 'queued' };
}

export const STORY_PRIMARY_BUTTON =
  'px-3 py-1.5 rounded text-sm font-medium bg-[var(--accent)]/20 text-[var(--accent)] ' +
  'hover:bg-[var(--accent)]/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed';

export const STORY_SECONDARY_BUTTON =
  'px-3 py-1.5 rounded text-sm bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] ' +
  'text-[var(--text-secondary)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed';
