import { useMemo, useState, useCallback, useEffect } from 'react';
import { motion } from 'framer-motion';
import { Chart as ChartJS, Tooltip, Legend } from 'chart.js';
import { TreemapController, TreemapElement } from 'chartjs-chart-treemap';
import { Chart } from 'react-chartjs-2';
import type { DirEntry, DirTreeData } from '../types';
import { anonText } from '../utils/anonymize';

// Register treemap components
ChartJS.register(TreemapController, TreemapElement, Tooltip, Legend);

interface DirTreemapProps {
  data: DirTreeData;
  onExpand?: (dirPath: string) => Promise<DirEntry | null>;
  expandedNodes?: Map<string, DirEntry>;
  isExpanding?: boolean;
  onVisibleDirectoriesChange?: (dirPaths: string[]) => void;
  onActiveNodeChange?: (node: DirEntry | null) => void;
}

// Color palette for different depths
const DEPTH_COLORS = [
  { bg: 'rgba(109, 163, 224, 0.7)', border: 'rgba(109, 163, 224, 1)' },   // blue
  { bg: 'rgba(99, 198, 157, 0.7)',  border: 'rgba(99, 198, 157, 1)' },    // mint
  { bg: 'rgba(224, 181, 97, 0.7)',  border: 'rgba(224, 181, 97, 1)' },    // amber
  { bg: 'rgba(224, 109, 131, 0.7)', border: 'rgba(224, 109, 131, 1)' },   // rose
  { bg: 'rgba(153, 123, 224, 0.7)', border: 'rgba(153, 123, 224, 1)' },   // violet
  { bg: 'rgba(101, 194, 217, 0.7)', border: 'rgba(101, 194, 217, 1)' },   // cyan
];

function formatSize(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function formatBreadcrumbName(name: string): string {
  const trimmed = String(name || '').trim().replace(/\/+$/, '');
  if (!trimmed) return name;
  if (!trimmed.includes('/')) return trimmed;
  const segments = trimmed.split('/').filter(Boolean);
  return segments[segments.length - 1] || trimmed;
}

function toDisplayNode(base: DirEntry, expanded?: DirEntry | null): DirEntry {
  const merged: DirEntry = expanded
    ? {
        ...base,
        children: expanded.children,
        hasHiddenChildren: expanded.hasHiddenChildren,
        fileCount: expanded.fileCount,
        size: expanded.size,
      }
    : base;

  const children = merged.children || [];
  const looksLikeCodeEnvLocationWrapper =
    children.length > 0 &&
    children.every((child) => typeof child.name === 'string' && child.name.startsWith('code-envs/'));

  if (!looksLikeCodeEnvLocationWrapper) {
    return merged;
  }

  const primary = [...children].sort((a, b) => b.size - a.size)[0];
  if (!primary || !primary.isDirectory) {
    return merged;
  }

  return {
    ...primary,
    name: merged.name,
  };
}

/**
 * Detects if a node is a "wrapper" that has only one significant child
 * with the same or similar display name.
 */
function isSingleChildWrapper(node: DirEntry): boolean {
  if (node.children.length !== 1) {
    return false;
  }

  const child = node.children[0];
  const parentBasename = formatBreadcrumbName(node.name);
  const childBasename = formatBreadcrumbName(child.name);

  // Check if parent and child have the same display name
  return parentBasename === childBasename;
}

/**
 * Checks if navigating to this node would result in the same effective view.
 * Returns true if navigation should be blocked.
 */
function wouldCreateDuplicateView(
  targetNode: DirEntry,
  currentActiveNode: DirEntry,
  expandedData?: DirEntry | null
): boolean {
  // Get the effective node after unwrapping
  const effectiveNode = toDisplayNode(targetNode, expandedData);

  // Check 1: Same path after unwrapping
  if (effectiveNode.path === currentActiveNode.path) {
    return true;
  }

  // Check 2: Single-child wrapper with same name as current
  if (isSingleChildWrapper(effectiveNode)) {
    const childName = formatBreadcrumbName(effectiveNode.children[0].name);
    const currentName = formatBreadcrumbName(currentActiveNode.name);
    if (childName === currentName) {
      return true;
    }
  }

  // Check 3: Target has same children as current (by comparing first few paths)
  if (effectiveNode.children.length > 0 && currentActiveNode.children.length > 0) {
    const targetChildPaths = effectiveNode.children.slice(0, 3).map(c => c.path);
    const currentChildPaths = currentActiveNode.children.slice(0, 3).map(c => c.path);
    if (targetChildPaths.length === currentChildPaths.length &&
        targetChildPaths.every((p, i) => p === currentChildPaths[i])) {
      return true;
    }
  }

  return false;
}

export function DirTreemap({ data, onExpand, expandedNodes, isExpanding, onVisibleDirectoriesChange, onActiveNodeChange }: DirTreemapProps) {
  const [currentNode, setCurrentNode] = useState<DirEntry | null>(null);
  const [breadcrumbs, setBreadcrumbs] = useState<DirEntry[]>([]);
  const [loadingPath, setLoadingPath] = useState<string | null>(null);

  const activeNode = currentNode || data.root;

  useEffect(() => { onActiveNodeChange?.(currentNode); }, [currentNode, onActiveNodeChange]);

  const pushParentIfNeeded = useCallback(() => {
    if (!activeNode || !data.root) return;
    if (activeNode.path === data.root.path) return;
    setBreadcrumbs(prev => [...prev, activeNode]);
  }, [activeNode, data.root]);

  const navigateTo = useCallback(async (node: DirEntry) => {
    if (!node.isDirectory) return;

    // Check if we already have expanded data for this node
    const expandedData = expandedNodes?.get(node.path);

    // PREVENT DUPLICATE/WRAPPER NAVIGATION:
    // Check if navigating to this node would show the same content
    if (activeNode && wouldCreateDuplicateView(node, activeNode, expandedData)) {
      console.log('Blocked duplicate navigation to:', node.path);
      return;
    }

    // If has hidden children and no expanded data, trigger lazy expand
    if (node.hasHiddenChildren && !expandedData && onExpand) {
      setLoadingPath(node.path);
      const expanded = await onExpand(node.path);
      setLoadingPath(null);

      if (expanded) {
        // Re-check after expansion
        if (activeNode && wouldCreateDuplicateView(node, activeNode, expanded)) {
          console.log('Blocked duplicate navigation after expansion:', node.path);
          return;
        }
        pushParentIfNeeded();
        setCurrentNode(toDisplayNode(node, expanded));
        return;
      }
    }

    // Use expanded data if available
    if (expandedData) {
      pushParentIfNeeded();
      setCurrentNode(toDisplayNode(node, expandedData));
      return;
    }

    // Normal navigation
    if (node.children.length > 0) {
      pushParentIfNeeded();
      setCurrentNode(node);
    }
  }, [onExpand, expandedNodes, pushParentIfNeeded, activeNode]);

  const navigateUp = useCallback(() => {
    if (breadcrumbs.length > 0) {
      const newBreadcrumbs = [...breadcrumbs];
      const parent = newBreadcrumbs.pop();
      setBreadcrumbs(newBreadcrumbs);
      setCurrentNode(parent === data.root ? null : parent!);
      return;
    }
    if (currentNode) {
      setCurrentNode(null);
    }
  }, [breadcrumbs, currentNode, data.root]);

  const resetNavigation = useCallback(() => {
    setBreadcrumbs([]);
    setCurrentNode(null);
  }, []);

  const activeExpanded = expandedNodes?.get(activeNode?.path ?? '');

  const chartItems = useMemo(() => {
    if (!activeNode) return [];

    const effectiveChildren = activeExpanded?.children || activeNode.children;
    if (effectiveChildren.length > 0) {
      return effectiveChildren.map(child => ({
        name: child.name,
        size: child.size,
        depth: child.depth,
        isDir: child.isDirectory,
        fileCount: child.fileCount,
        fullPath: child.path,
        hasHiddenChildren: child.hasHiddenChildren,
        childCount: child.children.length,
        _node: child,
      }));
    }
    return [{
      name: activeNode.name,
      size: activeNode.size,
      depth: activeNode.depth,
      isDir: activeNode.isDirectory,
      fileCount: activeNode.fileCount,
      fullPath: activeNode.path,
      hasHiddenChildren: activeNode.hasHiddenChildren,
      childCount: activeNode.children.length,
      _node: activeNode,
    }];
  }, [activeNode, activeExpanded]);

  useEffect(() => {
    if (!activeNode || !onVisibleDirectoriesChange) return;
    const candidates = (activeExpanded?.children || activeNode.children)
      .filter((child) => child.isDirectory && child.hasHiddenChildren)
      .sort((a, b) => b.size - a.size)
      .slice(0, 3)
      .map((child) => child.path);
    onVisibleDirectoriesChange(candidates);
  }, [activeNode, activeExpanded, onVisibleDirectoriesChange]);

  const chartData = useMemo(() => {
    if (!activeNode) return { datasets: [] };

    return {
      datasets: [{
        tree: chartItems,
        key: 'size',
        backgroundColor: (ctx: { dataIndex: number; raw?: { _data?: { depth?: number; isDir?: boolean } } }) => {
          const item = chartItems[ctx.dataIndex];
          if (!item) return DEPTH_COLORS[0].bg;
          const colorIdx = (item.depth || 0) % DEPTH_COLORS.length;
          return item.isDir ? DEPTH_COLORS[colorIdx].bg : 'rgba(128, 128, 128, 0.5)';
        },
        borderColor: (ctx: { dataIndex: number }) => {
          const item = chartItems[ctx.dataIndex];
          if (!item) return DEPTH_COLORS[0].border;
          const colorIdx = (item.depth || 0) % DEPTH_COLORS.length;
          return item.isDir ? DEPTH_COLORS[colorIdx].border : 'rgba(128, 128, 128, 0.8)';
        },
        borderWidth: 2,
        spacing: 2,
        labels: {
          display: true,
          align: 'center' as const,
          position: 'middle' as const,
          formatter: (ctx: { raw?: { _data?: { name?: string; size?: number } } }) => {
            const raw = ctx.raw?._data;
            if (!raw) return '';
            // Canvas text is out of the DOM rewriter's reach — alias here
            // (identity function while screenshot mode is off).
            return anonText(raw.name || '');
          },
          color: '#f0f0f5',
          font: {
            family: "'JetBrains Mono', monospace",
            size: 11,
            weight: 'bold' as const,
          },
        },
      }],
    };
  }, [activeNode, chartItems]);

  const options = useMemo(() => ({
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: 'rgba(18, 18, 26, 0.95)',
        titleFont: { size: 13, family: "'Roboto', sans-serif" },
        bodyFont: { size: 12, family: "'JetBrains Mono', monospace" },
        padding: 12,
        cornerRadius: 8,
        borderColor: 'rgba(0, 245, 255, 0.3)',
        borderWidth: 1,
        callbacks: {
          title: () => '',
          label: (ctx: { raw?: { _data?: { name?: string; size?: number; isDir?: boolean; fileCount?: number; hasHiddenChildren?: boolean; childCount?: number } } }) => {
            const raw = ctx.raw?._data;
            if (!raw) return '';
            const lines = [
              anonText(raw.name || 'Unknown'),
              `Size: ${formatSize(raw.size || 0)}`,
            ];
            if (raw.isDir) {
              const isDrillable = (raw.childCount ?? 0) > 0 || raw.hasHiddenChildren;
              lines.push(`Files: ${raw.fileCount?.toLocaleString() || 0}${raw.hasHiddenChildren ? '+' : ''}`);
              lines.push(isDrillable ? 'Click to drill down' : 'Drilldown limit reached');
            }
            return lines;
          },
        },
      },
    },
    onClick: (_event: unknown, elements: Array<{ index: number }>) => {
      if (elements.length > 0 && activeNode && !loadingPath) {
        const idx = elements[0].index;
        const clickedNode = chartItems[idx]?._node as DirEntry | undefined;
        if (clickedNode && clickedNode.isDirectory && (clickedNode.children.length > 0 || clickedNode.hasHiddenChildren)) {
          navigateTo(clickedNode);
        }
      }
    },
  }), [activeNode, chartItems, navigateTo, loadingPath]);

  if (!data.root) {
    return (
      <div className="glass-card p-5 flex items-center justify-center h-[400px]">
        <span className="text-[var(--text-muted)]">No directory data available</span>
      </div>
    );
  }

  return (
    <motion.div
      className="glass-card p-5"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-neon-subtle">Treemap View</h3>
        <div className="text-xs text-[var(--text-muted)]">
          Click directories to drill down
        </div>
      </div>

      {/* Breadcrumb navigation */}
      <div className="flex items-center gap-2 mb-4 text-sm overflow-x-auto">
        <button
          onClick={resetNavigation}
          className={`px-2 py-1 rounded transition-colors ${
            breadcrumbs.length === 0
              ? 'text-[var(--neon-cyan)]'
              : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
          }`}
        >
          {formatBreadcrumbName(data.root.name)}
        </button>
        {breadcrumbs.map((node, idx) => (
          <span key={node.path} className="flex items-center gap-2">
            <span className="text-[var(--text-muted)]">/</span>
            <button
              onClick={() => {
                const newBreadcrumbs = breadcrumbs.slice(0, idx + 1);
                setBreadcrumbs(newBreadcrumbs.slice(0, -1));
                setCurrentNode(node);
              }}
              className="px-2 py-1 rounded text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
            >
              {formatBreadcrumbName(node.name)}
            </button>
          </span>
        ))}
        {currentNode && (
          <span className="flex items-center gap-2">
            <span className="text-[var(--text-muted)]">/</span>
            <span className="px-2 py-1 text-[var(--neon-cyan)]">{formatBreadcrumbName(currentNode.name)}</span>
          </span>
        )}
        {(breadcrumbs.length > 0 || currentNode) && (
          <button
            onClick={navigateUp}
            className="ml-auto px-3 py-1 text-xs rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-[var(--text-secondary)] transition-colors"
          >
            ↑ Up
          </button>
        )}
      </div>

      {/* Current directory info */}
      {activeNode && (
        <div className="flex gap-4 mb-4 text-xs text-[var(--text-muted)]">
          <span>Total: <span className="text-[var(--neon-green)] font-mono">{formatSize(activeNode.size)}</span></span>
          <span>Files: <span className="text-[var(--neon-cyan)] font-mono">{activeNode.fileCount.toLocaleString()}</span></span>
          <span>Items: <span className="text-[var(--neon-amber)] font-mono">{activeNode.children.length}</span></span>
        </div>
      )}

      {/* Chart */}
      <div style={{ height: '350px' }} className="relative">
        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
        <Chart type="treemap" data={chartData as any} options={options as any} />
        {/* Loading overlay */}
        {(loadingPath || isExpanding) && (
          <div className="absolute inset-0 bg-[var(--bg-void)]/50 flex items-center justify-center">
            <div className="flex flex-col items-center">
              <motion.div
                className="w-8 h-8 border-2 border-[var(--neon-cyan)] border-t-transparent rounded-full"
                animate={{ rotate: 360 }}
                transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
              />
              <span className="mt-2 text-sm text-[var(--text-muted)]">Loading...</span>
            </div>
          </div>
        )}
      </div>
    </motion.div>
  );
}
