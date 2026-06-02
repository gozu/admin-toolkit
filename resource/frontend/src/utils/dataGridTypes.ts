import type { ReactNode } from 'react';

export type ColumnAlign = 'left' | 'right' | 'center';

/**
 * ColumnDef<R> is the single column-config contract for the unified DataGrid
 * engine. It is a strict superset of the older DaughterColumn<R>, so the
 * spec-driven `build*Daughter` factories compile against it unchanged
 * (utils/userDaughterSpecs.ts re-exports DaughterColumn as an alias).
 *
 * All rich per-cell behaviour (clickable cells → modals, intra-cell expandable
 * sublists, badges, links) lives inside `render`. The engine never grows a
 * subrow / cell-click feature — those are expressed in `render`.
 */
export interface ColumnDef<R> {
  /** Stable id, also used as the sort key. */
  id: string;
  /** Header label. */
  label: string;
  /** Renders the cell body. ALL rich behaviour lives here. */
  render: (row: R) => ReactNode;
  /** Presence ⇒ the column is sortable; returns the comparable value. */
  sortValue?: (row: R) => number | string;
  /** Sort direction applied when this column first becomes the active sort. */
  defaultSortDir?: 'asc' | 'desc';
  /** Text alignment for header + cells. */
  align?: ColumnAlign;
  /** Emit `font-mono tabular-nums` on cells (numeric-alignment fix). */
  mono?: boolean;
  /** Per-cell class. String, or a function of the row (health/count/price/severity colors). */
  cellClassName?: string | ((row: R) => string);
  /** Extra class on the header cell. */
  headerClassName?: string;
  /** Native tooltip on the header. */
  headerTooltip?: string;
  /** Render a "(?)" marker after the label (UsersTable affordance). */
  headerTooltipMarker?: boolean;
  /** Sticky (frozen) column pinned this many px from the left edge. */
  sticky?: { left: number };
  /** Explicit column width (CSS length, e.g. '40%' or '12rem'). */
  width?: string;
  /** Hide the column when this predicate over the full row set returns true. */
  hidden?: (rows: R[]) => boolean;
}
