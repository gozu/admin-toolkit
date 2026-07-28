import { useState, useCallback, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useTableSort } from '../hooks/useTableSort';
import type { DirEntry, DirTreeData } from '../types';

/** Per-row delete affordance. `blocked` is a hard refusal from the fs-cleanup
 *  policy (e.g. the node's children name live projects) — it renders as a
 *  non-interactive chip carrying `reason`, never as an overridable button. */
export interface DirDeleteState {
  state: 'none' | 'ready' | 'blocked' | 'deleting';
  reason?: string;
}

interface DirTreeTableProps {
  data: DirTreeData;
  onExpand?: (dirPath: string) => Promise<DirEntry | null>;
  expandedNodes?: Map<string, DirEntry>;
  isExpanding?: boolean;
  rootNode?: DirEntry | null;
  // Optional: DirTreeSection renders this table without the delete column.
  onDeleteNode?: (node: DirEntry) => void;
  deleteStateFor?: (node: DirEntry) => DirDeleteState;
}

function formatSize(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function getSizeColor(bytes: number, maxBytes: number): string {
  const ratio = bytes / maxBytes;
  if (ratio > 0.5) return 'text-[var(--neon-red)]';
  if (ratio > 0.2) return 'text-[var(--neon-amber)]';
  if (ratio > 0.05) return 'text-[var(--neon-green)]';
  return 'text-[var(--text-muted)]';
}

function SizeBar({ size, maxSize }: { size: number; maxSize: number }) {
  const percentage = Math.min((size / maxSize) * 100, 100);
  const color = percentage > 50
    ? 'bg-[var(--neon-red)]'
    : percentage > 20
      ? 'bg-[var(--neon-amber)]'
      : 'bg-[var(--neon-green)]';

  return (
    <div className="w-20 h-2 bg-[var(--bg-glass)] rounded-full overflow-hidden">
      <motion.div
        className={`h-full ${color} rounded-full`}
        initial={{ width: 0 }}
        animate={{ width: `${percentage}%` }}
        transition={{ duration: 0.3, ease: 'easeOut' }}
      />
    </div>
  );
}

interface VisibleRow {
  node: DirEntry;
  depth: number;
  isExpanded: boolean;
  hasChildren: boolean;
  hasHiddenChildren: boolean;
  childCount: number;
}

// Precompute the flat list of visible rows via an iterative pre-order DFS that
// honors expansion, lazy-expanded children, and hidden-children — capped at
// maxVisible. Replaces the previous recursive render that incremented a counter
// mid-render (which mutated state during render).
function computeVisibleRows(
  root: DirEntry,
  expanded: Set<string>,
  lazyExpandedNodes: Map<string, DirEntry> | undefined,
  maxVisible: number,
): VisibleRow[] {
  const rows: VisibleRow[] = [];
  const stack: { node: DirEntry; depth: number }[] = [{ node: root, depth: 0 }];
  while (stack.length > 0 && rows.length < maxVisible) {
    const { node, depth } = stack.pop()!;

    const lazyExpandedNode = lazyExpandedNodes?.get(node.path);
    const hasChildren = node.isDirectory && (
      lazyExpandedNode
        ? lazyExpandedNode.children.length > 0
        : (node.children.length > 0 || node.hasHiddenChildren)
    );
    const hasHiddenChildren = node.hasHiddenChildren && !lazyExpandedNode;
    // Use lazy-expanded children if available, otherwise use regular children
    const effectiveChildren = lazyExpandedNode?.children || node.children;
    const isExpanded = expanded.has(node.path);

    rows.push({
      node,
      depth,
      isExpanded,
      hasChildren,
      hasHiddenChildren,
      childCount: effectiveChildren.length,
    });

    if (isExpanded && hasChildren) {
      // Push in reverse so children pop in their original order (pre-order DFS).
      for (let i = effectiveChildren.length - 1; i >= 0; i--) {
        stack.push({ node: effectiveChildren[i], depth: depth + 1 });
      }
    }
  }
  return rows;
}

function TreeRow({
  node,
  depth,
  maxSize,
  isExpanded,
  hasChildren,
  hasHiddenChildren,
  childCount,
  isExpanding,
  onToggle,
  deleteState,
  onDelete,
  showDeleteColumn,
}: {
  node: DirEntry;
  depth: number;
  maxSize: number;
  isExpanded: boolean;
  hasChildren: boolean;
  hasHiddenChildren: boolean;
  childCount: number;
  isExpanding?: boolean;
  onToggle: () => void;
  deleteState: DirDeleteState;
  onDelete?: (node: DirEntry) => void;
  showDeleteColumn: boolean;
}) {
  const indent = depth * 20;

  return (
    <motion.tr
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: -10 }}
      transition={{ duration: 0.15 }}
      onClick={hasChildren ? onToggle : undefined}
      role={hasChildren ? 'button' : undefined}
      tabIndex={hasChildren ? 0 : undefined}
      onKeyDown={hasChildren ? (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          if (e.key === ' ') e.preventDefault();
          onToggle();
        }
      } : undefined}
      className={`hover:bg-[var(--bg-glass-hover)] transition-colors group${hasChildren ? ' cursor-pointer' : ''}`}
    >
      <td className="py-2 px-3">
        <div className="flex items-center" style={{ paddingLeft: `${indent}px` }}>
          {hasChildren ? (
            <span className="w-5 h-5 flex items-center justify-center text-[var(--text-muted)] group-hover:text-[var(--text-primary)] transition-colors mr-2">
              {isExpanding && hasHiddenChildren ? (
                <motion.svg
                  className="w-4 h-4 text-[var(--neon-cyan)]"
                  viewBox="0 0 24 24"
                  animate={{ rotate: 360 }}
                  transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
                >
                  <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2" fill="none" strokeDasharray="30 70" />
                </motion.svg>
              ) : (
                <motion.svg
                  className="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                  animate={{ rotate: isExpanded ? 90 : 0 }}
                  transition={{ duration: 0.15 }}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </motion.svg>
              )}
            </span>
          ) : (
            <span className="w-5 h-5 mr-2" />
          )}

          {/* Icon */}
          {node.isDirectory ? (
            <svg className="w-4 h-4 text-[var(--neon-amber)] mr-2 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
              <path d="M2 6a2 2 0 012-2h5l2 2h5a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" />
            </svg>
          ) : (
            <svg className="w-4 h-4 text-[var(--text-muted)] mr-2 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" clipRule="evenodd" />
            </svg>
          )}

          <span className="truncate text-[var(--text-primary)] font-mono text-xs" title={node.name}>
            {node.name}
          </span>
        </div>
      </td>

      <td className="py-2 px-3 text-right">
        <span className={`font-mono text-xs ${getSizeColor(node.size, maxSize)}`}>
          {formatSize(node.size)}
        </span>
      </td>

      <td className="py-2 px-3">
        <SizeBar size={node.size} maxSize={maxSize} />
      </td>

      <td className="py-2 px-3 text-right">
        <span className="font-mono text-xs text-[var(--text-muted)]">
          {node.isDirectory ? node.fileCount.toLocaleString() : '-'}
          {hasHiddenChildren && (
            <span className="text-[var(--neon-amber)] ml-1" title="Has more files (click to expand)">+</span>
          )}
        </span>
      </td>

      <td className="py-2 px-3 text-right">
        <span className="font-mono text-xs text-[var(--text-muted)]">
          {node.isDirectory ? (
            <>
              {childCount}
              {hasHiddenChildren && (
                <span className="text-[var(--neon-amber)] ml-1" title="Has more items (click to expand)">+</span>
              )}
            </>
          ) : '-'}
        </span>
      </td>

      {showDeleteColumn && (
        <td className="py-2 px-3 text-right w-10">
          {deleteState.state === 'blocked' ? (
            <span
              className="text-xs cursor-help"
              title={deleteState.reason || 'Deletion refused by the fs-cleanup policy'}
              aria-label={deleteState.reason || 'Deletion refused'}
            >
              ⛔
            </span>
          ) : deleteState.state !== 'none' ? (
            <button
              type="button"
              // The whole row is a toggle — never let this bubble into it.
              onClick={(e) => { e.stopPropagation(); onDelete?.(node); }}
              disabled={deleteState.state === 'deleting'}
              title={`Delete the on-disk files of orphan project ${node.name}`}
              aria-label={`Delete orphan project ${node.name}`}
              className="opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity p-1 rounded text-[var(--text-muted)] hover:text-[var(--neon-red)] hover:bg-[var(--bg-glass-hover)] disabled:opacity-60"
            >
              {deleteState.state === 'deleting' ? (
                <motion.svg
                  className="w-3.5 h-3.5 text-[var(--neon-amber)]"
                  viewBox="0 0 24 24"
                  animate={{ rotate: 360 }}
                  transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
                >
                  <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2" fill="none" strokeDasharray="30 70" />
                </motion.svg>
              ) : (
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              )}
            </button>
          ) : null}
        </td>
      )}
    </motion.tr>
  );
}

type SortField = 'name' | 'size' | 'files' | 'items';

function SortHeader({
  field,
  label,
  sortField,
  sortDir,
  onSort,
}: {
  field: SortField;
  label: React.ReactNode;
  sortField: SortField;
  sortDir: 'asc' | 'desc';
  onSort: (field: SortField) => void;
}) {
  return (
    <th
      className="py-2 px-3 text-left text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider cursor-pointer hover:text-[var(--text-primary)] transition-colors select-none"
      onClick={() => onSort(field)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          if (e.key === ' ') e.preventDefault();
          onSort(field);
        }
      }}
    >
      <div className="flex items-center gap-1">
        {label}
        {sortField === field && (
          <span className="text-[var(--neon-cyan)]">
            {sortDir === 'asc' ? '↑' : '↓'}
          </span>
        )}
      </div>
    </th>
  );
}

const NO_DELETE: DirDeleteState = { state: 'none' };

export function DirTreeTable({
  data, onExpand, expandedNodes, isExpanding, rootNode, onDeleteNode, deleteStateFor,
}: DirTreeTableProps) {
  const showDeleteColumn = !!deleteStateFor;
  // Render root follows the treemap's drill-down when provided, else the full tree.
  const baseRoot = rootNode ?? data.root;
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(baseRoot ? [baseRoot.path] : []));
  // Re-anchor (and auto-expand) the table when the drill-down root changes.
  const [anchoredPath, setAnchoredPath] = useState(baseRoot?.path);
  if (baseRoot?.path !== anchoredPath) {
    setAnchoredPath(baseRoot?.path);
    setExpanded(new Set(baseRoot ? [baseRoot.path] : []));
  }
  const { sortKey: sortField, sortDir: sortDirection, handleSort } = useTableSort<SortField>({
    defaultKey: 'size',
    ascDefaultKeys: ['name'],
  });
  const [maxVisible] = useState(200);

  const toggleExpand = useCallback((path: string) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }, []);

  const expandAll = useCallback(() => {
    if (!baseRoot) return;
    const allPaths = new Set<string>();
    const collect = (node: DirEntry) => {
      if (node.isDirectory) {
        allPaths.add(node.path);
        node.children.forEach(collect);
      }
    };
    collect(baseRoot);
    setExpanded(allPaths);
  }, [baseRoot]);

  const collapseAll = useCallback(() => {
    if (!baseRoot) return;
    setExpanded(new Set([baseRoot.path]));
  }, [baseRoot]);

  const handleToggle = useCallback(
    async (node: DirEntry) => {
      if (isExpanding) return;
      const lazyExpandedNode = expandedNodes?.get(node.path);
      const isExpanded = expanded.has(node.path);
      const hasHiddenChildren = node.hasHiddenChildren && !lazyExpandedNode;
      // If has hidden children and not yet lazy-expanded, trigger lazy expand
      if (hasHiddenChildren && onExpand && !isExpanded) {
        await onExpand(node.path);
      }
      toggleExpand(node.path);
    },
    [isExpanding, expandedNodes, expanded, onExpand, toggleExpand],
  );

  // Sort the tree (only top-level children, keeps tree structure)
  const sortedRoot = useMemo(() => {
    if (!baseRoot) return null;

    const sortChildren = (node: DirEntry): DirEntry => {
      if (!node.isDirectory || node.children.length === 0) return node;

      const sortedChildren = [...node.children].sort((a, b) => {
        let cmp = 0;
        switch (sortField) {
          case 'name':
            cmp = a.name.localeCompare(b.name);
            break;
          case 'size':
            cmp = a.size - b.size;
            break;
          case 'files':
            cmp = a.fileCount - b.fileCount;
            break;
          case 'items':
            cmp = a.children.length - b.children.length;
            break;
        }
        return sortDirection === 'asc' ? cmp : -cmp;
      });

      return {
        ...node,
        children: sortedChildren.map(sortChildren),
      };
    };

    return sortChildren(baseRoot);
  }, [baseRoot, sortField, sortDirection]);

  if (!baseRoot) {
    return (
      <div className="glass-card p-5 flex items-center justify-center h-[400px]">
        <span className="text-[var(--text-muted)]">No directory data available</span>
      </div>
    );
  }

  const visibleRows = sortedRoot
    ? computeVisibleRows(sortedRoot, expanded, expandedNodes, maxVisible)
    : [];

  return (
    <motion.div
      className="glass-card p-5 flex flex-col flex-1 min-h-0"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-neon-subtle">Tree Table View</h3>
        <div className="flex items-center gap-2">
          <button
            onClick={expandAll}
            className="px-3 py-1 text-xs rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-[var(--text-secondary)] transition-colors"
          >
            Expand All
          </button>
          <button
            onClick={collapseAll}
            className="px-3 py-1 text-xs rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-[var(--text-secondary)] transition-colors"
          >
            Collapse All
          </button>
        </div>
      </div>

      {/* Summary stats */}
      <div className="flex gap-4 mb-4 text-xs text-[var(--text-muted)]">
        <span>Total: <span className="text-[var(--neon-green)] font-mono">{formatSize(data.totalSize)}</span></span>
        <span>Files: <span className="text-[var(--neon-cyan)] font-mono">{data.totalFiles.toLocaleString()}</span></span>
        <span>Root: <span className="text-[var(--neon-amber)] font-mono">{data.rootPath}</span></span>
      </div>

      {/* Table */}
      <div className="overflow-auto flex-1 min-h-0">
        <table className="w-full">
          <thead className="sticky top-0 bg-[var(--bg-glass)] z-10">
            <tr className="border-b border-[var(--border-glass)]">
              <SortHeader field="name" label="Name" sortField={sortField} sortDir={sortDirection} onSort={handleSort} />
              <SortHeader field="size" label="Size" sortField={sortField} sortDir={sortDirection} onSort={handleSort} />
              <th className="py-2 px-3 text-left text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider w-24">
                Usage
              </th>
              <SortHeader field="files" label="Files" sortField={sortField} sortDir={sortDirection} onSort={handleSort} />
              <SortHeader field="items" label="Items" sortField={sortField} sortDir={sortDirection} onSort={handleSort} />
              {showDeleteColumn && (
                <th className="py-2 px-3 text-right text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider w-10">
                  <span className="sr-only">Delete</span>
                </th>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border-glass)]">
            <AnimatePresence>
              {visibleRows.map(({ node, depth, isExpanded, hasChildren, hasHiddenChildren, childCount }) => (
                <TreeRow
                  key={node.path}
                  node={node}
                  depth={depth}
                  maxSize={baseRoot.size}
                  isExpanded={isExpanded}
                  hasChildren={hasChildren}
                  hasHiddenChildren={hasHiddenChildren}
                  childCount={childCount}
                  isExpanding={isExpanding}
                  onToggle={() => handleToggle(node)}
                  deleteState={deleteStateFor?.(node) ?? NO_DELETE}
                  onDelete={onDeleteNode}
                  showDeleteColumn={showDeleteColumn}
                />
              ))}
            </AnimatePresence>
          </tbody>
        </table>
      </div>

      {visibleRows.length >= maxVisible && (
        <div className="mt-2 text-xs text-[var(--text-muted)] text-center">
          Showing {maxVisible} items. Expand fewer directories to see more.
        </div>
      )}
    </motion.div>
  );
}
