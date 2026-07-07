import { useMemo, useState } from 'react';
import { DataGrid } from '../../common/DataGrid';
import { BarRow, ColumnStrip, SegmentBar, UsageBar } from '../missionControl/microViz';
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
  CruProjectRow,
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

// ── Daily activity: small multiples with a shared date axis + crosshair hover ──

const DAILY_FIELDS: { lens: CostLens; field: keyof Omit<CruDailyRow, 'date'> }[] = [
  { lens: 'mem', field: 'memGBh' },
  { lens: 'cpu', field: 'cpuH' },
  { lens: 'sql', field: 'sqlExecS' },
  { lens: 'k8s', field: 'k8sGBh' },
  { lens: 'llm', field: 'llmUSD' },
];

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function fmtDay(date: string): string {
  const m = Number(date.slice(5, 7));
  const d = Number(date.slice(8, 10));
  if (!m || !d) return date;
  return `${MONTHS[m - 1]} ${d}`;
}

// Left label / right total gutters of each strip row — the hover overlay and the
// date axis must align with the plot column, so these are shared constants.
const STRIP_GUTTER_L = 'calc(6rem + 0.5rem)'; // w-24 + gap-2
const STRIP_GUTTER_R = 'calc(4rem + 0.5rem)'; // w-16 + gap-2

export function DailyStrips({ daily }: { daily: CruDailyRow[] }) {
  const [hover, setHover] = useState<number | null>(null);
  if (daily.length < 2) return null;
  const strips = DAILY_FIELDS.map(({ lens, field }) => {
    const points: SparkPoint[] = daily.map((d) => ({
      label: `${fmtDay(d.date)} — ${formatLens(d[field], lens)}`,
      value: d[field],
    }));
    const total = points.reduce((s, p) => s + p.value, 0);
    return { lens, field, points, total };
  }).filter((s) => s.total > 0);
  if (strips.length === 0) return null;
  const n = daily.length;
  const step = Math.max(1, Math.ceil(n / 7));
  const ticks: number[] = [];
  for (let i = 0; i < n; i += step) ticks.push(i);
  const hovered = hover !== null ? daily[hover] : null;
  return (
    <div className="border-t border-[var(--border-glass)] px-4 py-3">
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
          Daily activity
        </span>
        <span className="font-mono text-[10px] text-[var(--text-muted)]">
          {fmtDay(daily[0].date)} → {fmtDay(daily[n - 1].date)} · {n} days
        </span>
      </div>
      <div className="relative">
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
        {/* shared date axis under the strips, aligned with the plot column */}
        <div className="mt-1 flex items-center gap-2">
          <span className="w-24 flex-shrink-0" />
          <div className="relative h-[11px] min-w-0 flex-1">
            {ticks.map((i) => (
              <span
                key={i}
                className="absolute top-0 -translate-x-1/2 whitespace-nowrap font-mono text-[8px] leading-none text-[var(--text-tertiary)]"
                style={{ left: `${((i + 0.5) / n) * 100}%` }}
              >
                {fmtDay(daily[i].date)}
              </span>
            ))}
          </div>
          <span className="w-16 flex-shrink-0" />
        </div>
        {/* crosshair hover layer over the plot column: one band across all strips */}
        <div
          className="absolute inset-y-0"
          style={{ left: STRIP_GUTTER_L, right: STRIP_GUTTER_R }}
          onMouseLeave={() => setHover(null)}
        >
          {hover !== null && (
            <div
              className="pointer-events-none absolute inset-y-0 rounded-sm"
              style={{
                left: `${(hover / n) * 100}%`,
                width: `${100 / n}%`,
                background: 'color-mix(in srgb, var(--accent) 12%, transparent)',
              }}
            />
          )}
          <div className="absolute inset-0 flex">
            {daily.map((d, i) => (
              <span key={d.date} className="h-full min-w-0 flex-1" onMouseEnter={() => setHover(i)} />
            ))}
          </div>
          {hovered && hover !== null && (
            <div
              className="pointer-events-none absolute top-0 z-10 min-w-36 rounded-md border border-[var(--border-glass)] bg-[var(--bg-elevated)] px-2.5 py-2 shadow-lg"
              style={
                hover < n / 2
                  ? { left: `${((hover + 1) / n) * 100}%`, marginLeft: 6 }
                  : { right: `${((n - hover) / n) * 100}%`, marginRight: 6 }
              }
            >
              <div className="mb-1 font-mono text-[10px] font-semibold text-[var(--text-primary)]">
                {fmtDay(hovered.date)}{' '}
                <span className="font-normal text-[var(--text-tertiary)]">{hovered.date}</span>
              </div>
              {strips.map(({ lens, field }) => (
                <div key={lens} className="flex items-center justify-between gap-3">
                  <span className="flex items-center gap-1.5">
                    <span
                      className="h-1.5 w-1.5 flex-shrink-0 rounded-full"
                      style={{ background: LENS_COLOR[lens] }}
                    />
                    <span className="text-[10px] text-[var(--text-secondary)]">
                      {LENS_META[lens].label}
                    </span>
                  </span>
                  <span
                    className={`font-mono text-[10px] tabular-nums ${
                      hovered[field] > 0 ? 'text-[var(--text-primary)]' : 'text-[var(--text-muted)]'
                    }`}
                  >
                    {formatLens(hovered[field], lens)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── CPU burn: local host vs Kubernetes (same unit ⇒ one axis is legitimate) ──

export function CpuSplitPanel({
  classTotals,
  projects,
}: {
  classTotals: CruClassTotals | undefined;
  projects: CruProjectRow[];
}) {
  const localCpu = classTotals?.local?.cpuH ?? 0;
  const k8sCpu = classTotals?.k8s?.cpuCoreH ?? 0;
  const rows = useMemo(
    () =>
      projects
        .map((p) => ({ key: p.projectKey, local: p.cpuH, k8s: p.k8sCpuCoreH ?? 0 }))
        .filter((r) => r.local + r.k8s > 0)
        .sort((a, b) => b.local + b.k8s - (a.local + a.k8s))
        .slice(0, 10),
    [projects],
  );
  if (localCpu <= 0 && k8sCpu <= 0) return null;
  const max = Math.max(1e-9, ...rows.map((r) => Math.max(r.local, r.k8s)));
  const total = localCpu + k8sCpu;
  return (
    <div className="chart-container">
      <div className="chart-header flex items-center justify-between gap-3">
        <h4 title="Local CPU·h comes from LOCAL_PROCESS cpuTotalMS on the DSS host; Kubernetes core·h integrates each pod's cpuCurrentMillis over the census snapshots. Same unit, so they share one axis.">
          CPU Burn — local host vs Kubernetes
        </h4>
        <span className="flex items-center gap-3 text-[10px] text-[var(--text-muted)]">
          <span className="flex items-center gap-1">
            <span className="h-1.5 w-1.5 rounded-full" style={{ background: LENS_COLOR.cpu }} />
            local CPU·h
          </span>
          <span className="flex items-center gap-1">
            <span className="h-1.5 w-1.5 rounded-full" style={{ background: LENS_COLOR.k8s }} />
            K8s core·h
          </span>
        </span>
      </div>
      <div className="px-4 py-3">
        <div className="flex items-center gap-4">
          <div className="flex-shrink-0">
            <div className="font-mono text-lg font-semibold leading-none text-[var(--text-primary)]">
              {localCpu.toFixed(1)}
            </div>
            <div className="mt-1 text-[9px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
              Local CPU·h
            </div>
          </div>
          <div className="min-w-0 flex-1">
            <SegmentBar
              height={8}
              segments={[
                {
                  value: localCpu,
                  color: LENS_COLOR.cpu,
                  title: `Local host: ${localCpu.toFixed(1)} CPU·h (${((localCpu / total) * 100).toFixed(0)}%)`,
                },
                {
                  value: k8sCpu,
                  color: LENS_COLOR.k8s,
                  title: `Kubernetes: ${k8sCpu.toFixed(1)} core·h (${((k8sCpu / total) * 100).toFixed(0)}%)`,
                },
              ]}
            />
          </div>
          <div className="flex-shrink-0 text-right">
            <div className="font-mono text-lg font-semibold leading-none text-[var(--text-primary)]">
              {k8sCpu.toFixed(1)}
            </div>
            <div className="mt-1 text-[9px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
              K8s core·h
            </div>
          </div>
        </div>
        {k8sCpu <= 0 && (
          <div className="mt-2 text-[10px] text-[var(--text-muted)]">
            No Kubernetes CPU measured in this window — every CPU hour burned on the DSS host
            itself.
          </div>
        )}
        {rows.length > 0 && (
          <div className="mt-3 space-y-2 border-t border-[var(--border-glass)] pt-3">
            {rows.map((r) => (
              <div key={r.key} className="min-w-0">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="min-w-0 truncate font-mono text-xs text-[var(--text-primary)]">
                    {r.key}
                  </span>
                  <span className="flex-shrink-0 font-mono text-[10px] tabular-nums text-[var(--text-secondary)]">
                    {r.local.toFixed(2)} local{r.k8s > 0 && ` · ${r.k8s.toFixed(2)} k8s`}
                  </span>
                </div>
                <div className="mt-0.5 space-y-[2px]">
                  <div className="h-[5px] overflow-hidden rounded-sm bg-[var(--bg-elevated)]">
                    <div
                      className="h-full rounded-sm"
                      style={{ width: `${(r.local / max) * 100}%`, background: LENS_COLOR.cpu }}
                    />
                  </div>
                  {r.k8s > 0 && (
                    <div className="h-[5px] overflow-hidden rounded-sm bg-[var(--bg-elevated)]">
                      <div
                        className="h-full rounded-sm"
                        style={{ width: `${(r.k8s / max) * 100}%`, background: LENS_COLOR.k8s }}
                      />
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
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

// ── LLM: frontier-provider popularity (OpenAI vs Anthropic vs Gemini) ──

type Provider = 'OpenAI' | 'Anthropic' | 'Gemini' | 'Other';

// Fixed identity slots (viz-cat order, never cycled). "Other" wears the last
// slot so the three frontier providers keep stable colors.
const PROVIDER_ORDER: Provider[] = ['OpenAI', 'Anthropic', 'Gemini', 'Other'];
const PROVIDER_COLOR: Record<Provider, string> = {
  OpenAI: 'var(--viz-cat-1)',
  Anthropic: 'var(--viz-cat-2)',
  Gemini: 'var(--viz-cat-3)',
  Other: 'var(--viz-cat-6)',
};

// Provider detection over every identity field DSS gives us (llmType is the
// connection type, e.g. OPENAI / ANTHROPIC / VERTEX / BEDROCK; model names
// disambiguate aggregators like Bedrock that host several providers).
function providerOf(m: CruLlmModelRow): Provider {
  const s = `${m.llmType} ${m.llmId} ${m.model} ${m.connection}`.toLowerCase();
  if (/anthropic|claude/.test(s)) return 'Anthropic';
  if (/gemini|vertex|palm|bison|google/.test(s)) return 'Gemini';
  if (/openai|gpt|davinci|o[134][- ]?(mini|preview)?\b/.test(s)) return 'OpenAI';
  return 'Other';
}

export function LlmProvidersPanel({ models }: { models: CruLlmModelRow[] }) {
  const rows = useMemo(() => {
    const agg = new Map<Provider, { usd: number; tok: number; calls: number; models: number }>();
    for (const p of PROVIDER_ORDER) agg.set(p, { usd: 0, tok: 0, calls: 0, models: 0 });
    for (const m of models) {
      const a = agg.get(providerOf(m))!;
      a.usd += m.usd;
      a.tok += m.ptok + m.ctok;
      a.calls += m.queries;
      a.models += 1;
    }
    return PROVIDER_ORDER.map((p) => ({ provider: p, ...agg.get(p)! }))
      .filter((r) => r.provider !== 'Other' || r.calls > 0)
      // Rank by popularity; colors stay bound to the provider, not the rank.
      .sort((a, b) => b.calls - a.calls);
  }, [models]);
  if (models.length === 0) return null;
  const totalCalls = rows.reduce((s, r) => s + r.calls, 0);
  const maxCalls = Math.max(1, ...rows.map((r) => r.calls));
  // The split bar only earns its place when there is an actual split to show —
  // a single-provider 100% bar reads as an unexplained stray line.
  const showSplit = rows.filter((r) => r.calls > 0).length >= 2;
  return (
    <div className="chart-container">
      <div className="chart-header flex items-center justify-between gap-3">
        <h4 title="Every model in the window mapped to its frontier provider (connection type + model name). Popularity = share of LLM calls.">
          Frontier Providers — popularity
        </h4>
        <span className="text-[10px] text-[var(--text-muted)]">share of LLM calls</span>
      </div>
      <div className="px-4 py-3">
        {showSplit && (
          <SegmentBar
            height={8}
            segments={rows.map((r) => ({
              value: r.calls,
              color: PROVIDER_COLOR[r.provider],
              title: `${r.provider}: ${r.calls.toLocaleString()} calls (${((r.calls / totalCalls) * 100).toFixed(0)}%)`,
            }))}
          />
        )}
        <div className={`space-y-2.5 ${showSplit ? 'mt-3' : ''}`}>
          {rows.map((r) => {
            const pct = totalCalls > 0 ? (r.calls / totalCalls) * 100 : 0;
            const empty = r.calls === 0;
            return (
              <div key={r.provider} className={`min-w-0 ${empty ? 'opacity-50' : ''}`}>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="flex min-w-0 items-center gap-1.5">
                    <span
                      className="h-2 w-2 flex-shrink-0 rounded-full"
                      style={{ background: PROVIDER_COLOR[r.provider] }}
                    />
                    <span className="truncate text-xs font-medium text-[var(--text-primary)]">
                      {r.provider}
                    </span>
                  </span>
                  <span className="flex-shrink-0 font-mono text-xs tabular-nums text-[var(--text-primary)]">
                    {empty ? '—' : `${r.calls.toLocaleString()} calls · ${pct.toFixed(0)}%`}
                  </span>
                </div>
                <div className="mt-1 flex items-center gap-2">
                  <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-[var(--bg-elevated)]">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${(r.calls / maxCalls) * 100}%`,
                        background: PROVIDER_COLOR[r.provider],
                      }}
                    />
                  </div>
                  <span className="flex-shrink-0 whitespace-nowrap font-mono text-[10px] text-[var(--text-muted)]">
                    {empty
                      ? 'no usage seen'
                      : `$${r.usd.toFixed(4)} · ${r.tok.toLocaleString()} tok · ${r.models} model${r.models === 1 ? '' : 's'}`}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ── Heaviest local processes: first-class, always open, drillable rows ──

function ProcessDetail({
  proc,
  onSelectProject,
}: {
  proc: CruTopProcess;
  onSelectProject: (key: string) => void;
}) {
  const idleResident = proc.memGBh >= 1 && proc.cpuH < 0.05;
  return (
    <div className="border-t border-[var(--border-glass)] bg-[var(--bg-glass)] px-4 py-3">
      <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 md:grid-cols-4">
        <div>
          <div className="text-[9px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
            User
          </div>
          <div className="font-mono text-xs text-[var(--text-primary)]">
            {proc.authIdentifier ?? '—'}
          </div>
        </div>
        <div>
          <div className="text-[9px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
            Active
          </div>
          <div className="font-mono text-xs text-[var(--text-primary)]">
            {proc.firstDay && proc.lastDay
              ? proc.firstDay === proc.lastDay
                ? fmtDay(proc.firstDay)
                : `${fmtDay(proc.firstDay)} → ${fmtDay(proc.lastDay)}`
              : '—'}
          </div>
        </div>
        <div>
          <div className="text-[9px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
            Memory / CPU
          </div>
          <div className="font-mono text-xs text-[var(--text-primary)]">
            {proc.memGBh.toFixed(1)} GB·h · {proc.cpuH.toFixed(2)} CPU·h
            {idleResident && (
              <span className="badge-warning ml-1.5 rounded px-1.5 py-0.5 font-mono text-[9px]">
                idle-resident
              </span>
            )}
          </div>
        </div>
        <div className="min-w-0">
          <div className="text-[9px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
            Project
          </div>
          <button
            type="button"
            onClick={() => onSelectProject(proc.projectKey)}
            className="font-mono text-xs text-[var(--text-primary)] hover:text-[var(--neon-cyan)]"
            title="Open this project's compute drilldown"
          >
            {proc.projectKey} ↗
          </button>
        </div>
      </div>
      {proc.commandName && (
        <div className="mt-2">
          <div className="text-[9px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
            Command
          </div>
          <div className="break-all font-mono text-[10px] leading-relaxed text-[var(--text-secondary)]">
            {proc.commandName}
          </div>
        </div>
      )}
      <div className="mt-2 break-all font-mono text-[9px] text-[var(--text-muted)]" title="CRU id">
        {proc.id}
      </div>
    </div>
  );
}

export function TopProcessesPanel({
  processes,
  onSelectProject,
}: {
  processes: CruTopProcess[];
  onSelectProject: (key: string) => void;
}) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const maxMem = useMemo(
    () => Math.max(1e-9, ...processes.map((p) => p.memGBh)),
    [processes],
  );
  const maxCpu = useMemo(() => Math.max(1e-9, ...processes.map((p) => p.cpuH)), [processes]);
  const columns = useMemo<ColumnDef<CruTopProcess>[]>(
    () => [
      {
        id: 'contextType',
        label: 'Process',
        render: (p) => {
          const open = expanded.has(p.id);
          return (
            <button
              type="button"
              onClick={() =>
                setExpanded((cur) => {
                  const next = new Set(cur);
                  if (!next.delete(p.id)) next.add(p.id);
                  return next;
                })
              }
              aria-expanded={open}
              className="flex max-w-full items-center gap-1.5 text-left hover:text-[var(--neon-cyan)]"
            >
              <span className="font-mono text-[10px] text-[var(--text-tertiary)]">
                {open ? '▾' : '▸'}
              </span>
              <span className="truncate">{p.contextType}</span>
            </button>
          );
        },
        sortValue: (p) => p.contextType,
      },
      {
        id: 'projectKey',
        label: 'Project',
        mono: true,
        render: (p) =>
          p.projectKey === 'NONE' ? (
            <span className="text-[var(--text-muted)]">—</span>
          ) : (
            <button
              type="button"
              onClick={() => onSelectProject(p.projectKey)}
              className="hover:text-[var(--neon-cyan)]"
              title="Open this project's compute drilldown"
            >
              {p.projectKey}
            </button>
          ),
        sortValue: (p) => p.projectKey,
      },
      {
        id: 'authIdentifier',
        label: 'User',
        mono: true,
        cellClassName: 'text-[var(--text-secondary)]',
        render: (p) => (p.authIdentifier && p.authIdentifier !== 'NONE' ? p.authIdentifier : '—'),
        sortValue: (p) => p.authIdentifier ?? '',
      },
      {
        id: 'lastDay',
        label: 'Last seen',
        mono: true,
        cellClassName: 'text-[var(--text-secondary)] whitespace-nowrap',
        render: (p) => (p.lastDay ? fmtDay(p.lastDay) : '—'),
        sortValue: (p) => p.lastDay ?? '',
      },
      {
        id: 'commandName',
        label: 'Command',
        mono: true,
        cellClassName: 'text-[var(--text-muted)] max-w-64 truncate',
        render: (p) => p.commandName || '—',
        sortValue: (p) => p.commandName,
      },
      {
        id: 'memGBh',
        label: 'GB·h',
        align: 'right',
        render: (p) => (
          <div className="flex items-center justify-end gap-2">
            <span className="font-mono text-xs tabular-nums text-[var(--text-primary)]">
              {p.memGBh.toFixed(1)}
            </span>
            <span className="w-14">
              <UsageBar pct={(p.memGBh / maxMem) * 100} tone="info" />
            </span>
          </div>
        ),
        sortValue: (p) => p.memGBh,
      },
      {
        id: 'cpuH',
        label: 'CPU·h',
        align: 'right',
        render: (p) => (
          <div className="flex items-center justify-end gap-2">
            <span className="font-mono text-xs tabular-nums text-[var(--text-primary)]">
              {p.cpuH.toFixed(2)}
            </span>
            <span className="w-14">
              <UsageBar pct={(p.cpuH / maxCpu) * 100} tone="ok" />
            </span>
          </div>
        ),
        sortValue: (p) => p.cpuH,
      },
    ],
    [expanded, maxMem, maxCpu, onSelectProject],
  );
  if (processes.length === 0) return null;
  return (
    <DataGrid
      title="Heaviest Local Processes"
      countBadge={{ total: processes.length }}
      rows={processes}
      columns={columns}
      rowKey={(p) => p.id}
      defaultSortColumnId="memGBh"
      defaultSortDir="desc"
      renderExpandedRow={(p) => <ProcessDetail proc={p} onSelectProject={onSelectProject} />}
      expandedRowKeys={expanded}
      emptyMessage="No heavy processes."
      scroll="card"
    />
  );
}
