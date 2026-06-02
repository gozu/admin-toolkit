import { useMemo, type ReactNode } from 'react';
import { useTableSort } from '../../hooks/useTableSort';
import { ProgressIndicator } from './ProgressIndicator';
import type { ColumnAlign, ColumnDef } from '../../utils/dataGridTypes';
import type { Lifecycle } from '../../types';

/**
 * DataGrid — the unified, column-config table engine. Purely presentational:
 * it owns sort state, the `.table-dark` markup (zebra/hover/sticky header from
 * CSS), click-to-sort + indicators, align/mono/sticky-column class emission,
 * empty/no-match/loading states, an optional row-count footer, optional
 * `chart-container` chrome (only when `title` is given) and a count badge.
 *
 * Parents own all filtering UI and pass already-filtered `rows`; modal state +
 * modal components; and any row-derived context. Clickable cells and
 * intra-cell expandable lists are expressed entirely inside each column's
 * `render`. The engine drives loading via `lifecycle` (never a `tone` prop).
 */
interface DataGridProps<R> {
  rows: R[];
  columns: ColumnDef<R>[];
  rowKey: (row: R, i: number) => string;
  defaultSortColumnId?: string;
  defaultSortDir?: 'asc' | 'desc';
  emptyMessage?: ReactNode;
  noMatchMessage?: ReactNode;
  filtersActive?: boolean;
  /** Drives the inline ProgressIndicator. NEVER pass a tone. */
  lifecycle?: Lifecycle | null;
  /** When given, wrap in `chart-container` chrome with this header title. */
  title?: ReactNode;
  countBadge?: { total: number; filtered?: number };
  /** Extra header content (avg lines, react-select filters …) rendered by the parent. */
  headerExtra?: ReactNode;
  showRowCount?: boolean;
  /** `table-fit` (width:auto) instead of full-width. */
  fit?: boolean;
  /** Scroll container: card-fill, fixed max-height, or none (parent controls). */
  scroll?: 'card' | { maxH: string } | 'none';
  id?: string;
  rowClassName?: (row: R) => string;
}

function alignClass(align: ColumnAlign | undefined): string {
  if (align === 'right') return 'text-right';
  if (align === 'center') return 'text-center';
  return '';
}

function cx(...parts: Array<string | false | undefined>): string {
  return parts.filter(Boolean).join(' ');
}

const STICKY_BG = 'bg-[var(--bg-app)]';

export function DataGrid<R>({
  rows,
  columns,
  rowKey,
  defaultSortColumnId,
  defaultSortDir = 'desc',
  emptyMessage,
  noMatchMessage,
  filtersActive = false,
  lifecycle,
  title,
  countBadge,
  headerExtra,
  showRowCount = false,
  fit = false,
  scroll = 'none',
  id,
  rowClassName,
}: DataGridProps<R>) {
  // Conditional columns are resolved against the full (filtered) row set.
  const visibleColumns = useMemo(
    () => columns.filter((c) => !c.hidden?.(rows)),
    [columns, rows],
  );
  const hasSticky = visibleColumns.some((c) => c.sticky);

  // A column's defaultSortDir governs the direction when you *switch* to it
  // (via ascDefaultKeys); the grid-level defaultSortDir is the initial-load
  // direction for defaultSortColumnId. These are intentionally independent —
  // e.g. a column can load desc but flip to asc on re-select.
  const ascDefaultKeys = useMemo(
    () => columns.filter((c) => c.defaultSortDir === 'asc').map((c) => c.id),
    [columns],
  );

  const { sortKey, sortDir, handleSort, sortIndicator } = useTableSort<string>({
    defaultKey: defaultSortColumnId ?? '',
    defaultDir: defaultSortDir,
    ascDefaultKeys,
  });

  const sortedRows = useMemo(() => {
    // Null the active sort when its column is hidden or non-sortable.
    const col = visibleColumns.find((c) => c.id === sortKey);
    if (!col?.sortValue) return rows;
    const sortFn = col.sortValue;
    const clone = [...rows];
    clone.sort((a, b) => {
      const av = sortFn(a);
      const bv = sortFn(b);
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortDir === 'asc' ? av - bv : bv - av;
      }
      const cmp = String(av).localeCompare(String(bv));
      return sortDir === 'asc' ? cmp : -cmp;
    });
    return clone;
  }, [rows, visibleColumns, sortKey, sortDir]);

  const lc = lifecycle ?? null;
  const isLoading = lc?.phase === 'running' || lc?.phase === 'queued';

  const badgeText = (() => {
    if (!countBadge) return null;
    const { total, filtered } = countBadge;
    if (total === 0) return '...';
    if (filtered != null && filtered !== total) return `${filtered}/${total}`;
    return String(total);
  })();

  const table = (
    <table className={cx('table-dark', fit && 'table-fit')}>
      <thead>
        <tr>
          {visibleColumns.map((col) => {
            const sortable = !!col.sortValue;
            const style: React.CSSProperties = {};
            if (col.width) style.width = col.width;
            if (col.sticky) style.left = col.sticky.left;
            return (
              <th
                key={col.id}
                className={cx(
                  sortable && 'cursor-pointer hover:text-[var(--neon-cyan)]',
                  alignClass(col.align),
                  col.sticky && cx('sticky z-20', STICKY_BG),
                  col.headerClassName,
                )}
                style={Object.keys(style).length ? style : undefined}
                title={col.headerTooltip}
                onClick={sortable ? () => handleSort(col.id) : undefined}
              >
                {col.label}
                {col.headerTooltipMarker && (
                  <span className="ml-0.5 cursor-help text-[var(--text-muted)]">(?)</span>
                )}
                {sortable && sortIndicator(col.id)}
              </th>
            );
          })}
        </tr>
      </thead>
      <tbody>
        {sortedRows.map((row, i) => (
          <tr
            key={rowKey(row, i)}
            className={cx(
              'transition-colors hover:bg-[var(--bg-hover)]',
              hasSticky && 'group',
              rowClassName?.(row),
            )}
          >
            {visibleColumns.map((col) => {
              const extra =
                typeof col.cellClassName === 'function'
                  ? col.cellClassName(row)
                  : col.cellClassName;
              return (
                <td
                  key={col.id}
                  className={cx(
                    alignClass(col.align),
                    col.mono && 'font-mono tabular-nums',
                    col.sticky &&
                      cx('sticky z-10', STICKY_BG, 'group-hover:bg-[var(--bg-hover)]'),
                    extra,
                  )}
                  style={col.sticky ? { left: col.sticky.left } : undefined}
                >
                  {col.render(row)}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );

  const rowCount = showRowCount && (
    <div className="mt-3 text-xs text-[var(--text-muted)]">
      {sortedRows.length} row{sortedRows.length === 1 ? '' : 's'}
    </div>
  );

  let body: ReactNode;
  if (rows.length === 0) {
    const msg = filtersActive ? (noMatchMessage ?? 'No matching rows.') : (emptyMessage ?? 'No rows.');
    body = <div className="px-4 py-6 text-sm text-[var(--text-secondary)]">{msg}</div>;
  } else if (scroll === 'card') {
    body = (
      <div className="card-scroll-body">
        {table}
        {rowCount}
      </div>
    );
  } else if (scroll === 'none') {
    body = (
      <>
        {table}
        {rowCount}
      </>
    );
  } else {
    body = (
      <div className="overflow-auto" style={{ maxHeight: scroll.maxH }}>
        {table}
        {rowCount}
      </div>
    );
  }

  const loadingSection = isLoading && lc && (
    <div className="border-b border-[var(--border-glass)] px-4 py-3">
      <ProgressIndicator lifecycle={lc} compact={rows.length > 0} />
    </div>
  );

  // No chrome: the parent supplies its own card/header (modals, plain cards).
  if (title == null) {
    return (
      <>
        {loadingSection}
        {headerExtra}
        {body}
      </>
    );
  }

  return (
    <div className="col-span-full chart-container flex min-h-0 flex-1 flex-col" id={id}>
      <div className="chart-header">
        <div className="flex items-center justify-between gap-3">
          <h4>{title}</h4>
          {badgeText != null && (
            <span className="badge badge-info font-mono">{badgeText}</span>
          )}
        </div>
      </div>
      {loadingSection}
      {headerExtra}
      {body}
    </div>
  );
}
