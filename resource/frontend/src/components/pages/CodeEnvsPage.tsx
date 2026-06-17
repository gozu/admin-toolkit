import { useCallback, useEffect, useMemo, useState } from 'react';
import { useDiag } from '../../context/DiagContext';
import { useModal } from '../../hooks/useModal';
import { useRowSelection } from '../../hooks/useRowSelection';
import { useSortableTable } from '../../hooks/useSortableTable';
import { useTableFilter } from '../../hooks/useTableFilter';
import { fetchJson } from '../../utils/api';
import {
  dssUrls,
  getDssBaseUrl,
  objectLabel,
  objectUrl,
  projectUrl,
} from '../../utils/codeEnvUsageLinks';
import { formatSizeGb, getRelativeSizeColor } from '../../utils/formatters';
import { managedFoldersScan } from '../../state/managedFoldersStore';
import { Modal } from '../Modal';
import { Button } from '../common/Button';
import { ConfirmDeleteDialog } from '../common/ConfirmDeleteDialog';
import { ProgressIndicator } from '../common/ProgressIndicator';
import { StatTile } from '../common/StatTile';
import type {
  CodeEnv,
  CodeEnvReplaceResult,
  CodeEnvUsageRef,
  ProvisionalCodeEnv,
} from '../../types';

type SortField =
  | 'name'
  | 'owner'
  | 'ownerEmail'
  | 'version'
  | 'language'
  | 'size'
  | 'projects'
  | 'usages';
type SortDir = 'asc' | 'desc';

interface EnvRow {
  env?: CodeEnv;
  provisional?: ProvisionalCodeEnv;
  envKey: string;
  name: string;
  owner: string;
  usageCount: number;
  projectCount: number;
  isProvisional: boolean;
}

type RealEnvRow = EnvRow & { env: CodeEnv; isProvisional: false };

function codeEnvKey(env: CodeEnv): string {
  return `${env.language}:${env.name}`;
}

function sortRows(rows: EnvRow[], field: SortField | null, dir: SortDir): EnvRow[] {
  if (!field) {
    return [...rows].sort((a, b) => {
      const sizeA = a.env?.sizeBytes || 0;
      const sizeB = b.env?.sizeBytes || 0;
      if (sizeB !== sizeA) return sizeB - sizeA;
      return a.name.localeCompare(b.name);
    });
  }
  const m = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const aUnused = a.usageCount === 0;
    const bUnused = b.usageCount === 0;
    if (aUnused !== bUnused) return aUnused ? -1 : 1;
    switch (field) {
      case 'name':
        return m * a.name.localeCompare(b.name);
      case 'owner':
        return m * (a.owner || '').localeCompare(b.owner || '');
      case 'ownerEmail':
        return m * (a.env?.ownerEmail || '').localeCompare(b.env?.ownerEmail || '');
      case 'version':
        return (
          m *
          (a.env?.version || '').localeCompare(b.env?.version || '', undefined, { numeric: true })
        );
      case 'language':
        return m * (a.env?.language || '').localeCompare(b.env?.language || '');
      case 'size': {
        const sizeA = a.env?.sizeBytes || 0;
        const sizeB = b.env?.sizeBytes || 0;
        if (sizeA !== sizeB) return m * (sizeA - sizeB);
        return a.name.localeCompare(b.name);
      }
      case 'projects':
        if (a.projectCount !== b.projectCount) return m * (a.projectCount - b.projectCount);
        return a.name.localeCompare(b.name);
      case 'usages':
        if (a.usageCount !== b.usageCount) return m * (a.usageCount - b.usageCount);
        return a.name.localeCompare(b.name);
    }
  });
}

function LanguageBadge({ language }: { language: 'python' | 'r' }) {
  if (language === 'python') {
    return (
      <span className="px-2 py-0.5 text-xs font-semibold rounded bg-[var(--neon-cyan)]/20 text-[var(--neon-cyan)] border border-[var(--neon-cyan)]/30">
        Python
      </span>
    );
  }
  return (
    <span className="px-2 py-0.5 text-xs font-semibold rounded bg-[var(--neon-purple)]/20 text-[var(--neon-purple)] border border-[var(--neon-purple)]/30">
      R
    </span>
  );
}

function PythonVersionBadge({ version }: { version: string }) {
  const versionMatch = version.match(/(\d+)\.(\d+)/);
  let colorClass = 'text-[var(--text-secondary)]';
  if (versionMatch) {
    const major = parseInt(versionMatch[1], 10);
    const minor = parseInt(versionMatch[2], 10);
    if (major < 3) colorClass = 'text-[var(--neon-red)] font-bold';
    else if (major === 3 && minor >= 9) colorClass = 'text-[var(--neon-green)]';
    else colorClass = 'text-[var(--neon-amber)]';
  }
  return <span className={colorClass}>{version || '—'}</span>;
}

function ReplacementResultPanel({ result }: { result: CodeEnvReplaceResult }) {
  return (
    <div className="rounded-lg border border-[var(--border-glass)] bg-[var(--bg-elevated)] p-3 text-sm">
      <div className="text-[var(--text-primary)]">
        {result.dryRun ? 'Planned' : 'Applied'} rows:{' '}
        <span className="font-mono">{result.matchedRows}</span>
        {result.updatedRows > 0 && (
          <span>
            , updated: <span className="font-mono">{result.updatedRows}</span>
          </span>
        )}
        {result.skippedRows > 0 && (
          <span>
            , skipped: <span className="font-mono">{result.skippedRows}</span>
          </span>
        )}
        {result.failedRows > 0 && (
          <span className="text-[var(--neon-red)]">
            , failed: <span className="font-mono">{result.failedRows}</span>
          </span>
        )}
      </div>
      <div className="mt-2 max-h-48 space-y-1 overflow-auto">
        {result.results.slice(0, 100).map((row, idx) => (
          <div
            key={`${row.rowId || row.objectId || 'row'}-${idx}`}
            className="font-mono text-xs text-[var(--text-muted)]"
          >
            {row.status}: {row.projectKey || '*'} / {row.objectType || 'OBJECT'} /{' '}
            {row.objectName || row.objectId || '—'}
            {row.error ? ` - ${row.error}` : ''}
          </div>
        ))}
      </div>
    </div>
  );
}

function UsageModal({
  env,
  baseUrl,
  isOpen,
  onClose,
}: {
  env: CodeEnv | null;
  baseUrl: string;
  isOpen: boolean;
  onClose: () => void;
}) {
  const grouped = useMemo(() => {
    const groups = new Map<string, { projectName: string; rows: CodeEnvUsageRef[] }>();
    for (const usage of env?.usageDetails || []) {
      const projectKey = usage.projectKey || '';
      if (!projectKey) continue;
      const group = groups.get(projectKey) || {
        projectName: usage.projectName || projectKey,
        rows: [],
      };
      group.rows.push(usage);
      groups.set(projectKey, group);
    }
    return Array.from(groups.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [env]);

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={env ? `${env.name} usage` : 'Code env usage'}>
      <div className="max-h-[70vh] overflow-auto pr-1">
        {grouped.length === 0 ? (
          <div className="text-sm text-[var(--text-muted)]">No usage details are available.</div>
        ) : (
          <div className="space-y-4">
            {grouped.map(([projectKey, group]) => (
              <div
                key={projectKey}
                className="rounded-lg border border-[var(--border-glass)] bg-[var(--bg-surface)]"
              >
                <div className="flex min-h-11 items-center justify-between gap-3 border-b border-[var(--border-glass)] px-3 py-2">
                  <a
                    href={projectUrl(baseUrl, projectKey)}
                    target="_blank"
                    rel="noreferrer"
                    className="min-w-0 font-mono text-sm font-semibold text-[var(--text-primary)] hover:text-[var(--neon-cyan)] hover:underline"
                  >
                    {projectKey}
                  </a>
                  <span className="truncate text-xs text-[var(--text-muted)]">
                    {group.projectName}
                  </span>
                </div>
                <div className="divide-y divide-[var(--border-glass)]/70">
                  {group.rows.map((usage, idx) => (
                    <div
                      key={`${usage.objectType || usage.usageType}-${usage.objectId}-${idx}`}
                      className="grid min-h-10 grid-cols-[110px_minmax(0,1fr)] items-center gap-3 px-3 py-2 text-sm"
                    >
                      <span className="text-xs text-[var(--text-muted)]">{objectLabel(usage)}</span>
                      <a
                        href={objectUrl(baseUrl, usage)}
                        target="_blank"
                        rel="noreferrer"
                        className="truncate text-[var(--text-primary)] hover:text-[var(--neon-cyan)] hover:underline"
                      >
                        {usage.objectName || usage.objectId || '—'}
                      </a>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </Modal>
  );
}

export function CodeEnvsInsightsPage({ readOnly = false }: { readOnly?: boolean } = {}) {
  const { state } = useDiag();
  const { isVisible } = useTableFilter();
  const { parsedData } = state;

  const rawCodeEnvs = useMemo(() => parsedData.codeEnvs || [], [parsedData.codeEnvs]);
  const codeEnvSizes = parsedData.codeEnvSizes;
  const codeEnvs = useMemo(() => {
    if (!codeEnvSizes || !rawCodeEnvs.length) return rawCodeEnvs;
    return rawCodeEnvs.map((env) => {
      const sizeKey = `${(env.language || 'python').toLowerCase()}:${env.name}`;
      const size = codeEnvSizes[sizeKey];
      return size ? { ...env, sizeBytes: size } : env;
    });
  }, [rawCodeEnvs, codeEnvSizes]);

  const provisionalCodeEnvs = useMemo(
    () => parsedData.provisionalCodeEnvs || [],
    [parsedData.provisionalCodeEnvs],
  );
  const totalEnvCount = parsedData.totalEnvCount;
  const skippedEnvCount = parsedData.skippedEnvCount;
  const loading = parsedData.codeEnvsLoading;
  const isLoading = loading?.phase === 'running' || loading?.phase === 'queued';
  const analysisLoading = parsedData.analysisLoading;
  const showAnalysisProgress = Boolean(analysisLoading?.active);

  const baseUrl = useMemo(() => getDssBaseUrl(), []);

  // Managed-folder selection (Backup destination)
  const { data: foldersData, loading: foldersLoading, scanStarted } = managedFoldersScan.use();
  const folders = useMemo(() => foldersData?.folders ?? [], [foldersData?.folders]);
  const [folderIdRaw, setFolderId] = useState('');
  // Default the backup destination during render rather than via an effect.
  // `folderIdRaw` holds the user's explicit pick.
  const folderId = folderIdRaw || (folders[0]?.id ?? '');

  useEffect(() => {
    if (!readOnly && !scanStarted) void managedFoldersScan.load();
  }, [readOnly, scanStarted]);

  // Owner filter (Insights' click-to-filter)
  const [ownerFilter, setOwnerFilter] = useState<string | null>(null);

  // Row state — selection, deletion, sort
  const { sortField, sortDir, toggleSort, sortIndicator } = useSortableTable<SortField>();
  const [deletedKeys, setDeletedKeys] = useState<Set<string>>(new Set());
  const { selectedKeys, toggleSelect, toggleSelectAll, clear: clearSelection } = useRowSelection();

  // Delete modals
  const deleteModal = useModal();
  const [deleteTarget, setDeleteTarget] = useState<RealEnvRow | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const bulkDeleteModal = useModal();
  const [bulkDeleteLoading, setBulkDeleteLoading] = useState(false);
  const [bulkDeleteError, setBulkDeleteError] = useState<string | null>(null);
  const [bulkDeleteProgress, setBulkDeleteProgress] = useState('');

  // Replacement state (raw user picks; effective values derived below).
  const [sourceNameRaw, setSourceName] = useState('');
  const [targetNameRaw, setTargetName] = useState('');
  const [dryRun, setDryRun] = useState(true);
  const [confirmText, setConfirmText] = useState('');
  const [replaceLoading, setReplaceLoading] = useState(false);
  const [replaceError, setReplaceError] = useState<string | null>(null);
  const [replaceResult, setReplaceResult] = useState<CodeEnvReplaceResult | null>(null);
  const confirmModal = useModal();

  // Usage modal (Project column)
  const usageModal = useModal();
  const [usageEnv, setUsageEnv] = useState<CodeEnv | null>(null);

  // Build unified rows (real + provisional, dedup by name)
  const rows = useMemo<EnvRow[]>(() => {
    const realRows: EnvRow[] = codeEnvs.map((env) => ({
      env,
      envKey: codeEnvKey(env),
      name: env.name,
      owner: env.owner || 'Unknown',
      usageCount: env.usageCount || 0,
      projectCount:
        env.projectCount ??
        new Set((env.usageDetails || []).map((u) => u.projectKey).filter(Boolean)).size,
      isProvisional: false,
    }));
    const realNames = new Set(realRows.map((r) => r.name));
    const provisionalRows: EnvRow[] = provisionalCodeEnvs
      .filter((row) => !realNames.has(row.name))
      .map((row) => ({
        provisional: row,
        envKey: `provisional:${row.name}`,
        name: row.name,
        owner: 'Pending details',
        usageCount: row.usageCount,
        projectCount: 0,
        isProvisional: true,
      }));
    return [...realRows, ...provisionalRows];
  }, [codeEnvs, provisionalCodeEnvs]);

  const visibleRows = useMemo(
    () => rows.filter((r) => !deletedKeys.has(r.envKey)),
    [rows, deletedKeys],
  );
  const ownerFilteredRows = useMemo(
    () => (ownerFilter ? visibleRows.filter((r) => r.owner === ownerFilter) : visibleRows),
    [visibleRows, ownerFilter],
  );
  const sortedRows = useMemo(
    () => sortRows(ownerFilteredRows, sortField, sortDir),
    [ownerFilteredRows, sortField, sortDir],
  );
  const maxBytes = useMemo(
    () => rows.reduce((m, r) => Math.max(m, r.env?.sizeBytes || 0), 0),
    [rows],
  );

  const pythonCount = useMemo(
    () => visibleRows.filter((r) => r.env?.language === 'python').length,
    [visibleRows],
  );
  const rCount = useMemo(
    () => visibleRows.filter((r) => r.env?.language === 'r').length,
    [visibleRows],
  );
  const unusedCount = useMemo(
    () => visibleRows.filter((r) => r.usageCount === 0).length,
    [visibleRows],
  );
  const inUseCount = useMemo(
    () => visibleRows.filter((r) => r.usageCount > 0).length,
    [visibleRows],
  );

  // Source/Target selects
  const sortedRealEnvs = useMemo(
    () => [...codeEnvs].sort((a, b) => a.name.localeCompare(b.name)),
    [codeEnvs],
  );
  const inUseEnvs = useMemo(
    () => sortedRealEnvs.filter((env) => (env.usageCount || 0) > 0),
    [sortedRealEnvs],
  );
  // Default/clamp the source env to the first in-use env during render rather
  // than via an effect. `sourceNameRaw` holds the user's explicit pick.
  const sourceName =
    sourceNameRaw && inUseEnvs.some((env) => codeEnvKey(env) === sourceNameRaw)
      ? sourceNameRaw
      : inUseEnvs.length > 0
        ? codeEnvKey(inUseEnvs[0])
        : sourceNameRaw;
  const sourceEnv = useMemo(
    () => sortedRealEnvs.find((env) => codeEnvKey(env) === sourceName) || null,
    [sortedRealEnvs, sourceName],
  );
  const targetChoices = useMemo(
    () =>
      sortedRealEnvs.filter(
        (env) =>
          sourceEnv &&
          env.language === sourceEnv.language &&
          codeEnvKey(env) !== codeEnvKey(sourceEnv),
      ),
    [sortedRealEnvs, sourceEnv],
  );

  // Default/clamp the target env during render rather than via an effect.
  // `targetNameRaw` holds the user's explicit pick; falls back to the first
  // valid choice that isn't the source env.
  const nextTargetName = targetChoices[0]?.name || '';
  const targetName =
    targetNameRaw &&
    targetNameRaw !== sourceEnv?.name &&
    targetChoices.some((env) => env.name === targetNameRaw)
      ? targetNameRaw
      : nextTargetName || targetNameRaw;

  const selectableUnusedRows = useMemo(
    () =>
      visibleRows.filter((r): r is RealEnvRow => !r.isProvisional && r.usageCount === 0 && !!r.env),
    [visibleRows],
  );

  const selectedRows = useMemo(
    () =>
      visibleRows.filter(
        (r): r is RealEnvRow => selectedKeys.has(r.envKey) && !r.isProvisional && !!r.env,
      ),
    [visibleRows, selectedKeys],
  );

  const openDeleteConfirm = useCallback(
    (row: RealEnvRow) => {
      setDeleteTarget(row);
      setDeleteError(null);
      deleteModal.open();
    },
    [deleteModal],
  );

  const openBulkDelete = useCallback(() => {
    setBulkDeleteError(null);
    setBulkDeleteProgress('');
    bulkDeleteModal.open();
  }, [bulkDeleteModal]);

  const confirmDelete = useCallback(async () => {
    if (!deleteTarget) return;
    if (!folderId) return;
    setDeleteLoading(true);
    setDeleteError(null);
    try {
      await fetchJson(
        `/api/tools/code-env-cleaner/${deleteTarget.env.language.toUpperCase()}/${deleteTarget.env.name}?folderId=${encodeURIComponent(folderId)}`,
        { method: 'DELETE', headers: { 'X-Confirm-Name': deleteTarget.env.name } },
      );
      setDeletedKeys((prev) => new Set([...prev, deleteTarget.envKey]));
      deleteModal.close();
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeleteLoading(false);
    }
  }, [deleteTarget, deleteModal, folderId]);

  const confirmBulkDelete = useCallback(async () => {
    const count = selectedRows.length;
    if (count === 0) return;
    if (!folderId) return;
    setBulkDeleteLoading(true);
    setBulkDeleteError(null);
    try {
      for (let i = 0; i < selectedRows.length; i++) {
        const row = selectedRows[i];
        setBulkDeleteProgress(`Deleting ${i + 1} of ${count}: ${row.env.name}...`);
        await fetchJson(
          `/api/tools/code-env-cleaner/${row.env.language.toUpperCase()}/${row.env.name}?folderId=${encodeURIComponent(folderId)}`,
          { method: 'DELETE', headers: { 'X-Confirm-Name': row.env.name } },
        );
        setDeletedKeys((prev) => new Set([...prev, row.envKey]));
      }
      clearSelection();
      bulkDeleteModal.close();
    } catch (err) {
      setBulkDeleteError(err instanceof Error ? err.message : String(err));
    } finally {
      setBulkDeleteLoading(false);
      setBulkDeleteProgress('');
    }
  }, [selectedRows, bulkDeleteModal, folderId, clearSelection]);

  const runReplace = async (nextDryRun: boolean) => {
    if (!sourceEnv || !targetName || sourceEnv.name === targetName) return;
    setReplaceLoading(true);
    setReplaceError(null);
    setReplaceResult(null);
    try {
      const result = await fetchJson<CodeEnvReplaceResult>('/api/code-envs/replace', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sourceEnvName: sourceEnv.name,
          sourceLanguage: sourceEnv.language,
          targetEnvName: targetName,
          dryRun: nextDryRun,
        }),
      });
      setReplaceResult(result);
      if (!nextDryRun) {
        confirmModal.close();
        setConfirmText('');
        setDryRun(true);
      }
    } catch (err) {
      setReplaceError(err instanceof Error ? err.message : String(err));
    } finally {
      setReplaceLoading(false);
    }
  };

  const requestReplace = () => {
    if (dryRun) {
      void runReplace(true);
      return;
    }
    setConfirmText('');
    confirmModal.open();
  };

  const canSubmit = Boolean(
    sourceEnv &&
    targetName &&
    sourceEnv.name !== targetName &&
    targetChoices.some((env) => env.name === targetName) &&
    !replaceLoading,
  );
  const canLiveApply = canSubmit && confirmText === 'CONFIRM';

  const openUsage = (env: CodeEnv) => {
    setUsageEnv(env);
    usageModal.open();
  };

  if (!isVisible('code-envs-table')) return null;

  return (
    <div className="page-fill">
      <div className="flex flex-col gap-4 flex-1 min-h-0">
        {/* Shared header bar */}
        <section className="glass-card p-4 space-y-3">
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <h3 className="text-lg font-semibold text-[var(--text-primary)]">
              {visibleRows.length > 0
                ? ownerFilter
                  ? `${ownerFilteredRows.length} of ${visibleRows.length} Code Envs`
                  : `${visibleRows.length} Code Envs`
                : 'Code Envs'}
            </h3>
            <div className="flex flex-col items-end gap-1">
              <div className="flex items-center gap-2">
                {pythonCount > 0 && (
                  <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-[var(--neon-cyan)]/10 text-[var(--neon-cyan)] border border-[var(--neon-cyan)]/30">
                    {pythonCount} Python
                  </span>
                )}
                {rCount > 0 && (
                  <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-[var(--neon-purple)]/10 text-[var(--neon-purple)] border border-[var(--neon-purple)]/30">
                    {rCount} R
                  </span>
                )}
              </div>
              {skippedEnvCount != null && skippedEnvCount > 0 && totalEnvCount != null && (
                <span className="text-xs font-normal text-[var(--text-muted)]">
                  {visibleRows.length} of {totalEnvCount} — {skippedEnvCount} plugin-managed
                  excluded
                </span>
              )}
            </div>
          </div>

          {!readOnly && (
            <p className="text-sm text-[var(--text-muted)]">
              A backup is uploaded to the selected managed folder before deletion.
            </p>
          )}

          {showAnalysisProgress && (
            <div>
              <div className="flex items-center justify-between text-xs text-[var(--text-secondary)]">
                <span>{analysisLoading?.message || 'Analyzing code environments…'}</span>
              </div>
              {/* Indeterminate pulse — no progress % (binary spinner semantics). */}
              <div className="mt-2 h-2 rounded-full bg-[var(--bg-glass)] overflow-hidden">
                <div className="h-full w-full rounded-full bg-gradient-to-r from-[var(--neon-cyan)] to-[var(--neon-green)] animate-pulse motion-reduce:animate-none" />
              </div>
              {analysisLoading?.phase && (
                <div className="mt-1 text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
                  {analysisLoading.phase.replace(/_/g, ' ')}
                </div>
              )}
            </div>
          )}

          {/* Action toolbar — Backup / Source / Target / Dry run / Apply / Language */}
          {!readOnly && (
            <div className="flex flex-wrap items-center gap-3">
              <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                <span className="whitespace-nowrap">Backup</span>
                <select
                  value={folderId}
                  onChange={(e) => setFolderId(e.target.value)}
                  disabled={foldersLoading || folders.length === 0}
                  className="input-glass text-sm py-1 px-2 rounded min-w-[180px]"
                >
                  {foldersLoading ? (
                    <option value="">Loading...</option>
                  ) : folders.length === 0 ? (
                    <option value="">No managed folders in project</option>
                  ) : (
                    folders.map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.name}
                      </option>
                    ))
                  )}
                </select>
              </label>

              <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                <span className="whitespace-nowrap">Source</span>
                <select
                  value={sourceName}
                  onChange={(e) => setSourceName(e.target.value)}
                  disabled={inUseEnvs.length === 0}
                  className="input-glass text-sm py-1 px-2 rounded min-w-[200px]"
                >
                  {inUseEnvs.length === 0 ? (
                    <option value="">No in-use envs</option>
                  ) : (
                    inUseEnvs.map((env) => (
                      <option key={codeEnvKey(env)} value={codeEnvKey(env)}>
                        {env.name} ({env.language})
                      </option>
                    ))
                  )}
                </select>
              </label>

              <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                <span className="whitespace-nowrap">Target</span>
                <select
                  value={targetName}
                  onChange={(e) => setTargetName(e.target.value)}
                  disabled={targetChoices.length === 0}
                  className="input-glass text-sm py-1 px-2 rounded min-w-[200px]"
                >
                  {targetChoices.length === 0 ? (
                    <option value="">No same-language target</option>
                  ) : (
                    targetChoices.map((env) => (
                      <option key={env.name} value={env.name}>
                        {env.name}
                      </option>
                    ))
                  )}
                </select>
              </label>

              <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)] rounded border border-[var(--border-glass)] bg-[var(--bg-glass)] px-3 py-1">
                <input
                  type="checkbox"
                  checked={dryRun}
                  onChange={(e) => setDryRun(e.target.checked)}
                  className="h-4 w-4 accent-[var(--neon-cyan)]"
                />
                Dry run
              </label>

              <button
                onClick={requestReplace}
                disabled={!canSubmit}
                className="rounded bg-[var(--accent)] px-4 py-1 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
              >
                {replaceLoading ? 'Running...' : 'Replace'}
              </button>
            </div>
          )}

          {!readOnly && replaceError && (
            <div className="text-sm text-[var(--neon-red)]">{replaceError}</div>
          )}
          {!readOnly && replaceResult && <ReplacementResultPanel result={replaceResult} />}
        </section>

        {/* Stats bar */}
        <section className="glass-card p-4">
          <div className="grid grid-cols-4 gap-4">
            <StatTile
              value={
                totalEnvCount && totalEnvCount > 0
                  ? `${visibleRows.length}/${totalEnvCount - (skippedEnvCount || 0)}`
                  : visibleRows.length
              }
              label="Total"
            />
            <StatTile value={unusedCount} label="Unused" valueClassName="text-[var(--warning)]" />
            <StatTile value={inUseCount} label="In Use" valueClassName="text-[var(--neon-green)]" />
            <StatTile
              value={deletedKeys.size}
              label="Deleted This Session"
              valueClassName="text-[var(--neon-red)]"
            />
          </div>
        </section>

        {ownerFilter && (
          <div className="glass-card px-4 py-2 flex items-center gap-2 text-sm">
            <span className="text-[var(--text-secondary)]">Filtered by owner:</span>
            <span className="px-2 py-0.5 rounded-full bg-[var(--neon-cyan)]/10 text-[var(--neon-cyan)] border border-[var(--neon-cyan)]/30 text-xs font-medium">
              {ownerFilter}
            </span>
            <button
              onClick={() => setOwnerFilter(null)}
              className="ml-1 px-2 py-0.5 text-xs rounded text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-glass-hover)] transition-colors"
            >
              Clear
            </button>
          </div>
        )}

        {/* Bulk action bar */}
        {!readOnly && selectedKeys.size > 0 && (
          <section className="glass-card p-3 flex items-center justify-between">
            <span className="text-sm text-[var(--text-secondary)]">
              {selectedKeys.size} env{selectedKeys.size !== 1 ? 's' : ''} selected
            </span>
            <div className="flex items-center gap-2">
              <Button variant="ghost" onClick={clearSelection}>
                Clear
              </Button>
              <Button variant="danger" onClick={openBulkDelete} disabled={!folderId}>
                Delete Selected
              </Button>
            </div>
          </section>
        )}

        {/* Unified table */}
        <section
          className="rounded-xl overflow-hidden flex flex-col flex-1 min-h-0"
          id="code-envs-table"
        >
          {isLoading && rows.length === 0 ? (
            <div className="px-4 py-3">
              <ProgressIndicator lifecycle={loading} />
            </div>
          ) : rows.length === 0 ? (
            <div className="p-4 text-sm text-[var(--text-secondary)]">
              Waiting for code environment data...
            </div>
          ) : (
            <div className="overflow-auto flex-1 min-h-0">
              <table className="w-full">
                <thead className="bg-[var(--bg-app)] sticky top-0">
                  <tr>
                    {!readOnly && (
                      <th className="px-3 py-3 text-left w-10">
                        <input
                          type="checkbox"
                          checked={
                            selectableUnusedRows.length > 0 &&
                            selectableUnusedRows.every((r) => selectedKeys.has(r.envKey))
                          }
                          onChange={() =>
                            toggleSelectAll(selectableUnusedRows.map((r) => r.envKey))
                          }
                          className="accent-[var(--neon-cyan)]"
                          title="Select all unused envs"
                        />
                      </th>
                    )}
                    <th
                      className="px-4 py-3 text-left text-sm font-semibold text-[var(--text-secondary)] cursor-pointer select-none"
                      onClick={() => toggleSort('name')}
                    >
                      Name{sortIndicator('name')}
                    </th>
                    <th
                      className="px-4 py-3 text-left text-sm font-semibold text-[var(--text-secondary)] cursor-pointer select-none"
                      onClick={() => toggleSort('owner')}
                    >
                      Owner{sortIndicator('owner')}
                    </th>
                    <th
                      className="px-4 py-3 text-left text-sm font-semibold text-[var(--text-secondary)] cursor-pointer select-none"
                      onClick={() => toggleSort('ownerEmail')}
                    >
                      Owner Email{sortIndicator('ownerEmail')}
                    </th>
                    <th
                      className="px-4 py-3 text-left text-sm font-semibold text-[var(--text-secondary)] cursor-pointer select-none"
                      onClick={() => toggleSort('version')}
                    >
                      Version{sortIndicator('version')}
                    </th>
                    <th
                      className="px-4 py-3 text-left text-sm font-semibold text-[var(--text-secondary)] cursor-pointer select-none"
                      onClick={() => toggleSort('language')}
                    >
                      Language{sortIndicator('language')}
                    </th>
                    <th
                      className="px-4 py-3 text-left text-sm font-semibold text-[var(--text-secondary)] cursor-pointer select-none"
                      onClick={() => toggleSort('size')}
                    >
                      Size (GB){sortIndicator('size')}
                    </th>
                    <th
                      className="px-4 py-3 text-left text-sm font-semibold text-[var(--text-secondary)] cursor-pointer select-none"
                      onClick={() => toggleSort('projects')}
                    >
                      Project{sortIndicator('projects')}
                    </th>
                    <th
                      className="px-4 py-3 text-left text-sm font-semibold text-[var(--text-secondary)] cursor-pointer select-none"
                      onClick={() => toggleSort('usages')}
                    >
                      Usages{sortIndicator('usages')}
                    </th>
                    {!readOnly && (
                      <th className="px-4 py-3 text-left text-sm font-semibold text-[var(--text-secondary)]">
                        Actions
                      </th>
                    )}
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border-glass)]">
                  {sortedRows.length === 0 ? (
                    <tr>
                      <td
                        colSpan={readOnly ? 8 : 10}
                        className="py-6 text-center text-sm text-[var(--text-muted)]"
                      >
                        {showAnalysisProgress
                          ? 'Scanning usages. Rows will appear as usage checks and env details stream in.'
                          : 'No code environments match the current filter.'}
                      </td>
                    </tr>
                  ) : (
                    sortedRows.map((row) => {
                      const isUnused = row.usageCount === 0;
                      const env = row.env;
                      return (
                        <tr key={row.envKey} className="hover:bg-[var(--bg-glass-hover)]">
                          {!readOnly && (
                            <td className="px-3 py-3">
                              {!row.isProvisional && isUnused ? (
                                <input
                                  type="checkbox"
                                  checked={selectedKeys.has(row.envKey)}
                                  onChange={() => toggleSelect(row.envKey)}
                                  className="accent-[var(--neon-cyan)]"
                                />
                              ) : null}
                            </td>
                          )}
                          <td className="px-4 py-3">
                            {env ? (
                              <a
                                href={dssUrls.codeEnv(env.language, env.name)}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-[var(--neon-cyan)] hover:underline"
                              >
                                {env.name}
                              </a>
                            ) : (
                              <span className="text-[var(--text-primary)]">
                                {row.name}
                                <span className="ml-2 inline-block px-1.5 py-0.5 rounded text-[10px] bg-[var(--neon-cyan)]/20 text-[var(--neon-cyan)]">
                                  provisional
                                </span>
                              </span>
                            )}
                          </td>
                          <td className="px-4 py-3">
                            {env ? (
                              <button
                                type="button"
                                onClick={() => setOwnerFilter(row.owner)}
                                className="text-[var(--neon-cyan)] hover:underline cursor-pointer bg-transparent border-none p-0 font-inherit text-inherit"
                              >
                                {row.owner}
                              </button>
                            ) : (
                              <span className="text-[var(--text-muted)]">{row.owner}</span>
                            )}
                          </td>
                          <td className="px-4 py-3 text-[var(--text-secondary)]">
                            {env?.ownerEmail ? (
                              <a
                                href={`mailto:${env.ownerEmail}`}
                                className="text-[var(--neon-cyan)] hover:underline"
                              >
                                {env.ownerEmail}
                              </a>
                            ) : (
                              <span className="text-[var(--text-muted)]">—</span>
                            )}
                          </td>
                          <td className="px-4 py-3">
                            {env?.language === 'python' ? (
                              <PythonVersionBadge version={env.version} />
                            ) : env?.language === 'r' ? (
                              <span className="text-[var(--neon-purple)] font-medium">
                                {env.version || '—'}
                              </span>
                            ) : (
                              <span className="text-[var(--text-muted)]">—</span>
                            )}
                          </td>
                          <td className="px-4 py-3">
                            {env?.language ? <LanguageBadge language={env.language} /> : null}
                          </td>
                          <td
                            className={`px-4 py-3 font-mono ${getRelativeSizeColor(env?.sizeBytes || 0, maxBytes)}`}
                          >
                            {formatSizeGb(env?.sizeBytes)}
                          </td>
                          <td className="px-4 py-3">
                            {env && row.projectCount > 0 ? (
                              <button
                                type="button"
                                onClick={() => openUsage(env)}
                                className="cursor-pointer bg-transparent p-0 font-mono text-sm font-semibold text-[var(--neon-cyan)] underline decoration-[var(--neon-cyan)]/50 underline-offset-4 hover:text-[var(--text-primary)] hover:decoration-[var(--text-primary)]"
                                aria-label={`Show ${row.projectCount} project${row.projectCount === 1 ? '' : 's'} using ${env.name}`}
                              >
                                {row.projectCount}
                              </button>
                            ) : (
                              <span className="font-mono text-sm text-[var(--text-muted)]">0</span>
                            )}
                          </td>
                          <td className="px-4 py-3">
                            {row.isProvisional ? (
                              row.provisional?.isSkipped ? (
                                <span className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-[var(--bg-glass)] text-[var(--text-muted)]">
                                  {row.provisional?.statusLabel || 'Skipped'}
                                </span>
                              ) : isUnused ? (
                                <span className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-[var(--neon-amber)]/20 text-[var(--warning)]">
                                  Unused
                                </span>
                              ) : (
                                <span className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-[var(--neon-green)]/20 text-[var(--neon-green)]">
                                  {row.provisional?.statusLabel ||
                                    `${row.usageCount} usage${row.usageCount !== 1 ? 's' : ''}`}
                                </span>
                              )
                            ) : isUnused ? (
                              <span className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-[var(--neon-amber)]/20 text-[var(--warning)]">
                                Unused
                              </span>
                            ) : (
                              <span className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-[var(--neon-green)]/20 text-[var(--neon-green)]">
                                {row.usageCount} usage{row.usageCount !== 1 ? 's' : ''}
                              </span>
                            )}
                          </td>
                          {!readOnly && (
                            <td className="px-4 py-3">
                              {!row.isProvisional && isUnused && env && (
                                <Button
                                  variant="danger"
                                  onClick={() => openDeleteConfirm(row as RealEnvRow)}
                                  disabled={!folderId}
                                  title={
                                    !folderId ? 'Select a backup destination first' : undefined
                                  }
                                >
                                  Delete
                                </Button>
                              )}
                            </td>
                          )}
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Usage modal */}
        <UsageModal
          env={usageEnv}
          baseUrl={baseUrl}
          isOpen={usageModal.isOpen}
          onClose={usageModal.close}
        />

        {!readOnly && (
          <>
            {/* Bulk Delete Confirmation Modal */}
            <ConfirmDeleteDialog
              isOpen={bulkDeleteModal.isOpen}
              onClose={bulkDeleteModal.close}
              title="Confirm Bulk Deletion"
              confirmPhrase={`delete ${selectedRows.length} envs`}
              confirmLabel={`Delete ${selectedRows.length} Envs`}
              loadingLabel="Deleting..."
              loading={bulkDeleteLoading}
              error={bulkDeleteError}
              progress={bulkDeleteProgress}
              onConfirm={() => void confirmBulkDelete()}
            >
              <p className="text-[var(--text-secondary)]">
                Are you sure you want to delete {selectedRows.length} code environment
                {selectedRows.length !== 1 ? 's' : ''}?
              </p>
              <div className="max-h-32 overflow-y-auto rounded bg-[var(--bg-glass)] p-2">
                {selectedRows.map((r) => (
                  <div key={r.envKey} className="text-xs font-mono text-[var(--neon-red)] py-0.5">
                    {r.env.name}
                  </div>
                ))}
              </div>
              <p className="text-sm text-[var(--text-muted)]">
                A backup will be uploaded to the selected managed folder before each deletion.
              </p>
            </ConfirmDeleteDialog>

            {/* Single Delete Confirmation Modal */}
            <ConfirmDeleteDialog
              isOpen={deleteModal.isOpen}
              onClose={deleteModal.close}
              title="Confirm Deletion"
              confirmPhrase={`delete ${deleteTarget?.env.name || ''}`}
              confirmLabel="Delete"
              loadingLabel="Backing up & deleting..."
              loading={deleteLoading}
              error={deleteError}
              onConfirm={() => void confirmDelete()}
            >
              {deleteTarget && (
                <>
                  <p className="text-[var(--text-secondary)]">
                    Are you sure you want to delete code environment{' '}
                    <span className="font-mono text-[var(--neon-red)]">
                      {deleteTarget.env.name}
                    </span>
                    ?
                  </p>
                  <p className="text-sm text-[var(--text-muted)]">
                    A backup will be uploaded to the selected managed folder before deletion.
                  </p>
                </>
              )}
            </ConfirmDeleteDialog>

            {/* Live-apply replacement confirmation */}
            <Modal
              isOpen={confirmModal.isOpen}
              onClose={confirmModal.close}
              title="Apply Code Env Replacement"
            >
              <div className="space-y-4">
                <div className="rounded-lg border border-[var(--neon-red)]/30 bg-[var(--neon-red)]/10 px-3 py-2 text-sm text-[var(--text-primary)]">
                  This will replace usages from{' '}
                  <span className="font-mono">{sourceEnv?.name || sourceName}</span> to{' '}
                  <span className="font-mono">{targetName}</span>. Type{' '}
                  <span className="font-mono">CONFIRM</span> to enable the live apply.
                </div>
                <input
                  value={confirmText}
                  onChange={(e) => setConfirmText(e.target.value)}
                  placeholder="CONFIRM"
                  className="w-full rounded border border-[var(--border-glass)] bg-[var(--bg-elevated)] px-2 py-2 text-[var(--text-primary)]"
                />
                <div className="flex justify-end gap-2">
                  <button
                    onClick={confirmModal.close}
                    className="rounded border border-[var(--border-glass)] px-3 py-2 text-sm text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)]"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => void runReplace(false)}
                    disabled={!canLiveApply}
                    className="rounded bg-[var(--neon-red)] px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
                  >
                    {replaceLoading ? 'Replacing...' : 'Replace'}
                  </button>
                </div>
              </div>
            </Modal>
          </>
        )}
      </div>
    </div>
  );
}
