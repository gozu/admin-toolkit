import { useMemo, useState } from 'react';
import { useDiag } from '../../context/DiagContext';
import { useThresholds } from '../../hooks/useThresholds';
import { ProgressIndicator } from '../common/ProgressIndicator';
import { UsersTable } from '../users/UsersTable';
import { buildUserMatrixContext } from '../../utils/userMatrix';
import { resolveLifecycleById } from '../../utils/pageLifecycle';

export function UsersPage() {
  const { state } = useDiag();
  const { parsedData } = state;
  const { thresholds } = useThresholds();

  const [search, setSearch] = useState('');
  const [onlyWithIssues, setOnlyWithIssues] = useState(false);
  const [hideZeroColumns, setHideZeroColumns] = useState(false);

  const users = parsedData.users || [];
  // Composite lifecycle: this page joins user × projectFootprint × codeEnvs ×
  // llmAudit. The inline progress and the sidebar glyph must read the same
  // aggregate so they can never disagree.
  const pageLifecycle = resolveLifecycleById('users', parsedData);
  const isLoading =
    pageLifecycle.phase === 'running' || pageLifecycle.phase === 'queued';

  const ctx = useMemo(
    () =>
      buildUserMatrixContext(parsedData, {
        codeEnvCountUnhealthy: thresholds.codeEnvCountUnhealthy,
        deprecatedPythonPrefixes: thresholds.deprecatedPythonPrefixes,
      }),
    [parsedData, thresholds.codeEnvCountUnhealthy, thresholds.deprecatedPythonPrefixes],
  );

  if (users.length === 0 && !isLoading) {
    return (
      <div className="w-full py-4">
        <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface)] p-6 text-sm text-[var(--text-secondary)]">
          No users in this diagnostic.
        </div>
      </div>
    );
  }

  return (
    <div className="page-fill">
      <div className="flex flex-col gap-4 flex-1 min-h-0">
        <div className="bg-[var(--bg-app)] p-4">
          <div className="flex flex-wrap items-center gap-3">
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter login or email…"
              className="flex-1 min-w-[200px] max-w-[360px] px-3 py-1.5 text-sm rounded-md bg-[var(--bg-glass)] border border-[var(--border-default)] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--neon-cyan)]"
            />
            <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)] cursor-pointer select-none">
              <input
                type="checkbox"
                checked={onlyWithIssues}
                onChange={(e) => setOnlyWithIssues(e.target.checked)}
                className="cursor-pointer"
              />
              Only users with issues
              <span className="text-[var(--text-muted)] text-xs">
                ({ctx.flaggedUsers.size})
              </span>
            </label>
            <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)] cursor-pointer select-none">
              <input
                type="checkbox"
                checked={hideZeroColumns}
                onChange={(e) => setHideZeroColumns(e.target.checked)}
                className="cursor-pointer"
              />
              Hide zero columns
            </label>
            <span className="ml-auto text-xs text-[var(--text-muted)]">
              {users.length} user{users.length === 1 ? '' : 's'} total
            </span>
          </div>

          {isLoading && (
            <div className="mt-3">
              <ProgressIndicator lifecycle={pageLifecycle} compact />
            </div>
          )}
        </div>

        <UsersTable
          users={users}
          ctx={ctx}
          search={search}
          onlyWithIssues={onlyWithIssues}
          hideZeroColumns={hideZeroColumns}
        />
      </div>
    </div>
  );
}
