import { motion } from 'framer-motion';
import { useDiag } from '../context/DiagContext';
import { useTableFilter } from '../hooks/useTableFilter';
import { ExternalLinkIcon } from './ExternalLinkIcon';
import { DataGrid } from './common/DataGrid';
import type { ColumnDef } from '../utils/dataGridTypes';
import type { DisabledFeature } from '../types';

type Row = { name: string } & DisabledFeature;

const HEADER_CLASS = 'text-[var(--neon-amber)] opacity-70';

const columns: ColumnDef<Row>[] = [
  {
    id: 'feature',
    label: 'Feature',
    defaultSortDir: 'asc',
    headerClassName: HEADER_CLASS,
    render: (row) => (
      <a
        href={row.url}
        target="_blank"
        rel="noopener noreferrer"
        className="font-medium text-[var(--neon-cyan)] hover:underline"
      >
        {row.name}
        <ExternalLinkIcon />
      </a>
    ),
    sortValue: (row) => row.name,
  },
  {
    id: 'status',
    label: 'Status',
    defaultSortDir: 'asc',
    headerClassName: HEADER_CLASS,
    render: (row) => <span className="badge badge-warning font-mono">{row.status}</span>,
    sortValue: (row) => row.status,
  },
  {
    id: 'description',
    label: 'Description',
    defaultSortDir: 'asc',
    headerClassName: HEADER_CLASS,
    render: (row) => row.description,
    sortValue: (row) => row.description,
  },
];

export function DisabledFeaturesTable() {
  const { state } = useDiag();
  const { isVisible } = useTableFilter();
  const { parsedData } = state;
  const disabledFeatures = parsedData.disabledFeatures || {};
  const rows: Row[] = Object.entries(disabledFeatures).map(([name, feature]) => ({
    name,
    ...feature,
  }));

  if (!isVisible('disabledFeatures-table') || rows.length === 0) {
    return null;
  }

  return (
    <motion.div
      className="rounded-xl overflow-hidden col-span-full"
      id="disabledFeatures-table"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="px-4 py-3 border-b border-[var(--status-warning-border)]">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-[var(--status-warning-bg)] flex items-center justify-center">
            <svg
              className="w-5 h-5 text-[var(--neon-amber)]"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
              />
            </svg>
          </div>
          <div>
            <h4 className="text-lg font-semibold text-[var(--neon-amber)]">Disabled Features</h4>
            <p className="text-sm text-[var(--neon-amber)] opacity-70">
              The following features are disabled in this instance
            </p>
          </div>
          <span className="ml-auto badge badge-warning font-mono">
            {rows.length} {rows.length === 1 ? 'feature' : 'features'}
          </span>
        </div>
      </div>

      <DataGrid
        rows={rows}
        columns={columns}
        rowKey={(row) => row.name}
        scroll={{ maxH: '400px' }}
      />
    </motion.div>
  );
}
