import { Fragment, useCallback, useEffect, useMemo, useRef, type ReactNode } from 'react';
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
interface DataGridProps<R, C = never> {
  rows: R[];
  columns: ColumnDef<R>[];
  rowKey: (row: R, i: number) => string;
  /**
   * Expandable child rows (optional, additive). When a parent row's key is in
   * `expandedRowKeys`, its children render as extra `<tr>`s directly after it.
   * Children are excluded from sorting; cells inherit the matching column's
   * align/mono classes. The parent component owns the expansion state and the
   * expand/collapse affordance (rendered inside a column's `render`).
   * Two expansion modes share `expandedRowKeys`: column-aligned child rows
   * (`getRowChildren` + `renderChildRow`) and a full-width detail panel
   * (`renderExpandedRow`) — a given grid uses one or the other.
   */
  getRowChildren?: (row: R) => readonly C[];
  /** One ReactNode per visible column for a child row. */
  renderChildRow?: (child: C, parent: R, childIndex: number) => ReactNode[];
  childRowKey?: (child: C, parent: R, childIndex: number) => string;
  /** Full-width expanded detail rendered beneath the row (one <td colSpan>).
   *  Keyed by expandedRowKeys, independent of getRowChildren. */
  renderExpandedRow?: (row: R) => ReactNode;
  expandedRowKeys?: ReadonlySet<string>;
  childRowClassName?: string;
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

export function DataGrid<R, C = never>({
  rows,
  columns,
  rowKey,
  getRowChildren,
  renderChildRow,
  childRowKey,
  renderExpandedRow,
  expandedRowKeys,
  childRowClassName,
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
}: DataGridProps<R, C>) {
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

  const { sortKey, sortDir, handleSort } = useTableSort<string>({
    defaultKey: defaultSortColumnId ?? '',
    defaultDir: defaultSortDir,
    ascDefaultKeys,
  });

  // rAF-throttled scroll-shadow toggle. Mutates the DOM attribute directly so
  // scroll events never re-render the grid; CSS keys off [data-scrolled].
  const scrollRafRef = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (scrollRafRef.current != null) cancelAnimationFrame(scrollRafRef.current);
    },
    [],
  );
  const handleScrollShadow = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    if (scrollRafRef.current != null) return;
    scrollRafRef.current = requestAnimationFrame(() => {
      scrollRafRef.current = null;
      if (el.scrollTop > 0) el.setAttribute('data-scrolled', 'true');
      else el.removeAttribute('data-scrolled');
    });
  }, []);

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
            const isActiveSort = sortable && sortKey === col.id;
            // Inactive columns rest at the direction they'd adopt when
            // clicked, so the fade-in never co-animates a rotation.
            const arrowDir = isActiveSort
              ? sortDir
              : ascDefaultKeys.includes(col.id)
                ? 'asc'
                : 'desc';
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
                aria-sort={
                  isActiveSort ? (sortDir === 'asc' ? 'ascending' : 'descending') : undefined
                }
                role={sortable ? 'button' : undefined}
                tabIndex={sortable ? 0 : undefined}
                onClick={sortable ? () => handleSort(col.id) : undefined}
                onKeyDown={
                  sortable
                    ? (e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          if (e.key === ' ') e.preventDefault();
                          handleSort(col.id);
                        }
                      }
                    : undefined
                }
              >
                {col.label}
                {col.headerTooltipMarker && (
                  <span className="ml-0.5 cursor-help text-[var(--text-muted)]">(?)</span>
                )}
                {sortable && (
                  <span
                    aria-hidden="true"
                    className="dg-sort-arrow"
                    data-active={isActiveSort || undefined}
                    data-dir={arrowDir}
                  >
                    {'▲'}
                  </span>
                )}
              </th>
            );
          })}
        </tr>
      </thead>
      <tbody>
        {sortedRows.map((row, i) => {
          const key = rowKey(row, i);
          const parentTr = (
            <tr
              key={key}
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
          );
          if (renderExpandedRow && expandedRowKeys?.has(key)) {
            return (
              <Fragment key={key}>
                {parentTr}
                <tr key={`${key}::expanded`} className={childRowClassName}>
                  <td colSpan={visibleColumns.length} className="p-0">
                    {renderExpandedRow(row)}
                  </td>
                </tr>
              </Fragment>
            );
          }
          const renderChild = renderChildRow;
          const children =
            getRowChildren && renderChild && expandedRowKeys?.has(key)
              ? getRowChildren(row)
              : undefined;
          if (!children || children.length === 0 || !renderChild) return parentTr;
          return (
            <Fragment key={key}>
              {parentTr}
              {children.map((child, ci) => (
                <tr
                  key={childRowKey ? childRowKey(child, row, ci) : `${key}::child::${ci}`}
                  className={cx(
                    'transition-colors hover:bg-[var(--bg-hover)]',
                    childRowClassName,
                  )}
                >
                  {renderChild(child, row, ci).map((cell, colIdx) => {
                    const col = visibleColumns[colIdx];
                    return (
                      <td
                        key={col?.id ?? colIdx}
                        className={cx(
                          alignClass(col?.align),
                          col?.mono && 'font-mono tabular-nums',
                        )}
                      >
                        {cell}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </Fragment>
          );
        })}
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
      <div className="card-scroll-body dg-scrollable" onScroll={handleScrollShadow}>
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
      <div
        className="overflow-auto dg-scrollable"
        style={{ maxHeight: scroll.maxH }}
        onScroll={handleScrollShadow}
      >
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
    <div
      className={cx(
        'col-span-full chart-container flex min-h-0 flex-col',
        // A maxH-capped body can never use extra height — growing as a flex item
        // only inflates the card with empty space (flex intrinsic sizing equalizes
        // flex-1 siblings to the tallest one in an auto-height column).
        typeof scroll === 'object' ? 'flex-none' : 'flex-1',
      )}
      id={id}
    >
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
