import { useEffect, useMemo, useState } from 'react';
import { useDiag } from '../../context/DiagContext';
import { projectCostScan } from '../../state/projectCostScan';
import { resolveLifecycleById } from '../../utils/pageLifecycle';
import { DataGrid } from '../common/DataGrid';
import { ProgressIndicator } from '../common/ProgressIndicator';
import { BigStat, BarRow, UsageBar } from './missionControl/microViz';
import { ProjectCostTreemap } from '../ProjectCostTreemap';
import { LENS_META, projectTone } from './projectCost/lens';
import type { CostLens } from './projectCost/lens';
import type { ColumnDef } from '../../utils/dataGridTypes';
import type { CruProjectRow } from '../../types';

const EMPTY: never[] = [];
const LENSES: CostLens[] = ['mem', 'cpu', 'llm'];
const LENS_COLUMN_ID: Record<CostLens, string> = { mem: 'memGBh', cpu: 'cpuH', llm: 'llmUSD' };

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

function MetricCell({ value, text, pct, tone }: { value: string; text: string; pct: number; tone: 'ok' | 'warn' | 'crit' | 'info' }) {
  return (
    <div className="flex items-center justify-end gap-2">
      <span className="w-20 text-right font-mono text-xs tabular-nums text-[var(--text-primary)]">{value}</span>
      <span className="w-16">
        <UsageBar pct={pct} tone={tone} />
      </span>
      <span className="sr-only">{text}</span>
    </div>
  );
}

function ProjectDetailPanel({ row }: { row: CruProjectRow }) {
  const users = row.byUser ?? EMPTY;
  const ctxTypes = row.byContextType ?? EMPTY;
  const userMax = Math.max(1, ...users.map((u) => u.memGBh));
  const ctxMax = Math.max(1, ...ctxTypes.map((c) => c.memGBh));
  return (
    <div className="grid grid-cols-1 gap-4 border-t border-[var(--border-glass)] bg-[var(--bg-glass)] px-4 py-3 md:grid-cols-2">
      <div className="min-w-0">
        <div className="mb-2 text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
          By user / owner
        </div>
        <div className="space-y-1">
          {users.length === 0 && <div className="text-xs text-[var(--text-muted)]">No local-process records.</div>}
          {users.map((u) => (
            <BarRow
              key={u.authIdentifier}
              label={u.authIdentifier}
              value={`${u.memGBh.toFixed(1)} GB·h`}
              pct={(u.memGBh / userMax) * 100}
              tone="info"
            />
          ))}
        </div>
      </div>
      <div className="min-w-0">
        <div className="mb-2 text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
          By context type
        </div>
        <div className="space-y-1">
          {ctxTypes.length === 0 && <div className="text-xs text-[var(--text-muted)]">No local-process records.</div>}
          {ctxTypes.map((c) => (
            <BarRow
              key={c.type}
              label={c.type}
              value={`${c.memGBh.toFixed(1)} GB·h`}
              pct={(c.memGBh / ctxMax) * 100}
              tone="info"
            />
          ))}
        </div>
      </div>
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

  const colMax = useMemo(
    () => ({
      mem: Math.max(1, ...projects.map((p) => p.memGBh)),
      cpu: Math.max(1, ...projects.map((p) => p.cpuH)),
      llm: Math.max(1e-9, ...projects.map((p) => p.llmUSD)),
      rec: Math.max(1, ...projects.map((p) => p.records)),
    }),
    [projects],
  );

  const toggleSelect = (key: string) => setSelectedKey((cur) => (cur === key ? null : key));

  const expandedRowKeys = useMemo(
    () => new Set(selectedKey ? [selectedKey] : []),
    [selectedKey],
  );

  const columns = useMemo<ColumnDef<CruProjectRow>[]>(
    () => [
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
        label: 'Memory GB·h',
        align: 'right',
        render: (row) => (
          <MetricCell value={row.memGBh.toFixed(1)} text={`${row.memGBh} GB·h`} pct={(row.memGBh / colMax.mem) * 100} tone={TONE_BAR[projectTone(row)]} />
        ),
        sortValue: (row) => row.memGBh,
      },
      {
        id: 'cpuH',
        label: 'CPU·h',
        align: 'right',
        render: (row) => (
          <MetricCell value={row.cpuH.toFixed(2)} text={`${row.cpuH} CPU·h`} pct={(row.cpuH / colMax.cpu) * 100} tone="ok" />
        ),
        sortValue: (row) => row.cpuH,
      },
      {
        id: 'llmUSD',
        label: 'LLM $',
        align: 'right',
        render: (row) => (
          <MetricCell value={`$${row.llmUSD.toFixed(4)}`} text={`$${row.llmUSD}`} pct={(row.llmUSD / colMax.llm) * 100} tone="warn" />
        ),
        sortValue: (row) => row.llmUSD,
      },
      {
        id: 'records',
        label: 'Records',
        align: 'right',
        mono: true,
        cellClassName: 'text-[var(--text-secondary)]',
        render: (row) => row.records.toLocaleString(),
        sortValue: (row) => row.records,
      },
    ],
    [colMax, selectedKey],
  );

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
            <BigStat value={totals ? totals.memGBh.toFixed(1) : '—'} label="Total GB·h" sub="mem" />
            <BigStat value={totals ? totals.cpuH.toFixed(1) : '—'} label="Total CPU·h" sub="cpu" />
            <BigStat value={totals ? `$${totals.llmUSD.toFixed(2)}` : '—'} label="LLM cost" />
            <BigStat value={totals ? totals.projectCount : '—'} label="Projects" />
            <BigStat value={span ? spanDays(span.firstTs, span.lastTs).toFixed(1) : '—'} label="Span (days)" />
            <BigStat value={span ? span.cruRecords.toLocaleString() : '—'} label="CRU records" />
          </div>
        </div>

        {/* Lens toggle */}
        <div className="flex items-center gap-2">
          <span className="text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">Lens</span>
          <div className="inline-flex overflow-hidden rounded-md border border-[var(--border-glass)]">
            {LENSES.map((l) => (
              <button
                key={l}
                type="button"
                onClick={() => setLens(l)}
                className={`px-3 py-1 font-mono text-xs transition-colors ${
                  lens === l
                    ? 'bg-[var(--accent)] text-white'
                    : 'text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]'
                }`}
              >
                {LENS_META[l].short} {LENS_META[l].unit}
              </button>
            ))}
          </div>
        </div>

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
      </div>
    </div>
  );
}
