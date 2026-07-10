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
import { CumulativeCreationChart } from './CumulativeCreationChart';
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

// Rate charts (small multiples, group sparklines) show the recent era only —
// cumulative charts carry the full span.
const RHYTHM_MONTHS = 24;
const SPARK_MONTHS = 12;
// TTFB is hidden below this many measured users — "0d median, 2 users" is
// noise dressed as a stat.
const MIN_TTFB_USERS = 5;
// Recently-active window for the summary band + idle-seat detection.
const ACTIVE_DAYS = 90;

/** Thin section header: groups related cards under one title + one caveat
 * instead of stamping every card. */
function SectionHeader({
  title,
  caption,
  right,
}: {
  title: string;
  caption?: string;
  right?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-[var(--border-glass)] pb-2">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-0.5">
        <h3 className="text-[12px] font-semibold uppercase tracking-[0.14em] text-[var(--text-secondary)]">
          {title}
        </h3>
        {caption && <span className="text-[10px] text-[var(--text-tertiary)]">{caption}</span>}
      </div>
      {right}
    </div>
  );
}

/** The one family-group legend — same fixed colors everywhere a family mix
 * appears (cumulative chart, small multiples, grid bars). */
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
              className="min-w-0 flex-1 truncate text-center font-mono text-[8px] leading-none text-[var(--text-tertiary)]"
            >
              {p.label ?? ''}
            </span>
          ))}
        </div>
      )}
      {(axisLeft || axisRight) && (
        <div className="mt-1 flex items-center justify-between font-mono text-[8px] leading-none text-[var(--text-tertiary)]">
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

/** First ms of the month AFTER a 'YYYY-MM' key — cumulative cut points. */
function monthEndMsUTC(month: string): number {
  const y = Number(month.slice(0, 4));
  const m = Number(month.slice(5, 7));
  return m === 12 ? Date.UTC(y + 1, 0, 1) : Date.UTC(y, m, 1);
}

// Friendly aggregation of run-bucket msgTypes for the pulse card. Verified
// against live audit logs: flow-job-start / scenario-run / runnable-run are
// what actually appears — keep in sync with the macro's _BUCKET_RULES.
const RUN_KINDS: Array<{ label: string; match: (msgType: string) => boolean }> = [
  { label: 'jobs started', match: (t) => t === 'flow-job-start' || t === 'job-start' },
  {
    label: 'scenario runs',
    match: (t) => t.startsWith('scenario-run') || t === 'scenario-fire-trigger',
  },
  { label: 'macro runs', match: (t) => t === 'runnable-run' },
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

/** Recent-activity pulse: the one fast-moving card on an otherwise
 * slow-history page. Everything here is the MEASURED audit-tail window. */
function PulseCard({ pulse, nowMs }: { pulse: AdoptionPulseData; nowMs: number }) {
  const hours = pulse.hours ?? EMPTY;
  const runTypes = pulse.runTypes ?? {};
  const buckets = pulse.buckets ?? {};
  const totalEvents = Object.values(buckets).reduce((s, v) => s + v, 0);

  // Friendly "what ran" rows; anything unmatched folds into one honest rest row.
  const matched = new Set<string>();
  const runRows = RUN_KINDS.map((kind) => {
    let count = 0;
    for (const [msgType, n] of Object.entries(runTypes)) {
      if (kind.match(msgType)) {
        count += n;
        matched.add(msgType);
      }
    }
    return { label: kind.label, count };
  }).filter((r) => r.count > 0);
  const restRun = Object.entries(runTypes)
    .filter(([t]) => !matched.has(t))
    .reduce((s, [, n]) => s + n, 0);

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
        <h4 title="Reverse tail-scan of the newest audit files: human events over the past few hours/days. The window label is MEASURED from actual event timestamps — rotated audit files often cover less than asked.">
          Recent activity — what's been running
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
        <div className="grid grid-cols-1 gap-x-6 gap-y-3 px-4 py-4 lg:grid-cols-[220px_minmax(0,1fr)]">
          <div className="flex flex-col gap-3">
            <div className="flex items-baseline gap-4">
              <BigStat value={pulse.humansActive ?? 0} label="People active" />
              <BigStat value={totalEvents.toLocaleString()} label="Human events" />
            </div>
            <div className="space-y-0.5">
              {runRows.map((r) => (
                <div
                  key={r.label}
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
              {(buckets.build ?? 0) > 0 && (
                <div className="adk-hover-row -mx-1 flex items-center justify-between gap-2 px-1 py-0.5 text-[11px]">
                  <span className="text-[var(--text-secondary)]">
                    config writes (saves/creates)
                  </span>
                  <span className="font-mono tabular-nums text-[var(--text-primary)]">
                    {(buckets.build ?? 0).toLocaleString()}
                  </span>
                </div>
              )}
            </div>
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

/** License utilization: licensed seat caps vs actual profile assignment, plus
 * designer seats nobody is using (downgrade candidates = money). */
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
  const rows = licensing.profiles
    .filter((p) => p.profile !== 'NONE')
    .map((p) => ({
      profile: p.profile,
      limit: p.licensedLimit ?? null,
      used: profileCounts[p.profile] ?? 0,
      creators: creatorsByProfile.get(p.profile) ?? 0,
    }))
    .filter((r) => r.used > 0 || (r.limit != null && r.limit > 0))
    .sort((a, b) => b.used - a.used || a.profile.localeCompare(b.profile));

  const expiresDays =
    licensing.expiresOnMs != null ? Math.floor((licensing.expiresOnMs - nowMs) / DAY_MS) : null;
  const expiryTone =
    licensing.expired || (expiresDays != null && expiresDays < 30)
      ? 'text-[var(--neon-red)]'
      : 'text-[var(--text-tertiary)]';

  return (
    <div className="chart-container">
      <div className="chart-header flex items-center justify-between gap-3">
        <h4 title="Licensed seat caps from the DSS license crossed with actual profile assignment (user snapshot) and config-history creators. Idle designer seats = designer-profile accounts with no surviving created object and no recent session.">
          License utilization — seats vs reality
        </h4>
      </div>
      <div className="px-4 py-3">
        {rows.length === 0 ? (
          <div className="text-xs text-[var(--text-muted)]">No profile data.</div>
        ) : (
          <div className="space-y-1.5">
            {rows.map((r) => {
              const capped = r.limit != null && r.limit > 0;
              const pct = capped ? Math.min(100, (r.used / (r.limit as number)) * 100) : null;
              return (
                <div
                  key={r.profile}
                  className="adk-hover-row -mx-1 flex items-center gap-2 px-1 py-0.5"
                  title={
                    capped
                      ? `${r.used.toLocaleString()} of ${(r.limit as number).toLocaleString()} licensed ${prettyProfile(r.profile)} seats assigned · ${r.creators} ever created a surviving object`
                      : `${r.used.toLocaleString()} ${prettyProfile(r.profile)} seats assigned (no licensed cap) · ${r.creators} ever created a surviving object`
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
                  <span className="w-24 flex-shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-primary)]">
                    {r.used.toLocaleString()}
                    {capped ? ` / ${(r.limit as number).toLocaleString()}` : ' · no cap'}
                  </span>
                </div>
              );
            })}
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
  // Reference "now": the payload's own timestamp (never the wall clock — the
  // React Compiler purity rule bans Date.now() in render, and server time is
  // the honest reference anyway). Falls back across the three payloads.
  const nowMs =
    data?.generatedAtMs ?? evState.data?.generatedAtMs ?? invState.data?.generatedAtMs ?? 0;
  const projects = data?.projectRows ?? EMPTY;
  const groups = data?.groups ?? EMPTY;
  const builders = data?.builderStats ?? EMPTY;
  const licensing = data?.licensing ?? null;
  const profileCounts = data?.profileCounts ?? {};
  const peopleMax = Math.max(1, ...projects.map((p) => p.authorCount));
  const expandedRowKeys = new Set(selectedKey ? [selectedKey] : []);

  // Partial-period honesty: RATE charts plot complete months/quarters only —
  // 10 days of July next to full months always reads as a collapse. CUMULATIVE
  // charts include the running month honestly (the last point is mid-climb;
  // an only-goes-up line can never fake a decline).
  const currentMonthKey = monthKeyUTC(nowMs);
  const gitTrendComplete = completeMonthsOnly(trend, nowMs);

  // Flagship cumulative series: people who ever built / projects ever active /
  // total commits, month by month (current month included).
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

  // Momentum (customer's "is usage increasing?"): mean active builders over the
  // last 3 complete months vs the 3 before.
  let momentumPct: number | null = null;
  if (gitTrendComplete.length >= 6) {
    const mean = (pts: AdoptionMonthPoint[]) =>
      pts.reduce((s, p) => s + p.activeBuilders, 0) / pts.length;
    const recent = mean(gitTrendComplete.slice(-3));
    const prior = mean(gitTrendComplete.slice(-6, -3));
    momentumPct = prior > 0 ? ((recent - prior) / prior) * 100 : null;
  }

  const activeBuilders90d = builders.filter(
    (b) => b.lastCommitMs != null && nowMs - b.lastCommitMs <= ACTIVE_DAYS * DAY_MS,
  ).length;

  // ── inventory-fed derivations (all null-safe) ─────────────────────────────
  const inventoryView = buildInventoryView(invState.data, recency);
  const invGeneratedMs = invState.data?.generatedAtMs ?? nowMs;
  const invTrendComplete = completeMonthsOnly(inventoryView?.trendPoints ?? EMPTY, invGeneratedMs);
  const rhythmPoints = invTrendComplete.slice(-RHYTHM_MONTHS);

  const busFactor = inventoryView?.busFactor;
  const singleSharePct =
    busFactor && busFactor.measuredProjects > 0
      ? Math.round((busFactor.singleCreator / busFactor.measuredProjects) * 100)
      : null;
  const busItems: MixItem[] = busFactor
    ? [
        {
          key: 'single',
          label: 'Single creator',
          value: busFactor.singleCreator,
          color: 'var(--neon-red)',
          hint: 'All context leaves with one person.',
        },
        {
          key: 'few',
          label: '2–3 creators',
          value: busFactor.twoToThree,
          color: 'var(--neon-amber)',
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

  // Designer seat use for the summary band: assigned vs licensed, over capped
  // designer profiles only.
  let designerSeatUse: { used: number; limit: number } | null = null;
  if (licensing) {
    let used = 0;
    let limit = 0;
    for (const p of licensing.profiles) {
      if (!isDesignerProfile(p.profile)) continue;
      if (p.licensedLimit == null || p.licensedLimit <= 0) continue;
      used += profileCounts[p.profile] ?? 0;
      limit += p.licensedLimit;
    }
    if (limit > 0) designerSeatUse = { used, limit };
  }

  // Onboarding trimesters: cohorts + TTFB share one quarter axis. Complete
  // quarters only; the running quarter is footnoted, never plotted.
  const currentQuarterKey = quarterKeyUTC(nowMs);
  const prevQuarterKey = (() => {
    const y = Number(currentQuarterKey.slice(0, 4));
    const q = Number(currentQuarterKey.slice(6));
    return q === 1 ? `${y - 1}-Q4` : `${y}-Q${q - 1}`;
  })();
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
      ? fillQuarterRange([...cohortQuarterCounts.keys(), prevQuarterKey])
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

  // Groups — expanded: share of commits, membership, and a per-group rhythm
  // sparkline on the same complete-month axis.
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
              className={`font-medium ${row.active ? 'text-[var(--neon-green)]' : 'text-[var(--text-secondary)]'}`}
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
            <UsageBar pct={(row.authorCount / peopleMax) * 100} tone="info" />
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
        {/* Summary band */}
        <motion.div {...blockProps} className="chart-container">
          <div className="chart-header flex items-center justify-between gap-3">
            <h4>Adoption &amp; Engagement</h4>
            <span
              className="hidden font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--text-tertiary)] sm:block"
              title="Project git history and the user snapshot span the full persistent record. Config-tree metrics cover the full history of objects that still exist — deleted work is invisible (survivorship bias). Only the recent-activity card uses the short audit window."
            >
              git + config history · audit tail for recent only
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
          <div
            className={`grid grid-cols-2 gap-4 px-4 py-4 sm:grid-cols-4 ${inventoryView ? 'lg:grid-cols-8' : 'lg:grid-cols-5'}`}
          >
            <BigStat
              value={totals ? `${totals.activeProjectCount}/${totals.projectCount}` : '—'}
              label={
                totals
                  ? `Active / total (${totals.inactiveThresholdDays}d)`
                  : 'Active / total projects'
              }
            />
            <BigStat value={totals ? totals.builderCount : '—'} label="Builders (all time)" />
            <BigStat
              value={data ? activeBuilders90d : '—'}
              label={`Active builders (${ACTIVE_DAYS}d)`}
            />
            <div title="Mean active builders over the last 3 complete months vs the 3 before — the in-progress month is excluded.">
              <BigStat
                value={
                  momentumPct == null
                    ? '—'
                    : `${momentumPct >= 0 ? '+' : ''}${momentumPct.toFixed(0)}%`
                }
                label="3-month momentum"
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
              title={
                designerSeatUse
                  ? `${designerSeatUse.used.toLocaleString()} of ${designerSeatUse.limit.toLocaleString()} licensed designer seats assigned`
                  : 'No licensed designer seat caps readable.'
              }
            >
              <BigStat
                value={
                  designerSeatUse ? pctLabel(designerSeatUse.used, designerSeatUse.limit) : '—'
                }
                label="Designer seats used"
              />
            </div>
            {inventoryView && (
              <>
                <div title="Projects whose surviving objects have exactly one creator — all context leaves with one person.">
                  <BigStat
                    value={singleSharePct == null ? '—' : `${singleSharePct}%`}
                    label="Single-creator projects"
                    tone={singleSharePct == null ? undefined : singleSharePct >= 50 ? 'warn' : 'ok'}
                  />
                </div>
                <BigStat
                  value={inventoryView.objectsBuilt.toLocaleString()}
                  label="Objects built (surviving)"
                />
                <BigStat value={inventoryView.allTimeCreators} label="All-time creators" />
              </>
            )}
          </div>
        </motion.div>

        {/* Flagship: cumulative adoption — only goes up, at different speeds. */}
        <motion.div {...blockProps} className="chart-container">
          <div className="chart-header flex items-center justify-between gap-3">
            <h4 title="Running totals from each project's full git history: distinct people who ever committed, projects ever touched, and commit volume. Cumulative lines include the running month honestly — the last point is simply mid-climb.">
              Cumulative adoption — people, projects &amp; commits
            </h4>
          </div>
          <CumulativeAdoptionChart points={cumulativePoints} />
        </motion.div>

        {/* Recent activity pulse — the one fast layer on the page. */}
        {pulse && (
          <motion.div {...blockProps}>
            <PulseCard pulse={pulse} nowMs={nowMs} />
          </motion.div>
        )}

        {/* What gets built — cumulative racing lines per family group. */}
        {inventoryView && inventoryView.trendPoints.length > 0 && (
          <motion.div {...blockProps} className="chart-container">
            <div className="chart-header flex items-center justify-between gap-3">
              <h4
                title={`Cumulative surviving objects by family group across the whole config history. Counts tagged objects only — ${inventoryView.taggedObjects.toLocaleString()} of ${inventoryView.objectsBuilt.toLocaleString()} objects carry creation tags (scenarios, notebooks and wikis are untagged and appear in totals, never in these curves).`}
              >
                What gets built here — cumulative, by family
              </h4>
            </div>
            <CumulativeCreationChart points={inventoryView.trendPoints} />
            <div className="border-t border-[var(--border-glass)] px-4 py-2 text-[10px] text-[var(--text-tertiary)]">
              surviving tagged objects only ({inventoryView.taggedObjects.toLocaleString()} of{' '}
              {inventoryView.objectsBuilt.toLocaleString()}) — deleted work is invisible
            </div>
          </motion.div>
        )}

        {/* Monthly creation rhythm — one small multiple per family group. */}
        {inventoryView && rhythmPoints.length > 0 && (
          <motion.div {...blockProps} className="chart-container">
            <div className="chart-header flex items-center justify-between gap-3">
              <h4 title="Objects created per complete month, one chart per family group — same slot colors as everywhere else. The running month is excluded (rate charts only plot complete months).">
                Monthly creation rhythm — last {rhythmPoints.length} complete months
              </h4>
            </div>
            <div className="grid grid-cols-1 gap-x-6 gap-y-4 px-4 py-4 sm:grid-cols-2 lg:grid-cols-3">
              {TREND_GROUPS.map((group, gi) => {
                const groupTotal = rhythmPoints.reduce((s, p) => s + (p.groups[gi] ?? 0), 0);
                const cols: ColPoint[] = rhythmPoints.map((p) => ({
                  key: p.month,
                  value: p.groups[gi] ?? 0,
                  title: `${monthLabel(p.month)} · ${(p.groups[gi] ?? 0).toLocaleString()} ${group.label.toLowerCase()}`,
                }));
                return (
                  <div key={group.key}>
                    <div className="mb-1.5 flex items-baseline justify-between gap-2">
                      <span className="inline-flex items-center gap-1.5 text-[10px] uppercase tracking-[0.1em] text-[var(--text-secondary)]">
                        <span
                          className="h-1.5 w-1.5 rounded-[2px]"
                          style={{ background: TREND_GROUP_COLORS[gi] }}
                        />
                        {group.label}
                      </span>
                      <span className="font-mono text-[10px] tabular-nums text-[var(--text-tertiary)]">
                        {groupTotal.toLocaleString()} in window
                      </span>
                    </div>
                    <MiniColumns
                      points={cols}
                      color={TREND_GROUP_COLORS[gi]}
                      height={56}
                      gap={2}
                      showValues={false}
                      axisLeft={monthLabel(rhythmPoints[0].month)}
                      axisRight={monthLabel(rhythmPoints[rhythmPoints.length - 1].month)}
                    />
                  </div>
                );
              })}
            </div>
          </motion.div>
        )}

        {/* License & knowledge risk */}
        {(licensing || busFactor) && (
          <motion.div {...blockProps} className="flex flex-col gap-4">
            <SectionHeader
              title="License & knowledge risk"
              caption="seat caps from the DSS license · creator joins from surviving objects only"
            />
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
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
                    <h4 title="Knowledge concentration: how many distinct creators each project's surviving objects have. Single-creator projects lose all context if that person leaves.">
                      Bus factor
                    </h4>
                  </div>
                  <div className="px-4 py-3">
                    <div className="mb-3 flex items-baseline gap-3">
                      <BigStat
                        value={singleSharePct == null ? '—' : `${singleSharePct}%`}
                        label="Single-creator projects"
                        tone={
                          singleSharePct == null ? undefined : singleSharePct >= 50 ? 'warn' : 'ok'
                        }
                      />
                      <span className="text-[10px] text-[var(--text-tertiary)]">
                        of {busFactor.measuredProjects} projects with tagged objects
                      </span>
                    </div>
                    <LinkedMix items={busItems} />
                  </div>
                </div>
              )}
            </div>
          </motion.div>
        )}

        {/* Who drives the activity: DSS groups + per-family builder boards */}
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <motion.div {...blockProps} className="chart-container">
            <div className="chart-header flex items-center justify-between gap-3">
              <h4 title="Git activity rolled up to DSS groups. A builder in several groups counts in each, so shares can overlap. The sparkline is the group's monthly commits over the last complete year.">
                Most active groups
              </h4>
            </div>
            <div className="px-4 py-3">
              {topGroups.length === 0 ? (
                <div className="text-xs text-[var(--text-muted)]">No group activity yet.</div>
              ) : (
                <div className="max-h-[22rem] space-y-2 overflow-y-auto">
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
              <h4 title="Top creators per family group, from config-history creation tags — surviving objects only. Bar widths are relative within each family.">
                Top builders — by family
              </h4>
            </div>
            <div className="max-h-[24rem] overflow-y-auto px-4 py-3">
              {!inventoryView ? (
                <div className="text-xs text-[var(--text-muted)]">
                  Waiting for the config inventory…
                </div>
              ) : (
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
              )}
            </div>
          </motion.div>
        </div>

        {/* Onboarding & activation — one trimester axis for both stories. */}
        {onboardingPoints.length > 0 && (
          <motion.div {...blockProps} className="chart-container">
            <div className="chart-header flex items-center justify-between gap-3">
              <h4 title="New accounts per quarter (from each user's creationDate) with the median days from signup to a first surviving build for that cohort. Complete quarters only; cohorts predating the surviving config history are excluded rather than measured dishonestly.">
                Onboarding &amp; activation — by trimester
              </h4>
              {showTtfb && ttfb && (
                <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
                  median {ttfb.overallMedianDays ?? '—'}d to first build · {ttfb.usersMeasured}{' '}
                  users measured
                </span>
              )}
            </div>
            <OnboardingChart points={onboardingPoints} showTtfb={showTtfb} />
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
        )}

        {/* Project leaderboard — the drill-down detail table, deliberately
            last: analytics first, row-level detail at the bottom. */}
        <motion.div {...blockProps} className="flex flex-col gap-3">
          {inventoryView && (
            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
              <span className="text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
                Objects column — family mix per project
              </span>
              <FamilyGroupLegend />
            </div>
          )}
          <DataGrid
            title="Projects — people & activity"
            countBadge={{
              total: projects.length,
              filtered: gridRows.length < projects.length ? gridRows.length : undefined,
            }}
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
