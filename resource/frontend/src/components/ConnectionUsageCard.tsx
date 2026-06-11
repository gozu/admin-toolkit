import { useEffect, useMemo, useState } from 'react';
import { useDiag } from '../context/DiagContext';
import { useConnectionUsageScan } from '../hooks/useConnectionUsageScan';
import { ScanIncompleteNotice } from './ScanIncompleteNotice';
import { Modal } from './Modal';
import { useModal } from '../hooks/useModal';
import { DataGrid } from './common/DataGrid';
import { Spinner } from './common/Spinner';
import { dssUrls } from '../utils/codeEnvUsageLinks';
import type { ColumnDef } from '../utils/dataGridTypes';
import type { ConnectionDatasetUsage, ConnectionLlmUsage, ConnectionUsageItem } from '../types';

const DEEP_LINK_CLASS =
  'hover:text-[var(--neon-cyan)] hover:underline focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--neon-cyan)] rounded-sm';

const LLM_MESH_TYPES = new Set([
  'OpenAI',
  'AzureOpenAI',
  'Anthropic',
  'Bedrock',
  'CustomLLM',
  'SnowflakeCortex',
  'VertexAILLM',
  'HuggingFaceLocal',
  'RemoteMCP',
  'Pinecone',
  'AzureAISearch',
  'ElasticSearch',
  // Types not on every instance but part of LLM mesh
  'Cohere',
  'MistralAI',
  'StabilityAI',
  'SageMakerLLM',
  'Milvus',
  'NVIDIANIMLLM',
  'AzureAIFoundry',
  'AzureLLM',
]);

/**
 * Merged-table row: a usage item tagged with its display category and its
 * data origin. `origin` (not `category`) picks the detail-modal shape —
 * LLM-mesh-typed connections found via datasets carry dataset usages.
 */
type CategorizedUsageItem = ConnectionUsageItem & {
  category: 'LLM Mesh' | 'Regular';
  origin: 'dataset' | 'llm';
};

const CATEGORY_COLOR: Record<CategorizedUsageItem['category'], string> = {
  'LLM Mesh': 'var(--neon-cyan)',
  Regular: '#7fb3ea',
};

export function ConnectionUsageCard() {
  const { state } = useDiag();
  const { parsedData } = state;

  const { scanning, scanned, total, error, failedProjectCount, scannedProjectCount, scan, abort } =
    useConnectionUsageScan();

  const datasetUsages = useMemo(
    () => parsedData.connectionDatasetUsages || [],
    [parsedData.connectionDatasetUsages],
  );
  const llmUsages = useMemo(
    () => parsedData.connectionLlmUsages || [],
    [parsedData.connectionLlmUsages],
  );
  const hasResults = datasetUsages.length > 0 || llmUsages.length > 0;
  const isLoading = scanning && total !== null && (scanned === null || scanned < total);

  // Split dataset usages into LLM mesh vs regular based on connection type
  const { meshDataset, regularDataset } = useMemo(() => {
    const mesh: ConnectionUsageItem[] = [];
    const regular: ConnectionUsageItem[] = [];
    for (const item of datasetUsages) {
      if (LLM_MESH_TYPES.has(item.type)) {
        mesh.push(item);
      } else {
        regular.push(item);
      }
    }
    return { meshDataset: mesh, regularDataset: regular };
  }, [datasetUsages]);

  const totalDatasetConns = regularDataset.length;
  const totalLlmConns = llmUsages.length + meshDataset.length;

  const mergedItems = useMemo<CategorizedUsageItem[]>(
    () => [
      ...llmUsages.map((i) => ({ ...i, category: 'LLM Mesh' as const, origin: 'llm' as const })),
      ...meshDataset.map((i) => ({
        ...i,
        category: 'LLM Mesh' as const,
        origin: 'dataset' as const,
      })),
      ...regularDataset.map((i) => ({
        ...i,
        category: 'Regular' as const,
        origin: 'dataset' as const,
      })),
    ],
    [llmUsages, meshDataset, regularDataset],
  );

  return (
    <div className="space-y-4">
      {/* Header */}
      <section className="glass-card p-4">
        <h3 className="text-lg font-semibold text-[var(--text-primary)]">Connection Usage</h3>
        <p className="text-sm text-[var(--text-muted)]">
          Scans all projects to find which connections are in use via datasets and LLM recipes. The
          scan auto-runs at session start.
        </p>
        <div className="mt-3 flex items-center gap-3">
          {/* Rescan / abort are kept as re-triggers only — deemphasized. */}
          <button
            onClick={scan}
            disabled={scanning}
            className="px-3 py-1 rounded-md text-xs font-medium text-[var(--text-secondary)] border border-[var(--text-tertiary)]/30 hover:bg-[var(--bg-glass-hover)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {scanning ? 'Rescanning…' : 'Rescan'}
          </button>
          {scanning && (
            <button
              onClick={abort}
              className="px-3 py-1 rounded-md text-xs font-medium text-[var(--text-secondary)] border border-[var(--text-tertiary)]/30 hover:bg-[var(--bg-glass-hover)] transition-colors"
            >
              Abort
            </button>
          )}
        </div>
      </section>

      {/* Scan incomplete notice (self-hides when no failures) */}
      <ScanIncompleteNotice
        failedProjectCount={failedProjectCount}
        scannedProjectCount={scannedProjectCount}
      />

      {/* Progress */}
      {isLoading && (
        <section className="glass-card p-4">
          <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
            <Spinner />
            {total !== null && scanned !== null
              ? `Scanning projects… ${scanned} / ${total}`
              : 'Discovering projects…'}
          </div>
        </section>
      )}

      {/* Error */}
      {error && (
        <section className="glass-card p-4">
          <div className="text-sm text-[var(--neon-red)]">
            <span className="font-medium">Scan error:</span> {error}
          </div>
        </section>
      )}

      {/* Stats */}
      {hasResults && !isLoading && (
        <section className="glass-card p-4">
          <div className="grid grid-cols-4 gap-4">
            <div className="text-center">
              <div className="text-2xl font-mono tabular-nums text-[var(--text-primary)]">
                {totalLlmConns + totalDatasetConns}
              </div>
              <div className="text-xs text-[var(--text-muted)]">Connections Used</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-mono tabular-nums text-[var(--neon-cyan)]">
                {totalLlmConns}
              </div>
              <div className="text-xs text-[var(--text-muted)]">LLM Mesh</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-mono tabular-nums text-[#7fb3ea]">
                {totalDatasetConns}
              </div>
              <div className="text-xs text-[var(--text-muted)]">Regular</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-mono tabular-nums text-[var(--text-muted)]">
                {total ?? '?'}
              </div>
              <div className="text-xs text-[var(--text-muted)]">Projects Scanned</div>
            </div>
          </div>
        </section>
      )}

      {/* One merged table: LLM Mesh + Regular connections, categorized per row */}
      {hasResults && (
        <section className="glass-card p-4">
          <div className="flex items-center gap-2 mb-3">
            <h4 className="text-sm font-semibold text-[var(--text-primary)]">Connections in Use</h4>
            <span className="text-xs font-mono text-[var(--text-muted)]">
              ({mergedItems.length})
            </span>
          </div>
          {mergedItems.length === 0 ? (
            <div className="py-4 text-center text-sm text-[var(--text-muted)]">
              No connections in use.
            </div>
          ) : (
            <ConnectionUsageTable items={mergedItems} />
          )}
        </section>
      )}
    </div>
  );
}

function ConnectionUsageTable({ items }: { items: CategorizedUsageItem[] }) {
  const { state, setFocusedConnectionFilter, setActivePage } = useDiag();
  const [search, setSearch] = useState('');
  const [detailConn, setDetailConn] = useState<CategorizedUsageItem | null>(null);
  const detailModal = useModal();
  const { open: openDetail } = detailModal;

  // Seed the search box when navigated here with a focused connection.
  useEffect(() => {
    const target = state.focusedConnectionFilter?.name ?? null;
    if (!target) return;
    if (!items.some((c) => c.name === target)) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- syncing context signal to local UI state
    setSearch(target);
    setFocusedConnectionFilter(null);
  }, [state.focusedConnectionFilter, items, setFocusedConnectionFilter]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (c) => c.name.toLowerCase().includes(q) || c.type.toLowerCase().includes(q),
    );
  }, [items, search]);

  const columns = useMemo<ColumnDef<CategorizedUsageItem>[]>(
    () => [
      {
        id: 'name',
        label: 'Connection',
        mono: true,
        defaultSortDir: 'asc',
        cellClassName: 'whitespace-nowrap',
        render: (conn) => (
          <button
            type="button"
            onClick={() => {
              setFocusedConnectionFilter({ name: conn.name });
              setActivePage('connections-insights');
            }}
            className={`bg-transparent p-0 text-[var(--text-primary)] ${DEEP_LINK_CLASS}`}
            title={`Open ${conn.name} in Insights`}
          >
            {conn.name}
          </button>
        ),
        sortValue: (conn) => conn.name.toLowerCase(),
      },
      {
        id: 'type',
        label: 'Type',
        defaultSortDir: 'asc',
        cellClassName: 'whitespace-nowrap',
        render: (conn) => conn.type,
        sortValue: (conn) => conn.type.toLowerCase(),
      },
      {
        id: 'category',
        label: 'Category',
        defaultSortDir: 'asc',
        cellClassName: 'whitespace-nowrap',
        render: (conn) => (
          <span
            className="text-xs font-semibold"
            style={{ color: CATEGORY_COLOR[conn.category] }}
          >
            {conn.category}
          </span>
        ),
        sortValue: (conn) => conn.category,
      },
      {
        id: 'projectCount',
        label: 'Projects',
        align: 'right',
        mono: true,
        render: (conn) => (
          <button
            type="button"
            onClick={() => {
              setDetailConn(conn);
              openDetail();
            }}
            className={DEEP_LINK_CLASS}
            title={`Show projects using ${conn.name}`}
          >
            {conn.projectCount}
          </button>
        ),
        sortValue: (conn) => conn.projectCount,
      },
      {
        id: 'datasets',
        label: 'Datasets',
        align: 'right',
        mono: true,
        render: (conn) => (conn.origin === 'dataset' ? (conn.datasetCount ?? 0) : '—'),
        sortValue: (conn) => (conn.origin === 'dataset' ? (conn.datasetCount ?? 0) : -1),
      },
      {
        id: 'recipes',
        label: 'Recipes',
        align: 'right',
        mono: true,
        render: (conn) => (conn.origin === 'llm' ? (conn.recipeCount ?? 0) : '—'),
        sortValue: (conn) => (conn.origin === 'llm' ? (conn.recipeCount ?? 0) : -1),
      },
    ],
    [openDetail, setFocusedConnectionFilter, setActivePage],
  );

  return (
    <div>
      {/* Search */}
      <div className="mb-2">
        <input
          type="text"
          placeholder="Filter connections..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full px-3 py-1.5 text-sm rounded-md border border-[var(--border-default)] bg-[var(--bg-surface)] text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)]"
        />
      </div>

      <DataGrid
        rows={filtered}
        columns={columns}
        rowKey={(conn) => `${conn.origin}:${conn.name}`}
        defaultSortColumnId="projectCount"
        filtersActive={search.trim().length > 0}
        noMatchMessage="No connections match the current filter."
        scroll={{ maxH: '60vh' }}
      />

      <Modal
        isOpen={detailModal.isOpen}
        onClose={detailModal.close}
        title={detailConn ? `${detailConn.name} — projects` : 'Projects'}
        sizePreset="large"
      >
        {detailConn && <ConnectionUsageDetail conn={detailConn} mode={detailConn.origin} />}
      </Modal>
    </div>
  );
}

const DATASET_DETAIL_COLUMNS: ColumnDef<ConnectionDatasetUsage>[] = [
  {
    id: 'project',
    label: 'Project',
    defaultSortDir: 'asc',
    render: (dp) => (
      <a
        href={dssUrls.project(dp.projectKey)}
        target="_blank"
        rel="noopener noreferrer"
        className={`text-[var(--neon-cyan)] ${DEEP_LINK_CLASS}`}
      >
        {dp.projectName || dp.projectKey}
      </a>
    ),
    sortValue: (dp) => (dp.projectName || dp.projectKey).toLowerCase(),
  },
  {
    id: 'dataset',
    label: 'Dataset',
    defaultSortDir: 'asc',
    render: (dp) => (
      <a
        href={dssUrls.dataset(dp.projectKey, dp.datasetName)}
        target="_blank"
        rel="noopener noreferrer"
        className={`text-[var(--text-secondary)] ${DEEP_LINK_CLASS}`}
      >
        {dp.datasetName}
      </a>
    ),
    sortValue: (dp) => dp.datasetName.toLowerCase(),
  },
  {
    id: 'type',
    label: 'Type',
    defaultSortDir: 'asc',
    render: (dp) => <span className="text-[var(--text-muted)]">{dp.datasetType}</span>,
    sortValue: (dp) => dp.datasetType || '',
  },
];

const LLM_DETAIL_COLUMNS: ColumnDef<ConnectionLlmUsage>[] = [
  {
    id: 'project',
    label: 'Project',
    defaultSortDir: 'asc',
    render: (lp) => (
      <a
        href={dssUrls.project(lp.projectKey)}
        target="_blank"
        rel="noopener noreferrer"
        className={`text-[var(--neon-cyan)] ${DEEP_LINK_CLASS}`}
      >
        {lp.projectName || lp.projectKey}
      </a>
    ),
    sortValue: (lp) => (lp.projectName || lp.projectKey).toLowerCase(),
  },
  {
    id: 'recipe',
    label: 'Recipe',
    defaultSortDir: 'asc',
    render: (lp) => (
      <a
        href={dssUrls.recipe(lp.projectKey, lp.recipeName)}
        target="_blank"
        rel="noopener noreferrer"
        className={`text-[var(--text-secondary)] ${DEEP_LINK_CLASS}`}
      >
        {lp.recipeName}
      </a>
    ),
    sortValue: (lp) => lp.recipeName.toLowerCase(),
  },
  {
    id: 'llmId',
    label: 'LLM ID',
    defaultSortDir: 'asc',
    render: (lp) => <span className="text-[var(--text-muted)]">{lp.llmId}</span>,
    sortValue: (lp) => lp.llmId || '',
  },
];

function ConnectionUsageDetail({
  conn,
  mode,
}: {
  conn: ConnectionUsageItem;
  mode: 'dataset' | 'llm';
}) {
  if (mode === 'dataset') {
    return (
      <DataGrid
        rows={conn.projects as ConnectionDatasetUsage[]}
        columns={DATASET_DETAIL_COLUMNS}
        rowKey={(dp, i) => `${dp.projectKey}-${dp.datasetName}-${i}`}
        defaultSortColumnId="project"
        defaultSortDir="asc"
        scroll={{ maxH: '70vh' }}
      />
    );
  }
  return (
    <DataGrid
      rows={conn.projects as ConnectionLlmUsage[]}
      columns={LLM_DETAIL_COLUMNS}
      rowKey={(lp, i) => `${lp.projectKey}-${lp.recipeName}-${i}`}
      defaultSortColumnId="project"
      defaultSortDir="asc"
      scroll={{ maxH: '70vh' }}
    />
  );
}
