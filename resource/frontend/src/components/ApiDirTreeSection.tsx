import { useCallback, useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { DirTreemap } from './DirTreemap';
import { DirTreeTable, type DirDeleteState } from './DirTreeTable';
import { UnlockModal } from './UnlockModal';
import { ConfirmDeleteDialog } from './common/ConfirmDeleteDialog';
import { useApiDirTree } from '../hooks';
import { useRedVisible } from '../state/redUnlockStore';
import { ApiRequestError, fetchJson } from '../utils/api';
import { formatBytes } from '../utils/formatters';
import type { DirEntry } from '../types';

// ── orphan-project deletion ─────────────────────────────────────────────────
// DSS reports `orphanProjects` (on-disk artifacts of projects that no longer
// exist) but has no API to reclaim them, so the delete goes through the
// fs-cleanup macro's `orphans` policy. Everything safety-relevant — the
// containment floor, the live-project check and the refusal of DSS's own false
// positives (a shared bucket whose children name live projects) — is decided
// in the macro; this component only reports what it says.

const ORPHAN_PREFIX = '/dss-data/orphanProjects/';
// The delete cap the macro enforces. Orphan leftovers are small; a scan over
// this means something unexpected is in scope, and we want the refusal.
const MAX_DELETE_GB = 50;

interface OrphanArea {
  area: string;
  path: string;
  bytes: number;
  deletable: boolean;
  reason: string;
}

interface OrphanEntry {
  areas: OrphanArea[];
  bytes: number;
  deletableAreas: number;
  blockedAreas: number;
}

interface OrphanScanResult {
  ok?: boolean;
  error?: string;
  message?: string;
  runAsUser?: string;
  orphans?: Record<string, OrphanEntry>;
}

interface OrphanDeleteResult {
  ok?: boolean;
  error?: string;
  message?: string;
  runAsUser?: string;
  partial?: boolean;
  totalReclaimedBytes?: number;
  failedPaths?: { path: string; errno?: number | null; message?: string }[];
}

/** The orphan key a tree node targets, or null when the node is not an
 *  orphan-project node. Both levels DSS describes qualify:
 *  /dss-data/orphanProjects/<KEY> and .../<KEY>/<area>. */
function orphanKeyFor(node: DirEntry): string | null {
  if (!node.path.startsWith(ORPHAN_PREFIX)) return null;
  const segments = node.path.slice(ORPHAN_PREFIX.length).split('/').filter(Boolean);
  if (segments.length < 1 || segments.length > 2) return null;
  return segments[0];
}

export function ApiDirTreeSection() {
  const { state, loadRoot, abortLoad, expandDirectory, schedulePrefetch, cancelPrefetch } = useApiDirTree();
  const [activeNode, setActiveNode] = useState<DirEntry | null>(null);
  const redVisible = useRedVisible();

  // Feature state lives here, above DirTreeTable — the table wipes its own
  // local state whenever the treemap drill-down re-anchors it.
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [pending, setPending] = useState<{ key: string; entry: OrphanEntry; runAsUser?: string } | null>(null);
  const [blockedKeys, setBlockedKeys] = useState<Record<string, string>>({});
  const [deleting, setDeleting] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: 'ok' | 'warn' | 'error'; text: string } | null>(null);
  const [showUnlock, setShowUnlock] = useState(false);
  const noticeRef = useRef<HTMLDivElement | null>(null);

  const scope = state.scope;
  const projectKey = state.projectKey;

  // The banner sits above the treemap, but the row that triggers it is usually
  // far down a scrolled table — a refusal the user never sees is not an
  // explanation. Bring it into view whenever it changes.
  useEffect(() => {
    if (notice) noticeRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [notice]);

  const handleLoad = useCallback(() => {
    if (!state.isLoading) {
      loadRoot({ scope, projectKey });
    }
  }, [loadRoot, scope, projectKey, state.isLoading]);

  useEffect(() => {
    if (!state.tree && !state.isLoading && !state.error) {
      loadRoot();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => () => {
    cancelPrefetch();
  }, [cancelPrefetch]);

  const handleApiError = useCallback((exc: unknown, fallback: string): string => {
    if (exc instanceof ApiRequestError) {
      const body = exc.body as { error?: string; message?: string } | undefined;
      if (exc.status === 403 && body?.error === 'advanced-locked') {
        // utils/api.ts already flipped the store to locked; offer the unlock.
        setShowUnlock(true);
        return 'Agentic Actions are locked — unlock to delete orphan-project files.';
      }
      return body?.message || body?.error || exc.message || fallback;
    }
    return exc instanceof Error ? exc.message : fallback;
  }, []);

  // Click → scan the orphan key. The scan is what decides whether a delete is
  // even offered: a key whose every location is refused becomes a ⛔ row.
  const handleDeleteNode = useCallback(async (node: DirEntry) => {
    const key = orphanKeyFor(node);
    if (!key || busyKey) return;
    setNotice(null);
    setDialogError(null);
    setBusyKey(key);
    try {
      const scan = await fetchJson<OrphanScanResult>(
        `/api/tools/fs-cleanup/scan?policy=orphans&projectKey=${encodeURIComponent(key)}`,
      );
      const entry = scan.orphans?.[key];
      if (!entry || entry.areas.length === 0) {
        setNotice({
          tone: 'warn',
          text: `${key} is no longer on disk under any orphan-project location — reload the tree.`,
        });
        return;
      }
      if (entry.deletableAreas === 0) {
        const reason = entry.areas[0]?.reason || 'refused by the fs-cleanup policy';
        setBlockedKeys((prev) => ({ ...prev, [key]: reason }));
        setNotice({
          tone: 'error',
          text: `${key} cannot be deleted: ${reason}. DSS classifies it as an orphan project, `
            + 'but the fs-cleanup policy refuses it — there is no override.',
        });
        return;
      }
      setPending({ key, entry, runAsUser: scan.runAsUser });
    } catch (exc) {
      setNotice({ tone: 'error', text: handleApiError(exc, `Could not scan ${key}.`) });
    } finally {
      setBusyKey(null);
    }
  }, [busyKey, handleApiError]);

  const handleConfirmDelete = useCallback(async () => {
    if (!pending) return;
    const { key } = pending;
    setDeleting(true);
    setDialogError(null);
    try {
      const result = await fetchJson<OrphanDeleteResult>('/api/tools/fs-cleanup/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          policy: 'orphans', projectKey: key, dryRun: false, maxDeleteGB: MAX_DELETE_GB,
        }),
      });
      if (!result.ok) {
        setDialogError(result.message || result.error || 'The macro refused the delete.');
        return;
      }
      const reclaimed = formatBytes(result.totalReclaimedBytes || 0);
      const failed = result.failedPaths || [];
      setPending(null);
      setNotice(failed.length > 0 || result.partial
        ? {
          tone: 'warn',
          text: `${key} was partially deleted — ${reclaimed} reclaimed, ${failed.length} `
            + `path${failed.length === 1 ? '' : 's'} could not be removed as `
            + `${result.runAsUser || 'the macro user'}: `
            + failed.slice(0, 3).map((f) => f.path).join(', ')
            + (failed.length > 3 ? ` (+${failed.length - 3} more)` : ''),
        }
        : { tone: 'ok', text: `Deleted the on-disk files of ${key} — ${reclaimed} reclaimed.` });
      await loadRoot({ scope, projectKey });
    } catch (exc) {
      setDialogError(handleApiError(exc, `Could not delete ${key}.`));
    } finally {
      setDeleting(false);
    }
  }, [pending, loadRoot, scope, projectKey, handleApiError]);

  const deleteStateFor = useCallback((node: DirEntry): DirDeleteState => {
    const key = orphanKeyFor(node);
    if (!key) return { state: 'none' };
    if (blockedKeys[key]) return { state: 'blocked', reason: blockedKeys[key] };
    if (busyKey === key || (deleting && pending?.key === key)) return { state: 'deleting' };
    return { state: 'ready' };
  }, [blockedKeys, busyKey, deleting, pending]);

  if (state.isLoading && !state.tree) {
    return (
      <div className="col-span-full">
        <motion.div
          className="glass-card p-6"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
        >
          <h3 className="text-lg font-semibold text-neon-subtle mb-4">
            Directory Space Analysis
          </h3>
          <p className="text-sm text-[var(--text-muted)]">Loading directory tree from server (DSS Data Directory)...</p>
          <button
            onClick={abortLoad}
            className="mt-4 px-4 py-2 text-sm rounded bg-[var(--status-warning-bg)] border border-[var(--status-warning-border)] text-[var(--text-primary)] hover:opacity-90 transition-colors"
          >
            Abort
          </button>
        </motion.div>
      </div>
    );
  }

  if (state.error) {
    return (
      <div className="col-span-full">
        <motion.div
          className="glass-card p-6 border-l-4 border-[var(--neon-red)]"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <h3 className="text-lg font-semibold text-[var(--neon-red)] mb-2">
            Failed to Load Directory Analysis
          </h3>
          <p className="text-sm text-[var(--text-muted)]">{state.error}</p>
          <button
            onClick={handleLoad}
            className="mt-4 px-4 py-2 text-sm rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-[var(--text-secondary)] transition-colors"
          >
            Retry
          </button>
        </motion.div>
      </div>
    );
  }

  if (!state.tree) {
    return (
      <div className="col-span-full">
        <motion.div
          className="glass-card p-6"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
        >
          <h3 className="text-lg font-semibold text-neon-subtle mb-4">
            Directory Space Analysis
          </h3>
          <p className="text-sm text-[var(--text-muted)] mb-4">Analyze disk usage of the DSS Data Directory.</p>
          <button
            onClick={handleLoad}
            disabled={state.isLoading}
            className="px-4 py-2 text-sm rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-[var(--text-secondary)] transition-colors disabled:opacity-60"
          >
            {state.isLoading ? 'Loading...' : 'Load Directory Tree'}
          </button>
        </motion.div>
      </div>
    );
  }

  const deletable = pending?.entry.areas.filter((a) => a.deletable) ?? [];
  const blockedAreas = pending?.entry.areas.filter((a) => !a.deletable) ?? [];
  const deletableBytes = deletable.reduce((sum, a) => sum + a.bytes, 0);

  return (
    <div className="col-span-full flex flex-col flex-1 min-h-0">
      {notice && (
        <motion.div
          ref={noticeRef}
          initial={{ opacity: 0, y: -6 }}
          animate={{ opacity: 1, y: 0 }}
          className={`mb-3 px-3 py-2 rounded text-sm border ${
            notice.tone === 'ok'
              ? 'border-[var(--status-success-border)] bg-[var(--status-success-bg)] text-[var(--text-primary)]'
              : notice.tone === 'warn'
                ? 'border-[var(--status-warning-border)] bg-[var(--status-warning-bg)] text-[var(--text-primary)]'
                : 'border-[var(--neon-red)] bg-[var(--bg-glass)] text-[var(--text-primary)]'
          }`}
        >
          <div className="flex items-start justify-between gap-3">
            <span>{notice.text}</span>
            <button
              type="button"
              onClick={() => setNotice(null)}
              className="text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)] shrink-0"
            >
              Dismiss
            </button>
          </div>
        </motion.div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 flex-1 min-h-0">
        <DirTreemap
          data={state.tree}
          onExpand={expandDirectory}
          expandedNodes={state.expandedNodes}
          isExpanding={state.isExpanding}
          onVisibleDirectoriesChange={schedulePrefetch}
          onActiveNodeChange={setActiveNode}
        />
        <DirTreeTable
          data={state.tree}
          onExpand={expandDirectory}
          expandedNodes={state.expandedNodes}
          isExpanding={state.isExpanding}
          rootNode={activeNode}
          onDeleteNode={redVisible ? handleDeleteNode : undefined}
          deleteStateFor={redVisible ? deleteStateFor : undefined}
        />
      </div>

      <ConfirmDeleteDialog
        isOpen={!!pending}
        onClose={() => { setPending(null); setDialogError(null); }}
        title={`Delete orphan project files — ${pending?.key ?? ''}`}
        confirmPhrase={`delete ${pending?.key ?? ''}`}
        confirmLabel={`Delete ${formatBytes(deletableBytes)}`}
        loadingLabel="Deleting…"
        loading={deleting}
        error={dialogError}
        onConfirm={handleConfirmDelete}
      >
        <p className="text-sm text-[var(--text-secondary)]">
          DSS reports <span className="font-mono">{pending?.key}</span> as an orphan project — its
          on-disk artifacts remain but the project itself is gone. This permanently removes{' '}
          {deletable.length} location{deletable.length === 1 ? '' : 's'} from disk. There is no backup.
        </p>
        <ul className="space-y-1 text-xs font-mono">
          {deletable.map((area) => (
            <li key={area.path} className="flex items-center justify-between gap-3">
              <span className="truncate text-[var(--text-primary)]" title={area.path}>{area.path}</span>
              <span className="shrink-0 text-[var(--text-muted)]">{formatBytes(area.bytes)}</span>
            </li>
          ))}
        </ul>
        {blockedAreas.length > 0 && (
          <div className="text-xs">
            <div className="text-[var(--text-muted)] mb-1">
              Kept — refused by the fs-cleanup policy:
            </div>
            <ul className="space-y-1 font-mono">
              {blockedAreas.map((area) => (
                <li key={area.path} className="text-[var(--text-muted)]">
                  <span className="truncate" title={area.path}>{area.path}</span>
                  {' — '}
                  <span className="text-[var(--neon-amber)]">{area.reason}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        <p className="text-xs text-[var(--text-muted)]">
          Runs on the DSS host as{' '}
          <span className="font-mono">{pending?.runAsUser || 'the macro user'}</span>. On instances
          with user isolation some files are owned by other users and cannot be removed — the result
          will say so and name the paths rather than report a clean delete.
        </p>
      </ConfirmDeleteDialog>

      <UnlockModal isOpen={showUnlock} onClose={() => setShowUnlock(false)} />
    </div>
  );
}
