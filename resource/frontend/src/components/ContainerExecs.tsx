import { useEffect, useMemo, useState } from 'react';
import { Modal } from './Modal';
import { useModal } from '../hooks/useModal';
import { fetchJson, getBackendUrl } from '../utils/api';
import { ProgressIndicator } from './common/ProgressIndicator';
import { SkeletonRows } from './common/SkeletonRows';
import { ScanIncompleteNotice } from './ScanIncompleteNotice';
import { containerExecsScan } from '../state/containerExecsStore';
import type { ReactNode } from 'react';
import type {
  ContainerExecConfig,
  ContainerExecProjectRow,
  ContainerExecReplaceResult,
  ContainerExecUsageRow,
} from '../types';

const INHERIT_TARGET = '__INHERIT__';
const INHERIT_LABEL = 'Inherit instance default';

function fmt(value: unknown): string {
  if (value == null || value === '') return '--';
  return String(value);
}

function formatTarget(value: string | null | undefined): string {
  return value === INHERIT_TARGET ? INHERIT_LABEL : String(value || '');
}

function rowConfig(row: ContainerExecUsageRow): string {
  return String(row.containerConf || row.effectiveContainerConf || '');
}

function objectKind(row: ContainerExecUsageRow): string {
  if (row.objectSubtype) return row.objectSubtype;
  if (row.recipeType) return `${row.recipeType} recipe`;
  if (row.surfaceLabel) return row.surfaceLabel;
  return row.objectType.replace(/_/g, ' ').toLowerCase();
}

function objectName(row: ContainerExecUsageRow): string {
  return fmt(row.objectName || row.objectId);
}

function encodeSegment(value: string | undefined): string {
  return encodeURIComponent(value || '');
}

function getDssBaseUrl(): string {
  const backendUrl = getBackendUrl('/');
  const parsed = new URL(backendUrl, window.location.origin);
  return `${parsed.protocol}//${parsed.host}`;
}

function projectUrl(baseUrl: string, projectKey: string | undefined): string {
  return `${baseUrl}/projects/${encodeSegment(projectKey)}/`;
}

function objectUrl(baseUrl: string, row: ContainerExecUsageRow): string {
  const project = projectUrl(baseUrl, row.projectKey);
  if (row.objectType === 'RECIPE') return `${baseUrl}/projects/${encodeSegment(row.projectKey)}/recipes/${encodeSegment(row.objectId)}/`;
  if (row.objectType === 'WEBAPP') return `${baseUrl}/projects/${encodeSegment(row.projectKey)}/webapps/${encodeSegment(row.objectId)}/`;
  if (row.objectType === 'ML_TASK' && row.analysisId && row.mlTaskId) {
    return `${baseUrl}/projects/${encodeSegment(row.projectKey)}/analysis/${encodeSegment(row.analysisId)}/mltasks/${encodeSegment(row.mlTaskId)}/`;
  }
  return project;
}

function configUrl(baseUrl: string): string {
  return `${baseUrl}/admin/general/containers/#adminSettingUIView`;
}

function groupUsageRows(rows: ContainerExecUsageRow[]): ContainerExecProjectRow[] {
  const groups = new Map<string, ContainerExecProjectRow>();
  for (const row of rows) {
    const projectKey = row.projectKey || '';
    if (!projectKey) continue;
    const group = groups.get(projectKey) || {
      projectKey,
      projectName: row.projectName || projectKey,
      projectOverrides: [],
      jobOverrides: [],
    };
    if (row.overrideLevel === 'project' || row.objectType === 'PROJECT') group.projectOverrides.push(row);
    else if (row.overrideLevel === 'job') group.jobOverrides.push(row);
    groups.set(projectKey, group);
  }
  return Array.from(groups.values())
    .filter((group) => group.projectOverrides.length > 0 || group.jobOverrides.length > 0)
    .sort((a, b) => a.projectKey.localeCompare(b.projectKey));
}

function uniqueSorted(values: string[]): string[] {
  return Array.from(new Set(values.filter(Boolean))).sort((a, b) => a.localeCompare(b));
}

function Stat({ label, value, tone = 'neutral' }: { label: string; value: number | string; tone?: 'neutral' | 'danger' }) {
  return (
    <div className="rounded-lg px-4 py-3">
      <div className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">{label}</div>
      <div className={`mt-1 font-mono text-xl font-semibold ${tone === 'danger' ? 'text-[var(--neon-red)]' : 'text-[var(--text-primary)]'}`}>
        {value}
      </div>
    </div>
  );
}

function ProjectCell({ baseUrl, project }: { baseUrl: string; project: ContainerExecProjectRow }) {
  return (
    <a
      href={projectUrl(baseUrl, project.projectKey)}
      target="_blank"
      rel="noreferrer"
      className="group inline-flex max-w-full flex-col"
    >
      <span className="truncate font-mono text-sm font-semibold text-[var(--text-primary)] group-hover:text-[var(--neon-cyan)] group-hover:underline">
        {project.projectKey}
      </span>
      <span className="mt-0.5 truncate text-[11px] text-[var(--text-muted)]">
        {project.projectName || project.projectKey}
      </span>
    </a>
  );
}

function ConfigLink({
  baseUrl,
  name,
  validConfigs,
}: {
  baseUrl: string;
  name?: string | null;
  validConfigs?: Set<string>;
}) {
  if (!name) return <span className="text-[var(--text-muted)]">--</span>;
  const isMissing = validConfigs != null && !validConfigs.has(name);
  return (
    <a
      href={configUrl(baseUrl)}
      target="_blank"
      rel="noreferrer"
      className={`font-mono hover:underline ${isMissing ? 'text-[var(--neon-red)]' : 'text-[var(--neon-cyan)]'}`}
      title={isMissing ? `${name} is not a current container execution config` : `Open ${name} in DSS container execution settings`}
    >
      {name}
      {isMissing ? <span className="ml-1 text-[10px] text-[var(--neon-red)]">missing</span> : null}
    </a>
  );
}

function ProjectConfigLines({
  baseUrl,
  rows,
  validConfigs,
}: {
  baseUrl: string;
  rows: ContainerExecUsageRow[];
  validConfigs: Set<string>;
}) {
  if (rows.length === 0) return <span className="text-xs text-[var(--text-muted)]">--</span>;
  return (
    <div className="divide-y divide-[var(--border-glass)]/60">
      {rows.map((row) => (
        <div key={row.id} className="flex min-h-9 items-center gap-2 py-1.5">
          <ConfigLink baseUrl={baseUrl} name={rowConfig(row)} validConfigs={validConfigs} />
          {rows.length > 1 && <span className="truncate text-xs text-[var(--text-muted)]">{objectKind(row)}</span>}
        </div>
      ))}
    </div>
  );
}

function ObjectOverrideLines({
  baseUrl,
  rows,
  expanded,
  onToggle,
}: {
  baseUrl: string;
  rows: ContainerExecUsageRow[];
  expanded: boolean;
  onToggle: () => void;
}) {
  const visibleRows = expanded ? rows : rows.slice(0, 3);
  const hasOverflow = rows.length > visibleRows.length;

  return (
    <div>
      <div className="divide-y divide-[var(--border-glass)]/60">
        {visibleRows.map((row) => (
          <div key={row.id} className="flex min-h-10 items-center gap-2 py-1.5">
            <a
              href={objectUrl(baseUrl, row)}
              target="_blank"
              rel="noreferrer"
              className="min-w-0 truncate text-[var(--text-primary)] hover:text-[var(--neon-cyan)] hover:underline"
            >
              {objectName(row)}
            </a>
            <span className="shrink-0 text-xs text-[var(--text-muted)]">{objectKind(row)}</span>
          </div>
        ))}
      </div>
      {hasOverflow || expanded ? (
        <button
          type="button"
          onClick={onToggle}
          className="mt-2 rounded border border-[var(--border-glass)] px-2 py-1 text-xs text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)]"
        >
          {expanded ? 'Show less' : `Show all ${rows.length}`}
        </button>
      ) : null}
    </div>
  );
}

function ObjectConfigLines({
  baseUrl,
  rows,
  expanded,
  validConfigs,
}: {
  baseUrl: string;
  rows: ContainerExecUsageRow[];
  expanded: boolean;
  validConfigs: Set<string>;
}) {
  const visibleRows = expanded ? rows : rows.slice(0, 3);
  const hasFooterSpace = rows.length > visibleRows.length || expanded;
  return (
    <div>
      <div className="divide-y divide-[var(--border-glass)]/60">
        {visibleRows.map((row) => (
          <div key={row.id} className="flex min-h-10 items-center py-1.5">
            <ConfigLink baseUrl={baseUrl} name={rowConfig(row)} validConfigs={validConfigs} />
          </div>
        ))}
      </div>
      {hasFooterSpace && <div className="mt-2 h-[30px]" aria-hidden="true" />}
    </div>
  );
}

function ContainerExecTable({
  title,
  count,
  emptyText,
  loading = false,
  children,
}: {
  title: string;
  count: number;
  emptyText: string;
  loading?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="overflow-hidden rounded-xl">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--border-glass)] px-4 py-3">
        <h3 className="text-base font-semibold text-[var(--text-primary)]">
          {title} <span className="text-sm font-normal text-[var(--text-muted)]">({count})</span>
        </h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full table-fixed text-sm">
          <thead className="bg-[var(--bg-elevated)] text-[var(--text-secondary)]">
            <tr>
              <th className="w-[28%] px-4 py-2 text-left">Project</th>
              <th className="w-[44%] px-4 py-2 text-left">Object</th>
              <th className="w-[28%] px-4 py-2 text-left">Config</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border-glass)]">
            {count > 0 ? children : loading ? (
              <SkeletonRows cols={3} />
            ) : (
              <tr>
                <td colSpan={3} className="px-4 py-6 text-center text-[var(--text-muted)]">
                  {emptyText}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ReplacementResult({ result }: { result: ContainerExecReplaceResult }) {
  return (
    <div className="rounded-lg border border-[var(--border-glass)] bg-[var(--bg-elevated)] p-3 text-sm">
      <div className="text-[var(--text-primary)]">
        {result.dryRun ? 'Planned' : 'Applied'} rows: <span className="font-mono">{result.matchedRows}</span>
        {result.failedRows > 0 && (
          <span className="text-[var(--neon-red)]">, failed: <span className="font-mono">{result.failedRows}</span></span>
        )}
      </div>
      <div className="text-xs text-[var(--text-muted)]">
        {result.sourceConfig} → {formatTarget(result.targetConfig)}
      </div>
      <div className="mt-2 max-h-72 space-y-1 overflow-auto">
        {result.results.slice(0, 100).map((row, idx) => (
          <div key={`${row.rowId || row.objectId || 'row'}-${idx}`} className="font-mono text-xs text-[var(--text-muted)]">
            <div>
              {row.status}: {row.projectKey || '*'} / {row.objectType} / {row.objectName || row.objectId} → {formatTarget(row.to)}
              {row.error ? ` - ${row.error}` : ''}
            </div>
            {row.diag && (
              <details className="ml-3 mt-0.5 text-[10px] text-[var(--text-secondary)]">
                <summary className="cursor-pointer">diag</summary>
                <pre className="mt-1 whitespace-pre-wrap break-all rounded bg-[var(--bg-glass)] p-2">
                  {JSON.stringify(row.diag, null, 2)}
                </pre>
              </details>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export function ContainerExecs() {
  const { data, loading, progressPct, scanPhase, scanMessage, error, scanStarted } = containerExecsScan.use();
  const [sourceConfig, setSourceConfig] = useState('');
  const [targetConfig, setTargetConfig] = useState('');
  const [dryRun, setDryRun] = useState(true);
  const [replaceResult, setReplaceResult] = useState<ContainerExecReplaceResult | null>(null);
  const [showDetails, setShowDetails] = useState(false);
  const [expandedProjects, setExpandedProjects] = useState<Set<string>>(() => new Set());
  const [confirmText, setConfirmText] = useState('');
  const [replaceLoading, setReplaceLoading] = useState(false);
  const [replaceError, setReplaceError] = useState<string | null>(null);
  const confirmModal = useModal();

  useEffect(() => {
    if (!scanStarted) {
      void containerExecsScan.load();
    }
  }, [scanStarted]);

  const configs = useMemo(() => data?.configs || [], [data?.configs]);
  const configNames = useMemo(
    () => uniqueSorted(configs.map((cfg) => cfg.name)),
    [configs],
  );
  const validConfigSet = useMemo(() => new Set(configNames), [configNames]);
  const projectRows = useMemo(
    () => data?.projectRows || groupUsageRows(data?.usageRows || []),
    [data?.projectRows, data?.usageRows],
  );
  const visibleRows = useMemo(
    () => projectRows.flatMap((project) => [...project.projectOverrides, ...project.jobOverrides]),
    [projectRows],
  );
  const sourceConfigNames = useMemo(
    () => uniqueSorted(visibleRows.filter((row) => row.replacementSupported).map(rowConfig)),
    [visibleRows],
  );
  const orphanedConfigObjectCount = useMemo(() => {
    if (validConfigSet.size === 0) return 0;
    return visibleRows.filter((row) => {
      const config = rowConfig(row);
      return Boolean(config) && row.replacementSupported && !validConfigSet.has(config);
    }).length;
  }, [validConfigSet, visibleRows]);
  const projectOnlyRows = useMemo(
    () => projectRows.filter((project) => project.projectOverrides.length > 0),
    [projectRows],
  );
  const objectOverrideRows = useMemo(
    () => projectRows.filter((project) => project.jobOverrides.length > 0),
    [projectRows],
  );
  const nonCarrierEntries = useMemo(
    () => Object.entries(data?.nonCarrierCounts || {}).filter(([, count]) => count > 0),
    [data?.nonCarrierCounts],
  );
  const dssBaseUrl = useMemo(() => getDssBaseUrl(), []);

  const toggleProjectExpanded = (projectKey: string) => {
    setExpandedProjects((prev) => {
      const next = new Set(prev);
      if (next.has(projectKey)) next.delete(projectKey);
      else next.add(projectKey);
      return next;
    });
  };

  useEffect(() => {
    if (!sourceConfig && sourceConfigNames.length > 0) {
      setSourceConfig(sourceConfigNames[0]);
      return;
    }
    if (sourceConfig && sourceConfigNames.length > 0 && !sourceConfigNames.includes(sourceConfig)) {
      setSourceConfig(sourceConfigNames[0]);
    }
  }, [sourceConfig, sourceConfigNames]);

  useEffect(() => {
    const nextTarget = configNames.find((name) => name !== sourceConfig) || '';
    const targetIsValid = targetConfig === INHERIT_TARGET || configNames.includes(targetConfig);
    if ((!targetConfig || targetConfig === sourceConfig || !targetIsValid) && nextTarget) {
      setTargetConfig(nextTarget);
    }
  }, [configNames, sourceConfig, targetConfig]);

  const runReplace = async (nextDryRun: boolean) => {
    if (!sourceConfig || !targetConfig || sourceConfig === targetConfig) return;
    if (nextDryRun) {
      const targetIsInherit = targetConfig === INHERIT_TARGET;
      const matched = visibleRows.filter((row) => {
        if (row.containerMode !== 'EXPLICIT_CONTAINER') return false;
        if (!row.replacementSupported) return false;
        if (row.containerConf !== sourceConfig) return false;
        if (targetIsInherit) {
          const surface = String(row.surface || '');
          if (surface.startsWith('code_studio_template_') || surface === 'bundle_remapping') return false;
        }
        return true;
      });
      setReplaceResult({
        dryRun: true,
        sourceConfig,
        targetConfig,
        scanCached: true,
        matchedRows: matched.length,
        updatedRows: 0,
        skippedRows: 0,
        failedRows: 0,
        results: matched.map((row) => ({
          rowId: row.id,
          projectKey: row.projectKey,
          objectType: row.objectType,
          objectId: row.objectId,
          objectName: row.objectName,
          surface: row.surface,
          rawPath: row.rawPath,
          from: sourceConfig,
          to: targetConfig,
          status: 'planned',
        })),
      });
      return;
    }
    setReplaceLoading(true);
    setReplaceError(null);
    try {
      const result = await fetchJson<ContainerExecReplaceResult>('/api/container-execs/replace', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sourceConfig, targetConfig, dryRun: nextDryRun }),
      });
      setReplaceResult(result);
      if (!nextDryRun) {
        confirmModal.close();
        setConfirmText('');
        setDryRun(true);
        await containerExecsScan.load(true);
      }
    } catch (err) {
      setReplaceError(err instanceof Error ? err.message : String(err));
    } finally {
      setReplaceLoading(false);
    }
  };

  const requestReplace = () => {
    setReplaceResult(null);
    setReplaceError(null);
    if (dryRun) {
      void runReplace(true);
      return;
    }
    setConfirmText('');
    confirmModal.open();
  };

  const canSubmitReplace = Boolean(
    sourceConfig
    && targetConfig
    && sourceConfig !== targetConfig
    && sourceConfigNames.includes(sourceConfig)
    && (targetConfig === INHERIT_TARGET || validConfigSet.has(targetConfig))
    && !replaceLoading,
  );
  const canLiveApply = canSubmitReplace && confirmText === 'CONFIRM';

  return (
    <div className="w-full py-4 space-y-4">
      <div className="px-4">
        <h2 className="text-xl font-semibold text-[var(--text-primary)]">Container Execs</h2>
        <p className="text-sm text-[var(--text-muted)]">
          Explicit project and job-level overrides that differ from the DSS instance default.
        </p>
        <ScanIncompleteNotice
          failedProjectCount={data?.failedProjectCount}
          scannedProjectCount={data?.scannedProjectCount}
          className="mt-2"
        />
      </div>

      {error && (
        <div className="rounded-lg border border-[var(--neon-red)]/30 bg-[var(--neon-red)]/10 px-4 py-3 text-sm text-[var(--neon-red)]">
          {error}
        </div>
      )}

      {loading && (
        <div className="rounded-lg px-4 py-3">
          <ProgressIndicator
            active
            pct={progressPct}
            message={scanMessage}
            phase={scanPhase}
          />
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <Stat label="Configs" value={data?.summary?.configCount ?? 0} />
        <Stat label="Project overrides" value={data?.summary?.projectOverrideRowCount ?? data?.summary?.projectOverrideCount ?? 0} />
        <Stat label="Job Overrides" value={data?.summary?.jobOverrideCount ?? 0} />
        <Stat label="Replaceable" value={data?.summary?.replacementSupportedCount ?? 0} />
        <Stat
          label="Orphaned configs"
          value={orphanedConfigObjectCount}
          tone={orphanedConfigObjectCount > 0 ? 'danger' : 'neutral'}
        />
      </div>

      <div className="rounded-xl p-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <label className="flex min-w-0 flex-1 flex-col gap-1 text-sm text-[var(--text-secondary)] sm:flex-row sm:items-center">
            <span className="shrink-0">Source</span>
            <select
              value={sourceConfig}
              onChange={(e) => setSourceConfig(e.target.value)}
              disabled={sourceConfigNames.length === 0}
              className="min-h-10 min-w-0 flex-1 rounded border border-[var(--border-glass)] bg-[var(--bg-elevated)] px-2 py-2 text-[var(--text-primary)]"
            >
              {sourceConfigNames.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
          </label>
          <label className="flex min-w-0 flex-1 flex-col gap-1 text-sm text-[var(--text-secondary)] sm:flex-row sm:items-center">
            <span className="shrink-0">Target</span>
            <select
              value={targetConfig}
              onChange={(e) => setTargetConfig(e.target.value)}
              disabled={configNames.length === 0}
              className="min-h-10 min-w-0 flex-1 rounded border border-[var(--border-glass)] bg-[var(--bg-elevated)] px-2 py-2 text-[var(--text-primary)]"
            >
              <option value={INHERIT_TARGET}>{INHERIT_LABEL}</option>
              {configNames.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
          </label>
          <label className="flex min-h-10 items-center gap-2 rounded border border-[var(--border-glass)] bg-[var(--bg-glass)] px-3 text-sm text-[var(--text-secondary)]">
            <input
              type="checkbox"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
              className="h-4 w-4 accent-[var(--neon-cyan)]"
            />
            Dry run
          </label>
          <button
            onClick={requestReplace}
            disabled={!canSubmitReplace}
            className="min-h-10 rounded bg-[var(--accent)] px-4 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60 lg:w-auto"
          >
            {replaceLoading ? 'Running…' : 'Apply'}
          </button>
        </div>
        {replaceError && <div className="mt-3 text-sm text-[var(--neon-red)]">{replaceError}</div>}
        {replaceResult && (
          <div className="mt-3">
            <ReplacementResult result={replaceResult} />
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl px-4 py-3">
        <span className="text-sm text-[var(--text-secondary)]">
          Projects <span className="font-mono text-[var(--text-primary)]">{projectRows.length}</span>
        </span>
        <span className="text-xs text-[var(--text-muted)]">
          Default: {data?.globalDefaultConfig
            ? <ConfigLink baseUrl={dssBaseUrl} name={data.globalDefaultConfig} validConfigs={validConfigSet} />
            : <span className="text-[var(--text-muted)]">(none set in DSS)</span>}
        </span>
      </div>

      <ContainerExecTable
        title="Project-Level Overrides"
        count={projectOnlyRows.length}
        emptyText="No projects override the instance default."
        loading={loading}
      >
        {projectOnlyRows.map((project) => (
          <tr key={project.projectKey} className="align-top hover:bg-[var(--bg-glass-hover)]">
            <td className="px-4 py-3">
              <ProjectCell baseUrl={dssBaseUrl} project={project} />
            </td>
            <td className="px-4 py-3 text-[var(--text-primary)]">All</td>
            <td className="px-4 py-3">
              <ProjectConfigLines baseUrl={dssBaseUrl} rows={project.projectOverrides} validConfigs={validConfigSet} />
            </td>
          </tr>
        ))}
      </ContainerExecTable>

      <ContainerExecTable
        title="Object Overrides"
        count={objectOverrideRows.length}
        emptyText="No object-level overrides differ from their project baseline."
        loading={loading}
      >
        {objectOverrideRows.map((project) => {
          const expanded = expandedProjects.has(project.projectKey);
          return (
            <tr key={project.projectKey} className="align-top hover:bg-[var(--bg-glass-hover)]">
              <td className="px-4 py-3">
                <ProjectCell baseUrl={dssBaseUrl} project={project} />
              </td>
              <td className="px-4 py-3">
                <ObjectOverrideLines
                  baseUrl={dssBaseUrl}
                  rows={project.jobOverrides}
                  expanded={expanded}
                  onToggle={() => toggleProjectExpanded(project.projectKey)}
                />
              </td>
              <td className="px-4 py-3">
                <ObjectConfigLines
                  baseUrl={dssBaseUrl}
                  rows={project.jobOverrides}
                  expanded={expanded}
                  validConfigs={validConfigSet}
                />
              </td>
            </tr>
          );
        })}
      </ContainerExecTable>

      <div className="rounded-xl px-4 py-3">
        <button
          onClick={() => setShowDetails(!showDetails)}
          className="rounded border border-[var(--border-glass)] px-3 py-1.5 text-sm text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)]"
        >
          {showDetails ? 'Hide details' : 'More details'}
        </button>
        {showDetails && (
          <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div>
              <h3 className="mb-2 text-sm font-semibold text-[var(--text-primary)]">Configs Scanned</h3>
              <div className="max-h-60 overflow-auto rounded-lg border border-[var(--border-glass)]">
                <table className="w-full text-sm">
                  <thead className="bg-[var(--bg-elevated)] text-[var(--text-secondary)]">
                    <tr>
                      <th className="px-3 py-2 text-left">Name</th>
                      <th className="px-3 py-2 text-left">Type</th>
                      <th className="px-3 py-2 text-left">Namespace</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--border-glass)]">
                    {configs.map((cfg: ContainerExecConfig) => (
                      <tr key={cfg.name}>
                        <td className="px-3 py-2"><ConfigLink baseUrl={dssBaseUrl} name={cfg.name} validConfigs={validConfigSet} /></td>
                        <td className="px-3 py-2 text-[var(--text-secondary)]">{fmt(cfg.type)}</td>
                        <td className="px-3 py-2 text-[var(--text-secondary)]">{fmt(cfg.kubernetesNamespace)}</td>
                      </tr>
                    ))}
                    {configs.length === 0 && (
                      <tr>
                        <td colSpan={3} className="px-3 py-4 text-center text-[var(--text-muted)]">No configs found.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
            <div>
              <h3 className="mb-2 text-sm font-semibold text-[var(--text-primary)]">Scanned Non-Carriers</h3>
              <div className="flex flex-wrap gap-2">
                {nonCarrierEntries.map(([key, count]) => (
                  <span key={key} className="rounded-full border border-[var(--border-glass)] bg-[var(--bg-glass)] px-2 py-1 text-xs text-[var(--text-secondary)]">
                    {key}: <span className="font-mono text-[var(--text-primary)]">{count}</span>
                  </span>
                ))}
                {nonCarrierEntries.length === 0 && <span className="text-sm text-[var(--text-muted)]">No non-carrier counts reported.</span>}
              </div>
              <div className="mt-3 text-xs text-[var(--text-muted)]">
                Elapsed: {data?.elapsedMs ? `${Math.round(data.elapsedMs)}ms` : '--'}
              </div>
            </div>
          </div>
        )}
      </div>

      <Modal isOpen={confirmModal.isOpen} onClose={confirmModal.close} title="Apply Container Exec Replacement">
        <div className="space-y-4">
          <div className="rounded-lg border border-[var(--neon-red)]/30 bg-[var(--neon-red)]/10 px-3 py-2 text-sm text-[var(--text-primary)]">
            This will replace explicit visible overrides from <span className="font-mono">{sourceConfig}</span> to{' '}
            <span className="font-mono">{targetConfig}</span>. Type <span className="font-mono">CONFIRM</span> to enable the live apply.
          </div>
          <input
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            placeholder="CONFIRM"
            className="w-full rounded border border-[var(--border-glass)] bg-[var(--bg-elevated)] px-2 py-2 text-[var(--text-primary)]"
          />
          <div className="flex justify-end gap-2">
            <button
              onClick={confirmModal.close}
              className="rounded border border-[var(--border-glass)] px-3 py-2 text-sm text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)]"
            >
              Cancel
            </button>
            <button
              onClick={() => void runReplace(false)}
              disabled={!canLiveApply}
              className="rounded bg-[var(--neon-red)] px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
            >
              {replaceLoading ? 'Applying…' : 'Apply Replacement'}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
