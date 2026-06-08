import { useCallback, useEffect, useMemo, useState } from 'react';
import { Modal } from './Modal';
import { useModal } from '../hooks/useModal';
import { fetchJson, getBackendUrl } from '../utils/api';
import { getActiveHostId } from '../state/hostStore';
import { managedFoldersScan } from '../state/managedFoldersStore';
import {
  fetchInactiveProjects,
  getCachedInactiveProjects,
  type ProjectRow,
} from '../state/inactiveProjectsCache';

// ── Sort helpers ──

type SortField = 'name' | 'owner' | 'daysInactive';
type SortDir = 'asc' | 'desc';

function sortRows(rows: ProjectRow[], field: SortField, dir: SortDir): ProjectRow[] {
  const m = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    switch (field) {
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
      setRows(cachedForHost);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    fetchInactiveProjects()
      .then((projects) => { if (!cancelled) setRows(projects); })
      .catch((err) => { if (!cancelled) setFetchError(err instanceof Error ? err.message : String(err)); })
      .finally(() => { if (!cancelled) setIsLoading(false); });
    return () => { cancelled = true; };
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

  useEffect(() => {
    if (folders.length === 0) {
      if (folderId) setFolderId('');
      return;
    }
    if (!folders.some((folder) => folder.id === folderId)) {
      setFolderId(folders[0].id);
    }
  }, [folderId, folders]);

  const [sortField, setSortField] = useState<SortField | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [deletedKeys, setDeletedKeys] = useState<Set<string>>(new Set());
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());

  // Delete confirmation modal (single)
  const deleteModal = useModal();
  const [deleteTarget, setDeleteTarget] = useState<ProjectRow | null>(null);
  const [deleteInput, setDeleteInput] = useState('');
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // Bulk delete modal
  const bulkDeleteModal = useModal();
  const [bulkDeleteInput, setBulkDeleteInput] = useState('');
  const [bulkDeleteLoading, setBulkDeleteLoading] = useState(false);
  const [bulkDeleteError, setBulkDeleteError] = useState<string | null>(null);
  const [bulkDeleteProgress, setBulkDeleteProgress] = useState('');

  const visibleRows = useMemo(
    () => rows.filter((r) => !deletedKeys.has(r.projectKey)),
    [rows, deletedKeys],
  );

  const sortedRows = useMemo(() => {
    if (!sortField) return defaultSort(visibleRows);
    return sortRows(visibleRows, sortField, sortDir);
  }, [visibleRows, sortField, sortDir]);

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
    return sortDir === 'asc' ? ' \u25B2' : ' \u25BC';
  };

  const openDeleteConfirm = useCallback(
    (row: ProjectRow) => {
      setDeleteTarget(row);
      setDeleteInput('');
      setDeleteError(null);
      deleteModal.open();
    },
    [deleteModal],
  );

  const toggleSelect = useCallback((projectKey: string) => {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(projectKey)) next.delete(projectKey);
      else next.add(projectKey);
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback(() => {
    setSelectedKeys((prev) => {
      const allKeys = visibleRows.map((r) => r.projectKey);
      const allSelected = allKeys.every((k) => prev.has(k));
      if (allSelected) return new Set();
      return new Set(allKeys);
    });
  }, [visibleRows]);

  const selectedRows = useMemo(
    () => visibleRows.filter((r) => selectedKeys.has(r.projectKey)),
    [visibleRows, selectedKeys],
  );

  const openBulkDelete = useCallback(() => {
    setBulkDeleteInput('');
    setBulkDeleteError(null);
    setBulkDeleteProgress('');
    bulkDeleteModal.open();
  }, [bulkDeleteModal]);

  const confirmBulkDelete = useCallback(async () => {
    const count = selectedRows.length;
    if (count === 0) return;
    const expected = `delete ${count} projects`;
    if (bulkDeleteInput !== expected) return;
    if (!folderId) return;

    setBulkDeleteLoading(true);
    setBulkDeleteError(null);
    try {
      for (let i = 0; i < selectedRows.length; i++) {
        const row = selectedRows[i];
        setBulkDeleteProgress(`Deleting ${i + 1} of ${count}: ${row.projectKey}...`);
        await fetchJson(
          `/api/tools/project-cleaner/${row.projectKey}?folderId=${encodeURIComponent(folderId)}`,
          {
            method: 'DELETE',
            headers: { 'X-Confirm-Name': row.projectKey },
          },
        );
        setDeletedKeys((prev) => new Set([...prev, row.projectKey]));
      }
      setSelectedKeys(new Set());
      bulkDeleteModal.close();
    } catch (err) {
      setBulkDeleteError(err instanceof Error ? err.message : String(err));
    } finally {
      setBulkDeleteLoading(false);
      setBulkDeleteProgress('');
    }
  }, [selectedRows, bulkDeleteInput, bulkDeleteModal, folderId]);

  const confirmDelete = useCallback(async () => {
    if (!deleteTarget) return;
    const expected = `delete ${deleteTarget.projectKey}`;
    if (deleteInput !== expected) return;
    if (!folderId) return;

    setDeleteLoading(true);
    setDeleteError(null);
    try {
      await fetchJson(`/api/tools/project-cleaner/${deleteTarget.projectKey}?folderId=${encodeURIComponent(folderId)}`, {
        method: 'DELETE',
        headers: { 'X-Confirm-Name': deleteTarget.projectKey },
      });
      setDeletedKeys((prev) => new Set([...prev, deleteTarget.projectKey]));
      deleteModal.close();
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeleteLoading(false);
    }
  }, [deleteTarget, deleteInput, deleteModal, folderId]);

  const dssBaseUrl = useMemo(() => {
    const bUrl = getBackendUrl('/');
    try {
      const u = new URL(bUrl, window.location.origin);
      return `${u.protocol}//${u.host}`;
    } catch {
      return '';
    }
  }, []);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <section className="glass-card p-4">
          <h3 className="text-lg font-semibold text-[var(--text-primary)]">Inactive Project Cleaner</h3>
          <p className="text-sm text-[var(--text-muted)] mt-1">Loading inactive project data…</p>
        </section>
      </div>
    );
  }

  if (fetchError) {
    return (
      <div className="space-y-4">
        <section className="glass-card p-4">
          <h3 className="text-lg font-semibold text-[var(--text-primary)]">Inactive Project Cleaner</h3>
          <p className="text-sm text-[var(--neon-red)] mt-1">Failed to load inactive projects: {fetchError}</p>
        </section>
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="space-y-4">
        <section className="glass-card p-4">
          <h3 className="text-lg font-semibold text-[var(--text-primary)]">Inactive Project Cleaner</h3>
          <p className="text-sm text-[var(--text-muted)] mt-1">
            No inactive projects found. Projects with 180+ days of inactivity, no active scenarios, and no deployed bundles will appear here.
          </p>
        </section>
      </div>
    );
  }

  return (
    <>
      <div className="space-y-4">
        {/* Header */}
        <section className="glass-card p-4">
          <h3 className="text-lg font-semibold text-[var(--text-primary)]">Inactive Project Cleaner</h3>
          <p className="text-sm text-[var(--text-muted)]">
            Projects inactive for 180+ days with no active scenarios or deployed bundles. A backup is uploaded to the selected managed folder before deletion.
          </p>
          <div className="mt-3 flex items-center gap-2">
            <label className="text-sm text-[var(--text-secondary)] whitespace-nowrap" htmlFor="pc-folder-select">
              Backup destination
            </label>
            <select
              id="pc-folder-select"
              value={folderId}
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
                  <option key={f.id} value={f.id}>{f.name}</option>
                ))
              )}
            </select>
          </div>
        </section>

        {/* Stats bar */}
        <section className="glass-card p-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="text-center">
              <div className="text-2xl font-mono text-[var(--text-primary)]">{visibleRows.length}</div>
              <div className="text-xs text-[var(--text-muted)]">Total</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-mono text-[var(--neon-green)]">{deletedKeys.size}</div>
              <div className="text-xs text-[var(--text-muted)]">Deleted This Session</div>
            </div>
          </div>
        </section>

        {/* Bulk action bar */}
        {selectedKeys.size > 0 && (
          <section className="glass-card p-3 flex items-center justify-between">
            <span className="text-sm text-[var(--text-secondary)]">
              {selectedKeys.size} project{selectedKeys.size !== 1 ? 's' : ''} selected
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setSelectedKeys(new Set())}
                className="px-3 py-1 rounded-md text-xs font-medium text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] transition-colors"
              >
                Clear
              </button>
              <button
                onClick={openBulkDelete}
                disabled={!folderId}
                className="px-3 py-1 rounded-md text-xs font-medium border border-[var(--neon-red)]/30 bg-[var(--neon-red)]/10 text-[var(--neon-red)] hover:bg-[var(--neon-red)]/20 hover:border-[var(--neon-red)]/50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Delete Selected
              </button>
            </div>
          </section>
        )}

        {/* Project table */}
        <section className="glass-card p-4">
          <div className="overflow-auto max-h-[60vh]">
            <table className="table-dark w-full">
              <thead>
                <tr>
                  <th className="w-10">
                    <input
                      type="checkbox"
                      checked={visibleRows.length > 0 && visibleRows.every((r) => selectedKeys.has(r.projectKey))}
                      onChange={toggleSelectAll}
                      className="accent-[var(--neon-cyan)]"
                      title="Select all projects"
                    />
                  </th>
                  <th className="cursor-pointer select-none" onClick={() => toggleSort('name')}>
                    Project Name{sortIndicator('name')}
                  </th>
                  <th className="cursor-pointer select-none" onClick={() => toggleSort('owner')}>
                    Owner{sortIndicator('owner')}
                  </th>
                  <th className="cursor-pointer select-none" onClick={() => toggleSort('daysInactive')}>
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
                      <button
                        onClick={() => openDeleteConfirm(row)}
                        disabled={!folderId}
                        title={!folderId ? 'Select a backup destination first' : undefined}
                        className="px-3 py-1 rounded-md text-xs font-medium border border-[var(--neon-red)]/30 bg-[var(--neon-red)]/10 text-[var(--neon-red)] hover:bg-[var(--neon-red)]/20 hover:border-[var(--neon-red)]/50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      {/* Bulk Delete Confirmation Modal */}
      <Modal
        isOpen={bulkDeleteModal.isOpen}
        onClose={bulkDeleteModal.close}
        title="Confirm Bulk Deletion"
        footer={
          <div className="flex items-center justify-end gap-2">
            <button
              onClick={bulkDeleteModal.close}
              className="px-3 py-1.5 rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-[var(--text-secondary)]"
            >
              Cancel
            </button>
            <button
              onClick={confirmBulkDelete}
              disabled={bulkDeleteLoading || bulkDeleteInput !== `delete ${selectedRows.length} projects`}
              className="px-4 py-1.5 rounded bg-[var(--neon-red)]/20 text-[var(--neon-red)] hover:bg-[var(--neon-red)]/30 disabled:opacity-50 transition-colors"
            >
              {bulkDeleteLoading ? 'Deleting…' : `Delete ${selectedRows.length} Projects`}
            </button>
          </div>
        }
      >
        <div className="space-y-4">
          <p className="text-[var(--text-secondary)]">
            Are you sure you want to delete {selectedRows.length} project{selectedRows.length !== 1 ? 's' : ''}?
          </p>
          <div className="max-h-32 overflow-y-auto rounded bg-[var(--bg-glass)] p-2">
            {selectedRows.map((r) => (
              <div key={r.projectKey} className="text-xs font-mono text-[var(--neon-red)] py-0.5">{r.projectKey}</div>
            ))}
          </div>
          <p className="text-sm text-[var(--text-muted)]">
            A backup will be uploaded to the selected managed folder before each deletion.
          </p>
          <p className="text-sm text-[var(--text-muted)]">
            Type{' '}
            <code className="px-1.5 py-0.5 rounded bg-[var(--bg-glass)] text-[var(--text-primary)]">
              delete {selectedRows.length} projects
            </code>{' '}
            to confirm.
          </p>
          <input
            type="text"
            value={bulkDeleteInput}
            onChange={(e) => setBulkDeleteInput(e.target.value)}
            placeholder={`delete ${selectedRows.length} projects`}
            className="w-full input-glass font-mono text-sm"
            autoFocus
          />
          {bulkDeleteProgress && (
            <div className="text-sm text-[var(--text-secondary)]">{bulkDeleteProgress}</div>
          )}
          {bulkDeleteError && (
            <div className="text-sm text-[var(--neon-red)]">{bulkDeleteError}</div>
          )}
        </div>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        isOpen={deleteModal.isOpen}
        onClose={deleteModal.close}
        title="Confirm Deletion"
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
              disabled={deleteLoading || deleteInput !== `delete ${deleteTarget?.projectKey || ''}`}
              className="px-4 py-1.5 rounded bg-[var(--neon-red)]/20 text-[var(--neon-red)] hover:bg-[var(--neon-red)]/30 disabled:opacity-50 transition-colors"
            >
              {deleteLoading ? 'Backing up & deleting...' : 'Delete'}
            </button>
          </div>
        }
      >
        {deleteTarget && (
          <div className="space-y-4">
            <p className="text-[var(--text-secondary)]">
              Are you sure you want to delete project{' '}
              <span className="font-mono text-[var(--neon-red)]">{deleteTarget.projectKey}</span>?
            </p>
            <p className="text-sm text-[var(--text-muted)]">
              A backup will be uploaded to the selected managed folder before deletion.
            </p>
            <p className="text-sm text-[var(--text-muted)]">
              Type{' '}
              <code className="px-1.5 py-0.5 rounded bg-[var(--bg-glass)] text-[var(--text-primary)]">
                delete {deleteTarget.projectKey}
              </code>{' '}
              to confirm.
            </p>
            <input
              type="text"
              value={deleteInput}
              onChange={(e) => setDeleteInput(e.target.value)}
              placeholder={`delete ${deleteTarget.projectKey}`}
              className="w-full input-glass font-mono text-sm"
              autoFocus
            />
            {deleteError && (
              <div className="text-sm text-[var(--neon-red)]">{deleteError}</div>
            )}
          </div>
        )}
      </Modal>
    </>
  );
}
