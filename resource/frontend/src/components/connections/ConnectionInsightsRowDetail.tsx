import { DataGrid } from '../common/DataGrid';
import { dssUrls } from '../../utils/codeEnvUsageLinks';
import type { ColumnDef } from '../../utils/dataGridTypes';
import type {
  ConnectionDatasetUsage,
  ConnectionLlmUsage,
  ConnectionLocalFilesystemUsage,
  ConnectionUsageItem,
} from '../../types';

const DEEP_LINK_CLASS =
  'hover:text-[var(--neon-cyan)] hover:underline focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--neon-cyan)] rounded-sm';

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

const FS_DETAIL_COLUMNS: ColumnDef<ConnectionLocalFilesystemUsage>[] = [
  {
    id: 'project',
    label: 'Project',
    defaultSortDir: 'asc',
    render: (fu) => (
      <a
        href={dssUrls.project(fu.projectKey)}
        target="_blank"
        rel="noopener noreferrer"
        className={`text-[var(--neon-cyan)] ${DEEP_LINK_CLASS}`}
      >
        {fu.projectName || fu.projectKey}
      </a>
    ),
    sortValue: (fu) => (fu.projectName || fu.projectKey).toLowerCase(),
  },
  {
    id: 'object',
    label: 'Object',
    defaultSortDir: 'asc',
    render: (fu) => (
      <span title={fu.objectSubtype ? `${fu.objectType} (${fu.objectSubtype})` : fu.objectType}>
        {fu.objectName}
      </span>
    ),
    sortValue: (fu) => fu.objectName.toLowerCase(),
  },
  {
    id: 'type',
    label: 'Type',
    defaultSortDir: 'asc',
    render: (fu) => <span className="text-[var(--text-muted)]">{fu.objectType}</span>,
    sortValue: (fu) => fu.objectType,
  },
  {
    id: 'path',
    label: 'Path',
    defaultSortDir: 'asc',
    mono: true,
    render: (fu) => <span className="text-[var(--text-muted)]">{fu.path ?? '—'}</span>,
    sortValue: (fu) => fu.path ?? '',
  },
  {
    id: 'owner',
    label: 'Owner',
    defaultSortDir: 'asc',
    render: (fu) => <span title={fu.ownerEmail}>{fu.owner}</span>,
    sortValue: (fu) => fu.owner.toLowerCase(),
  },
];

interface ConnectionInsightsRowDetailProps {
  connectionName: string;
  /** From `parsedData.connectionDatasetUsages`. */
  datasetUsage: ConnectionUsageItem | undefined;
  /** From `parsedData.connectionLlmUsages`. */
  llmUsage: ConnectionUsageItem | undefined;
  fsUsages: readonly ConnectionLocalFilesystemUsage[];
}

/**
 * Inline expanded-row content for the Insights matrix: every non-empty usage
 * source (datasets, LLM recipes, local filesystem rows) for one connection.
 */
export function ConnectionInsightsRowDetail({
  connectionName,
  datasetUsage,
  llmUsage,
  fsUsages,
}: ConnectionInsightsRowDetailProps) {
  const datasetRows = (datasetUsage?.projects ?? []) as ConnectionDatasetUsage[];
  const llmRows = (llmUsage?.projects ?? []) as ConnectionLlmUsage[];

  if (datasetRows.length === 0 && llmRows.length === 0 && fsUsages.length === 0) {
    return (
      <div className="px-4 py-3 text-sm text-[var(--text-muted)]">
        No recorded usage for {connectionName}.
      </div>
    );
  }

  return (
    <div className="px-4 py-3 space-y-3">
      {datasetRows.length > 0 && (
        <section>
          <h5 className="mb-1 text-xs font-semibold text-[var(--text-secondary)]">
            Datasets ({datasetRows.length})
          </h5>
          <DataGrid
            rows={datasetRows}
            columns={DATASET_DETAIL_COLUMNS}
            rowKey={(dp, i) => `${dp.projectKey}-${dp.datasetName}-${i}`}
            defaultSortColumnId="project"
            defaultSortDir="asc"
            scroll={{ maxH: '40vh' }}
          />
        </section>
      )}
      {llmRows.length > 0 && (
        <section>
          <h5 className="mb-1 text-xs font-semibold text-[var(--text-secondary)]">
            LLM recipes ({llmRows.length})
          </h5>
          <DataGrid
            rows={llmRows}
            columns={LLM_DETAIL_COLUMNS}
            rowKey={(lp, i) => `${lp.projectKey}-${lp.recipeName}-${i}`}
            defaultSortColumnId="project"
            defaultSortDir="asc"
            scroll={{ maxH: '40vh' }}
          />
        </section>
      )}
      {fsUsages.length > 0 && (
        <section>
          <h5 className="mb-1 text-xs font-semibold text-[var(--text-secondary)]">
            Local filesystem usages ({fsUsages.length})
          </h5>
          <DataGrid
            rows={[...fsUsages]}
            columns={FS_DETAIL_COLUMNS}
            rowKey={(fu, i) => `${fu.projectKey}-${fu.objectId}-${i}`}
            defaultSortColumnId="project"
            defaultSortDir="asc"
            scroll={{ maxH: '40vh' }}
          />
        </section>
      )}
    </div>
  );
}
