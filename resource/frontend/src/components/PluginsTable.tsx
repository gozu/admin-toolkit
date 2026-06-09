import { useMemo, useState } from 'react';
import { type MultiValue } from 'react-select';
import { useDiag } from '../context/DiagContext';
import { DataGrid } from './common/DataGrid';
import { FilterField, type SelectOption } from './common/FilterSelect';
import type { ColumnDef } from '../utils/dataGridTypes';
import type { PluginInfo } from '../types';

const CARD_CLASS = 'rounded-xl overflow-hidden';

const COUNT_BUTTON_CLASS =
  'cursor-pointer bg-transparent p-0 font-mono font-semibold underline decoration-current/40 underline-offset-4 hover:decoration-[var(--neon-cyan)] hover:text-[var(--neon-cyan)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--neon-cyan)]/60';

const TYPE_OPTIONS: SelectOption[] = [
  { value: 'dev', label: 'Dev' },
  { value: 'installed', label: 'Installed' },
];

interface PluginsTableProps {
  onOpenUsage?: (plugin: PluginInfo) => void;
}

export function PluginsTable({ onOpenUsage }: PluginsTableProps = {}) {
  const { state } = useDiag();
  const { parsedData } = state;
  const plugins = parsedData.plugins || [];
  const details = parsedData.pluginDetails;
  const usagesPending = parsedData.pluginUsagesPending ?? false;

  const [pluginFilters, setPluginFilters] = useState<MultiValue<SelectOption>>([]);
  const [typeFilters, setTypeFilters] = useState<MultiValue<SelectOption>>([]);
  const [projectsFilters, setProjectsFilters] = useState<MultiValue<SelectOption>>([]);

  const pluginOptions = useMemo<SelectOption[]>(() => {
    const set = new Set<string>();
    for (const p of details ?? []) set.add(p.label || p.id);
    return [...set].sort((a, b) => a.localeCompare(b)).map((v) => ({ value: v, label: v }));
  }, [details]);

  const projectsOptions = useMemo<SelectOption[]>(() => {
    let hasUnknown = false;
    const set = new Set<number>();
    for (const p of details ?? []) {
      if (p.projectsUsingCount == null) hasUnknown = true;
      else set.add(p.projectsUsingCount);
    }
    const opts = [...set]
      .sort((a, b) => a - b)
      .map((n) => ({ value: String(n), label: String(n) }));
    if (hasUnknown) opts.push({ value: '?', label: 'Unknown' });
    return opts;
  }, [details]);

  const filteredRows = useMemo(() => {
    const rows = details ?? [];
    const pluginSet = new Set(pluginFilters.map((o) => o.value));
    const typeSet = new Set(typeFilters.map((o) => o.value));
    const projectsSet = new Set(projectsFilters.map((o) => o.value));
    return rows.filter((row) => {
      if (pluginSet.size > 0 && !pluginSet.has(row.label || row.id)) return false;
      if (typeSet.size > 0 && !typeSet.has(row.isDev ? 'dev' : 'installed')) return false;
      if (projectsSet.size > 0) {
        const key = row.projectsUsingCount == null ? '?' : String(row.projectsUsingCount);
        if (!projectsSet.has(key)) return false;
      }
      return true;
    });
  }, [details, pluginFilters, typeFilters, projectsFilters]);

  const hasAnyFilter = pluginFilters.length + typeFilters.length + projectsFilters.length > 0;
  const clearAllFilters = () => {
    setPluginFilters([]);
    setTypeFilters([]);
    setProjectsFilters([]);
  };

  const detailColumns = useMemo<ColumnDef<PluginInfo>[]>(
    () => [
      {
        id: 'name',
        label: 'Plugin Name',
        defaultSortDir: 'asc',
        render: (plugin) => <span>{plugin.label || plugin.id}</span>,
        sortValue: (plugin) => plugin.label || plugin.id,
      },
      {
        id: 'version',
        label: 'Installed',
        mono: true,
        render: (plugin) => plugin.installedVersion || '--',
        sortValue: (plugin) => plugin.installedVersion || '',
      },
      {
        id: 'latest',
        label: 'Latest',
        mono: true,
        render: (plugin) => plugin.latestVersion || '--',
        sortValue: (plugin) => plugin.latestVersion || '',
      },
      {
        id: 'type',
        label: 'Type',
        render: (plugin) =>
          plugin.isDev ? (
            <span className="rounded bg-[var(--neon-purple)]/20 px-1.5 py-0.5 text-xs font-medium text-[var(--neon-purple)]">
              DEV
            </span>
          ) : (
            <span className="text-[var(--text-muted)]">Installed</span>
          ),
        sortValue: (plugin) => (plugin.isDev ? 1 : 0),
      },
      {
        id: 'projectsUsing',
        label: 'Projects',
        align: 'right',
        mono: true,
        sortValue: (row) => row.projectsUsingCount ?? -1,
        render: (row) => {
          if (row.projectsUsingCount == null) {
            if (usagesPending && !row.usagesError) {
              return (
                <span className="text-[var(--text-muted)]" title="Scanning plugin usages…">
                  …
                </span>
              );
            }
            return (
              <span
                className="text-[var(--text-muted)]"
                title={
                  row.usagesError
                    ? `Usage scan failed: ${row.usagesError}`
                    : 'Usage scan unavailable'
                }
              >
                ?
              </span>
            );
          }
          const count = row.projectsUsingCount;
          if (count === 0) {
            return <span className="text-[var(--text-muted)]">0</span>;
          }
          if (!onOpenUsage) {
            return <span>{count}</span>;
          }
          return (
            <button
              type="button"
              onClick={() => onOpenUsage(row)}
              className={COUNT_BUTTON_CLASS}
              aria-label={`Show ${count} project${count === 1 ? '' : 's'} using ${row.label || row.id}`}
            >
              {count}
            </button>
          );
        },
      },
    ],
    [onOpenUsage, usagesPending],
  );

  const nameOnlyColumns = useMemo<ColumnDef<string>[]>(
    () => [{ id: 'name', label: 'Plugin Name', render: (name) => name }],
    [],
  );

  if (plugins.length === 0) {
    return null;
  }

  // Enriched view with version + Type + Projects, sortable/filterable.
  if (details && details.length > 0) {
    const headerExtra = (
      <div className="px-4 py-3 border-b border-[var(--border-glass)] grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        <FilterField
          label="Plugin"
          options={pluginOptions}
          value={pluginFilters}
          onChange={setPluginFilters}
          placeholder="All plugins"
        />
        <FilterField
          label="Type"
          options={TYPE_OPTIONS}
          value={typeFilters}
          onChange={setTypeFilters}
          placeholder="All types"
        />
        <FilterField
          label="# Projects"
          options={projectsOptions}
          value={projectsFilters}
          onChange={setProjectsFilters}
          placeholder="Any count"
        />
        {hasAnyFilter && (
          <div className="md:col-span-2 xl:col-span-3 flex justify-end">
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
    );

    return (
      <DataGrid
        id="plugins-table"
        title="Installed Plugins"
        countBadge={{ total: details.length, filtered: filteredRows.length }}
        lifecycle={parsedData.pluginsLoading}
        headerExtra={headerExtra}
        rows={filteredRows}
        columns={detailColumns}
        rowKey={(plugin) => plugin.id}
        defaultSortColumnId="projectsUsing"
        filtersActive={hasAnyFilter}
        noMatchMessage="No plugins match the selected filters."
        scroll="card"
      />
    );
  }

  // Fallback: name only
  return (
    <div className={CARD_CLASS} id="plugins-table">
      <div className="px-4 py-3 border-b border-[var(--border-glass)]">
        <h4 className="text-lg font-semibold text-[var(--text-primary)]">
          {plugins.length} Installed Plugins
        </h4>
      </div>
      <DataGrid
        rows={plugins}
        columns={nameOnlyColumns}
        rowKey={(_, i) => String(i)}
        scroll={{ maxH: '400px' }}
      />
    </div>
  );
}
