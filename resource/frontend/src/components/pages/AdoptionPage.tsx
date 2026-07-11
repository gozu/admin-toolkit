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
  familyGroupIndex,
  fillQuarterRange,
  monthToQuarter,
  quarterKeyUTC,
  quarterLabel,
  MATURITY_DIMENSIONS,
  TREND_GROUPS,
  TREND_GROUP_COLORS,
  type InventoryProjectViewRow,
} from '../../utils/inventoryData';
import { DataGrid } from '../common/DataGrid';
import { ProgressIndicator } from '../common/ProgressIndicator';
import { BigStat, SegmentBar, UsageBar } from './missionControl/microViz';
import { TILE_VARIANTS } from './missionControl/tokens';
import { EngagementTrendChart } from './EngagementTrendChart';
import { BuilderLifecycleChart, type LifecycleQuarterPoint } from './BuilderLifecycleChart';
import { MonthlyCreationChart } from './MonthlyCreationChart';
import { OnboardingChart, type OnboardingQuarterPoint } from './OnboardingChart';
import type { ColumnDef } from '../../utils/dataGridTypes';
import type {
  AdoptionLicensing,
  AdoptionMonthPoint,
  AdoptionProjectRow,
  AdoptionPulseData,
} from '../../types';
import './adoption.css';

const EMPTY: never[] = [];
const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;

// Rate charts show bounded recent windows — cumulative context lives in the
// running totals, not in always-up curves.
const CREATION_MONTHS = 24;
const SPARK_MONTHS = 12;
const LIFECYCLE_QUARTERS = 12;
// TTFB is hidden below this many measured users — "0d median, 2 users" is
// noise dressed as a stat.
const MIN_TTFB_USERS = 5;
// "Recently active" window for the funnel and idle-seat detection.
const ACTIVE_DAYS = 90;
// Momentum compares mean active builders over this many complete months vs
// the same span before.
const MOMENTUM_MONTHS = 12;
// A licensed cap this large (or ≥20× assigned seats) is a sentinel, not a
// budget — rendering "3 / 10,000" reads as shelfware and misleads.
const UNMETERED_CAP = 1000;
// Below these floors, onboarding renders a sentence instead of slab bars.
const MIN_ONBOARDING_QUARTERS = 3;
const MIN_ONBOARDING_USERS = 5;

/** Chapter header: the page reads as three questions, not eight coequal
 * sections. Bigger than a card h4, quieter than the page title. */
function ChapterHeader({
  no,
  title,
  caption,
  right,
}: {
  no: string;
  title: string;
  caption?: string;
  right?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-[var(--border-glass)] pb-2">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-0.5">
        <span className="font-mono text-[10px] tracking-[0.2em] text-[var(--text-muted)]">
          {no}
        </span>
        <h3 className="text-[14px] font-semibold text-[var(--text-primary)]">{title}</h3>
        {caption && <span className="text-[10px] text-[var(--text-tertiary)]">{caption}</span>}
      </div>
      {right}
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

/** 'FULL_DESIGNER' → 'Full Designer'. */
function prettyProfile(profile: string): string {
  return profile
    .toLowerCase()
    .split(/[_\s]+/)
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(' ');
}

function isDesignerProfile(profile: string): boolean {
  const p = profile.toUpperCase();
  return p.includes('DESIGNER') || p === 'DATA_SCIENTIST';
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
    label: 'Building (saves & creates)',
    color: 'var(--viz-cat-1)',
    hint: 'Config writes: object creation, edits, settings saves.',
  },
  {
    key: 'run',
    label: 'Running (jobs · scenarios · macros)',
    color: 'var(--viz-cat-3)',
    hint: 'Compute actually executed: flow jobs, scenario runs, macro runs.',
  },
  {
    key: 'explore',
    label: 'Exploring (flows, datasets, search)',
    color: 'var(--viz-cat-5)',
    hint: 'Reads and navigation: browsing flows, previewing data, catalog.',
  },
  {
    key: 'consume',
    label: 'Consuming (dashboards & exports)',
    color: 'var(--viz-cat-4)',
    hint: 'Value leaving the platform: dashboards viewed, exports, downloads.',
  },
  {
    key: 'other',
    label: 'Other events',
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
              {pulse.humansActive != null && (
                <BigStat value={pulse.humansActive} label="People active" />
              )}
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

/** License reality: assigned seats per profile, with sentinel caps rendered as
 * "unmetered" (never "3 / 10,000" shelfware theater), zero rows collapsed,
 * plus designer seats nobody is using (downgrade candidates = money). */
function LicenseCard({
  licensing,
  profileCounts,
  creatorsByProfile,
  idleDesigners,
  hasInventory,
  nowMs,
}: {
  licensing: AdoptionLicensing;
  profileCounts: Record<string, number>;
  creatorsByProfile: Map<string, number>;
  idleDesigners: Array<{ login: string; lastSessionMs: number | null }>;
  hasInventory: boolean;
  nowMs: number;
}) {
  const allRows = licensing.profiles
    .filter((p) => p.profile !== 'NONE')
    .map((p) => {
      const used = profileCounts[p.profile] ?? 0;
      const rawLimit = p.licensedLimit ?? null;
      // A cap of UNMETERED_CAP+ (or ≥20× the assigned seats) is a sentinel,
      // not a real budget — treat as unmetered.
      const metered =
        rawLimit != null &&
        rawLimit > 0 &&
        rawLimit < UNMETERED_CAP &&
        (used === 0 || rawLimit < used * 20);
      return {
        profile: p.profile,
        limit: metered ? rawLimit : null,
        unmetered: !metered && rawLimit != null && rawLimit !== 0,
        used,
        creators: creatorsByProfile.get(p.profile) ?? 0,
      };
    });
  const rows = allRows
    .filter((r) => r.used > 0 || r.creators > 0)
    .sort((a, b) => b.used - a.used || a.profile.localeCompare(b.profile));
  const emptyProfiles = allRows.length - rows.length;

  const expiresDays =
    licensing.expiresOnMs != null ? Math.floor((licensing.expiresOnMs - nowMs) / DAY_MS) : null;
  const expiryTone =
    licensing.expired || (expiresDays != null && expiresDays < 30)
      ? 'text-[var(--neon-red)]'
      : 'text-[var(--text-tertiary)]';

  return (
    <div className="chart-container">
      <div className="chart-header flex items-center justify-between gap-3">
        <h4 title="Seat assignment from the user snapshot, licensed caps from the DSS license. Caps of 1,000+ seats are sentinels ('effectively unlimited') and are shown as unmetered instead of fake percentages. Idle designer seats = designer-profile accounts with no surviving created object and no recent session.">
          License reality — seats assigned vs licensed
        </h4>
      </div>
      <div className="px-4 py-3">
        {rows.length === 0 ? (
          <div className="text-xs text-[var(--text-muted)]">No assigned profiles.</div>
        ) : (
          <div className="space-y-1.5">
            {rows.map((r) => {
              const pct = r.limit != null ? Math.min(100, (r.used / r.limit) * 100) : null;
              return (
                <div
                  key={r.profile}
                  className="adk-hover-row -mx-1 flex items-center gap-2 px-1 py-0.5"
                  title={
                    r.limit != null
                      ? `${r.used.toLocaleString()} of ${r.limit.toLocaleString()} licensed ${prettyProfile(r.profile)} seats assigned · ${r.creators} ever created a surviving object`
                      : `${r.used.toLocaleString()} ${prettyProfile(r.profile)} seats assigned (${r.unmetered ? 'unmetered license — cap is a sentinel' : 'no licensed cap'}) · ${r.creators} ever created a surviving object`
                  }
                >
                  <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--text-secondary)]">
                    {prettyProfile(r.profile)}
                    <span className="ml-1.5 text-[10px] text-[var(--text-tertiary)]">
                      {r.creators > 0
                        ? `${r.creators} ${r.creators === 1 ? 'creator' : 'creators'}`
                        : 'no creators'}
                    </span>
                  </span>
                  {pct != null && (
                    <span className="h-1 w-20 flex-shrink-0 overflow-hidden rounded-full bg-[var(--bg-elevated)]">
                      <span
                        className="block h-full rounded-full bg-[var(--accent)] transition-[width] duration-500"
                        style={{ width: `${Math.max(pct, r.used > 0 ? 2 : 0)}%` }}
                      />
                    </span>
                  )}
                  <span className="w-28 flex-shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-primary)]">
                    {r.used.toLocaleString()}
                    {r.limit != null
                      ? ` / ${r.limit.toLocaleString()}`
                      : r.unmetered
                        ? ' · unmetered'
                        : ' · no cap'}
                  </span>
                </div>
              );
            })}
            {emptyProfiles > 0 && (
              <div className="pt-0.5 text-[10px] text-[var(--text-tertiary)]">
                +{emptyProfiles} licensed {emptyProfiles === 1 ? 'profile' : 'profiles'} with no
                assigned seats
              </div>
            )}
          </div>
        )}
        <div className="mt-3 flex items-baseline gap-3 border-t border-[var(--border-glass)] pt-3">
          <BigStat
            value={hasInventory ? idleDesigners.length : '—'}
            label="Idle designer seats"
            tone={hasInventory && idleDesigners.length > 0 ? 'warn' : undefined}
          />
          <span
            className="min-w-0 text-[10px] text-[var(--text-tertiary)]"
            title={
              idleDesigners.length > 0
                ? idleDesigners
                    .slice(0, 10)
                    .map((d) => d.login)
                    .join(', ') + (idleDesigners.length > 10 ? ', …' : '')
                : undefined
            }
          >
            {hasInventory
              ? `designer profile, zero surviving objects, no session in ${ACTIVE_DAYS}d — downgrade candidates`
              : 'needs the config inventory to spot idle designer seats'}
          </span>
        </div>
        <div className={`pt-2 text-[10px] ${expiryTone}`}>
          {licensing.licenseKind ?? 'license'} ·{' '}
          {licensing.expired
            ? 'EXPIRED'
            : licensing.expiresOnMs != null
              ? `expires ${fmtDate(licensing.expiresOnMs)}${expiresDays != null ? ` (${expiresDays}d)` : ''}`
              : 'no expiry date'}
          {!licensing.valid && !licensing.expired ? ' · license invalid' : ''}
        </div>
      </div>
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
  const builderMonthly = data?.builderMonthly;
  const licensing = data?.licensing ?? null;
  const profileCounts = data?.profileCounts ?? {};
  const peopleMax = Math.max(1, ...projects.map((p) => p.authorCount));
  const expandedRowKeys = new Set(selectedKey ? [selectedKey] : []);

  // Partial-period honesty: rate charts plot complete months/quarters only —
  // 10 days of July next to full months always reads as a collapse.
  const gitTrendComplete = completeMonthsOnly(trend, nowMs);

  // The people funnel — one reconciled account of "how many people":
  // accounts on the instance → ever built (git) → built in the last 90d.
  const accountCount = recency.length;
  const activeRecently = builders.filter(
    (b) => b.lastCommitMs != null && nowMs - b.lastCommitMs <= ACTIVE_DAYS * DAY_MS,
  ).length;

  // Momentum (customer's "is usage increasing?"): mean active builders over the
  // last 12 complete months vs the 12 before.
  let momentumPct: number | null = null;
  if (gitTrendComplete.length >= MOMENTUM_MONTHS * 2) {
    const mean = (pts: AdoptionMonthPoint[]) =>
      pts.reduce((s, p) => s + p.activeBuilders, 0) / pts.length;
    const recent = mean(gitTrendComplete.slice(-MOMENTUM_MONTHS));
    const prior = mean(gitTrendComplete.slice(-MOMENTUM_MONTHS * 2, -MOMENTUM_MONTHS));
    momentumPct = prior > 0 ? ((recent - prior) / prior) * 100 : null;
  }

  // Builder lifecycle by quarter: new / returning / lapsed, complete quarters
  // only, from per-builder monthly git activity.
  const currentQuarterKey = quarterKeyUTC(nowMs);
  const lifecyclePoints: LifecycleQuarterPoint[] = (() => {
    if (!builderMonthly) return [];
    const activeByQ = new Map<string, Set<string>>();
    const firstQ = new Map<string, string>();
    for (const [login, months] of Object.entries(builderMonthly)) {
      for (const m of Object.keys(months)) {
        const q = monthToQuarter(m);
        if (q >= currentQuarterKey) continue;
        let set = activeByQ.get(q);
        if (!set) {
          set = new Set();
          activeByQ.set(q, set);
        }
        set.add(login);
        const cur = firstQ.get(login);
        if (cur == null || q < cur) firstQ.set(login, q);
      }
    }
    if (activeByQ.size < 2) return [];
    const axis = fillQuarterRange([...activeByQ.keys()]).slice(-LIFECYCLE_QUARTERS);
    return axis.map((quarter) => {
      const active = activeByQ.get(quarter) ?? new Set<string>();
      const prev = activeByQ.get(prevQuarterKey(quarter)) ?? new Set<string>();
      let newBuilders = 0;
      for (const login of active) if (firstQ.get(login) === quarter) newBuilders++;
      let lapsed = 0;
      for (const login of prev) if (!active.has(login)) lapsed++;
      return { quarter, newBuilders, returning: active.size - newBuilders, lapsed };
    });
  })();

  // ── inventory-fed derivations (all null-safe) ─────────────────────────────
  const inventoryView = buildInventoryView(invState.data, recency);
  const invGeneratedMs = invState.data?.generatedAtMs ?? nowMs;
  const invTrendComplete = completeMonthsOnly(inventoryView?.trendPoints ?? EMPTY, invGeneratedMs);
  const creationPoints = invTrendComplete.slice(-CREATION_MONTHS);

  // Current surviving portfolio, rolled up to the family groups.
  const portfolioGroups: number[] = TREND_GROUPS.map(() => 0);
  for (const row of inventoryView?.composition ?? [])
    portfolioGroups[familyGroupIndex(row.family)] += row.count;
  const portfolioItems: MixItem[] = TREND_GROUPS.map((g, gi) => ({
    key: g.key,
    label: g.label,
    color: TREND_GROUP_COLORS[gi],
    value: portfolioGroups[gi],
  })).filter((it) => it.value > 0);

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

  // License joins: creators per profile + idle designer seats.
  const creatorsByProfile = new Map<string, number>();
  for (const row of inventoryView?.seatTypes ?? [])
    creatorsByProfile.set(row.profile, row.creators);
  const sessionRef = recency.reduce(
    (max, r) => (r.lastSessionActivity != null ? Math.max(max, r.lastSessionActivity) : max),
    0,
  );
  const idleDesigners = inventoryView
    ? recency
        .filter((r) => r.userProfile && isDesignerProfile(r.userProfile))
        .filter((r) => (inventoryView.inventory.creators[r.login]?.created ?? 0) === 0)
        .filter(
          (r) =>
            r.lastSessionActivity == null ||
            (sessionRef > 0 && sessionRef - r.lastSessionActivity > ACTIVE_DAYS * DAY_MS),
        )
        .map((r) => ({ login: r.login, lastSessionMs: r.lastSessionActivity ?? null }))
        .sort((a, b) => (a.lastSessionMs ?? 0) - (b.lastSessionMs ?? 0))
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
  const verdict: ReactNode[] = [];
  if (totals) {
    verdict.push(
      <span key="funnel">
        <strong>{totals.builderCount}</strong> of <strong>{accountCount || '—'}</strong> accounts
        have built on this instance, <strong>{activeRecently}</strong> of them in the last{' '}
        {ACTIVE_DAYS} days.
      </span>,
    );
    if (momentumPct != null) {
      verdict.push(
        <span key="momentum">
          {' '}
          Monthly building activity is{' '}
          <strong>
            {momentumPct >= 2 ? 'up' : momentumPct <= -2 ? 'down' : 'flat at'}{' '}
            {momentumPct >= 2 || momentumPct <= -2
              ? `${Math.abs(momentumPct).toFixed(0)}%`
              : `${momentumPct >= 0 ? '+' : ''}${momentumPct.toFixed(0)}%`}
          </strong>{' '}
          year over year.
        </span>,
      );
    }
    if (inventoryView) {
      verdict.push(
        <span key="objects">
          {' '}
          The platform holds <strong>{inventoryView.objectsBuilt.toLocaleString()}</strong>{' '}
          surviving objects across <strong>{totals.projectCount}</strong> projects
          {busFactor && busFactor.measuredProjects > 0 ? (
            <>
              ; <strong>{busFactor.singleCreator}</strong> of{' '}
              <strong>{busFactor.measuredProjects}</strong> measured projects rely on a single
              creator
            </>
          ) : null}
          .
        </span>,
      );
    }
  }

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
            {/* People funnel — one reconciled account of "how many people". */}
            <div className="flex items-end gap-3">
              <BigStat value={accountCount || '—'} label="Accounts" />
              <span className="pb-3 font-mono text-[var(--text-muted)]">→</span>
              <div title="Distinct people with at least one git commit in any project, all time.">
                <BigStat
                  value={totals ? totals.builderCount : '—'}
                  label="Ever built"
                  sub={
                    totals && accountCount > 0
                      ? pctLabel(totals.builderCount, accountCount)
                      : undefined
                  }
                />
              </div>
              <span className="pb-3 font-mono text-[var(--text-muted)]">→</span>
              <div title={`Builders with a git commit in the last ${ACTIVE_DAYS} days.`}>
                <BigStat
                  value={totals ? activeRecently : '—'}
                  label={`Built in last ${ACTIVE_DAYS}d`}
                  sub={
                    totals && totals.builderCount > 0
                      ? pctLabel(activeRecently, totals.builderCount)
                      : undefined
                  }
                />
              </div>
            </div>
            <div className="hidden h-8 w-px bg-[var(--border-glass)] sm:block" />
            <div
              title={`Mean monthly active builders, last ${MOMENTUM_MONTHS} complete months vs the ${MOMENTUM_MONTHS} before — the in-progress month is excluded.`}
            >
              <BigStat
                value={
                  momentumPct == null
                    ? '—'
                    : `${momentumPct >= 0 ? '+' : ''}${momentumPct.toFixed(0)}%`
                }
                label="Momentum (yr vs prior yr)"
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
                  value={`${repeatBuilders.repeat}/${repeatBuilders.total}`}
                  label="Builders who returned"
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
            caption="full git history · complete months & quarters only"
          />
          <div className="chart-container">
            <div className="chart-header flex items-center justify-between gap-3">
              <h4 title="Distinct people committing per complete month (top) and commit volume (bottom) — two aligned panels, one shared time axis, no dual-axis tricks. A decline is visible here; cumulative curves structurally hide it.">
                Building activity — people &amp; commits by month
              </h4>
            </div>
            <EngagementTrendChart points={gitTrendComplete} />
          </div>
          {lifecyclePoints.length >= 2 && (
            <div className="chart-container">
              <div className="chart-header flex items-center justify-between gap-3">
                <h4 title="Every active builder each quarter is either new (first-ever commit) or returning (had built before). Lapsed = built the previous quarter but not this one. Complete quarters only.">
                  Builder lifecycle — new, returning &amp; lapsed by quarter
                </h4>
              </div>
              <BuilderLifecycleChart points={lifecyclePoints} />
            </div>
          )}
          {pulse && <PulseCard pulse={pulse} nowMs={nowMs} />}
        </motion.div>

        {/* ── 02 · What's being made, and by whom? ───────────────────────── */}
        {inventoryView && (
          <motion.div {...blockProps} className="flex flex-col gap-4">
            <ChapterHeader
              no="02"
              title="What's being made, and by whom?"
              caption={`surviving objects only — deleted work is invisible · ${inventoryView.taggedObjects.toLocaleString()} of ${inventoryView.objectsBuilt.toLocaleString()} carry creator tags`}
              right={<FamilyGroupLegend />}
            />
            {creationPoints.length > 0 && (
              <div className="chart-container">
                <div className="chart-header flex items-center justify-between gap-3">
                  <h4 title="Objects created per complete month, stacked by family group — one shared axis, actual magnitudes. Counts tagged objects only; scenarios, notebooks and wikis carry no creation tag and appear in totals, never here.">
                    Objects created by month — last {creationPoints.length} complete months
                  </h4>
                </div>
                <MonthlyCreationChart points={creationPoints} />
              </div>
            )}
            <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
              <div className="chart-container">
                <div className="chart-header flex items-center justify-between gap-3">
                  <h4 title="Everything that exists on the instance today, by family group. A survivorship-biased snapshot by construction: deleted work is invisible.">
                    Current surviving portfolio
                  </h4>
                </div>
                <div className="px-4 py-3">
                  <div className="mb-3 flex items-baseline gap-4">
                    <BigStat
                      value={inventoryView.objectsBuilt.toLocaleString()}
                      label="Surviving objects"
                    />
                    <BigStat value={inventoryView.allTimeCreators} label="Human creators" />
                  </div>
                  <LinkedMix items={portfolioItems} />
                  {inventoryView.automationCreated > 0 && (
                    <div className="pt-2 text-[10px] text-[var(--text-tertiary)]">
                      +{inventoryView.automationCreated.toLocaleString()} objects created by
                      service accounts (excluded from creator counts)
                    </div>
                  )}
                </div>
              </div>
              <div className="chart-container">
                <div className="chart-header flex items-center justify-between gap-3">
                  <h4 title="Top creators per family group, from config-history creation tags — surviving objects only, service accounts excluded. Bar widths are relative within each family.">
                    Top builders — by family
                  </h4>
                </div>
                <div className="px-4 py-3">
                  <div className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
                    {inventoryView.topCreatorsByGroup.map((board, gi) => {
                      const group = TREND_GROUPS[gi];
                      const max = Math.max(1, ...board.creators.map((c) => c.created));
                      return (
                        <div key={board.key}>
                          <div className="mb-1 inline-flex items-center gap-1.5 text-[10px] uppercase tracking-[0.1em] text-[var(--text-secondary)]">
                            <span
                              className="h-1.5 w-1.5 rounded-[2px]"
                              style={{ background: TREND_GROUP_COLORS[gi] }}
                            />
                            {group.label}
                          </div>
                          {board.creators.length === 0 ? (
                            <div className="text-[10px] text-[var(--text-muted)]">
                              no tagged creations
                            </div>
                          ) : (
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
                                        background: TREND_GROUP_COLORS[gi],
                                      }}
                                    />
                                  </span>
                                  <span className="w-12 flex-shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-primary)]">
                                    {c.created.toLocaleString()}
                                  </span>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            </div>
          </motion.div>
        )}

        {/* Groups + onboarding — who the activity belongs to, how people start. */}
        <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
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
                <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
                  median {ttfb.overallMedianDays ?? '—'}d to first build · {ttfb.usersMeasured}{' '}
                  users measured
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
                {onboardingPoints.length} complete {onboardingPoints.length === 1 ? 'quarter' : 'quarters'}
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
        {(licensing || busFactor) && (
          <motion.div {...blockProps} className="flex flex-col gap-4">
            <ChapterHeader
              no="03"
              title="What's at risk?"
              caption="seat caps from the DSS license · creator joins from surviving objects only"
            />
            <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
              {licensing && (
                <LicenseCard
                  licensing={licensing}
                  profileCounts={profileCounts}
                  creatorsByProfile={creatorsByProfile}
                  idleDesigners={idleDesigners}
                  hasInventory={!!inventoryView}
                  nowMs={nowMs}
                />
              )}
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
