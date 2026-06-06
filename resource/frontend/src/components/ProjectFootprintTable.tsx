import { useMemo, useState } from 'react';
import { type MultiValue } from 'react-select';
import { useDiag } from '../context/DiagContext';
import { useTableFilter } from '../hooks/useTableFilter';
import { useModal } from '../hooks/useModal';
import { DataGrid } from './common/DataGrid';
import { FilterField, type SelectOption } from './common/FilterSelect';
import { ProjectCodeEnvUsageModal } from './ProjectCodeEnvUsageModal';
import { ProjectSavedModelsModal } from './ProjectSavedModelsModal';
import { ProjectCodeStudiosModal } from './ProjectCodeStudiosModal';
import { ProjectFolderBreakdownModal } from './ProjectFolderBreakdownModal';
import { ScanIncompleteNotice } from './ScanIncompleteNotice';
import { dssUrls } from '../utils/codeEnvUsageLinks';
import { formatGb } from '../utils/formatters';
import { normalizeModelValue } from '../utils/modelKind';
import type { ColumnDef } from '../utils/dataGridTypes';
import type { ProjectFootprintHealth, ProjectFootprintRow } from '../types';

const OWNER_MAX_CHARS = 20;

const EMPTY_ARR: never[] = [];

function truncate(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

type ModelFilter =
  | 'all'
  | 'prediction'
  | 'binary'
  | 'multiclass'
  | 'regression'
  | 'timeseries'
  | 'clustering'
  | 'unknown';

function healthCellClass(value: ProjectFootprintHealth | undefined): string {
  if (!value) return 'text-[var(--text-secondary)]';
  if (value === 'green') {
    return 'text-[var(--neon-green)]';
  }
  if (value === 'yellow') {
    return 'text-[#facc15]';
  }
  if (value === 'orange') {
    return 'text-[var(--neon-amber)]';
  }
  if (value === 'red') {
    return 'text-[var(--neon-red)]';
  }
  return 'text-[var(--neon-red)] font-bold pulse-glow';
}

function codeEnvCountClass(count: number): string {
  if (count >= 5) return 'text-[var(--neon-red)] font-bold pulse-glow';
  if (count === 4) return 'text-[var(--neon-red)]';
  if (count === 3) return 'text-[var(--neon-amber)]';
  if (count === 2) return 'text-[#facc15]';
  return 'text-[var(--neon-green)]';
}

function codeStudioCountClass(count: number): string {
  if (count > 10) return 'text-[var(--neon-red)] font-bold pulse-glow';
  if (count > 7) return 'text-[var(--neon-red)]';
  if (count > 4) return 'text-[var(--neon-amber)]';
  if (count > 2) return 'text-[#facc15]';
  return 'text-[var(--neon-green)]';
}

const COUNT_BUTTON_CLASS =
  'cursor-pointer bg-transparent p-0 font-mono font-semibold underline decoration-current/40 underline-offset-4 hover:decoration-[var(--neon-cyan)] hover:text-[var(--neon-cyan)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--neon-cyan)]/60';

const MODEL_FILTERS: Array<{ key: ModelFilter; label: string }> = [
  { key: 'all', label: 'All' },
  { key: 'prediction', label: 'Prediction' },
  { key: 'binary', label: 'Binary' },
  { key: 'multiclass', label: 'Multiclass' },
  { key: 'regression', label: 'Regression' },
  { key: 'timeseries', label: 'Time series' },
  { key: 'clustering', label: 'Clustering' },
  { key: 'unknown', label: 'Unknown' },
];

function modelMatchesFilter(row: ProjectFootprintRow, filter: ModelFilter): boolean {
  if (filter === 'all') return true;
  const models = row.savedModels || [];
  if (models.length === 0) return false;
  return models.some((model) => {
    const type = normalizeModelValue(model.type);
    const predictionType = normalizeModelValue(model.predictionType);
    if (filter === 'prediction') return type === 'PREDICTION';
    if (filter === 'clustering') return type === 'CLUSTERING';
    if (filter === 'binary') return predictionType === 'BINARY_CLASSIFICATION';
    if (filter === 'multiclass') {
      return predictionType === 'MULTICLASS' || predictionType === 'MULTICLASS_CLASSIFICATION';
    }
    if (filter === 'regression') return predictionType === 'REGRESSION';
    if (filter === 'timeseries') {
      return predictionType === 'TIMESERIES_FORECAST' || predictionType === 'TIME_SERIES_FORECAST';
    }
    return type === 'UNKNOWN' || (!type && !predictionType);
  });
}

export function ProjectFootprintTable() {
  const { state } = useDiag();
  const { isVisible } = useTableFilter();
  const rows = state.parsedData.projectFootprint ?? EMPTY_ARR;
  const users = state.parsedData.users ?? EMPTY_ARR;
  const [modelFilters, setModelFilters] = useState<MultiValue<SelectOption>>([]);
  const [projectFilters, setProjectFilters] = useState<MultiValue<SelectOption>>([]);
  const [ownerFilters, setOwnerFilters] = useState<MultiValue<SelectOption>>([]);
  const [codeEnvFilters, setCodeEnvFilters] = useState<MultiValue<SelectOption>>([]);
  const [usageProject, setUsageProject] = useState<ProjectFootprintRow | null>(null);
  const usageModal = useModal();
  const [modelsProject, setModelsProject] = useState<ProjectFootprintRow | null>(null);
  const modelsModal = useModal();
  const [studiosProject, setStudiosProject] = useState<ProjectFootprintRow | null>(null);
  const studiosModal = useModal();
  const [breakdownProject, setBreakdownProject] = useState<ProjectFootprintRow | null>(null);
  const breakdownModal = useModal();
  // Destructure the stable open() callbacks so the column memo's deps are
  // plain identifiers (React-Compiler-verifiable) rather than member access.
  const { open: openUsage } = usageModal;
  const { open: openModels } = modelsModal;
  const { open: openStudios } = studiosModal;
  const { open: openBreakdown } = breakdownModal;
  const loading = state.parsedData.projectFootprintLoading;
  const isLoading = loading?.phase === 'running' || loading?.phase === 'queued';
  const avgProjectGb =
    state.parsedData.projectFootprintSummary?.instanceAvgProjectGB ??
    rows[0]?.instanceAvgProjectGB ??
    0;
  const footprintSummary = state.parsedData.projectFootprintSummary;

  const ownerEmailByLogin = useMemo(() => {
    const m = new Map<string, string>();
    for (const u of users) {
      if (u.email) m.set(u.login, u.email);
    }
    return m;
  }, [users]);

  const projectOptions = useMemo<SelectOption[]>(
    () =>
      [...rows]
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((r) => ({ value: r.projectKey, label: r.name || r.projectKey })),
    [rows],
  );
  const ownerOptions = useMemo<SelectOption[]>(() => {
    const set = new Set<string>();
    for (const r of rows) if (r.owner) set.add(r.owner);
    return [...set].sort().map((o) => ({ value: o, label: o }));
  }, [rows]);
  const codeEnvOptions = useMemo<SelectOption[]>(() => {
    const set = new Set<string>();
    for (const r of rows) for (const k of r.codeEnvKeys || []) set.add(k);
    return [...set].sort().map((k) => ({ value: k, label: k }));
  }, [rows]);
  const modelOptions = useMemo<SelectOption[]>(
    () => MODEL_FILTERS.filter((f) => f.key !== 'all').map((f) => ({ value: f.key, label: f.label })),
    [],
  );

  const filteredRows = useMemo(() => {
    const projectSet = new Set(projectFilters.map((o) => o.value));
    const ownerSet = new Set(ownerFilters.map((o) => o.value));
    const codeEnvSet = new Set(codeEnvFilters.map((o) => o.value));
    const modelSet = new Set(modelFilters.map((o) => o.value as ModelFilter));
    return rows.filter((row) => {
      if (projectSet.size > 0 && !projectSet.has(row.projectKey)) return false;
      if (ownerSet.size > 0 && !ownerSet.has(row.owner)) return false;
      if (codeEnvSet.size > 0) {
        const keys = row.codeEnvKeys || [];
        if (!keys.some((k) => codeEnvSet.has(k))) return false;
      }
      if (modelSet.size > 0) {
        let any = false;
        for (const m of modelSet) {
          if (modelMatchesFilter(row, m)) {
            any = true;
            break;
          }
        }
        if (!any) return false;
      }
      return true;
    });
  }, [rows, projectFilters, ownerFilters, codeEnvFilters, modelFilters]);

  const hasAnyFilter =
    projectFilters.length + ownerFilters.length + codeEnvFilters.length + modelFilters.length > 0;
  const clearAllFilters = () => {
    setProjectFilters([]);
    setOwnerFilters([]);
    setCodeEnvFilters([]);
    setModelFilters([]);
  };

  const hasCodeStudios = useMemo(() => rows.some((row) => (row.codeStudioCount ?? 0) > 0), [rows]);

  const columns = useMemo<ColumnDef<ProjectFootprintRow>[]>(
    () => [
      {
        id: 'projectKey',
        label: 'Project',
        defaultSortDir: 'asc',
        render: (row) => {
          const ownerEmail = ownerEmailByLogin.get(row.owner);
          return (
            <div className="font-medium">
              <a
                href={dssUrls.project(row.projectKey)}
                target="_blank"
                rel="noreferrer"
                title={ownerEmail ? `Email: ${ownerEmail}` : undefined}
                className="text-[var(--text-primary)] hover:text-[var(--neon-cyan)] hover:underline"
              >
                {row.name || row.projectKey}
              </a>
            </div>
          );
        },
        sortValue: (row) => row.projectKey,
      },
      {
        id: 'owner',
        label: 'Owner',
        cellClassName: 'text-sm whitespace-nowrap',
        render: (row) =>
          row.owner ? (
            <span title={row.owner.length > OWNER_MAX_CHARS ? row.owner : undefined}>
              {truncate(row.owner, OWNER_MAX_CHARS)}
            </span>
          ) : (
            <span className="text-[var(--text-muted)]">Unknown</span>
          ),
      },
      {
        id: 'codeEnvCount',
        label: 'Code Envs',
        align: 'right',
        mono: true,
        cellClassName: 'font-semibold',
        render: (row) =>
          (row.codeEnvCount || 0) > 0 ? (
            <button
              type="button"
              onClick={() => {
                setUsageProject(row);
                openUsage();
              }}
              className={`${COUNT_BUTTON_CLASS} ${codeEnvCountClass(row.codeEnvCount || 0)}`}
              aria-label={`Show ${row.codeEnvCount} code env${row.codeEnvCount === 1 ? '' : 's'} used by ${row.projectKey}`}
            >
              {row.codeEnvCount}
            </button>
          ) : (
            <span className={codeEnvCountClass(row.codeEnvCount || 0)}>{row.codeEnvCount}</span>
          ),
        sortValue: (row) => row.codeEnvCount || 0,
      },
      {
        id: 'codeStudioCount',
        label: 'Code Studios',
        align: 'right',
        mono: true,
        cellClassName: 'font-semibold',
        hidden: () => !hasCodeStudios,
        render: (row) =>
          (row.codeStudioCount ?? 0) > 0 && (row.codeStudios?.length ?? 0) > 0 ? (
            <button
              type="button"
              onClick={() => {
                setStudiosProject(row);
                openStudios();
              }}
              className={`${COUNT_BUTTON_CLASS} ${codeStudioCountClass(row.codeStudioCount || 0)}`}
              aria-label={`Show ${row.codeStudioCount} code studio${row.codeStudioCount === 1 ? '' : 's'} in ${row.projectKey}`}
            >
              {row.codeStudioCount}
            </button>
          ) : (
            <span className={codeStudioCountClass(row.codeStudioCount || 0)}>
              {row.codeStudioCount ?? 0}
            </span>
          ),
        sortValue: (row) => row.codeStudioCount ?? 0,
      },
      {
        id: 'savedModelCount',
        label: 'Models',
        align: 'right',
        mono: true,
        render: (row) => (
          <div className="font-mono font-semibold text-[var(--text-primary)]">
            {(row.savedModelCount ?? 0) > 0 && (row.savedModels?.length ?? 0) > 0 ? (
              <button
                type="button"
                onClick={() => {
                  setModelsProject(row);
                  openModels();
                }}
                className="cursor-pointer bg-transparent p-0 font-mono font-semibold text-[var(--text-primary)] underline decoration-current/40 underline-offset-4 hover:decoration-[var(--neon-cyan)] hover:text-[var(--neon-cyan)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--neon-cyan)]/60"
                aria-label={`Show ${row.savedModelCount} saved model${row.savedModelCount === 1 ? '' : 's'} in ${row.projectKey}`}
              >
                {row.savedModelCount}
              </button>
            ) : (
              (row.savedModelCount ?? 0)
            )}
          </div>
        ),
        sortValue: (row) => row.savedModelCount ?? 0,
      },
      {
        id: 'bundleCount',
        label: 'Bundles',
        align: 'right',
        mono: true,
        cellClassName: 'font-semibold text-[var(--text-primary)]',
        render: (row) => row.bundleCount ?? 0,
        sortValue: (row) => row.bundleCount ?? 0,
      },
      {
        id: 'totalBytes',
        label: 'Size',
        align: 'right',
        mono: true,
        cellClassName: 'font-semibold',
        render: (row) =>
          (row.footprintBreakdown?.buckets?.length ?? 0) > 0 ? (
            <button
              type="button"
              onClick={() => {
                setBreakdownProject(row);
                openBreakdown();
              }}
              className={`${COUNT_BUTTON_CLASS} ${healthCellClass(row.projectSizeHealth)}`}
              aria-label={`Show storage breakdown for ${row.projectKey}`}
            >
              {formatGb(row.totalBytes)}
            </button>
          ) : (
            <span className={healthCellClass(row.projectSizeHealth)}>{formatGb(row.totalBytes)}</span>
          ),
        sortValue: (row) => row.totalBytes || 0,
      },
    ],
    [
      ownerEmailByLogin,
      hasCodeStudios,
      openUsage,
      openStudios,
      openModels,
      openBreakdown,
    ],
  );

  if (!isVisible('project-footprint-table') || (rows.length === 0 && !isLoading)) {
    return null;
  }

  const headerExtra = (
    <>
      {(footprintSummary?.failedProjectCount ?? 0) > 0 && (
        <div className="px-4 py-2 border-b border-[var(--border-glass)]">
          <ScanIncompleteNotice
            failedProjectCount={footprintSummary?.failedProjectCount}
            scannedProjectCount={footprintSummary?.scannedProjectCount}
          />
        </div>
      )}
      <div className="px-4 py-2 text-sm text-[var(--text-secondary)] border-b border-[var(--border-glass)]">
        Average project size on instance:{' '}
        <span className="font-mono text-[var(--text-primary)]">{avgProjectGb.toFixed(2)} GB</span>
      </div>

      <div className="px-4 py-3 border-b border-[var(--border-glass)] grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        <FilterField label="Project" options={projectOptions} value={projectFilters} onChange={setProjectFilters} placeholder="All projects" />
        <FilterField label="Owner" options={ownerOptions} value={ownerFilters} onChange={setOwnerFilters} placeholder="All owners" />
        <FilterField label="Code env" options={codeEnvOptions} value={codeEnvFilters} onChange={setCodeEnvFilters} placeholder="All code envs" />
        <FilterField label="Model" options={modelOptions} value={modelFilters} onChange={setModelFilters} placeholder="All models" />
        {hasAnyFilter && (
          <div className="md:col-span-2 xl:col-span-4 flex justify-end">
            <button
              type="button"
              onClick={clearAllFilters}
              className="px-2.5 py-1 rounded-md text-xs font-medium text-[var(--text-secondary)] border border-[var(--border-glass)] hover:bg-[var(--bg-glass)] transition-colors"
            >
              Clear all filters
            </button>
          </div>
        )}
      </div>
    </>
  );

  return (
    <>
      <DataGrid
        id="project-footprint-table"
        title="Project Footprint & Code Env Usage"
        countBadge={{ total: rows.length, filtered: filteredRows.length }}
        lifecycle={loading}
        headerExtra={headerExtra}
        rows={filteredRows}
        columns={columns}
        rowKey={(row) => row.projectKey}
        defaultSortColumnId="totalBytes"
        filtersActive={hasAnyFilter}
        emptyMessage="Waiting for project analysis data..."
        noMatchMessage="No projects match the selected filters."
        scroll="card"
      />

      <ProjectCodeEnvUsageModal
        project={usageProject}
        isOpen={usageModal.isOpen}
        onClose={usageModal.close}
      />

      <ProjectSavedModelsModal
        project={modelsProject}
        isOpen={modelsModal.isOpen}
        onClose={modelsModal.close}
      />

      <ProjectCodeStudiosModal
        project={studiosProject}
        isOpen={studiosModal.isOpen}
        onClose={studiosModal.close}
      />

      <ProjectFolderBreakdownModal
        project={breakdownProject}
        isOpen={breakdownModal.isOpen}
        onClose={breakdownModal.close}
      />
    </>
  );
}
