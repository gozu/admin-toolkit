import { useEffect, useMemo, useState } from 'react';
import { useDiag } from '../../context/DiagContext';
import { projectCostScan } from '../../state/projectCostScan';
import { resolveLifecycleById } from '../../utils/pageLifecycle';
import { DataGrid } from '../common/DataGrid';
import { ProgressIndicator } from '../common/ProgressIndicator';
import { BigStat, BarRow, UsageBar } from './missionControl/microViz';
import { ProjectCostTreemap } from '../ProjectCostTreemap';
import {
  ClassCards,
  DailyStrips,
  K8sPanel,
  LlmPanel,
  SqlConnectionsPanel,
  TopProcessesPanel,
} from './projectCost/panels';
import { LENS_COLOR, formatSeconds, k8sGBh, projectTone } from './projectCost/lens';
import type { CostLens } from './projectCost/lens';
import type { ColumnDef } from '../../utils/dataGridTypes';
import type { CruDetailRow, CruProjectRow } from '../../types';

const EMPTY: never[] = [];
const LENS_COLUMN_ID: Record<CostLens, string> = {
  mem: 'memGBh',
  cpu: 'cpuH',
  sql: 'sqlExecS',
  k8s: 'k8sGBh',
  llm: 'llmUSD',
};

const TONE_TEXT: Record<ReturnType<typeof projectTone>, string> = {
  ok: 'text-[var(--neon-green)]',
  warn: 'text-[var(--neon-amber)]',
  crit: 'text-[var(--neon-red)]',
  neutral: 'text-[var(--text-secondary)]',
};
const TONE_BAR: Record<ReturnType<typeof projectTone>, 'ok' | 'warn' | 'crit' | 'info'> = {
  ok: 'ok',
  warn: 'warn',
  crit: 'crit',
  neutral: 'info',
};

function spanDays(firstTs?: string | null, lastTs?: string | null): number {
  if (!firstTs || !lastTs) return 0;
  const a = Date.parse(firstTs);
  const b = Date.parse(lastTs);
  if (!Number.isFinite(a) || !Number.isFinite(b) || b <= a) return 0;
  return (b - a) / 86_400_000;
}

function MetricCell({
  value,
  text,
  pct,
  tone,
}: {
  value: string;
  text: string;
  pct: number;
  tone: 'ok' | 'warn' | 'crit' | 'info';
}) {
  return (
    <div className="flex items-center justify-end gap-2">
      <span className="w-20 text-right font-mono text-xs tabular-nums text-[var(--text-primary)]">{value}</span>
      <span className="w-14">
        <UsageBar pct={pct} tone={tone} />
      </span>
      <span className="sr-only">{text}</span>
    </div>
  );
}

// One drilldown quadrant: rows are ranked + sized on their class-native metric.
function DetailList({
  title,
  rows,
  color,
  metric,
  format,
  empty,
}: {
  title: string;
  rows: { key: string; row: CruDetailRow }[];
  color: string;
  metric: (r: CruDetailRow) => number;
  format: (v: number) => string;
  empty: string;
}) {
  const shown = rows.filter((r) => metric(r.row) > 0);
  const max = Math.max(1e-9, ...shown.map((r) => metric(r.row)));
  return (
    <div className="min-w-0">
      <div className="mb-2 flex items-center gap-1.5">
        <span className="h-1.5 w-1.5 flex-shrink-0 rounded-full" style={{ background: color }} />
        <span className="text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
          {title}
        </span>
      </div>
      <div className="space-y-1">
        {shown.length === 0 && <div className="text-xs text-[var(--text-muted)]">{empty}</div>}
        {shown.map(({ key, row }) => (
          <BarRow
            key={key}
            label={key}
            value={format(metric(row))}
            pct={(metric(row) / max) * 100}
            tone="info"
          />
        ))}
      </div>
    </div>
  );
}

function ProjectDetailPanel({ row }: { row: CruProjectRow }) {
  const users = (row.byUser ?? EMPTY).map((u) => ({ key: u.authIdentifier, row: u }));
  const ctxTypes = (row.byContextType ?? EMPTY).map((c) => ({ key: c.type, row: c }));
  const conns = (row.byConnection ?? EMPTY).map((c) => ({ key: c.connection, row: c }));
  const models = (row.byModel ?? EMPTY).map((m) => ({ key: m.model, row: m }));
  return (
    <div className="grid grid-cols-1 gap-4 border-t border-[var(--border-glass)] bg-[var(--bg-glass)] px-4 py-3 md:grid-cols-2 xl:grid-cols-4">
      <DetailList
        title="By user / owner"
        rows={users}
        color={LENS_COLOR.mem}
        metric={(r) => r.memGBh}
        format={(v) => `${v.toFixed(1)} GB·h`}
        empty="No local-process records."
      />
      <DetailList
        title="By workload"
        rows={ctxTypes}
        color={LENS_COLOR.mem}
        metric={(r) => Math.max(r.memGBh, r.k8sGBh)}
        format={(v) => `${v.toFixed(1)} GB·h`}
        empty="No local-process records."
      />
      <DetailList
        title="By SQL connection"
        rows={conns}
        color={LENS_COLOR.sql}
        metric={(r) => r.sqlExecS}
        format={formatSeconds}
        empty="No SQL queries."
      />
      <DetailList
        title="By LLM model"
        rows={models}
        color={LENS_COLOR.llm}
        metric={(r) => r.llmUSD}
        format={(v) => `$${v.toFixed(4)}`}
        empty="No LLM usage."
      />
    </div>
  );
}

export function ProjectCostPage() {
  const { state } = useDiag();
  const { data, scanStarted, error } = projectCostScan.use();
  const [lens, setLens] = useState<CostLens>('mem');
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  useEffect(() => {
    if (!scanStarted) void projectCostScan.load();
  }, [scanStarted]);

  const lifecycle = resolveLifecycleById('project-cost', state.parsedData);
  const isLoading = lifecycle.phase === 'running' || lifecycle.phase === 'queued';

  const projects = useMemo<CruProjectRow[]>(
    () => (data?.projects ?? EMPTY).filter((p) => p.projectKey !== 'NONE'),
    [data?.projects],
  );
  const idle = data?.idleResources ?? EMPTY;
  const totals = data?.totals;
  const span = data?.span;
  const classTotals = data?.classTotals;
  const hasSql = (classTotals?.sql?.queries ?? 0) > 0;
  const hasK8s =
    (classTotals?.k8s?.jobs ?? 0) > 0 || (classTotals?.k8s?.censusPods ?? 0) > 0;
  const hasLlm = (classTotals?.llm?.records ?? 0) > 0;

  const colMax = useMemo(
    () => ({
      mem: Math.max(1, ...projects.map((p) => p.memGBh)),
      cpu: Math.max(1, ...projects.map((p) => p.cpuH)),
      sql: Math.max(1e-9, ...projects.map((p) => p.sqlExecS ?? 0)),
      k8s: Math.max(1e-9, ...projects.map((p) => k8sGBh(p))),
      llm: Math.max(1e-9, ...projects.map((p) => p.llmUSD)),
    }),
    [projects],
  );

  const toggleSelect = (key: string) => setSelectedKey((cur) => (cur === key ? null : key));

  const expandedRowKeys = useMemo(
    () => new Set(selectedKey ? [selectedKey] : []),
    [selectedKey],
  );

  const columns = useMemo<ColumnDef<CruProjectRow>[]>(() => {
    const cols: ColumnDef<CruProjectRow>[] = [
      {
        id: 'projectKey',
        label: 'Project',
        defaultSortDir: 'asc',
        render: (row) => {
          const tone = projectTone(row);
          const open = selectedKey === row.projectKey;
          return (
            <button
              type="button"
              onClick={() => toggleSelect(row.projectKey)}
              className="flex items-center gap-1.5 text-left hover:text-[var(--neon-cyan)]"
              aria-expanded={open}
            >
              <span className="font-mono text-[10px] text-[var(--text-tertiary)]">{open ? '▾' : '▸'}</span>
              <span className={`font-medium ${TONE_TEXT[tone]}`}>{row.projectKey}</span>
            </button>
          );
        },
        sortValue: (row) => row.projectKey,
      },
      {
        id: 'memGBh',
        label: 'Mem GB·h',
        align: 'right',
        render: (row) => (
          <MetricCell
            value={row.memGBh.toFixed(1)}
            text={`${row.memGBh} GB·h`}
            pct={(row.memGBh / colMax.mem) * 100}
            tone={TONE_BAR[projectTone(row)]}
          />
        ),
        sortValue: (row) => row.memGBh,
      },
      {
        id: 'cpuH',
        label: 'CPU·h',
        align: 'right',
        render: (row) => (
          <MetricCell
            value={row.cpuH.toFixed(2)}
            text={`${row.cpuH} CPU·h`}
            pct={(row.cpuH / colMax.cpu) * 100}
            tone="ok"
          />
        ),
        sortValue: (row) => row.cpuH,
      },
    ];
    if (hasSql) {
      cols.push({
        id: 'sqlExecS',
        label: 'SQL engine',
        align: 'right',
        render: (row) => (
          <MetricCell
            value={row.sqlExecS > 0 ? formatSeconds(row.sqlExecS) : '—'}
            text={`${row.sqlExecS} engine seconds over ${row.sqlQueries} queries`}
            pct={((row.sqlExecS ?? 0) / colMax.sql) * 100}
            tone="info"
          />
        ),
        sortValue: (row) => row.sqlExecS ?? 0,
      });
    }
    if (hasK8s) {
      cols.push({
        id: 'k8sGBh',
        label: 'K8s GB·h',
        align: 'right',
        render: (row) => (
          <MetricCell
            value={k8sGBh(row) > 0 ? k8sGBh(row).toFixed(1) : '—'}
            text={`${k8sGBh(row)} GB·h over ${row.k8sJobs} jobs`}
            pct={(k8sGBh(row) / colMax.k8s) * 100}
            tone="info"
          />
        ),
        sortValue: (row) => k8sGBh(row),
      });
    }
    if (hasLlm) {
      cols.push({
        id: 'llmUSD',
        label: 'LLM $',
        align: 'right',
        render: (row) => (
          <MetricCell
            value={row.llmUSD > 0 ? `$${row.llmUSD.toFixed(4)}` : '—'}
            text={`$${row.llmUSD}`}
            pct={(row.llmUSD / colMax.llm) * 100}
            tone="warn"
          />
        ),
        sortValue: (row) => row.llmUSD,
      });
    }
    cols.push({
      id: 'records',
      label: 'Records',
      align: 'right',
      mono: true,
      cellClassName: 'text-[var(--text-secondary)]',
      render: (row) => row.records.toLocaleString(),
      sortValue: (row) => row.records,
    });
    return cols;
  }, [colMax, selectedKey, hasSql, hasK8s, hasLlm]);

  return (
    <div className="page-fill">
      <div className="flex flex-col gap-6 flex-1 min-h-0">
        {/* Summary band */}
        <div className="chart-container">
          <div className="chart-header flex items-center justify-between gap-3">
            <h4>Compute Resource Usage</h4>
            {span && (
              <span className="badge badge-info font-mono">
                {span.filesRead}/{span.files} files
              </span>
            )}
          </div>
          {isLoading && (
            <div className="border-b border-[var(--border-glass)] px-4 py-3">
              <ProgressIndicator lifecycle={lifecycle} compact={!!data} />
            </div>
          )}
          {error && !data && (
            <div className="px-4 py-3 text-sm text-[var(--neon-red)]">{error}</div>
          )}
          <div className="grid grid-cols-2 gap-4 px-4 py-4 sm:grid-cols-3 lg:grid-cols-6">
            <BigStat value={totals ? totals.projectCount : '—'} label="Projects" />
            <BigStat value={totals ? totals.userCount : '—'} label="Users" />
            <BigStat value={span ? spanDays(span.firstTs, span.lastTs).toFixed(1) : '—'} label="Span (days)" />
            <BigStat value={span ? span.cruRecords.toLocaleString() : '—'} label="CRU records" />
            <BigStat
              value={span ? span.linesScanned.toLocaleString() : '—'}
              label="Audit lines"
            />
            <BigStat
              value={classTotals?.sql?.connections ?? (totals ? 0 : '—')}
              label="SQL connections"
            />
          </div>
          {(data?.daily?.length ?? 0) >= 2 && <DailyStrips daily={data!.daily!} />}
        </div>

        {/* Compute classes — the lens selector */}
        <ClassCards classTotals={classTotals} lens={lens} onLens={setLens} />

        {/* Hero treemap */}
        <ProjectCostTreemap
          rows={projects}
          lens={lens}
          selectedKey={selectedKey}
          onSelect={toggleSelect}
        />

        {/* Leaderboard */}
        <DataGrid
          key={lens}
          title="Project Leaderboard"
          countBadge={{ total: projects.length }}
          lifecycle={isLoading ? lifecycle : null}
          rows={projects}
          columns={columns}
          rowKey={(row) => row.projectKey}
          defaultSortColumnId={LENS_COLUMN_ID[lens]}
          defaultSortDir="desc"
          renderExpandedRow={(row) => <ProjectDetailPanel row={row} />}
          expandedRowKeys={expandedRowKeys}
          emptyMessage="Waiting for compute usage…"
          scroll="card"
        />

        {/* Per-class panels — rendered only when the class has data */}
        {hasSql && (
          <SqlConnectionsPanel
            connections={data?.connections ?? EMPTY}
            unattributed={classTotals?.sql?.unattributed}
          />
        )}
        {hasK8s && data?.k8s && <K8sPanel k8s={data.k8s} classTotals={classTotals} />}
        {hasLlm && <LlmPanel models={data?.llmModels ?? EMPTY} />}

        {/* Idle resources panel */}
        {idle.length > 0 && (
          <div className="chart-container">
            <div className="chart-header">
              <h4 title="High memory residency at near-zero CPU — candidates to stop (CRU.md §7).">
                Idle Resources — reaper candidates
              </h4>
            </div>
            <div className="space-y-1 px-4 py-3">
              {idle.map((r) => {
                const max = Math.max(1, ...idle.map((x) => x.memGBh));
                return (
                  <BarRow
                    key={r.id}
                    label={
                      <span>
                        <span className="text-[var(--text-secondary)]">{r.projectKey}</span>
                        <span className="ml-1.5 text-[10px] text-[var(--text-tertiary)]">{r.contextType}</span>
                      </span>
                    }
                    value={`${r.memGBh.toFixed(1)} GB·h`}
                    pct={(r.memGBh / max) * 100}
                    tone="crit"
                  />
                );
              })}
            </div>
          </div>
        )}

        <TopProcessesPanel processes={data?.topProcesses ?? EMPTY} />
      </div>
    </div>
  );
}
