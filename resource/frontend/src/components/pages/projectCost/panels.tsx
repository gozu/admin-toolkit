import { useMemo, useState } from 'react';
import { DataGrid } from '../../common/DataGrid';
import { BarRow, ColumnStrip } from '../missionControl/microViz';
import type { SparkPoint } from '../missionControl/microViz';
import type { ColumnDef } from '../../../utils/dataGridTypes';
import {
  LENS_COLOR,
  LENS_META,
  formatLens,
  formatSeconds,
  type CostLens,
} from './lens';
import type {
  CruClassTotals,
  CruConnectionRow,
  CruDailyRow,
  CruK8sData,
  CruLlmModelRow,
  CruTopProcess,
} from '../../../types';

const LENSES: CostLens[] = ['mem', 'cpu', 'sql', 'k8s', 'llm'];

// ── Class cards: one card per compute class, doubling as the lens selector ──

function classValue(lens: CostLens, ct: CruClassTotals | undefined): number {
  switch (lens) {
    case 'mem':
      return ct?.local?.memGBh ?? 0;
    case 'cpu':
      return ct?.local?.cpuH ?? 0;
    case 'sql':
      return ct?.sql?.execS ?? 0;
    case 'k8s':
      return Math.max(ct?.k8s?.actualGBh ?? 0, ct?.k8s?.reservedGBh ?? 0);
    case 'llm':
      return ct?.llm?.usd ?? 0;
  }
}

function classSub(lens: CostLens, ct: CruClassTotals | undefined): string {
  switch (lens) {
    case 'mem':
      return `${(ct?.local?.records ?? 0).toLocaleString()} processes`;
    case 'cpu':
      return 'batch + interactive burn';
    case 'sql': {
      const s = ct?.sql;
      if (!s) return 'no queries seen';
      return `${s.queries.toLocaleString()} queries · ${s.rows.toLocaleString()} rows`;
    }
    case 'k8s': {
      const k = ct?.k8s;
      if (!k) return 'no pods seen';
      return `${k.jobs.toLocaleString()} jobs · ${k.censusPods.toLocaleString()} pods`;
    }
    case 'llm': {
      const l = ct?.llm;
      if (!l) return 'no calls seen';
      const tok = l.ptok + l.ctok;
      return `${l.queries.toLocaleString()} calls · ${tok.toLocaleString()} tok`;
    }
  }
}

export function ClassCards({
  classTotals,
  lens,
  onLens,
}: {
  classTotals: CruClassTotals | undefined;
  lens: CostLens;
  onLens: (l: CostLens) => void;
}) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
      {LENSES.map((l) => {
        const active = l === lens;
        const value = classValue(l, classTotals);
        const empty = value <= 0;
        return (
          <button
            key={l}
            type="button"
            onClick={() => onLens(l)}
            aria-pressed={active}
            className={`rounded-lg border px-3 py-2.5 text-left transition-colors ${
              active
                ? 'border-[var(--accent)] bg-[var(--bg-glass)]'
                : 'border-[var(--border-glass)] hover:bg-[var(--bg-hover)]'
            } ${empty ? 'opacity-55' : ''}`}
          >
            <div className="flex items-center gap-1.5">
              <span
                className="h-2 w-2 flex-shrink-0 rounded-full"
                style={{ background: LENS_COLOR[l] }}
              />
              <span className="truncate text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
                {LENS_META[l].label}
              </span>
            </div>
            <div className="mt-1.5 font-mono text-lg font-semibold leading-none text-[var(--text-primary)]">
              {formatLens(value, l)}
            </div>
            <div className="mt-1 truncate text-[10px] text-[var(--text-muted)]">
              {classSub(l, classTotals)}
            </div>
          </button>
        );
      })}
    </div>
  );
}

// ── Daily activity: small multiples, one strip per class, shared x buckets ──

const DAILY_FIELDS: { lens: CostLens; field: keyof Omit<CruDailyRow, 'date'> }[] = [
  { lens: 'mem', field: 'memGBh' },
  { lens: 'cpu', field: 'cpuH' },
  { lens: 'sql', field: 'sqlExecS' },
  { lens: 'k8s', field: 'k8sGBh' },
  { lens: 'llm', field: 'llmUSD' },
];

export function DailyStrips({ daily }: { daily: CruDailyRow[] }) {
  if (daily.length < 2) return null;
  const strips = DAILY_FIELDS.map(({ lens, field }) => {
    const points: SparkPoint[] = daily.map((d) => ({
      label: `${d.date} — ${formatLens(d[field], lens)}`,
      value: d[field],
    }));
    const total = points.reduce((s, p) => s + p.value, 0);
    return { lens, points, total };
  }).filter((s) => s.total > 0);
  if (strips.length === 0) return null;
  return (
    <div className="border-t border-[var(--border-glass)] px-4 py-3">
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
          Daily activity
        </span>
        <span className="font-mono text-[10px] text-[var(--text-muted)]">
          {daily[0].date} → {daily[daily.length - 1].date}
        </span>
      </div>
      <div className="space-y-1.5">
        {strips.map(({ lens, points, total }) => (
          <div key={lens} className="flex items-center gap-2">
            <span className="flex w-24 flex-shrink-0 items-center gap-1.5">
              <span
                className="h-1.5 w-1.5 flex-shrink-0 rounded-full"
                style={{ background: LENS_COLOR[lens] }}
              />
              <span className="truncate text-[10px] text-[var(--text-secondary)]">
                {LENS_META[lens].short} {LENS_META[lens].unit}
              </span>
            </span>
            <div className="min-w-0 flex-1">
              <ColumnStrip points={points} color={LENS_COLOR[lens]} height={16} />
            </div>
            <span className="w-16 flex-shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-primary)]">
              {formatLens(total, lens)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── SQL warehouses: engine time vs wall time per connection ──

export function SqlConnectionsPanel({
  connections,
  unattributed,
}: {
  connections: CruConnectionRow[];
  unattributed?: { queries: number; execS: number };
}) {
  if (connections.length === 0) return null;
  const maxTotal = Math.max(1e-9, ...connections.map((c) => c.totalS));
  return (
    <div className="chart-container">
      <div className="chart-header flex items-center justify-between gap-3">
        <h4 title="statementExecutionTime = DB-engine compute; the rest of the wall time is queue + fetch + network. A mostly-hollow bar is fetch/egress-bound: the fix is pushing down / fetching less, not tuning SQL.">
          SQL Warehouses — engine vs wall time
        </h4>
        <span className="text-[10px] text-[var(--text-muted)]">
          solid = in-engine · hollow = fetch/queue overhead
        </span>
      </div>
      <div className="space-y-2.5 px-4 py-3">
        {connections.map((c) => {
          const enginePct = c.totalS > 0 ? (c.execS / c.totalS) * 100 : 0;
          const widthPct = (c.totalS / maxTotal) * 100;
          const fetchBound = c.fetchOverheadPct >= 80;
          return (
            <div key={c.connection} className="min-w-0">
              <div className="flex items-baseline justify-between gap-2">
                <span className="min-w-0 truncate font-mono text-xs text-[var(--text-primary)]">
                  {c.connection}
                </span>
                <span className="flex-shrink-0 font-mono text-[10px] text-[var(--text-muted)]">
                  {c.queries.toLocaleString()} q · {c.rows.toLocaleString()} rows
                </span>
              </div>
              <div className="mt-1 flex items-center gap-2">
                {/* wall-time track with in-engine overlay — one axis, one bar */}
                <div
                  className="h-2 overflow-hidden rounded-sm"
                  style={{
                    width: `${Math.max(2, widthPct)}%`,
                    background: `color-mix(in srgb, ${LENS_COLOR.sql} 22%, transparent)`,
                  }}
                >
                  <div
                    className="h-full rounded-sm"
                    style={{ width: `${enginePct}%`, background: LENS_COLOR.sql }}
                  />
                </div>
                <span className="whitespace-nowrap font-mono text-[10px] tabular-nums text-[var(--text-secondary)]">
                  {formatSeconds(c.execS)} / {formatSeconds(c.totalS)}
                </span>
                <span
                  className={`whitespace-nowrap rounded px-1.5 py-0.5 font-mono text-[9px] ${
                    fetchBound
                      ? 'badge-warning'
                      : 'bg-[var(--bg-glass)] text-[var(--text-secondary)]'
                  }`}
                  title={`${c.fetchOverheadPct.toFixed(0)}% of wall time was outside the DB engine`}
                >
                  {fetchBound ? 'fetch-bound' : 'engine-bound'} {c.fetchOverheadPct.toFixed(0)}%
                </span>
              </div>
              {c.topProjects.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {c.topProjects.map((p) => (
                    <span
                      key={p.projectKey}
                      className="rounded bg-[var(--bg-glass)] px-1.5 py-0.5 font-mono text-[9px] text-[var(--text-secondary)]"
                    >
                      {p.projectKey} · {formatSeconds(p.execS)}
                    </span>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {unattributed && unattributed.queries > 0 && (
        <div className="border-t border-[var(--border-glass)] px-4 py-2 text-[10px] text-[var(--text-muted)]">
          {unattributed.queries.toLocaleString()} queries ({formatSeconds(unattributed.execS)}{' '}
          in-engine) opened their connection in a rotated-out log file and cannot be attributed
          to a project.
        </div>
      )}
    </div>
  );
}

// ── Kubernetes: per-instance actuals, exec types, clusters ──

export function K8sPanel({ k8s, classTotals }: { k8s: CruK8sData; classTotals?: CruClassTotals }) {
  const nodes = k8s.nodes ?? [];
  const execTypes = k8s.execTypes ?? [];
  const clusters = k8s.clusters ?? [];
  if (nodes.length === 0 && clusters.length === 0) return null;
  const nodeMax = Math.max(1e-9, ...nodes.map((n) => n.actualGBh));
  const etMax = Math.max(1e-9, ...execTypes.map((t) => t.actualGBh));
  const clMax = Math.max(1e-9, ...clusters.map((c) => c.reservedGBh));
  const kt = classTotals?.k8s;
  return (
    <div className="chart-container">
      <div className="chart-header flex items-center justify-between gap-3">
        <h4 title="Actual GB·h integrates each pod's resident memory over the measured gaps between cluster census snapshots. Reserved GB·h = memory request × job lifetime (what the scheduler blocks out for you).">
          Kubernetes — where the pods actually live
        </h4>
        {kt && (
          <span className="font-mono text-[10px] text-[var(--text-muted)]">
            {kt.censusSnapshots.toLocaleString()} census snapshots · {kt.sparkJobs} spark jobs
          </span>
        )}
      </div>
      <div className="grid grid-cols-1 gap-4 px-4 py-3 md:grid-cols-3">
        <div className="min-w-0">
          <div className="mb-2 text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
            By DSS instance (node) — actual GB·h
          </div>
          <div className="space-y-1">
            {nodes.length === 0 && (
              <div className="text-xs text-[var(--text-muted)]">No pod census in the logs.</div>
            )}
            {nodes.map((n) => (
              <BarRow
                key={n.nodeId}
                label={
                  <span>
                    {n.nodeId}
                    <span className="ml-1.5 text-[9px] text-[var(--text-tertiary)]">
                      {n.pods} pods
                    </span>
                  </span>
                }
                value={`${n.actualGBh.toFixed(1)} GB·h`}
                pct={(n.actualGBh / nodeMax) * 100}
                tone="info"
              />
            ))}
          </div>
        </div>
        <div className="min-w-0">
          <div className="mb-2 text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
            By workload type — actual GB·h
          </div>
          <div className="space-y-1">
            {execTypes.map((t) => (
              <BarRow
                key={t.type}
                label={t.type}
                value={`${t.actualGBh.toFixed(1)} GB·h`}
                pct={(t.actualGBh / etMax) * 100}
                tone="info"
              />
            ))}
          </div>
        </div>
        <div className="min-w-0">
          <div className="mb-2 text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
            By cluster — reserved GB·h (requests × lifetime)
          </div>
          <div className="space-y-1">
            {clusters.map((c) => (
              <BarRow
                key={c.clusterId}
                label={
                  <span>
                    {c.clusterId}
                    <span className="ml-1.5 text-[9px] text-[var(--text-tertiary)]">
                      {c.jobs} jobs{c.sparkJobs > 0 ? ` · ${c.sparkJobs} spark` : ''}
                    </span>
                  </span>
                }
                value={`${c.reservedGBh.toFixed(2)} GB·h`}
                pct={(c.reservedGBh / clMax) * 100}
                tone="info"
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── LLM: spend per model, cache effectiveness ──

export function LlmPanel({ models }: { models: CruLlmModelRow[] }) {
  if (models.length === 0) return null;
  const maxUsd = Math.max(1e-9, ...models.map((m) => m.usd));
  return (
    <div className="chart-container">
      <div className="chart-header flex items-center justify-between gap-3">
        <h4 title="estimatedCostUSD from the LLM_USAGE audit records — the only direct dollar figure DSS emits. Cache hits are queries the LLM cache answered for free.">
          LLM Spend — by model
        </h4>
        <span className="text-[10px] text-[var(--text-muted)]">
          real $ from DSS cost estimates
        </span>
      </div>
      <div className="space-y-2 px-4 py-3">
        {models.map((m) => {
          const calls = m.cacheHit + m.cacheMiss;
          const hitPct = calls > 0 ? (m.cacheHit / calls) * 100 : 0;
          return (
            <div key={m.llmId} className="min-w-0">
              <div className="flex items-baseline justify-between gap-2">
                <span className="min-w-0 truncate font-mono text-xs text-[var(--text-primary)]">
                  {m.model}
                  <span className="ml-1.5 text-[9px] text-[var(--text-tertiary)]">
                    {m.llmType.toLowerCase()} · {m.connection}
                  </span>
                </span>
                <span className="flex-shrink-0 font-mono text-xs font-semibold tabular-nums text-[var(--text-primary)]">
                  ${m.usd.toFixed(4)}
                </span>
              </div>
              <div className="mt-1 flex items-center gap-2">
                <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-[var(--bg-elevated)]">
                  <div
                    className="h-full rounded-full"
                    style={{ width: `${(m.usd / maxUsd) * 100}%`, background: LENS_COLOR.llm }}
                  />
                </div>
                <span className="flex-shrink-0 whitespace-nowrap font-mono text-[10px] text-[var(--text-muted)]">
                  {(m.ptok + m.ctok).toLocaleString()} tok · {m.queries.toLocaleString()} calls
                  {calls > 0 && ` · ${hitPct.toFixed(0)}% cached`}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Top local processes (collapsed by default; commandName reveals code-env/plugin) ──

const PROCESS_COLUMNS: ColumnDef<CruTopProcess>[] = [
  {
    id: 'projectKey',
    label: 'Project',
    mono: true,
    render: (p) => p.projectKey,
    sortValue: (p) => p.projectKey,
  },
  {
    id: 'contextType',
    label: 'Context',
    cellClassName: 'text-[var(--text-muted)]',
    render: (p) => p.contextType,
    sortValue: (p) => p.contextType,
  },
  {
    id: 'commandName',
    label: 'Command',
    mono: true,
    cellClassName: 'text-[var(--text-muted)] max-w-72 truncate',
    render: (p) => p.commandName || '—',
    sortValue: (p) => p.commandName,
  },
  {
    id: 'memGBh',
    label: 'GB·h',
    align: 'right',
    mono: true,
    render: (p) => p.memGBh.toFixed(1),
    sortValue: (p) => p.memGBh,
  },
  {
    id: 'cpuH',
    label: 'CPU·h',
    align: 'right',
    mono: true,
    render: (p) => p.cpuH.toFixed(2),
    sortValue: (p) => p.cpuH,
  },
];

export function TopProcessesPanel({ processes }: { processes: CruTopProcess[] }) {
  const [open, setOpen] = useState(false);
  const rows = useMemo(() => (open ? processes : []), [open, processes]);
  if (processes.length === 0) return null;
  if (!open) {
    return (
      <div className="chart-container">
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-expanded={false}
          className="chart-header flex w-full items-center justify-between gap-3 text-left"
        >
          <h4>Heaviest Local Processes</h4>
          <span className="font-mono text-[10px] text-[var(--text-muted)]">
            {processes.length} processes ▸
          </span>
        </button>
      </div>
    );
  }
  return (
    <DataGrid
      title="Heaviest Local Processes"
      countBadge={{ total: processes.length }}
      rows={rows}
      columns={PROCESS_COLUMNS}
      rowKey={(p) => p.id}
      defaultSortColumnId="memGBh"
      defaultSortDir="desc"
      emptyMessage="No heavy processes."
      scroll="card"
    />
  );
}
