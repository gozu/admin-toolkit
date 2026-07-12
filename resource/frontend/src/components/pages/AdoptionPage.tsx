import { useEffect, useState, type ReactNode } from 'react';
import { motion } from 'framer-motion';
import { useDiag } from '../../context/DiagContext';
import { adoptionScan } from '../../state/adoptionScan';
import { adoptionInventoryScan } from '../../state/adoptionInventoryScan';
import { adoptionEventsScan } from '../../state/adoptionEventsScan';
import { resolveLifecycleFromFields } from '../../utils/pageLifecycle';
import {
  buildInventoryView,
  completeMonthsOnly,
  fillQuarterRange,
  monthKeyUTC,
  monthToQuarter,
  quarterKeyUTC,
  quarterLabel,
  DETAIL_GROUPS,
  DETAIL_GROUP_COLORS,
  MATURITY_DIMENSIONS,
  TREND_GROUPS,
  TREND_GROUP_COLORS,
  type InventoryProjectViewRow,
} from '../../utils/inventoryData';
import { DataGrid } from '../common/DataGrid';
import { ProgressIndicator } from '../common/ProgressIndicator';
import { BigStat, SegmentBar, UsageBar } from './missionControl/microViz';
import { TILE_VARIANTS } from './missionControl/tokens';
import { CumulativeAdoptionChart, type CumulativePoint } from './CumulativeAdoptionChart';
import { OnboardingChart, type OnboardingQuarterPoint } from './OnboardingChart';
import type { ColumnDef } from '../../utils/dataGridTypes';
import type { AdoptionMonthPoint, AdoptionProjectRow, AdoptionPulseData } from '../../types';
import './adoption.css';

const EMPTY: never[] = [];
const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;

const SPARK_MONTHS = 12;
// TTFB is hidden below this many measured users — "0d median, 2 users" is
// noise dressed as a stat.
const MIN_TTFB_USERS = 5;
// "Recently active" window for the funnel and idle-seat detection.
const ACTIVE_DAYS = 90;
// Momentum compares human commit volume over this many complete months vs
// the same span before.
const MOMENTUM_MONTHS = 12;
// Below these floors, onboarding renders a sentence instead of slab bars.
const MIN_ONBOARDING_QUARTERS = 3;
const MIN_ONBOARDING_USERS = 5;

/** Chapter header: the page reads as three questions, each with a COMPUTED
 * answer — the question is the loudest text in its section, and it never
 * goes unanswered. */
function ChapterHeader({
  no,
  title,
  answer,
  caption,
  right,
}: {
  no: string;
  title: string;
  /** One computed sentence answering the question — never marketing copy. */
  answer?: ReactNode;
  caption?: string;
  right?: ReactNode;
}) {
  return (
    <div className="border-b border-[var(--border-glass)] px-4 pb-2.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-0.5">
          <span className="font-mono text-[11px] tracking-[0.2em] text-[var(--text-muted)]">
            {no}
          </span>
          <h3 className="text-[17px] font-semibold text-[var(--text-primary)]">{title}</h3>
          {caption && <span className="text-[10px] text-[var(--text-tertiary)]">{caption}</span>}
        </div>
        {right}
      </div>
      {answer && (
        <div className="mt-1 pl-[34px] text-[12px] leading-relaxed text-[var(--text-secondary)] [&_strong]:font-semibold [&_strong]:text-[var(--text-primary)]">
          {answer}
        </div>
      )}
    </div>
  );
}

/** The one family-group legend — same fixed colors everywhere a family mix
 * appears (creation chart, portfolio mix, grid bars). */
function FamilyGroupLegend({ className = '' }: { className?: string }) {
  return (
    <div className={`flex flex-wrap items-center gap-x-3 gap-y-1 ${className}`}>
      {TREND_GROUPS.map((g, gi) => (
        <span
          key={g.key}
          className="inline-flex items-center gap-1 whitespace-nowrap text-[9px] uppercase tracking-[0.08em] text-[var(--text-tertiary)]"
        >
          <span
            className="h-1.5 w-1.5 flex-shrink-0 rounded-[2px]"
            style={{ background: TREND_GROUP_COLORS[gi] }}
          />
          {g.label}
        </span>
      ))}
    </div>
  );
}

/** Share label that never rounds a real count down to "0%". */
function pctLabel(value: number, total: number): string {
  if (value <= 0 || total <= 0) return '0%';
  const pct = (value / total) * 100;
  return pct < 1 ? '<1%' : `${Math.round(pct)}%`;
}

interface MixItem {
  key: string;
  label: string;
  color: string;
  value: number;
  /** Hover tooltip for the legend row. */
  hint?: string;
}

/** 100%-stacked bar + its legend, hover-linked: pointing at a legend row
 * spotlights that segment (and vice versa). */
function LinkedMix({
  items,
  height = 6,
  pctTotal,
}: {
  items: MixItem[];
  height?: number;
  /** Share denominator override (defaults to the sum of the items). */
  pctTotal?: number;
}) {
  const [hot, setHot] = useState<string | null>(null);
  const total = items.reduce((s, it) => s + it.value, 0);
  const denom = pctTotal ?? total;
  return (
    <div>
      <div
        className="flex w-full gap-[2px] overflow-hidden rounded-full bg-[var(--bg-elevated)]"
        style={{ height }}
        onMouseLeave={() => setHot(null)}
      >
        {items
          .filter((it) => it.value > 0)
          .map((it) => (
            <div
              key={it.key}
              title={`${it.label} · ${it.value.toLocaleString()} (${pctLabel(it.value, denom)})`}
              onMouseEnter={() => setHot(it.key)}
              className={`adk-seg rounded-[1px] ${
                hot === null ? '' : hot === it.key ? 'adk-seg-hot' : 'adk-seg-dim'
              }`}
              style={{ flexGrow: it.value, flexBasis: 0, background: it.color }}
            />
          ))}
      </div>
      <div className="mt-3 space-y-0.5">
        {items.map((it) => (
          <div
            key={it.key}
            title={it.hint}
            onMouseEnter={() => setHot(it.key)}
            onMouseLeave={() => setHot(null)}
            className={`adk-legend-row -mx-1 flex items-center gap-2 px-1 py-0.5 ${
              hot !== null && hot !== it.key ? 'adk-legend-row-dim' : ''
            }`}
          >
            <span
              className="adk-dot h-2 w-2 flex-shrink-0 rounded-[2px]"
              style={{ background: it.color }}
            />
            <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--text-secondary)]">
              {it.label}
            </span>
            <span className="w-10 flex-shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-tertiary)]">
              {pctLabel(it.value, denom)}
            </span>
            <span className="w-16 flex-shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-primary)]">
              {it.value.toLocaleString()}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

interface ColPoint {
  key: string;
  value: number;
  title: string;
  /** Optional per-column x label. */
  label?: string;
  /** Dimmed column (no measurable value). */
  muted?: boolean;
}

/** CSS mini column chart (2D, canvas-free): bars grow in on mount with a
 * slight stagger, brighten on hover, value revealed above the hovered bar. */
function MiniColumns({
  points,
  color = 'var(--accent)',
  height = 96,
  gap = 3,
  axisLeft,
  axisRight,
  valueSuffix = '',
  showValues = true,
}: {
  points: ColPoint[];
  color?: string;
  height?: number;
  gap?: number;
  axisLeft?: string;
  axisRight?: string;
  valueSuffix?: string;
  /** Hide the hover value row (dense sparklines). */
  showValues?: boolean;
}) {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const id = requestAnimationFrame(() => setReady(true));
    return () => cancelAnimationFrame(id);
  }, []);
  if (points.length === 0) return null;
  const max = Math.max(1, ...points.map((p) => p.value));
  const hasLabels = points.some((p) => p.label);
  return (
    <div>
      <div className="flex items-end" style={{ height, gap: `${gap}px` }}>
        {points.map((p, i) => (
          <div
            key={p.key}
            title={p.title}
            className="adk-colwrap flex h-full min-w-0 flex-1 flex-col justify-end"
          >
            {showValues && (
              <span className="adk-colval pb-0.5 text-center font-mono text-[9px] leading-none text-[var(--text-secondary)]">
                {p.muted ? '—' : `${p.value.toLocaleString()}${valueSuffix}`}
              </span>
            )}
            <span
              className="adk-col w-full rounded-t-[2px]"
              style={{
                height: ready ? `${p.value <= 0 ? 2 : Math.max(5, (p.value / max) * 100)}%` : '0%',
                background: color,
                opacity: p.muted || p.value <= 0 ? 0.18 : 0.85,
                transitionDelay: `${i * 14}ms`,
              }}
            />
          </div>
        ))}
      </div>
      {hasLabels && (
        <div className="mt-1 flex" style={{ gap: `${gap}px` }}>
          {points.map((p) => (
            <span
              key={p.key}
              className="min-w-0 flex-1 truncate text-center font-mono text-[9px] leading-none text-[var(--text-tertiary)]"
            >
              {p.label ?? ''}
            </span>
          ))}
        </div>
      )}
      {(axisLeft || axisRight) && (
        <div className="mt-1 flex items-center justify-between font-mono text-[9px] leading-none text-[var(--text-tertiary)]">
          <span>{axisLeft}</span>
          <span>{axisRight}</span>
        </div>
      )}
    </div>
  );
}

/** SVG mini cumulative curve for the per-family small multiples: an
 * always-rising line with a soft area fill, per-month hover titles, and the
 * running total on the right. Single-series and directly titled, so hue reuse
 * across cards stays CVD-legal. */
function MiniCumulative({
  values,
  months,
  color,
  unitLabel,
  height = 64,
}: {
  /** Cumulative totals, one per month (aligned with `months`). */
  values: number[];
  months: string[];
  color: string;
  /** Lowercase noun for hover titles, e.g. 'datasets'. */
  unitLabel: string;
  height?: number;
}) {
  if (values.length === 0) return null;
  const max = Math.max(1, values[values.length - 1]);
  const n = values.length;
  const W = 100;
  const xAt = (i: number) => (n === 1 ? W : (i / (n - 1)) * W);
  const yAt = (v: number) => height - (v / max) * (height - 4) - 2;
  const line = values
    .map((v, i) => `${i === 0 ? 'M' : 'L'}${xAt(i).toFixed(2)},${yAt(v).toFixed(2)}`)
    .join(' ');
  const area = `${line} L${W},${height} L0,${height} Z`;
  return (
    <div>
      <svg
        width="100%"
        height={height}
        viewBox={`0 0 ${W} ${height}`}
        preserveAspectRatio="none"
        role="img"
      >
        <path d={area} fill={color} opacity={0.12} />
        <path
          d={line}
          fill="none"
          stroke={color}
          strokeWidth={1.5}
          vectorEffect="non-scaling-stroke"
          strokeLinejoin="round"
        />
        {values.map((v, i) => (
          <rect
            key={months[i]}
            x={i === 0 ? 0 : (xAt(i) + xAt(i - 1)) / 2}
            y={0}
            width={n === 1 ? W : W / (n - 1)}
            height={height}
            fill="transparent"
          >
            <title>{`through ${monthLabel(months[i])} · ${v.toLocaleString()} ${unitLabel} built so far`}</title>
          </rect>
        ))}
      </svg>
      <div className="mt-1 flex items-center justify-between font-mono text-[9px] leading-none text-[var(--text-tertiary)]">
        <span>{monthLabel(months[0])}</span>
        <span>{monthLabel(months[n - 1])}</span>
      </div>
    </div>
  );
}

const MONTH_ABBR = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

function fmtDate(ms: number | null | undefined): string {
  if (ms == null) return '—';
  const d = new Date(ms);
  return Number.isFinite(d.getTime()) ? d.toISOString().slice(0, 10) : '—';
}

function monthLabel(ym: string): string {
  const [y, m] = ym.split('-');
  const idx = Number.parseInt(m ?? '', 10) - 1;
  return `${MONTH_ABBR[idx] ?? m ?? ''} '${(y ?? '').slice(2)}`;
}

function relDays(
  ms: number | null | undefined,
  nowMs: number,
): { text: string; days: number | null } {
  if (ms == null) return { text: '—', days: null };
  const days = Math.floor((nowMs - ms) / DAY_MS);
  if (days <= 0) return { text: 'today', days: 0 };
  if (days === 1) return { text: '1d ago', days };
  if (days < 30) return { text: `${days}d ago`, days };
  if (days < 365) return { text: `${Math.floor(days / 30)}mo ago`, days };
  return { text: `${(days / 365).toFixed(1)}y ago`, days };
}

/** First ms of the month AFTER a 'YYYY-MM' key — cumulative cut points. */
function monthEndMsUTC(month: string): number {
  const y = Number(month.slice(0, 4));
  const m = Number(month.slice(5, 7));
  return m === 12 ? Date.UTC(y + 1, 0, 1) : Date.UTC(y, m, 1);
}

/** 'YYYY-Qn' → the quarter before it. */
function prevQuarterKey(q: string): string {
  const y = Number(q.slice(0, 4));
  const n = Number(q.slice(6));
  return n === 1 ? `${y - 1}-Q4` : `${y}-Q${n - 1}`;
}

// Friendly aggregation of run-bucket msgTypes for the pulse card. Verified
// against live audit logs: flow-job-start / scenario-run / runnable-run are
// what actually appears — keep in sync with the macro's _BUCKET_RULES.
const RUN_KINDS: Array<{ label: string; match: (msgType: string) => boolean; hint?: string }> = [
  { label: 'jobs started', match: (t) => t === 'flow-job-start' || t === 'job-start' },
  {
    label: 'scenario runs',
    match: (t) => t.startsWith('scenario-run') || t === 'scenario-fire-trigger',
  },
  {
    label: 'macro runs',
    match: (t) => t === 'runnable-run',
    hint: 'Includes this toolkit’s own scan macros — treat as operational, not adoption.',
  },
];

// Pulse bucket presentation — labels + fixed colors for the activity mix.
// Every audit event lands in exactly one bucket, so the mix always sums to
// the headline (the arithmetic reconciles by construction).
const PULSE_BUCKETS: Array<{ key: string; label: string; color: string; hint: string }> = [
  {
    key: 'build',
    label: 'Building',
    color: 'var(--viz-cat-1)',
    hint: 'Config writes: object creation, edits, settings saves.',
  },
  {
    key: 'run',
    label: 'Running',
    color: 'var(--viz-cat-3)',
    hint: 'Compute actually executed: flow jobs, scenario runs, macro runs.',
  },
  {
    key: 'explore',
    label: 'Exploring',
    color: 'var(--viz-cat-5)',
    hint: 'Reads and navigation: browsing flows, previewing data, catalog & search.',
  },
  {
    key: 'consume',
    label: 'Consuming',
    color: 'var(--viz-cat-4)',
    hint: 'Value leaving the platform: dashboards viewed, exports, downloads.',
  },
  {
    key: 'other',
    label: 'Other',
    color: 'var(--text-tertiary)',
    hint: 'Everything else, incl. internal monitoring noise that slipped the filters.',
  },
];

/** Per-project config-history drill-down: creator counts + family mix. Used
 * inside the expanded project row. */
function InventoryCreatorsSection({
  inv,
  invNowMs,
}: {
  inv: InventoryProjectViewRow;
  invNowMs: number;
}) {
  const creators = Object.entries(inv.creators).sort((a, b) => b[1] - a[1]);
  const shown = creators.slice(0, 40);
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
        <span>
          Config history — {creators.length} {creators.length === 1 ? 'creator' : 'creators'} ·{' '}
          {inv.objectCount.toLocaleString()} surviving objects
        </span>
        <span className="normal-case tracking-normal text-[var(--text-muted)]">
          last config edit {relDays(inv.lastEditMs, invNowMs).text}
          {inv.lastEditor ? ` by ${inv.lastEditor}` : ''} · maturity {inv.maturityScore}/
          {MATURITY_DIMENSIONS.length}
          {inv.notebookRecipeRatio != null
            ? ` · ${inv.notebookRecipeRatio.toFixed(2)} notebooks per recipe`
            : ''}
        </span>
      </div>
      <div className="mb-1.5 max-w-md">
        <SegmentBar
          height={4}
          segments={TREND_GROUPS.map((g, gi) => ({
            value: inv.groups[gi],
            color: TREND_GROUP_COLORS[gi],
            title: `${g.label} · ${inv.groups[gi].toLocaleString()}`,
          }))}
        />
      </div>
      <FamilyGroupLegend className="mb-2" />
      <div className="flex flex-wrap gap-1.5">
        {creators.length === 0 && (
          <span className="text-xs text-[var(--text-muted)]">
            No creation tags in this project.
          </span>
        )}
        {shown.map(([login, count]) => (
          <span
            key={login}
            className="adk-chip rounded border border-[var(--border-glass)] bg-[var(--bg-elevated)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--text-secondary)]"
            title={`${count.toLocaleString()} surviving objects created by ${login} in this project`}
          >
            {login} <span className="text-[var(--text-tertiary)]">×{count.toLocaleString()}</span>
          </span>
        ))}
        {creators.length > shown.length && (
          <span className="text-[10px] text-[var(--text-tertiary)]">
            +{creators.length - shown.length} more
          </span>
        )}
      </div>
    </div>
  );
}

function ProjectAuthorsPanel({
  row,
  inv,
  invNowMs,
}: {
  row: AdoptionProjectRow;
  inv?: InventoryProjectViewRow;
  invNowMs: number;
}) {
  const authors = row.authors ?? EMPTY;
  return (
    <div className="border-t border-[var(--border-glass)] bg-[var(--bg-glass)] px-4 py-3">
      <div className="mb-2 flex items-center gap-3 text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
        <span>
          {authors.length} distinct {authors.length === 1 ? 'builder' : 'builders'} (git)
        </span>
        <span className="text-[var(--text-muted)] normal-case tracking-normal">
          first {fmtDate(row.firstCommitMs)} · last {fmtDate(row.lastCommitMs)}
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {authors.length === 0 && (
          <span className="text-xs text-[var(--text-muted)]">
            No human commits (automation only).
          </span>
        )}
        {authors.map((a) => (
          <span
            key={a}
            className="adk-chip rounded border border-[var(--border-glass)] bg-[var(--bg-elevated)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--text-secondary)]"
          >
            {a}
          </span>
        ))}
      </div>
      {inv && (
        <div className="mt-3 border-t border-[var(--border-glass)] pt-3">
          <InventoryCreatorsSection inv={inv} invNowMs={invNowMs} />
        </div>
      )}
    </div>
  );
}

/** 72-hour operational pulse — deliberately demoted: a short-window systems
 * check, never adoption evidence. The mix is exhaustive over the measured
 * events, so the headline and the breakdown reconcile by construction. */
function PulseCard({ pulse, nowMs }: { pulse: AdoptionPulseData; nowMs: number }) {
  const hours = pulse.hours ?? EMPTY;
  const runTypes = pulse.runTypes ?? {};
  const buckets = pulse.buckets ?? {};
  const totalEvents = Object.values(buckets).reduce((s, v) => s + v, 0);

  // Run-kind detail rows (subset of the "run" bucket).
  const matched = new Set<string>();
  const runRows = RUN_KINDS.map((kind) => {
    let count = 0;
    for (const [msgType, n] of Object.entries(runTypes)) {
      if (kind.match(msgType)) {
        count += n;
        matched.add(msgType);
      }
    }
    return { label: kind.label, count, hint: kind.hint };
  }).filter((r) => r.count > 0);
  const restRun = Object.entries(runTypes)
    .filter(([t]) => !matched.has(t))
    .reduce((s, [, n]) => s + n, 0);

  const mixItems: MixItem[] = PULSE_BUCKETS.map((b) => ({
    key: b.key,
    label: b.label,
    color: b.color,
    value: buckets[b.key] ?? 0,
    hint: b.hint,
  })).filter((it) => it.value > 0);

  // Zero-fill the hourly axis — an idle hour is a real zero, not a gap.
  const pulseCols: ColPoint[] = [];
  if (hours.length > 0) {
    const byHour = new Map(hours.map((h) => [h.hourMs, h]));
    const first = hours[0].hourMs;
    const last = hours[hours.length - 1].hourMs;
    for (let h = first; h <= last && pulseCols.length < 100; h += HOUR_MS) {
      const row = byHour.get(h);
      pulseCols.push({
        key: String(h),
        value: row?.events ?? 0,
        title: `${new Date(h).toISOString().slice(5, 16).replace('T', ' ')}Z · ${(row?.events ?? 0).toLocaleString()} events · ${row?.humans ?? 0} ${row?.humans === 1 ? 'person' : 'people'}`,
      });
    }
  }

  const coverage = pulse.coverageHours;
  const windowLabel =
    coverage == null
      ? 'no events in window'
      : coverage >= 48
        ? `last ${(coverage / 24).toFixed(1)}d`
        : `last ${coverage}h`;
  const topHumans = (pulse.topHumans ?? EMPTY).slice(0, 3);

  return (
    <div className="chart-container">
      <div className="chart-header flex items-center justify-between gap-3">
        <h4 title="Reverse tail-scan of the newest audit files. A 72-hour operational sample — useful as a systems check, never as adoption evidence. The window label is MEASURED from actual event timestamps.">
          Last 72 hours — operational pulse
        </h4>
        <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
          audit tail · {windowLabel}
        </span>
      </div>
      {totalEvents === 0 ? (
        <div className="px-4 py-4 text-xs text-[var(--text-muted)]">
          No human audit events in the last {pulse.windowHours ?? 72}h.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-x-6 gap-y-3 px-4 py-4 lg:grid-cols-[260px_minmax(0,1fr)]">
          <div className="flex flex-col gap-3">
            <div className="flex items-baseline gap-4">
              <BigStat value={totalEvents.toLocaleString()} label="Audit events (human)" />
            </div>
            <LinkedMix items={mixItems} />
            {runRows.length > 0 && (
              <div className="space-y-0.5 border-t border-[var(--border-glass)] pt-2">
                {runRows.map((r) => (
                  <div
                    key={r.label}
                    title={r.hint}
                    className="adk-hover-row -mx-1 flex items-center justify-between gap-2 px-1 py-0.5 text-[11px]"
                  >
                    <span className="text-[var(--text-secondary)]">{r.label}</span>
                    <span className="font-mono tabular-nums text-[var(--text-primary)]">
                      {r.count.toLocaleString()}
                    </span>
                  </div>
                ))}
                {restRun > 0 && (
                  <div className="adk-hover-row -mx-1 flex items-center justify-between gap-2 px-1 py-0.5 text-[11px]">
                    <span className="text-[var(--text-tertiary)]">other run events</span>
                    <span className="font-mono tabular-nums text-[var(--text-tertiary)]">
                      {restRun.toLocaleString()}
                    </span>
                  </div>
                )}
              </div>
            )}
            {topHumans.length > 0 && (
              <div className="text-[10px] text-[var(--text-tertiary)]">
                most active:{' '}
                {topHumans.map((h, i) => (
                  <span key={h.login}>
                    {i > 0 && ' · '}
                    <span className="font-mono text-[var(--text-secondary)]">{h.login}</span> (
                    {h.events.toLocaleString()})
                  </span>
                ))}
              </div>
            )}
          </div>
          <div className="min-w-0">
            <MiniColumns
              points={pulseCols}
              height={110}
              gap={1}
              showValues={false}
              axisLeft={
                pulse.firstEventMs != null
                  ? `${Math.max(1, Math.round((nowMs - pulse.firstEventMs) / HOUR_MS))}h ago`
                  : undefined
              }
              axisRight={
                pulse.lastEventMs != null
                  ? `newest ${Math.round((nowMs - pulse.lastEventMs) / HOUR_MS) <= 0 ? 'this hour' : `${Math.round((nowMs - pulse.lastEventMs) / HOUR_MS)}h ago`}`
                  : undefined
              }
            />
            <div className="pt-1.5 text-[10px] text-[var(--text-tertiary)]">
              human events per hour ·{' '}
              {pulse.exhaustedFiles
                ? `the rotated audit files reach back only ${windowLabel.replace('last ', '')} — that's the whole retained trail`
                : coverage != null && coverage >= (pulse.windowHours ?? 72) - 1
                  ? `covered the requested ${pulse.windowHours ?? 72}h window`
                  : `scan stopped at ${windowLabel.replace('last ', '')} — audit volume cap reached`}{' '}
              · {pulse.filesRead ?? 0} {pulse.filesRead === 1 ? 'file' : 'files'} read
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function AdoptionPage() {
  const { state } = useDiag();
  const { data, scanStarted, error } = adoptionScan.use();
  const invState = adoptionInventoryScan.use();
  const evState = adoptionEventsScan.use();
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [showAllProjects, setShowAllProjects] = useState(false);

  useEffect(() => {
    if (!scanStarted) void adoptionScan.load();
  }, [scanStarted]);
  useEffect(() => {
    if (!invState.scanStarted) void adoptionInventoryScan.load();
  }, [invState.scanStarted]);
  useEffect(() => {
    if (!evState.scanStarted) void adoptionEventsScan.load();
  }, [evState.scanStarted]);

  const toggleSelect = (key: string) => setSelectedKey((cur) => (cur === key ? null : key));

  // Field-scoped lifecycles: the git spine drives the page-level progress; the
  // macro layers degrade card-by-card instead of failing the whole page.
  const lifecycle = resolveLifecycleFromFields(['adoptionLoading'], state.parsedData);
  const isLoading = lifecycle.phase === 'running' || lifecycle.phase === 'queued';

  // Plain derivations — the React Compiler auto-memoizes; manual useMemo over
  // `?? EMPTY` fallbacks can't be preserved (react-hooks/preserve-manual-memoization).
  const totals = data?.totals;
  const trend = data?.monthlyTrend ?? EMPTY;
  const cohorts = data?.cohorts ?? EMPTY;
  const recency = data?.builderRecency ?? EMPTY;
  const repeatBuilders = data?.repeatBuilders;
  // Reference "now": the payload's own timestamp (never the wall clock — the
  // React Compiler purity rule bans Date.now() in render, and server time is
  // the honest reference anyway). Falls back across the three payloads.
  const nowMs =
    data?.generatedAtMs ?? evState.data?.generatedAtMs ?? invState.data?.generatedAtMs ?? 0;
  const projects = data?.projectRows ?? EMPTY;
  const groups = data?.groups ?? EMPTY;
  const builders = data?.builderStats ?? EMPTY;
  const peopleMax = Math.max(1, ...projects.map((p) => p.authorCount));
  const expandedRowKeys = new Set(selectedKey ? [selectedKey] : []);

  // Partial-period honesty: rate charts plot complete months/quarters only —
  // 10 days of July next to full months always reads as a collapse.
  const gitTrendComplete = completeMonthsOnly(trend, nowMs);

  // The people funnel — one reconciled account of "how many people":
  // accounts on the instance → ever built (git).
  // Git history remembers departed builders, so "ever built" can EXCEED the
  // current account count — the subset framing (funnel arrows, "X of Y
  // accounts") is only honest when it actually holds.
  const accountCount = recency.length;
  const activeRecently = builders.filter(
    (b) => b.lastCommitMs != null && nowMs - b.lastCommitMs <= ACTIVE_DAYS * DAY_MS,
  ).length;
  const buildersAreSubset = totals != null && accountCount >= totals.builderCount;

  // Momentum on COMMIT VOLUME (the highest-base series we have), always with
  // the absolute before/after visible. A percentage over a tiny base is a
  // coin flip dressed as a trend — below the floor we show only absolutes.
  const sum = (pts: AdoptionMonthPoint[]) => pts.reduce((s, p) => s + p.commits, 0);
  const commitsRecent12 =
    gitTrendComplete.length >= MOMENTUM_MONTHS
      ? sum(gitTrendComplete.slice(-MOMENTUM_MONTHS))
      : null;
  const commitsPrior12 =
    gitTrendComplete.length >= MOMENTUM_MONTHS * 2
      ? sum(gitTrendComplete.slice(-MOMENTUM_MONTHS * 2, -MOMENTUM_MONTHS))
      : null;
  const MOMENTUM_MIN_BASE = 50;
  const momentumPct =
    commitsRecent12 != null && commitsPrior12 != null && commitsPrior12 >= MOMENTUM_MIN_BASE
      ? ((commitsRecent12 - commitsPrior12) / commitsPrior12) * 100
      : null;

  // Flagship cumulative series: people who ever built / projects ever active /
  // total commits, month by month (current month included — a cumulative line
  // plots the running month honestly: the last point is simply mid-climb).
  const currentQuarterKey = quarterKeyUTC(nowMs);
  const currentMonthKey = monthKeyUTC(nowMs);
  const cumulativePoints: CumulativePoint[] = (() => {
    if (trend.length === 0) return [];
    const months = trend.map((p) => p.month);
    if (months[months.length - 1] < currentMonthKey) months.push(currentMonthKey);
    const commitsByMonth = new Map(trend.map((p) => [p.month, p.commits]));
    const builderFirsts = builders
      .map((b) => b.firstCommitMs)
      .filter((ms): ms is number => ms != null)
      .sort((a, b) => a - b);
    const projectFirsts = projects
      .map((p) => p.firstCommitMs)
      .filter((ms): ms is number => ms != null)
      .sort((a, b) => a - b);
    let bi = 0;
    let pi = 0;
    let commitSum = 0;
    return months.map((month) => {
      const end = monthEndMsUTC(month);
      while (bi < builderFirsts.length && builderFirsts[bi] < end) bi++;
      while (pi < projectFirsts.length && projectFirsts[pi] < end) pi++;
      commitSum += commitsByMonth.get(month) ?? 0;
      return { month, builders: bi, projects: pi, commits: commitSum };
    });
  })();

  // ── inventory-fed derivations (all null-safe) ─────────────────────────────
  const inventoryView = buildInventoryView(invState.data, recency);
  const creationTrend = inventoryView?.trendPoints ?? EMPTY;

  // Per-family cumulative curves (one small multiple each, no combined chart,
  // no "Other" bucket). Families whose objects never carry creation tags stay
  // flat at 0 — they're skipped and listed in the footnote instead.
  const detailCumulative = DETAIL_GROUPS.map((group, gi) => {
    let running = 0;
    const values = creationTrend.map((p) => (running += p.detail[gi] ?? 0));
    return { group, color: DETAIL_GROUP_COLORS[gi], values, total: running };
  });
  const detailCharts = detailCumulative.filter((d) => d.total > 0);
  const detailEmpty = detailCumulative.filter((d) => d.total === 0);
  const creationMonthsAxis = creationTrend.map((p) => p.month);

  const busFactor = inventoryView?.busFactor;
  const singleSharePct =
    busFactor && busFactor.measuredProjects > 0
      ? Math.round((busFactor.singleCreator / busFactor.measuredProjects) * 100)
      : null;
  // Unearned red teaches people to ignore red: concentration only warns when
  // the sample is big enough to mean something.
  const concentrationWarn =
    busFactor != null &&
    singleSharePct != null &&
    busFactor.measuredProjects >= 10 &&
    singleSharePct >= 50;
  const busItems: MixItem[] = busFactor
    ? [
        {
          key: 'single',
          label: 'Single creator',
          value: busFactor.singleCreator,
          color: concentrationWarn ? 'var(--neon-red)' : 'var(--neon-amber)',
          hint: 'All context leaves with one person.',
        },
        {
          key: 'few',
          label: '2–3 creators',
          value: busFactor.twoToThree,
          color: 'var(--viz-cat-1)',
        },
        {
          key: 'many',
          label: '4+ creators',
          value: busFactor.fourPlus,
          color: 'var(--neon-green)',
        },
      ]
    : EMPTY;

  // Onboarding quarters: cohorts + TTFB share one quarter axis. Complete
  // quarters only; the running quarter is footnoted, never plotted.
  const prevQuarter = prevQuarterKey(currentQuarterKey);
  const cohortQuarterCounts = new Map<string, number>();
  let cohortsThisQuarter = 0;
  for (const c of cohorts) {
    const q = monthToQuarter(c.month);
    if (q < currentQuarterKey) {
      cohortQuarterCounts.set(q, (cohortQuarterCounts.get(q) ?? 0) + c.newUsers);
    } else if (q === currentQuarterKey) {
      cohortsThisQuarter += c.newUsers;
    }
  }
  const ttfb = inventoryView?.ttfb;
  const ttfbByQuarter = new Map((ttfb?.cohorts ?? EMPTY).map((c) => [c.quarter, c]));
  const quarterAxis = (
    cohortQuarterCounts.size > 0
      ? fillQuarterRange([...cohortQuarterCounts.keys(), prevQuarter])
      : []
  ).slice(-16);
  const onboardingPoints: OnboardingQuarterPoint[] = quarterAxis.map((quarter) => {
    const t = ttfbByQuarter.get(quarter);
    return {
      quarter,
      newUsers: cohortQuarterCounts.get(quarter) ?? 0,
      medianDays: quarter < currentQuarterKey ? (t?.medianDays ?? null) : null,
      builders: t?.builders ?? 0,
    };
  });
  const showTtfb = !!ttfb && ttfb.usersMeasured >= MIN_TTFB_USERS;
  // Direction of time-to-first-build: DOWN is the win (people reach their
  // first build faster) — say so explicitly, a falling line reads as "decline"
  // everywhere else on this page.
  const ttfbMeasured = onboardingPoints.filter((p) => p.medianDays != null);
  const ttfbFirst = ttfbMeasured[0]?.medianDays ?? null;
  const ttfbLast = ttfbMeasured[ttfbMeasured.length - 1]?.medianDays ?? null;
  const ttfbDirection =
    showTtfb && ttfbMeasured.length >= 2 && ttfbFirst != null && ttfbLast != null
      ? ttfbLast < ttfbFirst
        ? ('faster' as const)
        : ttfbLast > ttfbFirst
          ? ('slower' as const)
          : ('flat' as const)
      : null;
  const onboardingTotal = onboardingPoints.reduce((s, p) => s + p.newUsers, 0);
  // Two slab bars on a 0–2 axis look broken — below the floors, say it in a
  // sentence instead.
  const onboardingAsChart =
    onboardingPoints.length >= MIN_ONBOARDING_QUARTERS && onboardingTotal >= MIN_ONBOARDING_USERS;

  // Groups — sparse-aware: a ranked list needs ≥2 active groups to rank.
  const activeGroups = groups.filter((g) => g.commits > 0);
  const topGroups = activeGroups.slice(0, 10);
  const quietGroups = groups.length - activeGroups.length;
  const totalGroupCommits = totals?.commitCount ?? 0;
  const sparkAxis = gitTrendComplete.slice(-SPARK_MONTHS).map((p) => p.month);

  // Per-project inventory roll-ups keyed for the projects grid; recency on
  // config edits is measured against the inventory's own newest edit, never
  // the wall clock.
  const invProjectByKey = new Map<string, InventoryProjectViewRow>();
  for (const row of inventoryView?.projectRows ?? []) invProjectByKey.set(row.projectKey, row);
  const invNowMs = inventoryView?.inventory.lastEditMs ?? nowMs;

  // Recent-activity pulse (audit tail).
  const pulse = evState.data && evState.data.ok !== false ? evState.data : null;

  // Projects grid — top 20 by commits by default, expandable to the full set.
  const topProjects = [...projects].sort((a, b) => b.commits - a.commits).slice(0, 20);
  const gridRows = showAllProjects ? projects : topProjects;

  // The generated verdict — the page states its own conclusion instead of
  // making the reader reverse-engineer it from charts. Every clause is
  // computed and omitted when unmeasurable.
  // The verdict opens with an ASSESSMENT, not a fact recital — the reader
  // should be able to repeat one sentence in a meeting and defend it.
  const breadthWord =
    totals && totals.builderCount > 0
      ? activeRecently === 0
        ? 'stalled'
        : activeRecently / totals.builderCount <= 0.34
          ? 'narrow'
          : activeRecently / totals.builderCount <= 0.67
            ? 'moderate'
            : 'broad'
      : null;
  const verdict: ReactNode[] = [];
  if (totals && breadthWord) {
    verdict.push(
      <span key="assessment">
        Adoption is{' '}
        <strong>
          {activeRecently > 0 ? 'active' : 'quiet'}
          {activeRecently > 0 ? ` but ${breadthWord}` : ''}
        </strong>
        :{' '}
        {buildersAreSubset ? (
          <>
            <strong>{activeRecently}</strong> of <strong>{totals.builderCount}</strong> all-time
            builders (from <strong>{accountCount}</strong> accounts) committed in the last{' '}
            {ACTIVE_DAYS} days
          </>
        ) : (
          <>
            <strong>{activeRecently}</strong> of <strong>{totals.builderCount}</strong> all-time
            builders committed in the last {ACTIVE_DAYS} days (the instance currently hosts{' '}
            <strong>{accountCount || '—'}</strong> accounts; git history remembers departed
            builders)
          </>
        )}
        {busFactor && busFactor.measuredProjects > 0 ? (
          <>
            , and <strong>{busFactor.singleCreator}</strong> of{' '}
            <strong>{busFactor.measuredProjects}</strong> measured projects rely on a single creator
          </>
        ) : null}
        .
      </span>,
    );
    if (commitsRecent12 != null && commitsPrior12 != null) {
      verdict.push(
        <span key="momentum">
          {' '}
          Commit volume moved from <strong>{commitsPrior12.toLocaleString()}</strong> to{' '}
          <strong>{commitsRecent12.toLocaleString()}</strong> over the last two 12-month spans
          {momentumPct != null ? (
            <>
              {' '}
              (<strong>{`${momentumPct >= 0 ? '+' : ''}${momentumPct.toFixed(0)}%`}</strong>)
            </>
          ) : null}
          .
        </span>,
      );
    }
    if (inventoryView) {
      verdict.push(
        <span key="objects">
          {' '}
          The instance holds <strong>{inventoryView.objectsBuilt.toLocaleString()}</strong>{' '}
          surviving objects across <strong>{totals.projectCount}</strong> projects.
        </span>,
      );
    }
  }

  // Computed chapter answers — the question headers never go unanswered.
  const last12 = gitTrendComplete.slice(-12);
  const monthsWithActivity = last12.filter((p) => p.commits > 0).length;
  const commitsLast12 = last12.reduce((s, p) => s + p.commits, 0);
  const ch1Answer =
    last12.length === 0 ? null : monthsWithActivity > 0 ? (
      <>
        Yes — building happened in{' '}
        <strong>
          {monthsWithActivity} of the last {last12.length}
        </strong>{' '}
        complete months (<strong>{commitsLast12.toLocaleString()}</strong> human commits).
      </>
    ) : (
      <>Not recently — no git activity in the last {last12.length} complete months.</>
    );

  // Leading family for the ch2 answer — by TAGGED creations (composition is
  // sorted by count desc, but counts include untagged families; the charts
  // below are tagged-only, so the answer matches what the reader sees).
  const taggedTotal = detailCumulative.reduce((s, d) => s + d.total, 0);
  const topDetail = detailCharts.reduce<(typeof detailCharts)[number] | null>(
    (best, d) => (best == null || d.total > best.total ? d : best),
    null,
  );
  const ch2Answer =
    inventoryView && topDetail && taggedTotal > 0 ? (
      <>
        <strong>{inventoryView.objectsBuilt.toLocaleString()}</strong> surviving objects by{' '}
        <strong>{inventoryView.allTimeCreators}</strong> human{' '}
        {inventoryView.allTimeCreators === 1 ? 'creator' : 'creators'} —{' '}
        {topDetail.group.label.toLowerCase()} lead ({pctLabel(topDetail.total, taggedTotal)} of
        tagged objects).
      </>
    ) : null;

  const ch3Answer =
    busFactor && busFactor.measuredProjects > 0 ? (
      <>
        <strong>{busFactor.singleCreator}</strong> of <strong>{busFactor.measuredProjects}</strong>{' '}
        measured projects single-creator
      </>
    ) : null;

  const columns: ColumnDef<AdoptionProjectRow>[] = [
    {
      id: 'projectKey',
      label: 'Project',
      defaultSortDir: 'asc',
      render: (row) => {
        const open = selectedKey === row.projectKey;
        return (
          <button
            type="button"
            onClick={() => toggleSelect(row.projectKey)}
            className="flex items-center gap-1.5 text-left hover:text-[var(--neon-cyan)]"
            aria-expanded={open}
          >
            <span className="font-mono text-[10px] text-[var(--text-tertiary)]">
              {open ? '▾' : '▸'}
            </span>
            <span
              className={`h-1.5 w-1.5 flex-shrink-0 rounded-full ${row.active ? 'bg-[var(--neon-green)]' : 'bg-[var(--text-muted)] opacity-40'}`}
              title={row.active ? 'Active (git commit within threshold)' : 'Dormant'}
            />
            <span
              className={`font-medium ${row.active ? 'text-[var(--text-primary)]' : 'text-[var(--text-secondary)]'}`}
            >
              {row.projectKey}
            </span>
            {!row.active && (
              <span className="text-[9px] uppercase tracking-wide text-[var(--text-tertiary)]">
                dormant
              </span>
            )}
          </button>
        );
      },
      sortValue: (row) => row.projectKey,
    },
    {
      id: 'authorCount',
      label: 'People',
      align: 'right',
      render: (row) => (
        <div className="flex items-center justify-end gap-2">
          <span className="w-8 text-right font-mono text-xs tabular-nums text-[var(--text-primary)]">
            {row.authorCount}
          </span>
          <span className="w-16">
            <UsageBar pct={Math.max(4, (row.authorCount / peopleMax) * 100)} tone="info" />
          </span>
        </div>
      ),
      sortValue: (row) => row.authorCount,
    },
    {
      id: 'commits',
      label: 'Commits',
      align: 'right',
      mono: true,
      cellClassName: 'text-[var(--text-secondary)]',
      render: (row) =>
        row.truncated ? (
          <span title="History deeper than the fetch cap — this count is a floor.">
            ≥{row.commits.toLocaleString()}
          </span>
        ) : (
          row.commits.toLocaleString()
        ),
      sortValue: (row) => row.commits,
    },
    {
      id: 'activeMonths',
      label: 'Active months',
      align: 'right',
      mono: true,
      cellClassName: 'text-[var(--text-secondary)]',
      render: (row) => row.activeMonths,
      sortValue: (row) => row.activeMonths,
    },
    {
      id: 'lastCommitMs',
      label: 'Last active',
      align: 'right',
      render: (row) => {
        const rel = relDays(row.lastCommitMs, nowMs);
        return (
          <span
            className={`font-mono text-xs tabular-nums ${row.active ? 'text-[var(--text-primary)]' : 'text-[var(--text-tertiary)]'}`}
          >
            {rel.text}
          </span>
        );
      },
      sortValue: (row) => row.lastCommitMs ?? 0,
    },
    // Config-history columns (survivorship caveat applies) — only when an
    // inventory exists; git metrics stay untouched without it.
    ...(inventoryView
      ? ([
          {
            id: 'invObjects',
            label: 'Objects',
            align: 'right',
            render: (row) => {
              const inv = invProjectByKey.get(row.projectKey);
              if (!inv) return <span className="text-[var(--text-muted)]">—</span>;
              return (
                <div className="flex items-center justify-end gap-2">
                  <span className="w-16">
                    <SegmentBar
                      height={4}
                      segments={TREND_GROUPS.map((g, gi) => ({
                        value: inv.groups[gi],
                        color: TREND_GROUP_COLORS[gi],
                        title: `${g.label} · ${inv.groups[gi].toLocaleString()}`,
                      }))}
                    />
                  </span>
                  <span className="w-14 text-right font-mono text-xs tabular-nums text-[var(--text-secondary)]">
                    {inv.objectCount.toLocaleString()}
                  </span>
                </div>
              );
            },
            sortValue: (row) => invProjectByKey.get(row.projectKey)?.objectCount ?? -1,
          },
          {
            id: 'invCreators',
            label: 'Creators',
            align: 'right',
            mono: true,
            cellClassName: 'text-[var(--text-secondary)]',
            render: (row) => {
              const inv = invProjectByKey.get(row.projectKey);
              if (!inv || inv.creatorCount === 0) return '—';
              return (
                <span
                  title={
                    inv.topCreator
                      ? `Top creator ${inv.topCreator} (${Math.round(inv.topCreatorShare * 100)}% of tagged objects)`
                      : undefined
                  }
                >
                  {inv.creatorCount}
                </span>
              );
            },
            sortValue: (row) => invProjectByKey.get(row.projectKey)?.creatorCount ?? -1,
          },
        ] as ColumnDef<AdoptionProjectRow>[])
      : []),
  ];

  // Entrance: each block fades up as it mounts (macro-fed blocks stream in
  // when their data lands). Static variants — no re-run on data updates.
  const blockProps = {
    variants: TILE_VARIANTS,
    initial: 'hidden' as const,
    animate: 'show' as const,
  };

  return (
    <div className="page-fill">
      <div className="flex flex-col gap-6 flex-1 min-h-0">
        {/* Verdict + people funnel — the page states its own conclusion. */}
        <motion.div {...blockProps} className="chart-container">
          <div className="chart-header flex items-center justify-between gap-3">
            <h4>User Activity</h4>
            <span
              className="hidden font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--text-tertiary)] sm:block"
              title="Project git history and the user snapshot span the full persistent record. Config-tree metrics cover the full history of objects that still exist — deleted work is invisible (survivorship bias). Only the 72h pulse card uses the short audit window."
            >
              as of {fmtDate(nowMs)} · git + config history
            </span>
          </div>
          {isLoading && (
            <div className="border-b border-[var(--border-glass)] px-4 py-3">
              <ProgressIndicator lifecycle={lifecycle} compact={!!data} />
            </div>
          )}
          {error && !data && (
            <div className="px-4 py-3 text-sm text-[var(--neon-red)]">{error}</div>
          )}
          {(invState.loading || evState.loading) && (
            <div className="border-b border-[var(--border-glass)] px-4 py-2 text-[11px] text-[var(--text-tertiary)]">
              {invState.loading && 'Walking the config tree for object history… '}
              {evState.loading && 'Reading the audit tail for recent activity…'}
            </div>
          )}
          {!invState.loading && (invState.error || invState.data?.error) && (
            <div className="border-b border-[var(--border-glass)] px-4 py-2 text-[11px] text-[var(--text-tertiary)]">
              Config inventory unavailable: {invState.error || invState.data?.error}
            </div>
          )}
          {!evState.loading && (evState.error || evState.data?.error) && (
            <div className="border-b border-[var(--border-glass)] px-4 py-2 text-[11px] text-[var(--text-tertiary)]">
              Recent activity unavailable: {evState.error || evState.data?.error}
            </div>
          )}
          {verdict.length > 0 && (
            <div className="border-b border-[var(--border-glass)] px-4 py-3 text-[13px] leading-relaxed text-[var(--text-secondary)] [&_strong]:font-semibold [&_strong]:text-[var(--text-primary)]">
              {verdict}
            </div>
          )}
          <div className="flex flex-wrap items-end gap-x-6 gap-y-4 px-4 py-4">
            {/* People funnel — one reconciled account of "how many people".
                The arrow chain only appears when the subset actually holds
                (departed builders can outnumber current accounts). */}
            <div className="flex items-end gap-3">
              {buildersAreSubset && (
                <>
                  <div title="Accounts in the current user snapshot.">
                    <BigStat value={accountCount || '—'} label="Accounts" />
                  </div>
                  <span className="pb-3 font-mono text-[var(--text-muted)]">→</span>
                </>
              )}
              <div title="Distinct people with at least one git commit in any project, all time — includes builders whose account has since been removed.">
                <BigStat
                  value={totals ? totals.builderCount : '—'}
                  label="Ever built (all time)"
                  sub={
                    buildersAreSubset && totals && accountCount > 0
                      ? pctLabel(totals.builderCount, accountCount)
                      : undefined
                  }
                />
              </div>
              {!buildersAreSubset && (
                <>
                  <span className="pb-3 font-mono text-[var(--text-muted)]">·</span>
                  <div title="Accounts in the current user snapshot — smaller than all-time builders when people have left.">
                    <BigStat value={accountCount || '—'} label="Accounts exist today" />
                  </div>
                </>
              )}
            </div>
            <div className="hidden h-8 w-px bg-[var(--border-glass)] sm:block" />
            <div
              title={`Human commit volume, last ${MOMENTUM_MONTHS} complete months vs the ${MOMENTUM_MONTHS} before. The percentage is suppressed when the prior-year base is under ${'50'} commits — a % over a tiny base is a coin flip, not a trend.`}
            >
              <BigStat
                value={
                  momentumPct != null
                    ? `${momentumPct >= 0 ? '+' : ''}${momentumPct.toFixed(0)}%`
                    : commitsRecent12 != null && commitsPrior12 != null
                      ? `${commitsPrior12.toLocaleString()}→${commitsRecent12.toLocaleString()}`
                      : '—'
                }
                label="Commits — 12mo vs prior"
                sub={
                  momentumPct != null && commitsRecent12 != null && commitsPrior12 != null
                    ? `${commitsPrior12.toLocaleString()} → ${commitsRecent12.toLocaleString()}`
                    : undefined
                }
                tone={
                  momentumPct == null
                    ? undefined
                    : momentumPct >= 2
                      ? 'ok'
                      : momentumPct <= -2
                        ? 'warn'
                        : undefined
                }
              />
            </div>
            <div
              title={`Projects with a git commit within ${totals?.inactiveThresholdDays ?? '—'} days vs all projects.`}
            >
              <BigStat
                value={totals ? `${totals.activeProjectCount}/${totals.projectCount}` : '—'}
                label={`Active projects (${totals?.inactiveThresholdDays ?? '—'}d)`}
              />
            </div>
            {repeatBuilders && repeatBuilders.total > 0 && (
              <div title="Builders active in at least two distinct months — they came back after their first build.">
                <BigStat
                  value={`${repeatBuilders.repeat} of ${repeatBuilders.total}`}
                  label="Builders returned (≥2 months)"
                  sub={pctLabel(repeatBuilders.repeat, repeatBuilders.total)}
                />
              </div>
            )}
          </div>
        </motion.div>

        {/* ── 01 · Is it being used? ─────────────────────────────────────── */}
        <motion.div {...blockProps} className="flex flex-col gap-4">
          <ChapterHeader
            no="01"
            title="Is it being used?"
            answer={ch1Answer}
            caption="full git history · cumulative curves include the running month"
          />
          {/* Flagship: cumulative adoption — only goes up, at different speeds. */}
          <div className="chart-container">
            <div className="chart-header flex items-center justify-between gap-3">
              <h4 title="Running totals from each project's full git history: distinct people who ever committed, projects ever touched, and commit volume. Cumulative lines include the running month honestly — the last point is simply mid-climb.">
                Cumulative adoption — people, projects &amp; commits
              </h4>
            </div>
            <CumulativeAdoptionChart points={cumulativePoints} />
          </div>
          {pulse && <PulseCard pulse={pulse} nowMs={nowMs} />}
        </motion.div>

        {/* ── 02 · What's being made, and by whom? ───────────────────────── */}
        {inventoryView && (
          <motion.div {...blockProps} className="flex flex-col gap-4">
            <ChapterHeader
              no="02"
              title="What's being made, and by whom?"
              answer={ch2Answer}
              caption={`surviving objects only — deleted work is invisible · ${inventoryView.taggedObjects.toLocaleString()} of ${inventoryView.objectsBuilt.toLocaleString()} carry creator tags`}
            />
            {/* What gets built — one cumulative small multiple per family, no
                combined chart and no "Other" bucket: every family is named. */}
            {detailCharts.length > 0 && (
              <div className="chart-container">
                <div className="chart-header flex items-center justify-between gap-3">
                  <h4
                    title={`Cumulative surviving objects across the whole config history, one curve per family. Counts tagged objects only — ${inventoryView.taggedObjects.toLocaleString()} of ${inventoryView.objectsBuilt.toLocaleString()} objects carry creation tags; untagged families (scenarios, notebooks, wikis…) appear in totals, never in these curves.`}
                  >
                    What gets built here — cumulative, by family
                  </h4>
                </div>
                <div className="grid grid-cols-1 gap-x-6 gap-y-4 px-4 py-4 sm:grid-cols-2 lg:grid-cols-3">
                  {detailCharts.map((d) => {
                    // Every curve says what it counts — "Uncategorized"
                    // especially: list the actual subtypes on hover.
                    const subtypes = inventoryView.composition
                      .filter((row) => d.group.families.includes(row.family))
                      .flatMap((row) => row.topSubtypes)
                      .sort((a, b) => b.count - a.count)
                      .slice(0, 6);
                    const subtypeHint =
                      subtypes.length > 0
                        ? `contains: ${subtypes.map((s) => `${s.subtype} (${s.count})`).join(', ')}`
                        : undefined;
                    return (
                      <div key={d.group.key}>
                        <div className="mb-1.5 flex items-baseline justify-between gap-2">
                          <span
                            className="inline-flex items-center gap-1.5 text-[10px] uppercase tracking-[0.1em] text-[var(--text-secondary)]"
                            title={subtypeHint}
                          >
                            <span
                              className="h-1.5 w-1.5 rounded-[2px]"
                              style={{ background: d.color }}
                            />
                            {d.group.label}
                          </span>
                          <span className="font-mono text-[10px] tabular-nums text-[var(--text-tertiary)]">
                            {d.total.toLocaleString()} all time
                          </span>
                        </div>
                        <MiniCumulative
                          values={d.values}
                          months={creationMonthsAxis}
                          color={d.color}
                          unitLabel={d.group.label.toLowerCase()}
                        />
                      </div>
                    );
                  })}
                </div>
                <div className="border-t border-[var(--border-glass)] px-4 py-2 text-[10px] text-[var(--text-tertiary)]">
                  surviving tagged objects only ({inventoryView.taggedObjects.toLocaleString()} of{' '}
                  {inventoryView.objectsBuilt.toLocaleString()}) — deleted work is invisible
                  {detailEmpty.length > 0 &&
                    ` · no tagged creations yet: ${detailEmpty.map((d) => d.group.label.toLowerCase()).join(', ')}`}
                </div>
              </div>
            )}
            <div className="chart-container">
              <div className="chart-header flex items-center justify-between gap-3">
                <h4 title="Top 10 creators per family, from config-history creation tags — surviving objects only, service accounts excluded. Bar widths are relative within each family.">
                  Top builders — by family
                </h4>
              </div>
              <div className="px-4 py-3">
                <div className="grid grid-cols-1 items-start gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                  {inventoryView.topCreatorsByGroup
                    .map((board, gi) => ({
                      board,
                      group: DETAIL_GROUPS[gi],
                      color: DETAIL_GROUP_COLORS[gi],
                    }))
                    .filter(({ board }) => board.creators.length > 0)
                    .map(({ board, group, color }) => {
                      const max = Math.max(1, ...board.creators.map((c) => c.created));
                      return (
                        <div key={board.key}>
                          <div className="mb-1 inline-flex items-center gap-1.5 text-[10px] uppercase tracking-[0.1em] text-[var(--text-secondary)]">
                            <span
                              className="h-1.5 w-1.5 rounded-[2px]"
                              style={{ background: color }}
                            />
                            {group.label}
                          </div>
                          <div className="space-y-0.5">
                            {board.creators.map((c) => (
                              <div
                                key={c.login}
                                className="adk-hover-row -mx-1 flex items-center gap-2 px-1 py-0.5"
                                title={`${c.created.toLocaleString()} surviving ${group.label.toLowerCase()} created by ${c.login}`}
                              >
                                <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-[var(--text-secondary)]">
                                  {c.login}
                                </span>
                                <span className="h-1 w-14 flex-shrink-0 overflow-hidden rounded-full bg-[var(--bg-elevated)]">
                                  <span
                                    className="block h-full rounded-full transition-[width] duration-500"
                                    style={{
                                      width: `${(c.created / max) * 100}%`,
                                      background: color,
                                    }}
                                  />
                                </span>
                                <span className="w-12 flex-shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-primary)]">
                                  {c.created.toLocaleString()}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      );
                    })}
                </div>
              </div>
            </div>
          </motion.div>
        )}

        {/* Groups + onboarding — who the activity belongs to, how people start.
            Full-width rows: both cards carry per-row detail that was cramped
            at half width. */}
        <div className="flex flex-col gap-6">
          <motion.div {...blockProps} className="chart-container">
            <div className="chart-header flex items-center justify-between gap-3">
              <h4 title="Git activity rolled up to DSS groups. A builder in several groups counts in each, so shares can overlap. The sparkline is the group's monthly commits over the last complete year.">
                Most active groups
              </h4>
            </div>
            <div className="px-4 py-3">
              {topGroups.length === 0 ? (
                <div className="text-xs text-[var(--text-muted)]">No group activity yet.</div>
              ) : topGroups.length === 1 ? (
                // One active group is a fact, not a ranking — say it compactly.
                <div className="text-[12px] leading-relaxed text-[var(--text-secondary)]">
                  Git activity concentrates in one group:{' '}
                  <span className="font-mono text-[var(--text-primary)]">{topGroups[0].name}</span>{' '}
                  — {topGroups[0].builderCount} of {topGroups[0].memberCount} members building
                  across {topGroups[0].projectCount}{' '}
                  {topGroups[0].projectCount === 1 ? 'project' : 'projects'}, last active{' '}
                  {relDays(topGroups[0].lastCommitMs, nowMs).text}.
                  {quietGroups > 0 && (
                    <span className="text-[var(--text-tertiary)]">
                      {' '}
                      {quietGroups} other {quietGroups === 1 ? 'group has' : 'groups have'} no git
                      activity yet.
                    </span>
                  )}
                </div>
              ) : (
                <div className="space-y-2">
                  {topGroups.map((g) => {
                    const sparkCols: ColPoint[] = sparkAxis.map((month) => ({
                      key: month,
                      value: g.monthlyCommits?.[month] ?? 0,
                      title: `${monthLabel(month)} · ${(g.monthlyCommits?.[month] ?? 0).toLocaleString()} commits by ${g.name}`,
                    }));
                    return (
                      <div key={g.name} className="adk-hover-row -mx-1 px-1 py-1">
                        <div className="flex items-center gap-2">
                          <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--text-secondary)]">
                            {g.name}
                            <span className="ml-1.5 text-[10px] text-[var(--text-tertiary)]">
                              {g.builderCount}/{g.memberCount} building · {g.projectCount}{' '}
                              {g.projectCount === 1 ? 'project' : 'projects'} ·{' '}
                              {relDays(g.lastCommitMs, nowMs).text}
                            </span>
                          </span>
                          <span
                            className="w-10 flex-shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-tertiary)]"
                            title="Share of all human commits (group shares can overlap)."
                          >
                            {pctLabel(g.commits, totalGroupCommits)}
                          </span>
                          <span className="w-14 flex-shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-primary)]">
                            {g.commits.toLocaleString()}
                          </span>
                        </div>
                        {sparkCols.length > 0 && (
                          <div className="mt-1 max-w-[240px]">
                            <MiniColumns
                              points={sparkCols}
                              height={20}
                              gap={2}
                              showValues={false}
                            />
                          </div>
                        )}
                      </div>
                    );
                  })}
                  {(activeGroups.length > topGroups.length || quietGroups > 0) && (
                    <div className="pt-0.5 text-[10px] text-[var(--text-tertiary)]">
                      {activeGroups.length > topGroups.length &&
                        `+${activeGroups.length - topGroups.length} more active ${activeGroups.length - topGroups.length === 1 ? 'group' : 'groups'}`}
                      {activeGroups.length > topGroups.length && quietGroups > 0 && ' · '}
                      {quietGroups > 0 &&
                        `${quietGroups} ${quietGroups === 1 ? 'group has' : 'groups have'} no git activity yet`}
                    </div>
                  )}
                </div>
              )}
            </div>
          </motion.div>

          <motion.div {...blockProps} className="chart-container">
            <div className="chart-header flex items-center justify-between gap-3">
              <h4 title="New accounts per quarter (from each user's creationDate) with the median days from signup to a first surviving build for that cohort. Complete quarters only; cohorts predating the surviving config history are excluded rather than measured dishonestly.">
                Onboarding &amp; activation — quarterly
              </h4>
              {showTtfb && ttfb && (
                <span className="flex flex-wrap items-center gap-x-2 font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
                  <span>
                    median {ttfb.overallMedianDays ?? '—'}d to first build · {ttfb.usersMeasured}{' '}
                    users measured
                  </span>
                  {ttfbDirection && ttfbFirst != null && ttfbLast != null && (
                    <span
                      className={
                        ttfbDirection === 'faster'
                          ? 'text-[var(--neon-green)]'
                          : ttfbDirection === 'slower'
                            ? 'text-[var(--neon-amber)]'
                            : ''
                      }
                      title={`Median days from signup to first build moved from ${ttfbFirst}d (earliest measured cohort) to ${ttfbLast}d (latest). Down is the win: new people reach their first build sooner.`}
                    >
                      {ttfbDirection === 'faster'
                        ? `↓ ${ttfbFirst}d → ${ttfbLast}d — onboarding got faster (good)`
                        : ttfbDirection === 'slower'
                          ? `↑ ${ttfbFirst}d → ${ttfbLast}d — onboarding got slower`
                          : `→ steady at ${ttfbLast}d`}
                    </span>
                  )}
                </span>
              )}
            </div>
            {onboardingPoints.length === 0 ? (
              <div className="px-4 py-4 text-xs text-[var(--text-muted)]">
                No user creation dates.
              </div>
            ) : onboardingAsChart ? (
              <OnboardingChart points={onboardingPoints} showTtfb={showTtfb} />
            ) : (
              // Too few accounts/quarters for slab bars to mean anything —
              // state the facts in a sentence instead.
              <div className="px-4 py-4 text-[12px] leading-relaxed text-[var(--text-secondary)]">
                <strong className="text-[var(--text-primary)]">{onboardingTotal}</strong>{' '}
                {onboardingTotal === 1 ? 'account' : 'accounts'} created across{' '}
                {onboardingPoints.length} complete{' '}
                {onboardingPoints.length === 1 ? 'quarter' : 'quarters'}
                {onboardingTotal > 0 && (
                  <>
                    {' '}
                    (
                    {onboardingPoints
                      .filter((p) => p.newUsers > 0)
                      .map((p) => `${quarterLabel(p.quarter)}: ${p.newUsers}`)
                      .join(' · ')}
                    )
                  </>
                )}
                {' — '}too few for a stable activation chart.
              </div>
            )}
            <div className="border-t border-[var(--border-glass)] px-4 py-2 text-[10px] text-[var(--text-tertiary)]">
              complete quarters only
              {cohortsThisQuarter > 0 &&
                ` · +${cohortsThisQuarter} new ${cohortsThisQuarter === 1 ? 'account' : 'accounts'} so far in ${quarterLabel(currentQuarterKey)}`}
              {ttfb &&
                ttfb.excludedCohorts > 0 &&
                ` · ${ttfb.excludedCohorts} older ${ttfb.excludedCohorts === 1 ? 'cohort' : 'cohorts'} excluded (predate surviving history)`}
              {ttfb &&
                !showTtfb &&
                ` · time-to-first-build hidden (only ${ttfb.usersMeasured} measured ${ttfb.usersMeasured === 1 ? 'user' : 'users'} — needs ≥${MIN_TTFB_USERS})`}
            </div>
          </motion.div>
        </div>

        {/* ── 03 · What's at risk? ───────────────────────────────────────── */}
        {busFactor && busFactor.measuredProjects > 0 && (
          <motion.div {...blockProps} className="flex flex-col gap-4">
            <ChapterHeader
              no="03"
              title="What's at risk?"
              answer={ch3Answer}
              caption="creator joins from surviving objects only"
            />
            <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
              {busFactor && busFactor.measuredProjects > 0 && (
                <div className="chart-container">
                  <div className="chart-header flex items-center justify-between gap-3">
                    <h4 title="How many distinct creators each project's surviving objects have. Single-creator projects lose all context if that person leaves — expected to skew single on small teams, so this only alarms at ≥10 measured projects.">
                      Single-author concentration
                    </h4>
                  </div>
                  <div className="px-4 py-3">
                    <div className="mb-3 flex items-baseline gap-3">
                      <BigStat
                        value={singleSharePct == null ? '—' : `${singleSharePct}%`}
                        label="Single-creator projects"
                        tone={concentrationWarn ? 'warn' : undefined}
                      />
                      <span className="text-[10px] text-[var(--text-tertiary)]">
                        of {busFactor.measuredProjects} projects with tagged objects
                        {!concentrationWarn && busFactor.measuredProjects < 10
                          ? ' — small sample, expected to skew single'
                          : ''}
                      </span>
                    </div>
                    <LinkedMix items={busItems} />
                  </div>
                </div>
              )}
            </div>
          </motion.div>
        )}

        {/* Receipts: the drill-down project table, deliberately last. */}
        <motion.div {...blockProps} className="flex flex-col gap-3">
          <DataGrid
            title="Projects — people & activity"
            countBadge={{
              total: projects.length,
              filtered: gridRows.length < projects.length ? gridRows.length : undefined,
            }}
            headerExtra={inventoryView ? <FamilyGroupLegend /> : undefined}
            lifecycle={isLoading ? lifecycle : null}
            rows={gridRows}
            columns={columns}
            rowKey={(row) => row.projectKey}
            defaultSortColumnId="commits"
            defaultSortDir="desc"
            renderExpandedRow={(row) => (
              <ProjectAuthorsPanel
                row={row}
                inv={invProjectByKey.get(row.projectKey)}
                invNowMs={invNowMs}
              />
            )}
            expandedRowKeys={expandedRowKeys}
            emptyMessage="Waiting for git history…"
            scroll="card"
          />
          {projects.length > topProjects.length && (
            <button
              type="button"
              onClick={() => setShowAllProjects((v) => !v)}
              className="self-start rounded border border-[var(--border-glass)] bg-[var(--bg-elevated)] px-2.5 py-1 text-[11px] text-[var(--text-secondary)] transition-colors hover:border-[var(--border-strong,var(--border-default))] hover:text-[var(--text-primary)]"
            >
              {showAllProjects
                ? 'Show top 20 by commits'
                : `Show all ${projects.length.toLocaleString()} projects`}
            </button>
          )}
        </motion.div>
      </div>
    </div>
  );
}
