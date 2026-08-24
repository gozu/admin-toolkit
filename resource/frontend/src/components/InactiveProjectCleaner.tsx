import { useCallback, useEffect, useMemo, useState } from 'react';
import { useModal } from '../hooks/useModal';
import { useRowSelection } from '../hooks/useRowSelection';
import { useSortableTable } from '../hooks/useSortableTable';
import { Button } from './common/Button';
import { ConfirmDeleteDialog } from './common/ConfirmDeleteDialog';
import { StatTile } from './common/StatTile';
import { fetchJson } from '../utils/api';
import { getDssBaseUrl } from '../utils/codeEnvUsageLinks';
import { getActiveHostId } from '../state/hostStore';
import { managedFoldersScan } from '../state/managedFoldersStore';
import {
  fetchInactiveProjects,
  getCachedInactiveProjects,
  type ProjectRow,
} from '../state/inactiveProjectsCache';

// ── Sort helpers ──

type SortField = 'selected' | 'name' | 'owner' | 'daysInactive';
type SortDir = 'asc' | 'desc';

function sortRows(
  rows: ProjectRow[],
  field: SortField,
  dir: SortDir,
  selectedKeys: Set<string>,
): ProjectRow[] {
  const m = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    switch (field) {
      case 'selected': {
        const aS = selectedKeys.has(a.projectKey) ? 0 : 1;
        const bS = selectedKeys.has(b.projectKey) ? 0 : 1;
        return m * (aS - bS);
      }
      case 'name':
        return m * a.name.localeCompare(b.name);
      case 'owner':
        return m * a.owner.localeCompare(b.owner);
      case 'daysInactive':
        return m * (a.daysInactive - b.daysInactive);
    }
  });
}

function defaultSort(rows: ProjectRow[]): ProjectRow[] {
  return [...rows].sort((a, b) => b.daysInactive - a.daysInactive);
}

// ── Component ──

export function InactiveProjectCleaner() {
  const hostId = getActiveHostId();
  const cachedProjects = getCachedInactiveProjects(hostId);
  // Fetch inactive projects — uses module-level cache to survive remounts
  const [rows, setRows] = useState<ProjectRow[]>(cachedProjects ?? []);
  const [isLoading, setIsLoading] = useState(!cachedProjects);
  const [fetchError, setFetchError] = useState<string | null>(null);

  useEffect(() => {
    const currentHost = getActiveHostId();
    const cachedForHost = getCachedInactiveProjects(currentHost);
    if (cachedForHost) {
      // Cache-hit branch of the data-load effect — hydrating from cache.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setRows(cachedForHost);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    fetchInactiveProjects()
      .then((projects) => {
        if (!cancelled) setRows(projects);
      })
      .catch((err) => {
        if (!cancelled) setFetchError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [hostId]);

  // Managed folder state
  const {
    data: foldersData,
    loading: foldersLoading,
    scanStarted: foldersScanStarted,
  } = managedFoldersScan.use();
  const folders = useMemo(() => foldersData?.folders ?? [], [foldersData?.folders]);
  const [folderId, setFolderId] = useState('');

  useEffect(() => {
    if (!foldersScanStarted) void managedFoldersScan.load();
  }, [foldersScanStarted]);

  // Default/clamp the backup destination during render rather than via an effect.
  // `folderId` holds the user's explicit pick; reads use the effective value.
  // Without a pick, prefer the auto-provisioned archive folder (plugin setting).
  const archiveDefaultId = foldersData?.archiveDefaultId ?? '';
  const effectiveFolderId =
    folderId && folders.some((folder) => folder.id === folderId)
      ? folderId
      : archiveDefaultId && folders.some((folder) => folder.id === archiveDefaultId)
        ? archiveDefaultId
        : (folders[0]?.id ?? '');

  const { sortField, sortDir, toggleSort, sortIndicator } = useSortableTable<SortField>();
  const [deletedKeys, setDeletedKeys] = useState<Set<string>>(new Set());
  const { selectedKeys, toggleSelect, toggleSelectAll, clear: clearSelection } = useRowSelection();

  // Delete confirmation modal (single)
  const deleteModal = useModal();
  const [deleteTarget, setDeleteTarget] = useState<ProjectRow | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // Bulk delete modal
  const bulkDeleteModal = useModal();
  const [bulkDeleteLoading, setBulkDeleteLoading] = useState(false);
  const [bulkDeleteError, setBulkDeleteError] = useState<string | null>(null);
  const [bulkDeleteProgress, setBulkDeleteProgress] = useState('');

  const visibleRows = useMemo(
    () => rows.filter((r) => !deletedKeys.has(r.projectKey)),
    [rows, deletedKeys],
  );

  const sortedRows = useMemo(() => {
    if (!sortField) return defaultSort(visibleRows);
    return sortRows(visibleRows, sortField, sortDir, selectedKeys);
  }, [visibleRows, sortField, sortDir, selectedKeys]);

  const openDeleteConfirm = useCallback(
    (row: ProjectRow) => {
      setDeleteTarget(row);
      setDeleteError(null);
      deleteModal.open();
    },
    [deleteModal],
  );

  const selectedRows = useMemo(
    () => visibleRows.filter((r) => selectedKeys.has(r.projectKey)),
    [visibleRows, selectedKeys],
  );

  const openBulkDelete = useCallback(() => {
    setBulkDeleteError(null);
    setBulkDeleteProgress('');
    bulkDeleteModal.open();
  }, [bulkDeleteModal]);

  const confirmBulkDelete = useCallback(async () => {
    const count = selectedRows.length;
    if (count === 0) return;
    if (!effectiveFolderId) return;

    setBulkDeleteLoading(true);
    setBulkDeleteError(null);
    try {
      for (let i = 0; i < selectedRows.length; i++) {
        const row = selectedRows[i];
        setBulkDeleteProgress(`Deleting ${i + 1} of ${count}: ${row.projectKey}...`);
        await fetchJson(
          `/api/tools/project-cleaner/${row.projectKey}?folderId=${encodeURIComponent(effectiveFolderId)}`,
          {
            method: 'DELETE',
            headers: { 'X-Confirm-Name': row.projectKey },
          },
        );
        setDeletedKeys((prev) => new Set([...prev, row.projectKey]));
      }
      clearSelection();
      bulkDeleteModal.close();
    } catch (err) {
      setBulkDeleteError(err instanceof Error ? err.message : String(err));
    } finally {
      setBulkDeleteLoading(false);
      setBulkDeleteProgress('');
    }
  }, [selectedRows, bulkDeleteModal, effectiveFolderId, clearSelection]);

  const confirmDelete = useCallback(async () => {
    if (!deleteTarget) return;
    if (!effectiveFolderId) return;

    setDeleteLoading(true);
    setDeleteError(null);
    try {
      await fetchJson(
        `/api/tools/project-cleaner/${deleteTarget.projectKey}?folderId=${encodeURIComponent(effectiveFolderId)}`,
        {
          method: 'DELETE',
          headers: { 'X-Confirm-Name': deleteTarget.projectKey },
        },
      );
      setDeletedKeys((prev) => new Set([...prev, deleteTarget.projectKey]));
      deleteModal.close();
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeleteLoading(false);
    }
  }, [deleteTarget, deleteModal, effectiveFolderId]);

  const dssBaseUrl = getDssBaseUrl();

  if (isLoading) {
    return (
      <div className="space-y-4">
        <section className="glass-card p-4">
          <h3 className="text-lg font-semibold text-[var(--text-primary)]">
            Inactive Project Cleaner
          </h3>
          <p className="text-sm text-[var(--text-muted)] mt-1">Loading inactive project data…</p>
        </section>
      </div>
    );
  }

  if (fetchError) {
    return (
      <div className="space-y-4">
        <section className="glass-card p-4">
          <h3 className="text-lg font-semibold text-[var(--text-primary)]">
            Inactive Project Cleaner
          </h3>
          <p className="text-sm text-[var(--neon-red)] mt-1">
            Failed to load inactive projects: {fetchError}
          </p>
        </section>
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="space-y-4">
        <section className="glass-card p-4">
          <h3 className="text-lg font-semibold text-[var(--text-primary)]">
            Inactive Project Cleaner
          </h3>
          <p className="text-sm text-[var(--text-muted)] mt-1">
            No inactive projects found. Projects with 365+ days of inactivity, no active scenarios,
            and no deployed bundles will appear here.
          </p>
        </section>
      </div>
    );
  }

  return (
    <>
      <div className="flex-1 min-h-0 flex flex-col space-y-4">
        {/* Header */}
        <section className="glass-card p-4">
          <h3 className="text-lg font-semibold text-[var(--text-primary)]">
            Inactive Project Cleaner
          </h3>
          <p className="text-sm text-[var(--text-muted)]">
            Projects inactive for 365+ days with no active scenarios or deployed bundles. A backup
            is uploaded to the selected managed folder before deletion.
          </p>
          <div className="mt-3 flex items-center gap-2">
            <label
              className="text-sm text-[var(--text-secondary)] whitespace-nowrap"
              htmlFor="pc-folder-select"
            >
              Backup destination
            </label>
            <select
              id="pc-folder-select"
              value={effectiveFolderId}
              onChange={(e) => setFolderId(e.target.value)}
              disabled={foldersLoading || folders.length === 0}
              className="input-glass text-sm py-1 px-2 rounded min-w-[200px]"
            >
              {foldersLoading ? (
                <option value="">Loading…</option>
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
          </div>
        </section>

        {/* Stats bar */}
        <section className="glass-card p-4">
          <div className="grid grid-cols-2 gap-4">
            <StatTile value={visibleRows.length} label="Total" />
            <StatTile
              value={deletedKeys.size}
              label="Deleted This Session"
              valueClassName="text-[var(--neon-green)]"
            />
          </div>
        </section>

        {/* Bulk action bar */}
        {selectedKeys.size > 0 && (
          <section className="glass-card p-3 flex items-center justify-between">
            <span className="text-sm text-[var(--text-secondary)]">
              {selectedKeys.size} project{selectedKeys.size !== 1 ? 's' : ''} selected
            </span>
            <div className="flex items-center gap-2">
              <Button variant="ghost" onClick={clearSelection}>
                Clear
              </Button>
              <Button variant="danger" onClick={openBulkDelete} disabled={!effectiveFolderId}>
                Delete Selected
              </Button>
            </div>
          </section>
        )}

        {/* Project table */}
        <section className="glass-card p-4 flex-1 min-h-0 flex flex-col">
          <div className="flex-1 min-h-0 overflow-auto">
            <table className="table-dark w-full">
              <thead>
                <tr>
                  <th
                    className="w-10 cursor-pointer select-none"
                    onClick={() => toggleSort('selected')}
                  >
                    <input
                      type="checkbox"
                      checked={
                        visibleRows.length > 0 &&
                        visibleRows.every((r) => selectedKeys.has(r.projectKey))
                      }
                      onClick={(e) => e.stopPropagation()}
                      onChange={() => toggleSelectAll(visibleRows.map((r) => r.projectKey))}
                      className="accent-[var(--neon-cyan)]"
                      title="Select all projects"
                    />
                    {sortIndicator('selected')}
                  </th>
                  <th className="cursor-pointer select-none" onClick={() => toggleSort('name')}>
                    Project Name{sortIndicator('name')}
                  </th>
                  <th className="cursor-pointer select-none" onClick={() => toggleSort('owner')}>
                    Owner{sortIndicator('owner')}
                  </th>
                  <th
                    className="cursor-pointer select-none"
                    onClick={() => toggleSort('daysInactive')}
                  >
                    Days Inactive{sortIndicator('daysInactive')}
                  </th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {sortedRows.map((row) => (
                  <tr key={row.projectKey} className="hover:bg-[var(--bg-glass)]">
                    <td>
                      <input
                        type="checkbox"
                        checked={selectedKeys.has(row.projectKey)}
                        onChange={() => toggleSelect(row.projectKey)}
                        className="accent-[var(--neon-cyan)]"
                      />
                    </td>
                    <td>
                      <a
                        href={`${dssBaseUrl}/projects/${row.projectKey}/`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[var(--neon-cyan)] hover:underline"
                      >
                        {row.name}
                      </a>
                    </td>
                    <td className="text-[var(--text-secondary)]">{row.owner}</td>
                    <td>
                      <span
                        className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                          row.daysInactive >= 365
                            ? 'bg-[var(--neon-red)]/20 text-[var(--neon-red)]'
                            : 'bg-[var(--warning)]/20 text-[var(--warning)]'
                        }`}
                      >
                        {row.daysInactive}d
                      </span>
                    </td>
                    <td>
                      <Button
                        variant="danger"
                        onClick={() => openDeleteConfirm(row)}
                        disabled={!effectiveFolderId}
                        title={!effectiveFolderId ? 'Select a backup destination first' : undefined}
                      >
                        Delete
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      {/* Bulk Delete Confirmation Modal */}
      <ConfirmDeleteDialog
        isOpen={bulkDeleteModal.isOpen}
        onClose={bulkDeleteModal.close}
        title="Confirm Bulk Deletion"
        confirmPhrase={`delete ${selectedRows.length} projects`}
        confirmLabel={`Delete ${selectedRows.length} Projects`}
        loadingLabel="Deleting…"
        loading={bulkDeleteLoading}
        error={bulkDeleteError}
        progress={bulkDeleteProgress}
        onConfirm={() => void confirmBulkDelete()}
      >
        <p className="text-[var(--text-secondary)]">
          Are you sure you want to delete {selectedRows.length} project
          {selectedRows.length !== 1 ? 's' : ''}?
        </p>
        <div className="max-h-32 overflow-y-auto rounded bg-[var(--bg-glass)] p-2">
          {selectedRows.map((r) => (
            <div key={r.projectKey} className="text-xs font-mono text-[var(--neon-red)] py-0.5">
              {r.projectKey}
            </div>
          ))}
        </div>
        <p className="text-sm text-[var(--text-muted)]">
          A backup will be uploaded to the selected managed folder before each deletion.
        </p>
      </ConfirmDeleteDialog>

      {/* Delete Confirmation Modal */}
      <ConfirmDeleteDialog
        isOpen={deleteModal.isOpen}
        onClose={deleteModal.close}
        title="Confirm Deletion"
        confirmPhrase={`delete ${deleteTarget?.projectKey || ''}`}
        confirmLabel="Delete"
        loadingLabel="Backing up & deleting..."
        loading={deleteLoading}
        error={deleteError}
        onConfirm={() => void confirmDelete()}
      >
        {deleteTarget && (
          <>
            <p className="text-[var(--text-secondary)]">
              Are you sure you want to delete project{' '}
              <span className="font-mono text-[var(--neon-red)]">{deleteTarget.projectKey}</span>?
            </p>
            <p className="text-sm text-[var(--text-muted)]">
              A backup will be uploaded to the selected managed folder before deletion.
            </p>
          </>
        )}
      </ConfirmDeleteDialog>
    </>
  );
}
