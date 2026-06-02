import { useMemo, useState } from 'react';
import { useModal } from '../../hooks/useModal';
import { DataGrid } from '../common/DataGrid';
import type { ColumnDef } from '../../utils/dataGridTypes';
import type { User } from '../../types';
import { USER_COLUMNS, type UserColumnId, type UserMatrixCtx } from '../../utils/userMatrix';
import type { DaughterSpec } from '../../utils/userDaughterSpecs';
import { DaughterTableModal } from './DaughterTableModal';

interface UsersTableProps {
  users: User[];
  ctx: UserMatrixCtx;
  search: string;
  onlyWithIssues: boolean;
  hideZeroColumns: boolean;
}

type UserRow = { user: User; values: Partial<Record<UserColumnId, number>> };

export function UsersTable({
  users,
  ctx,
  search,
  onlyWithIssues,
  hideZeroColumns,
}: UsersTableProps) {
  const drilldownModal = useModal();
  const { open: openDrilldown } = drilldownModal;
  const [drilldownSpec, setDrilldownSpec] = useState<DaughterSpec | null>(null);

  const filteredUsers = useMemo(() => {
    const needle = search.trim().toLowerCase();
    let pool = users;
    if (needle) {
      pool = pool.filter(
        (u) =>
          u.login.toLowerCase().includes(needle) ||
          (u.email || '').toLowerCase().includes(needle),
      );
    }
    if (onlyWithIssues) {
      pool = pool.filter((u) => ctx.flaggedUsers.has(u.login));
    }
    return pool;
  }, [users, search, onlyWithIssues, ctx.flaggedUsers]);

  const userRows = useMemo<UserRow[]>(
    () =>
      filteredUsers.map((user) => {
        const values: Partial<Record<UserColumnId, number>> = {};
        for (const col of USER_COLUMNS) values[col.id] = col.accessor(user.login, ctx);
        return { user, values };
      }),
    [filteredUsers, ctx],
  );

  const columns = useMemo<ColumnDef<UserRow>[]>(() => {
    const cols: ColumnDef<UserRow>[] = [
      {
        id: 'login',
        label: 'Login',
        defaultSortDir: 'asc',
        mono: true,
        sticky: { left: 0 },
        render: ({ user }) => {
          const disabled = user.enabled === false;
          return (
            <span title={user.userProfile || ''}>
              <span className={disabled ? 'text-[var(--neon-red)]' : ''}>{user.login}</span>
              {disabled && (
                <span className="ml-1 text-[10px] uppercase tracking-wide text-[var(--neon-red)]">
                  disabled
                </span>
              )}
            </span>
          );
        },
        sortValue: ({ user }) => user.login,
      },
      {
        id: 'email',
        label: 'Email',
        defaultSortDir: 'asc',
        sticky: { left: 180 },
        cellClassName: 'text-[var(--text-muted)] truncate max-w-[260px]',
        render: ({ user }) => user.email || '—',
        sortValue: ({ user }) => user.email || '',
      },
    ];

    for (const uc of USER_COLUMNS) {
      cols.push({
        id: uc.id,
        label: uc.label,
        align: 'right',
        mono: true,
        headerClassName: 'whitespace-nowrap',
        headerTooltip: uc.tooltip,
        headerTooltipMarker: !!uc.tooltip,
        hidden: hideZeroColumns
          ? (rows) => !rows.some((r) => (r.values[uc.id] || 0) > 0)
          : undefined,
        render: ({ user, values }) => {
          const count = values[uc.id] || 0;
          return count > 0 ? (
            <button
              type="button"
              onClick={() => {
                setDrilldownSpec(uc.daughter(user.login, ctx));
                openDrilldown();
              }}
              className="bg-transparent p-0 font-mono underline decoration-current/40 underline-offset-4 hover:decoration-[var(--neon-cyan)] hover:text-[var(--neon-cyan)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--neon-cyan)]/60"
              aria-label={`Show ${count} ${uc.label} for ${user.login}`}
            >
              {count}
            </button>
          ) : (
            <span className="text-[var(--text-muted)]">0</span>
          );
        },
        sortValue: ({ values }) => values[uc.id] || 0,
      });
    }
    return cols;
  }, [ctx, hideZeroColumns, openDrilldown]);

  if (filteredUsers.length === 0) {
    return (
      <div className="rounded-lg p-6 text-sm text-[var(--text-secondary)]">
        No users match the current filters.
      </div>
    );
  }

  return (
    <>
      <div className="rounded-lg overflow-hidden flex flex-col flex-1 min-h-0">
        <div className="overflow-auto flex-1 min-h-0">
          <DataGrid
            rows={userRows}
            columns={columns}
            rowKey={({ user }) => user.login}
            defaultSortColumnId="projects"
            scroll="none"
          />
        </div>
      </div>

      <DaughterTableModal
        spec={drilldownSpec}
        isOpen={drilldownModal.isOpen}
        onClose={drilldownModal.close}
      />
    </>
  );
}
