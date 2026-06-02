import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDiag } from '../context/DiagContext';
import type { Lifecycle } from '../types';
import { Modal } from './Modal';
import { useModal } from '../hooks/useModal';
import { fetchJson, fetchRaw } from '../utils/api';
import {
  imageCleanerDetectScan,
  imageCleanerReleaseDates,
  loadReleaseDate,
} from '../state/imageCleanerStore';

// ── Types ──

type Provider = 'ecr' | 'acr' | 'gar';

interface RegistryImage {
  digest: string;
  tags: string[];
  pushedAt: string;
  deletable: boolean;
}

interface RegistryRepo {
  name: string;
  images: RegistryImage[];
  error?: string;
}

interface ErrorWithHint {
  message: string;
  hint?: string;
}

const PROVIDER_LABELS: Record<Provider, string> = {
  ecr: 'AWS ECR',
  acr: 'Azure ACR (beta)',
  gar: 'Google Artifact Registry (beta)',
};

// ── Sort helpers ──

type SortField = 'repo' | 'tags' | 'pushedAt' | 'status';
type SortDir = 'asc' | 'desc';

interface FlatRow {
  repo: string;
  image: RegistryImage;
  key: string;
}

function sortRows(rows: FlatRow[], field: SortField, dir: SortDir): FlatRow[] {
  const m = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    switch (field) {
      case 'repo':
        return m * a.repo.localeCompare(b.repo);
      case 'tags':
        return m * (a.image.tags[0] || '').localeCompare(b.image.tags[0] || '');
      case 'pushedAt':
        return m * a.image.pushedAt.localeCompare(b.image.pushedAt);
      case 'status': {
        const aD = a.image.deletable ? 0 : 1;
        const bD = b.image.deletable ? 0 : 1;
        return m * (aD - bD);
      }
    }
  });
}

// ── Component ──

export function ImageCleaner() {
  const { dispatch } = useDiag();
  const setLifecycle = useCallback(
    (lc: Lifecycle) => {
      dispatch({ type: 'SET_PARSED_DATA', payload: { imageCleanerLoading: lc } });
    },
    [dispatch],
  );

  // Phase 0: detect provider — cached session-wide in imageCleanerDetectScan.
  // The scan-store mirror writes `imageCleanerLoading` for the detect phase.
  const {
    data: detectData,
    loading: detectLoading,
    scanStarted: detectStarted,
  } = imageCleanerDetectScan.use();
  const registryUrl = detectData?.registryUrl ?? null;
  const detectSource = detectData?.source ?? 'none';
  const detectDone = detectStarted && !detectLoading;
  useEffect(() => {
    if (!detectStarted) void imageCleanerDetectScan.load();
  }, [detectStarted]);

  // Local provider — user can override the detected default via the dropdown.
  // We seed it from detectData once detect finishes, then leave it alone.
  const [provider, setProvider] = useState<Provider>('ecr');
  const [providerInitialized, setProviderInitialized] = useState(false);
  useEffect(() => {
    if (providerInitialized) return;
    if (detectData?.provider) {
      setProvider(detectData.provider);
      setProviderInitialized(true);
    } else if (detectDone) {
      setProviderInitialized(true);
    }
  }, [detectData, detectDone, providerInitialized]);

  // Phase 1: release date — cached per-provider in imageCleanerReleaseDates.
  const releaseState = imageCleanerReleaseDates.use().byProvider[provider];
  const releaseInfo = releaseState?.info ?? null;
  const releaseLoading = releaseState?.loading ?? false;
  const releaseError = releaseState?.error ?? null;

  const [cutoffDate, setCutoffDate] = useState('');

  const [scanRepos, setScanRepos] = useState<RegistryRepo[]>([]);
  const [scanTotal, setScanTotal] = useState<number | null>(null);
  const [scanLoading, setScanLoading] = useState(false);
  const [scanError, setScanError] = useState<ErrorWithHint | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const [sortField, setSortField] = useState<SortField | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  const [deletionEnabled, setDeletionEnabled] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [deletedKeys, setDeletedKeys] = useState<Set<string>>(new Set());

  const deleteModal = useModal();
  const [deleteInput, setDeleteInput] = useState('');
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteProgress, setDeleteProgress] = useState('');

  // Fire the release-date fetch (cached per-provider) when detect is done and
  // whenever the user picks a different provider.
  useEffect(() => {
    if (!detectStarted) return;
    void loadReleaseDate(provider);
  }, [detectStarted, provider]);

  // Default the cutoff date to the release-date max. Legacy behavior: every
  // successful release-date load overwrites the user's input. Preserve that —
  // we re-set whenever the active provider's releaseInfo changes (cache miss
  // OR provider switch). On a remount within the same session the cached
  // releaseInfo is referentially stable, so the effect doesn't re-run.
  useEffect(() => {
    if (releaseInfo) setCutoffDate(releaseInfo.maxCutoffDate);
  }, [releaseInfo]);

  // Reset scan-result state when the active provider changes.
  useEffect(() => {
    if (!detectStarted) return;
    setScanRepos([]);
    setScanTotal(null);
    setScanError(null);
    setSelectedKeys(new Set());
    setDeletedKeys(new Set());
    setDeletionEnabled(false);
  }, [detectStarted, provider]);

  // Phase 2: Scan (SSE streaming)
  const runScan = useCallback(async () => {
    if (!cutoffDate) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setScanLoading(true);
    setScanError(null);
    setScanRepos([]);
    setScanTotal(null);
    setSelectedKeys(new Set());
    setDeletedKeys(new Set());
    setDeletionEnabled(false);

    const scanStartedAt = new Date().toISOString();
    setLifecycle({
      phase: 'running',
      startedAt: scanStartedAt,
      progressPct: 0,
      message: 'Scanning registry',
      subPhase: 'scan',
      updatedAt: scanStartedAt,
    });

    try {
      const response = await fetchRaw(
        `/api/tools/image-cleaner/scan?provider=${provider}&cutoff=${cutoffDate}`,
        { signal: controller.signal },
      );

      if (!response.ok || !response.body) {
        const body = await response.text();
        let msg = `Scan failed: ${response.status} ${response.statusText}`;
        let hint: string | undefined;
        try {
          const parsed = JSON.parse(body) as { error?: string; hint?: string };
          msg = parsed.error || msg;
          hint = parsed.hint;
        } catch {
          /* not JSON */
        }
        const e = new Error(msg) as Error & { hint?: string };
        e.hint = hint;
        throw e;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() || '';

        for (const part of parts) {
          const eventMatch = part.match(/^event:\s*(\S+)/m);
          const dataMatch = part.match(/^data:\s*(.*)/m);
          if (!eventMatch || !dataMatch) continue;

          const eventType = eventMatch[1];
          let payload: Record<string, unknown>;
          try {
            payload = JSON.parse(dataMatch[1]) as Record<string, unknown>;
          } catch {
            continue;
          }

          if (eventType === 'error') {
            const e = new Error(String(payload.error || 'Scan error')) as Error & { hint?: string };
            if (typeof payload.hint === 'string') e.hint = payload.hint;
            throw e;
          } else if (eventType === 'init') {
            setScanTotal(Number(payload.total));
          } else if (eventType === 'repo') {
            setScanRepos((prev) => [...prev, payload as unknown as RegistryRepo]);
          }
        }
      }
      setLifecycle({
        phase: 'done',
        startedAt: scanStartedAt,
        finishedAt: new Date().toISOString(),
        isEmpty: false,
        message: 'Scan complete',
      });
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        setLifecycle({ phase: 'queued' });
        return;
      }
      const e = err as Error & { hint?: string };
      setScanError({ message: e.message, hint: e.hint });
      setLifecycle({
        phase: 'error',
        startedAt: scanStartedAt,
        finishedAt: new Date().toISOString(),
        error: e.message,
        progressPct: 0,
      });
    } finally {
      setScanLoading(false);
      abortRef.current = null;
    }
  }, [cutoffDate, provider, setLifecycle]);

  const abortScan = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const allRows = useMemo(() => {
    const rows: FlatRow[] = [];
    for (const repo of scanRepos) {
      for (const img of repo.images) {
        rows.push({ repo: repo.name, image: img, key: `${repo.name}:${img.digest}` });
      }
    }
    return rows;
  }, [scanRepos]);

  const visibleRows = useMemo(
    () => allRows.filter((r) => !deletedKeys.has(r.key)),
    [allRows, deletedKeys],
  );

  const sortedRows = useMemo(() => {
    if (!sortField) return sortRows(visibleRows, 'pushedAt', 'asc');
    return sortRows(visibleRows, sortField, sortDir);
  }, [visibleRows, sortField, sortDir]);

  const deletableRows = useMemo(() => visibleRows.filter((r) => r.image.deletable), [visibleRows]);
  const keptRows = useMemo(() => visibleRows.filter((r) => !r.image.deletable), [visibleRows]);

  const selectedDeletableRows = useMemo(
    () => deletableRows.filter((r) => selectedKeys.has(r.key)),
    [deletableRows, selectedKeys],
  );

  const toggleSort = useCallback(
    (field: SortField) => {
      if (sortField === field) {
        setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
      } else {
        setSortField(field);
        setSortDir('asc');
      }
    },
    [sortField],
  );

  const sortIndicator = (field: SortField) => {
    if (sortField !== field) return '';
    return sortDir === 'asc' ? ' ▲' : ' ▼';
  };

  const toggleSelect = useCallback((key: string) => {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback(() => {
    setSelectedKeys((prev) => {
      const allKeys = deletableRows.map((r) => r.key);
      const allSelected = allKeys.length > 0 && allKeys.every((k) => prev.has(k));
      return allSelected ? new Set() : new Set(allKeys);
    });
  }, [deletableRows]);

  const openDeleteConfirm = useCallback(() => {
    setDeleteInput('');
    setDeleteError(null);
    setDeleteProgress('');
    deleteModal.open();
  }, [deleteModal]);

  const confirmDelete = useCallback(async () => {
    const count = selectedDeletableRows.length;
    if (count === 0) return;
    if (deleteInput !== `delete ${count} images`) return;
    if (!cutoffDate) return;

    setDeleteLoading(true);
    setDeleteError(null);
    setDeleteProgress('Sending delete request...');
    try {
      const body = {
        provider,
        cutoff: cutoffDate,
        images: selectedDeletableRows.map((r) => ({
          repositoryName: r.repo,
          imageDigest: r.image.digest,
        })),
      };
      const resp = await fetchJson<{
        deleted: { repo: string; digest: string }[];
        failed: { repo: string; digest: string; reason: string }[];
      }>('/api/tools/image-cleaner/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const deletedDigests = new Set(resp.deleted.map((d) => `${d.repo}:${d.digest}`));
      setDeletedKeys((prev) => new Set([...prev, ...deletedDigests]));
      setSelectedKeys(new Set());

      if (resp.failed.length > 0) {
        setDeleteProgress(
          `Deleted ${resp.deleted.length}, failed ${resp.failed.length}: ${resp.failed.map((f) => f.reason).join('; ')}`,
        );
      } else {
        deleteModal.close();
      }
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeleteLoading(false);
    }
  }, [selectedDeletableRows, deleteInput, cutoffDate, provider, deleteModal]);

  const shortDigest = (d: string) => d.replace('sha256:', '').slice(0, 12);

  const hasResults = scanRepos.length > 0;

  return (
    <>
      <div className="space-y-4 p-6">
        <section className="glass-card p-4">
          <h3 className="text-lg font-semibold text-[var(--text-primary)]">Docker Image Cleanup</h3>
          <p className="text-sm text-[var(--text-muted)] mt-1">
            Find and remove stale container images pushed before the current DSS version was released.
          </p>
          {registryUrl && (
            <p className="text-xs text-[var(--text-muted)] mt-1 font-mono">
              Registry: {registryUrl}{' '}
              <span className="text-[var(--text-tertiary)]">(detected via {detectSource})</span>
            </p>
          )}
        </section>

        <section className="glass-card p-4 space-y-3">
          <div className="flex items-center gap-3">
            <label
              className="text-sm text-[var(--text-secondary)] whitespace-nowrap"
              htmlFor="image-cleaner-provider"
            >
              Registry
            </label>
            <select
              id="image-cleaner-provider"
              value={provider}
              onChange={(e) => setProvider(e.target.value as Provider)}
              className="input-glass text-sm py-1 px-2 rounded font-mono"
              disabled={scanLoading}
            >
              {(Object.keys(PROVIDER_LABELS) as Provider[]).map((p) => (
                <option key={p} value={p}>
                  {PROVIDER_LABELS[p]}
                </option>
              ))}
            </select>
          </div>

          {releaseLoading && (
            <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
              <span className="inline-block w-4 h-4 border-2 border-[var(--text-tertiary)] border-t-transparent rounded-full animate-spin" />
              Detecting DSS version and release date...
            </div>
          )}
          {releaseError && (
            <div className="text-sm text-[var(--neon-red)]">
              <span className="font-medium">Error:</span> {releaseError.message}
              {releaseError.hint && (
                <div className="mt-1 text-xs text-[var(--text-muted)]">{releaseError.hint}</div>
              )}
            </div>
          )}
          {releaseInfo && (
            <>
              <div className="grid grid-cols-3 gap-4">
                <div>
                  <div className="text-xs text-[var(--text-muted)] uppercase tracking-wide">DSS Version</div>
                  <div className="text-lg font-mono text-[var(--text-primary)]">{releaseInfo.version}</div>
                </div>
                <div>
                  <div className="text-xs text-[var(--text-muted)] uppercase tracking-wide">Released</div>
                  <div className="text-lg font-mono text-[var(--text-primary)]">{releaseInfo.releaseDate}</div>
                </div>
                <div>
                  <div className="text-xs text-[var(--text-muted)] uppercase tracking-wide">Max Cutoff</div>
                  <div className="text-lg font-mono text-[var(--text-primary)]">{releaseInfo.maxCutoffDate}</div>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <label
                  className="text-sm text-[var(--text-secondary)] whitespace-nowrap"
                  htmlFor="image-cleaner-cutoff"
                >
                  Delete images pushed before
                </label>
                <input
                  id="image-cleaner-cutoff"
                  type="date"
                  value={cutoffDate}
                  max={releaseInfo.maxCutoffDate}
                  onChange={(e) => setCutoffDate(e.target.value)}
                  className="input-glass text-sm py-1 px-2 rounded font-mono"
                />
                <button
                  onClick={runScan}
                  disabled={!cutoffDate || scanLoading}
                  className="px-4 py-1.5 rounded-md text-sm font-medium bg-[var(--accent)] text-white hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {scanLoading ? 'Scanning...' : `Scan ${PROVIDER_LABELS[provider]}`}
                </button>
              </div>
            </>
          )}
        </section>

        {scanLoading && (
          <section className="glass-card p-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                <span className="inline-block w-4 h-4 border-2 border-[var(--text-tertiary)] border-t-transparent rounded-full animate-spin" />
                {scanTotal !== null
                  ? `Scanning repositories... ${scanRepos.length} / ${scanTotal}`
                  : 'Discovering repositories...'}
              </div>
              <button
                onClick={abortScan}
                className="px-3 py-1 rounded-md text-xs font-medium text-[var(--text-secondary)] border border-[var(--text-tertiary)]/30 hover:bg-[var(--bg-glass-hover)] transition-colors"
              >
                Abort
              </button>
            </div>
          </section>
        )}
        {scanError && (
          <section className="glass-card p-4">
            <div className="text-sm text-[var(--neon-red)]">
              <span className="font-medium">Scan error:</span> {scanError.message}
              {scanError.hint && (
                <div className="mt-1 text-xs text-[var(--text-muted)]">{scanError.hint}</div>
              )}
            </div>
          </section>
        )}

        {hasResults && (
          <>
            <section className="glass-card p-4">
              <div className="grid grid-cols-4 gap-4">
                <div className="text-center">
                  <div className="text-2xl font-mono text-[var(--text-primary)]">{scanRepos.length}</div>
                  <div className="text-xs text-[var(--text-muted)]">Repositories</div>
                </div>
                <div className="text-center">
                  <div className="text-2xl font-mono text-[var(--text-primary)]">{visibleRows.length}</div>
                  <div className="text-xs text-[var(--text-muted)]">Total Images</div>
                </div>
                <div className="text-center">
                  <div className="text-2xl font-mono text-amber-400">{deletableRows.length}</div>
                  <div className="text-xs text-[var(--text-muted)]">Deletable</div>
                </div>
                <div className="text-center">
                  <div className="text-2xl font-mono text-[var(--neon-green)]">{keptRows.length}</div>
                  <div className="text-xs text-[var(--text-muted)]">Kept</div>
                </div>
              </div>
            </section>

            {!scanLoading && (
              <section className="glass-card p-3 flex items-center justify-between">
                <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)] cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={deletionEnabled}
                    onChange={(e) => {
                      setDeletionEnabled(e.target.checked);
                      if (!e.target.checked) setSelectedKeys(new Set());
                    }}
                    className="accent-[var(--neon-red)]"
                  />
                  Enable deletion mode
                </label>
                {deletionEnabled && selectedKeys.size > 0 && (
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-[var(--text-secondary)]">
                      {selectedKeys.size} selected
                    </span>
                    <button
                      onClick={() => setSelectedKeys(new Set())}
                      className="px-3 py-1 rounded-md text-xs font-medium text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] transition-colors"
                    >
                      Clear
                    </button>
                    <button
                      onClick={openDeleteConfirm}
                      className="px-3 py-1 rounded-md text-xs font-medium border border-[var(--neon-red)]/30 bg-[var(--neon-red)]/10 text-[var(--neon-red)] hover:bg-[var(--neon-red)]/20 hover:border-[var(--neon-red)]/50 transition-colors"
                    >
                      Delete Selected
                    </button>
                  </div>
                )}
              </section>
            )}

            <section className="glass-card p-4">
              <div className="overflow-auto max-h-[60vh]">
                <table className="table-dark w-full">
                  <thead>
                    <tr>
                      {deletionEnabled && !scanLoading && (
                        <th className="w-10">
                          <input
                            type="checkbox"
                            checked={deletableRows.length > 0 && deletableRows.every((r) => selectedKeys.has(r.key))}
                            onChange={toggleSelectAll}
                            className="accent-[var(--neon-cyan)]"
                            title="Select all deletable images"
                          />
                        </th>
                      )}
                      <th className="cursor-pointer select-none" onClick={() => toggleSort('repo')}>
                        Repository{sortIndicator('repo')}
                      </th>
                      <th className="cursor-pointer select-none" onClick={() => toggleSort('tags')}>
                        Tags{sortIndicator('tags')}
                      </th>
                      <th>Digest</th>
                      <th className="cursor-pointer select-none" onClick={() => toggleSort('pushedAt')}>
                        Pushed At{sortIndicator('pushedAt')}
                      </th>
                      <th className="cursor-pointer select-none" onClick={() => toggleSort('status')}>
                        Status{sortIndicator('status')}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedRows.length === 0 && (
                      <tr>
                        <td
                          colSpan={deletionEnabled && !scanLoading ? 6 : 5}
                          className="py-6 text-center text-sm text-[var(--text-muted)]"
                        >
                          No matching images found.
                        </td>
                      </tr>
                    )}
                    {sortedRows.map((row) => (
                      <tr key={row.key} className="hover:bg-[var(--bg-glass)]">
                        {deletionEnabled && !scanLoading && (
                          <td>
                            {row.image.deletable ? (
                              <input
                                type="checkbox"
                                checked={selectedKeys.has(row.key)}
                                onChange={() => toggleSelect(row.key)}
                                className="accent-[var(--neon-cyan)]"
                              />
                            ) : null}
                          </td>
                        )}
                        <td className="text-[var(--text-primary)] font-mono text-xs">{row.repo}</td>
                        <td>
                          <div className="flex flex-wrap gap-1">
                            {row.image.tags.length > 0 ? (
                              row.image.tags.map((t) => (
                                <span
                                  key={t}
                                  className="inline-block px-1.5 py-0.5 rounded text-[10px] font-mono bg-[var(--bg-glass)] text-[var(--text-secondary)]"
                                >
                                  {t}
                                </span>
                              ))
                            ) : (
                              <span className="text-xs text-[var(--text-muted)]">&lt;untagged&gt;</span>
                            )}
                          </div>
                        </td>
                        <td className="font-mono text-xs text-[var(--text-muted)]">{shortDigest(row.image.digest)}</td>
                        <td className="font-mono text-xs text-[var(--text-secondary)]">
                          {row.image.pushedAt.slice(0, 10)}
                        </td>
                        <td>
                          {row.image.deletable ? (
                            <span className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-amber-400/20 text-amber-400">
                              Deletable
                            </span>
                          ) : (
                            <span className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-[var(--neon-green)]/20 text-[var(--neon-green)]">
                              Keep
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            {deletedKeys.size > 0 && (
              <section className="glass-card p-3">
                <div className="text-sm text-[var(--neon-green)]">
                  {deletedKeys.size} image{deletedKeys.size !== 1 ? 's' : ''} deleted this session.
                </div>
              </section>
            )}
          </>
        )}
      </div>

      <Modal
        isOpen={deleteModal.isOpen}
        onClose={deleteModal.close}
        title="Confirm Image Deletion"
        footer={
          <div className="flex items-center justify-end gap-2">
            <button
              onClick={deleteModal.close}
              className="px-3 py-1.5 rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-[var(--text-secondary)]"
            >
              Cancel
            </button>
            <button
              onClick={confirmDelete}
              disabled={deleteLoading || deleteInput !== `delete ${selectedDeletableRows.length} images`}
              className="px-4 py-1.5 rounded bg-[var(--neon-red)]/20 text-[var(--neon-red)] hover:bg-[var(--neon-red)]/30 disabled:opacity-50 transition-colors"
            >
              {deleteLoading ? 'Deleting...' : `Delete ${selectedDeletableRows.length} Images`}
            </button>
          </div>
        }
      >
        <div className="space-y-4">
          <p className="text-[var(--text-secondary)]">
            Are you sure you want to delete {selectedDeletableRows.length} image
            {selectedDeletableRows.length !== 1 ? 's' : ''}? This action cannot be undone.
          </p>
          <div className="max-h-40 overflow-y-auto rounded bg-[var(--bg-glass)] p-2 space-y-1">
            {selectedDeletableRows.map((r) => (
              <div key={r.key} className="text-xs font-mono text-[var(--neon-red)] flex items-center gap-2">
                <span>{r.repo}</span>
                <span className="text-[var(--text-muted)]">{shortDigest(r.image.digest)}</span>
                <span className="text-[var(--text-muted)]">{r.image.tags.join(', ') || '<untagged>'}</span>
                <span className="text-[var(--text-muted)]">{r.image.pushedAt.slice(0, 10)}</span>
              </div>
            ))}
          </div>
          <p className="text-sm text-[var(--text-muted)]">
            Type{' '}
            <code className="px-1.5 py-0.5 rounded bg-[var(--bg-glass)] text-[var(--text-primary)]">
              delete {selectedDeletableRows.length} images
            </code>{' '}
            to confirm.
          </p>
          <input
            type="text"
            value={deleteInput}
            onChange={(e) => setDeleteInput(e.target.value)}
            placeholder={`delete ${selectedDeletableRows.length} images`}
            className="w-full input-glass font-mono text-sm"
            autoFocus
          />
          {deleteProgress && !deleteError && (
            <div className="text-sm text-[var(--text-secondary)]">{deleteProgress}</div>
          )}
          {deleteError && <div className="text-sm text-[var(--neon-red)]">{deleteError}</div>}
        </div>
      </Modal>
    </>
  );
}
