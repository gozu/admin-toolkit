import { Fragment, useEffect, useState, type ReactNode } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { useDiag } from '../../context/DiagContext';
import { adoptionScan } from '../../state/adoptionScan';
import { adoptionInventoryScan } from '../../state/adoptionInventoryScan';
import { adoptionEventsScan } from '../../state/adoptionEventsScan';
import { resolveLifecycleFromFields } from '../../utils/pageLifecycle';
import {
  buildInventoryView,
  completeMonthsOnly,
  familyGroupIndex,
  fillMonthRange,
  monthKeyUTC,
  sumFamilies,
  MATURITY_DIMENSIONS,
  TREND_GROUPS,
  TREND_GROUP_COLORS,
  type InventoryPersonaRow,
  type InventoryProjectViewRow,
  type InventoryView,
} from '../../utils/inventoryData';
import {
  AUDIT_EVENT_BUCKETS,
  AUDIT_EVENT_BUCKET_LABELS,
  classifyMsgType,
  type AuditEventBucket,
} from '../../utils/auditEventBuckets';
import { DataGrid } from '../common/DataGrid';
import { ProgressIndicator } from '../common/ProgressIndicator';
import { BigStat, SegmentBar, UsageBar } from './missionControl/microViz';
import { TILE_VARIANTS } from './missionControl/tokens';
import { AdoptionTrendChart } from './AdoptionTrendChart';
import { CreationTrendChart } from './CreationTrendChart';
import type { ColumnDef } from '../../utils/dataGridTypes';
import type { AdoptionMonthPoint, AdoptionProjectRow } from '../../types';
import './adoption.css';

const EMPTY: never[] = [];

// The stale-% column needs a real denominator before it may scream red — a
// 6-object toy project at "100% stale" is noise, not signal.
const MIN_STALE_SAMPLE = 10;
// Creation-trend chart shows the recent era; the full span is summarized in a
// footnote (a multi-year zero desert would crush the informative months).
const CREATION_CHART_MONTHS = 48;

// Window-honesty pills — three provenances on this page, each stamped once per
// card (or once per section for the health grid):
// - Full history (git history / user snapshot): spans the persistent record.
// - Config (inventory macro): full history but only SURVIVING objects.
// - Audit (events macro): whatever window the rotated audit files still cover.
function PersistentPill() {
  return (
    <span
      className="badge badge-info font-mono text-[10px] uppercase tracking-[0.1em]"
      title="Spans the full persistent history (git logs / user snapshot) — not a short audit window."
    >
      Full history
    </span>
  );
}

// Config-tree provenance: creationTag/versionTag mining spans the instance's
// full multi-year history — but only for objects that still exist. Deleted
// work is invisible (survivorship bias), so this pill keeps that caveat
// attached to every metric it stamps.
function ConfigHistoryPill() {
  return (
    <span
      className="badge badge-info font-mono text-[10px] uppercase tracking-[0.1em]"
      title="Mined from config-tree object metadata: full history, but only objects that still exist today — deleted work is invisible (survivorship bias)."
    >
      Config · surviving objects
    </span>
  );
}

function AuditWindowPill({ coverageDays }: { coverageDays: number | null }) {
  return (
    <span
      className="badge badge-info font-mono text-[10px] uppercase tracking-[0.1em]"
      title="Built from whatever audit-log span the rotated files still cover — not a full persistent history."
    >
      Audit · {coverageDays != null ? `last ${Math.ceil(coverageDays)}d` : 'captured window'}
    </span>
  );
}

/** Thin section header: groups related cards under one title + one provenance
 * pill instead of stamping every card. */
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
 * appears (trend chart, composition, grid bars, persona bars). */
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
 * spotlights that segment (and vice versa). One encoding + one visible
 * legend — never a second redundant bar list. */
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
  /** Optional per-column x label (e.g. maturity scores). */
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
  axisLeft,
  axisRight,
  valueSuffix = '',
}: {
  points: ColPoint[];
  color?: string;
  height?: number;
  axisLeft?: string;
  axisRight?: string;
  valueSuffix?: string;
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
      <div className="flex items-end gap-[3px]" style={{ height }}>
        {points.map((p, i) => (
          <div
            key={p.key}
            title={p.title}
            className="adk-colwrap flex h-full min-w-0 flex-1 flex-col justify-end"
          >
            <span className="adk-colval pb-0.5 text-center font-mono text-[9px] leading-none text-[var(--text-secondary)]">
              {p.muted ? '—' : `${p.value.toLocaleString()}${valueSuffix}`}
            </span>
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
        <div className="mt-1 flex gap-[3px]">
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

/** Persona chip for a Top-Builders row, when the inventory knows this login
 * as a creator. */
function BuilderPersonaChip({ persona }: { persona: InventoryPersonaRow }) {
  return (
    <span
      className="adk-chip rounded border border-[var(--border-glass)] bg-[var(--bg-elevated)] px-1 py-px font-mono text-[9px] uppercase tracking-[0.08em] text-[var(--text-tertiary)]"
      title={`${persona.created.toLocaleString()} surviving objects created · ${persona.shareSummary}`}
    >
      {persona.persona ?? `${persona.created} obj`}
    </span>
  );
}

// Audit bucket palette — deliberately NOT the family-group palette (neon vars
// instead of --viz-cat) so "explore" can never be misread as "Datasets".
// Always accompanied by a visible dot legend.
const BUCKET_COLORS: Record<AuditEventBucket, string> = {
  build: 'var(--neon-green)',
  run: 'var(--neon-purple)',
  explore: 'var(--neon-cyan)',
  consume: 'var(--neon-amber)',
  other: 'var(--text-tertiary)',
};

/** Compact build-share chip for a Top-Builders row (audit msgType buckets).
 * Tooltip carries the full bucket breakdown. */
function BuilderBucketChip({ buckets }: { buckets: Record<string, number> }) {
  const total = Object.values(buckets).reduce((s, v) => s + v, 0);
  if (total === 0) return null;
  return (
    <span
      className="adk-chip rounded border border-[var(--border-glass)] bg-[var(--bg-elevated)] px-1 py-px font-mono text-[9px] uppercase tracking-[0.08em] text-[var(--text-tertiary)]"
      title={AUDIT_EVENT_BUCKETS.map(
        (k) => `${AUDIT_EVENT_BUCKET_LABELS[k]}: ${pctLabel(buckets[k] ?? 0, total)}`,
      ).join(' · ')}
    >
      {pctLabel(buckets.build ?? 0, total)} build · {pctLabel(buckets.consume ?? 0, total)} consume
    </span>
  );
}

// Sequential ramp for edit intensity (same hue, rising weight — it's an
// ordered scale, not categories).
const EDIT_RAMP = [
  'var(--text-tertiary)',
  'color-mix(in srgb, var(--accent) 45%, var(--bg-elevated))',
  'color-mix(in srgb, var(--accent) 75%, var(--bg-elevated))',
  'var(--accent)',
];

/** Derived health cards: edit intensity, staleness, bus factor, maturity,
 * seat types, dormant creators — all view-time collapses of the inventory
 * macro's accumulator. The survivorship caveat is stamped once on the section
 * header. */
function InventoryHealthCards({
  view,
  nowMs,
  hasSessionData,
}: {
  view: InventoryView;
  nowMs: number;
  hasSessionData: boolean;
}) {
  const { staleness, busFactor, maturityHistogram, seatTypes, dormantCreators, editIntensity } =
    view;

  const editItems: MixItem[] = [
    { key: 'v1', label: 'Saved once', value: editIntensity.editBuckets.v1, color: EDIT_RAMP[0] },
    { key: 'v2', label: '2–5 saves', value: editIntensity.editBuckets.v2to5, color: EDIT_RAMP[1] },
    { key: 'v6', label: '6–20 saves', value: editIntensity.editBuckets.v6to20, color: EDIT_RAMP[2] },
    { key: 'v21', label: '21+ saves', value: editIntensity.editBuckets.v21plus, color: EDIT_RAMP[3] },
  ];

  const staleItems: MixItem[] = [
    { key: 'fresh', label: 'Fresh (≤3 mo)', value: staleness.freshCount, color: 'var(--neon-green)' },
    { key: 'aging', label: 'Aging (3–12 mo)', value: staleness.agingCount, color: 'var(--neon-amber)' },
    { key: 'stale', label: 'Stale (>12 mo)', value: staleness.staleCount, color: 'var(--neon-red)' },
    { key: 'undated', label: 'Undated', value: staleness.unknownCount, color: 'var(--text-tertiary)' },
  ];

  const busItems: MixItem[] = [
    {
      key: 'single',
      label: 'Single creator',
      value: busFactor.singleCreator,
      color: 'var(--neon-red)',
      hint: 'All context leaves with one person.',
    },
    { key: 'few', label: '2–3 creators', value: busFactor.twoToThree, color: 'var(--neon-amber)' },
    { key: 'many', label: '4+ creators', value: busFactor.fourPlus, color: 'var(--neon-green)' },
  ];
  const singleSharePct =
    busFactor.measuredProjects > 0
      ? Math.round((busFactor.singleCreator / busFactor.measuredProjects) * 100)
      : null;

  const maturityPoints: ColPoint[] = maturityHistogram.map((count, score) => ({
    key: `s${score}`,
    value: count,
    label: `${score}`,
    title: `Maturity ${score}/${MATURITY_DIMENSIONS.length} · ${count.toLocaleString()} ${count === 1 ? 'project' : 'projects'}`,
  }));

  const seatMax = Math.max(1, ...seatTypes.map((s) => s.users));
  const topDormant = dormantCreators.slice(0, 8);

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
      {/* Edit intensity */}
      <div className="chart-container">
        <div className="chart-header">
          <h4 title="How often surviving objects were re-saved after creation (versionTag save counts). Objects without a version tag are not measured.">
            Edit intensity
          </h4>
        </div>
        <div className="px-4 py-3">
          {editIntensity.versionedObjects === 0 ? (
            <div className="text-xs text-[var(--text-muted)]">No version tags found.</div>
          ) : (
            <>
              <LinkedMix items={editItems} />
              <div className="pt-2 text-[10px] text-[var(--text-tertiary)]">
                {editIntensity.savedOnce.toLocaleString()} objects never re-saved after creation ·{' '}
                {editIntensity.versionedObjects.toLocaleString()} versioned objects measured
              </div>
            </>
          )}
        </div>
      </div>

      {/* Staleness / zombies */}
      <div className="chart-container">
        <div className="chart-header">
          <h4 title="Age of each object's last config edit, measured against the newest edit anywhere in the config tree (never the wall clock). Zombie = a project whose newest config edit is over 12 months old.">
            Content staleness
          </h4>
        </div>
        <div className="px-4 py-3">
          <LinkedMix items={staleItems} />
          <div className="mt-3 flex items-baseline gap-3">
            <BigStat
              value={staleness.zombieProjects}
              label="Zombie projects"
              tone={staleness.zombieProjects > 0 ? 'warn' : 'ok'}
            />
            <span className="text-[10px] text-[var(--text-tertiary)]">
              no config edit in &gt;12 mo · {staleness.measuredProjects} projects with dated
              objects
            </span>
          </div>
        </div>
      </div>

      {/* Bus factor */}
      <div className="chart-container">
        <div className="chart-header">
          <h4 title="Knowledge concentration: how many distinct creators each project's surviving objects have. Single-creator projects lose all context if that person leaves.">
            Bus factor
          </h4>
        </div>
        <div className="px-4 py-3">
          {busFactor.measuredProjects === 0 ? (
            <div className="text-xs text-[var(--text-muted)]">No creation tags found.</div>
          ) : (
            <>
              <div className="mb-3 flex items-baseline gap-3">
                <BigStat
                  value={singleSharePct == null ? '—' : `${singleSharePct}%`}
                  label="Single-creator projects"
                  tone={singleSharePct == null ? undefined : singleSharePct >= 50 ? 'warn' : 'ok'}
                />
                <span className="text-[10px] text-[var(--text-tertiary)]">
                  of {busFactor.measuredProjects} projects with tagged objects
                </span>
              </div>
              <LinkedMix items={busItems} />
            </>
          )}
        </div>
      </div>

      {/* Maturity distribution — a real histogram */}
      <div className="chart-container">
        <div className="chart-header">
          <h4
            title={`One point per practice present in a project's surviving objects: ${MATURITY_DIMENSIONS.map((d) => d.label).join(' · ')}.`}
          >
            Project maturity (0–{MATURITY_DIMENSIONS.length})
          </h4>
        </div>
        <div className="px-4 py-3">
          <MiniColumns points={maturityPoints} height={110} />
          <div className="pt-2 text-[10px] text-[var(--text-tertiary)]">
            projects by practice score — hover a bar for the count
          </div>
        </div>
      </div>

      {/* Seat types × creators */}
      <div className="chart-container">
        <div className="chart-header">
          <h4 title="Seat profiles from the user snapshot crossed with config-history creators. Bar = share of that profile's accounts that ever created a surviving object.">
            Seat types — who actually builds
          </h4>
        </div>
        <div className="max-h-72 space-y-1.5 overflow-y-auto px-4 py-3">
          {seatTypes.length === 0 && (
            <div className="text-xs text-[var(--text-muted)]">No user snapshot available.</div>
          )}
          {seatTypes.map((s) => {
            const pct = s.users > 0 ? (s.creators / s.users) * 100 : null;
            return (
              <div
                key={s.profile}
                className="adk-hover-row -mx-1 flex items-center gap-2 px-1 py-0.5"
                title={
                  s.users > 0
                    ? `${s.creators} of ${s.users} ${s.profile} accounts created at least one surviving object`
                    : `${s.creators} creators are not in the current user snapshot (likely deleted accounts)`
                }
              >
                <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--text-secondary)]">
                  {s.profile}
                  <span className="ml-1.5 text-[10px] text-[var(--text-tertiary)]">
                    {s.creators} {s.creators === 1 ? 'creator' : 'creators'}
                    {s.users > 0 ? ` / ${s.users} ${s.users === 1 ? 'user' : 'users'}` : ''}
                  </span>
                </span>
                {pct != null && (
                  <span
                    className="h-1 flex-shrink-0 overflow-hidden rounded-full bg-[var(--bg-elevated)]"
                    style={{ width: `${Math.max(14, (s.users / seatMax) * 56)}px` }}
                  >
                    <span
                      className="block h-full rounded-full bg-[var(--accent)] transition-[width] duration-500"
                      style={{ width: `${Math.min(100, pct)}%` }}
                    />
                  </span>
                )}
                <span className="w-12 flex-shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-primary)]">
                  {pct == null ? '—' : `${Math.round(pct)}%`}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Dormant creators */}
      <div className="chart-container">
        <div className="chart-header">
          <h4
            title={`Creators with surviving objects but no session in the last ${view.dormantThresholdDays} days (measured against the newest session in the snapshot) — knowledge that may be walking out the door.`}
          >
            Dormant creators
          </h4>
        </div>
        <div className="px-4 py-3">
          {!hasSessionData ? (
            <div className="text-xs text-[var(--text-muted)]">
              No session-activity data — dormancy can't be measured.
            </div>
          ) : topDormant.length === 0 ? (
            <div className="text-xs text-[var(--text-muted)]">
              Every creator has a session within the last {view.dormantThresholdDays} days.
            </div>
          ) : (
            <div className="space-y-1">
              {topDormant.map((d) => (
                <div
                  key={d.login}
                  className="adk-hover-row -mx-1 flex items-center gap-2 px-1 py-0.5 text-[11px]"
                >
                  <span className="min-w-0 flex-1 truncate font-mono text-[var(--text-secondary)]">
                    {d.login}
                    {!d.inUserSnapshot && (
                      <span
                        className="ml-1.5 text-[9px] uppercase tracking-wide text-[var(--text-tertiary)]"
                        title="Login not in the current user snapshot — account likely deleted."
                      >
                        deleted?
                      </span>
                    )}
                  </span>
                  <span className="flex-shrink-0 text-[10px] text-[var(--text-tertiary)]">
                    {d.created.toLocaleString()} obj
                  </span>
                  <span className="w-20 flex-shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-tertiary)]">
                    {d.lastSessionMs == null ? 'no session' : relDays(d.lastSessionMs, nowMs).text}
                  </span>
                </div>
              ))}
              {dormantCreators.length > topDormant.length && (
                <div className="pt-0.5 text-[10px] text-[var(--text-tertiary)]">
                  +{dormantCreators.length - topDormant.length} more dormant creators
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

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

const MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
// Heat cells share the commit-bar hue from AdoptionTrendChart — same series,
// same color, different lens (the old blue-vs-violet split read as two datasets).
const HEAT_RGB = '153, 123, 224';

/** GitHub-style calendar of monthly activity intensity: one row per year, one
 * cell per month. Same data as the trend chart, different lens — the grid makes
 * seasonal rhythm and dormant stretches readable at a glance. The current
 * in-progress month renders dashed (the charts exclude it entirely). */
function ActivityHeatGrid({
  points,
  unitWord = 'commits',
  peopleWord = 'builders',
  currentMonthKey,
}: {
  points: AdoptionMonthPoint[];
  /** Tooltip nouns — never "active users": MAU is owned by another tool. */
  unitWord?: string;
  peopleWord?: string;
  /** 'YYYY-MM' of the wall-clock month — rendered as partial, not as data. */
  currentMonthKey?: string;
}) {
  if (points.length < 2) return null;
  const complete = points.filter((p) => p.month !== currentMonthKey);
  const maxCommits = Math.max(1, ...complete.map((p) => p.commits));
  const byMonth = new Map(points.map((p) => [p.month, p]));
  const years = [...new Set(points.map((p) => Number(p.month.slice(0, 4))))].sort();
  const hasPartial = currentMonthKey != null && byMonth.has(currentMonthKey);
  return (
    <div className="border-t border-[var(--border-glass)] px-4 pb-3 pt-2.5">
      <div
        className="grid items-center gap-[3px]"
        style={{ gridTemplateColumns: 'auto repeat(12, minmax(0, 1fr))' }}
      >
        <span />
        {MONTH_ABBR.map((m) => (
          <span
            key={m}
            className="text-center font-mono text-[8px] leading-none text-[var(--text-tertiary)]"
          >
            {m}
          </span>
        ))}
        {years.map((y) => (
          <Fragment key={y}>
            <span className="pr-2 text-right font-mono text-[9px] leading-none text-[var(--text-tertiary)]">
              {y}
            </span>
            {MONTH_ABBR.map((_, i) => {
              const key = `${y}-${String(i + 1).padStart(2, '0')}`;
              const p = byMonth.get(key);
              if (!p) {
                // outside the tracked span — visually absent, not "zero"
                return <span key={key} className="h-3 rounded-[2px]" />;
              }
              const isPartial = key === currentMonthKey;
              const alpha =
                p.commits === 0 ? 0 : 0.14 + 0.86 * Math.sqrt(Math.min(1, p.commits / maxCommits));
              return (
                <span
                  key={key}
                  title={`${key} · ${p.commits.toLocaleString()} ${unitWord} · ${p.activeBuilders} ${peopleWord}${isPartial ? ' · month in progress' : ''}`}
                  className="adk-heatcell h-3 rounded-[2px]"
                  style={{
                    background:
                      p.commits === 0
                        ? 'var(--bg-elevated)'
                        : `rgba(${HEAT_RGB}, ${isPartial ? alpha * 0.5 : alpha})`,
                    outline: isPartial ? `1px dashed rgba(${HEAT_RGB}, 0.8)` : undefined,
                    outlineOffset: isPartial ? -1 : undefined,
                  }}
                />
              );
            })}
          </Fragment>
        ))}
      </div>
      <div className="mt-2 flex items-center justify-between font-mono text-[8px] leading-none text-[var(--text-tertiary)]">
        <span className="inline-flex items-center gap-1">
          less
          {[0.14, 0.35, 0.58, 0.8, 1].map((a) => (
            <span key={a} className="h-2 w-2 rounded-[2px]" style={{ background: `rgba(${HEAT_RGB}, ${a})` }} />
          ))}
          more
        </span>
        {hasPartial && <span>dashed = current month, still in progress</span>}
      </div>
    </div>
  );
}

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
  const days = Math.floor((nowMs - ms) / 86_400_000);
  if (days <= 0) return { text: 'today', days: 0 };
  if (days === 1) return { text: '1d ago', days };
  if (days < 30) return { text: `${days}d ago`, days };
  if (days < 365) return { text: `${Math.floor(days / 30)}mo ago`, days };
  return { text: `${(days / 365).toFixed(1)}y ago`, days };
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

export function AdoptionPage() {
  const { state } = useDiag();
  const { data, scanStarted, error } = adoptionScan.use();
  const invState = adoptionInventoryScan.use();
  const evState = adoptionEventsScan.use();
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const reduced = useReducedMotion();

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
  // two macro layers degrade card-by-card instead of failing the whole page
  // (a remote host running an older plugin build simply has no macro yet).
  const lifecycle = resolveLifecycleFromFields(['adoptionLoading'], state.parsedData);
  const isLoading = lifecycle.phase === 'running' || lifecycle.phase === 'queued';

  // Plain derivations — the React Compiler auto-memoizes; manual useMemo over
  // `?? EMPTY` fallbacks can't be preserved (react-hooks/preserve-manual-memoization).
  const totals = data?.totals;
  const trend = data?.monthlyTrend ?? EMPTY;
  const cohorts = data?.cohorts ?? EMPTY;
  const repeat = data?.repeatBuilders;
  const recency = data?.builderRecency ?? EMPTY;
  const nowMs = data?.generatedAtMs ?? Date.now();
  const projects = data?.projectRows ?? EMPTY;
  const groups = data?.groups ?? EMPTY;
  const builders = data?.builderStats ?? EMPTY;
  const peopleMax = Math.max(1, ...projects.map((p) => p.authorCount));
  const expandedRowKeys = new Set(selectedKey ? [selectedKey] : []);
  const topRecency = [...recency]
    .filter((r) => r.lastSessionActivity != null)
    .sort((a, b) => (b.lastSessionActivity ?? 0) - (a.lastSessionActivity ?? 0))
    .slice(0, 10);

  // Partial-period honesty: every chart on this page plots COMPLETE months
  // only — 10 days of July next to full months always reads as a collapse.
  // The current month is visible in exactly one place, the heat grid, where
  // it renders dashed and labeled "in progress".
  const currentMonthKey = monthKeyUTC(nowMs);
  const gitTrendComplete = completeMonthsOnly(trend, nowMs);

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

  const activeGroups = groups.filter((g) => g.commits > 0);
  const topGroups = activeGroups.slice(0, 8);
  const groupCommitsMax = Math.max(1, ...topGroups.map((g) => g.commits));
  const quietGroups = groups.length - activeGroups.length;
  const topBuilders = builders.slice(0, 12);
  const builderCommitsMax = Math.max(1, ...topBuilders.map((b) => b.commits));

  // Onboarding cohorts — complete months, zero-filled, last 24; the running
  // month is a footnote, never a bar.
  const cohortByMonth = new Map(cohorts.map((c) => [c.month, c.newUsers]));
  const cohortMonths = fillMonthRange(
    cohorts.filter((c) => c.month < currentMonthKey).map((c) => c.month),
  ).slice(-24);
  const cohortPoints: ColPoint[] = cohortMonths.map((month) => {
    const n = cohortByMonth.get(month) ?? 0;
    return {
      key: month,
      value: n,
      title: `${monthLabel(month)} · ${n} new ${n === 1 ? 'account' : 'accounts'}`,
    };
  });
  const cohortsThisMonth = cohortByMonth.get(currentMonthKey) ?? 0;

  // ── inventory-fed derivations (all null-safe) ─────────────────────────────
  // Config-tree object inventory (full history of surviving objects) — absent
  // until the macro returns; every section fed by it null-guards.
  const inventoryView = buildInventoryView(invState.data, recency);
  const composition = inventoryView?.composition ?? EMPTY;
  const topComposition = composition.slice(0, 8);
  const restComposition = composition.slice(topComposition.length);
  const compositionItems: MixItem[] = [
    ...topComposition.map((c) => ({
      key: c.family,
      label: c.label,
      color: TREND_GROUP_COLORS[familyGroupIndex(c.family)],
      value: c.count,
      hint:
        c.topSubtypes.length > 0
          ? `Tagged ${c.tagged.toLocaleString()} of ${c.count.toLocaleString()} · top types: ${c.topSubtypes.map((s) => `${s.subtype} (${s.count.toLocaleString()})`).join(', ')}`
          : `Tagged ${c.tagged.toLocaleString()} of ${c.count.toLocaleString()}`,
    })),
    ...(restComposition.length > 0
      ? [
          {
            key: '__rest',
            label: `${restComposition.length} smaller ${restComposition.length === 1 ? 'family' : 'families'}`,
            color: 'var(--bg-elevated)',
            value: restComposition.reduce((s, c) => s + c.count, 0),
            hint: restComposition
              .map((c) => `${c.label} (${c.count.toLocaleString()})`)
              .join(', '),
          },
        ]
      : []),
  ];

  const invGeneratedMs = invState.data?.generatedAtMs ?? nowMs;
  const invTrendComplete = completeMonthsOnly(inventoryView?.trendPoints ?? EMPTY, invGeneratedMs);
  const invTrendShown = invTrendComplete.slice(-CREATION_CHART_MONTHS);
  const invTrendHiddenMonths = invTrendComplete.length - invTrendShown.length;

  const ttfb = inventoryView?.ttfb;
  const ttfbCohorts = (ttfb?.cohorts ?? EMPTY)
    .filter((c) => c.month < currentMonthKey)
    .slice(-12);
  const ttfbPoints: ColPoint[] = ttfbCohorts.map((c) => ({
    key: c.month,
    value: c.medianDays ?? 0,
    muted: c.medianDays == null,
    title: `${monthLabel(c.month)} cohort · ${c.builders}/${c.cohortUsers} built something that survives · median ${c.medianDays == null ? 'not measurable' : `${c.medianDays}d to first build`}`,
  }));

  const familyMixSegments = (persona: InventoryPersonaRow) =>
    TREND_GROUPS.map((group, gi) => {
      const value = sumFamilies(persona.byFamily, group.families);
      return {
        value,
        color: TREND_GROUP_COLORS[gi],
        title: `${group.label} · ${value.toLocaleString()}`,
      };
    });
  // Per-project inventory roll-ups keyed for the projects grid; recency on
  // config edits is measured against the inventory's own newest edit, never
  // the wall clock.
  const invProjectByKey = new Map<string, InventoryProjectViewRow>();
  for (const row of inventoryView?.projectRows ?? []) invProjectByKey.set(row.projectKey, row);
  const invNowMs = inventoryView?.inventory.lastEditMs ?? nowMs;
  const hasSessionData = recency.some((r) => r.lastSessionActivity != null);
  const anyPersonaShown = topBuilders.some((b) => inventoryView?.personas[b.login]);

  // ── audit event mix (msgType buckets, UI-vs-API split, hot list) ──────────
  const events = evState.data && evState.data.ok !== false ? evState.data : null;
  const coverageDays = events?.coverageDays ?? null;
  const humans = events?.humans ?? {};
  let auditMix: {
    buckets: Record<string, number>;
    classified: number;
    fromUi: number;
    viaApi: number;
    authTotal: number;
    hot: Array<[string, number]>;
  } | null = null;
  {
    const buckets: Record<string, number> = {};
    const authSources: Record<string, number> = {};
    for (const h of Object.values(humans)) {
      for (const [b, n] of Object.entries(h.buckets ?? {})) buckets[b] = (buckets[b] ?? 0) + n;
      for (const [a, n] of Object.entries(h.authSources ?? {}))
        authSources[a] = (authSources[a] ?? 0) + n;
    }
    const classified = Object.values(buckets).reduce((s, v) => s + v, 0);
    if (classified > 0) {
      const fromUi = authSources.USER_FROM_UI ?? 0;
      const authTotal = Object.values(authSources).reduce((s, v) => s + v, 0);
      const hot = Object.entries(events?.msgTypeCounts ?? {})
        .sort((a, b) => b[1] - a[1])
        .slice(0, 10);
      auditMix = { buckets, classified, fromUi, viaApi: authTotal - fromUi, authTotal, hot };
    }
  }
  const bucketItems: MixItem[] = auditMix
    ? AUDIT_EVENT_BUCKETS.map((b) => ({
        key: b,
        label: AUDIT_EVENT_BUCKET_LABELS[b],
        color: BUCKET_COLORS[b],
        value: auditMix.buckets[b] ?? 0,
      }))
    : EMPTY;
  const hotMax = Math.max(1, ...(auditMix?.hot ?? []).map(([, n]) => n));
  // Audit-window shading for the creation trend — units differ, series are
  // never merged; the band only shows how little of the long spine the audit
  // logs cover.
  const auditWindow =
    events?.firstEventMs != null && events?.lastEventMs != null
      ? { firstMs: events.firstEventMs, lastMs: events.lastEventMs }
      : null;

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
          {
            id: 'invLastEdit',
            label: 'Config edit',
            align: 'right',
            render: (row) => {
              const inv = invProjectByKey.get(row.projectKey);
              const rel = relDays(inv?.lastEditMs, invNowMs);
              return (
                <span
                  className="font-mono text-xs tabular-nums text-[var(--text-tertiary)]"
                  title={inv?.lastEditor ? `Last config edit by ${inv.lastEditor}` : undefined}
                >
                  {rel.text}
                </span>
              );
            },
            sortValue: (row) => invProjectByKey.get(row.projectKey)?.lastEditMs ?? 0,
          },
          {
            id: 'invStale',
            label: 'Stale %',
            align: 'right',
            mono: true,
            render: (row) => {
              const inv = invProjectByKey.get(row.projectKey);
              if (!inv || inv.datedObjects === 0)
                return <span className="text-[var(--text-muted)]">—</span>;
              const pct = Math.round(inv.stalePct);
              // Tiny denominators stay muted: 5 objects at "100% stale" is
              // noise, not a red flag.
              const tooSmall = inv.datedObjects < MIN_STALE_SAMPLE;
              return (
                <span
                  className={
                    !tooSmall && pct >= 50
                      ? 'text-[var(--neon-red)]'
                      : tooSmall
                        ? 'text-[var(--text-tertiary)]'
                        : 'text-[var(--text-secondary)]'
                  }
                  title={`Share of dated objects whose last config edit is over 12 months old (${inv.datedObjects.toLocaleString()} dated objects${tooSmall ? ' — too few to flag' : ''}).`}
                >
                  {pct}%
                </span>
              );
            },
            sortValue: (row) => invProjectByKey.get(row.projectKey)?.stalePct ?? -1,
          },
        ] as ColumnDef<AdoptionProjectRow>[])
      : []),
  ];

  // Entrance: each block fades up as it mounts (macro-fed blocks stream in
  // when their data lands). Static variants — no re-run on data updates.
  const blockProps = {
    variants: TILE_VARIANTS,
    initial: reduced ? false : ('hidden' as const),
    animate: 'show' as const,
  };

  return (
    <div className="page-fill">
      <div className="flex flex-col gap-6 flex-1 min-h-0">
        {/* Summary band */}
        <motion.div {...blockProps} className="chart-container">
          <div className="chart-header flex items-center justify-between gap-3">
            <h4>Adoption &amp; Engagement</h4>
            <div className="flex items-center gap-2">
              {inventoryView && <ConfigHistoryPill />}
              <PersistentPill />
            </div>
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
              {evState.loading && 'Mining the audit logs for the event mix…'}
            </div>
          )}
          {!invState.loading && (invState.error || invState.data?.error) && (
            <div className="border-b border-[var(--border-glass)] px-4 py-2 text-[11px] text-[var(--text-tertiary)]">
              Config inventory unavailable: {invState.error || invState.data?.error}
            </div>
          )}
          {!evState.loading && (evState.error || evState.data?.error) && (
            <div className="border-b border-[var(--border-glass)] px-4 py-2 text-[11px] text-[var(--text-tertiary)]">
              Audit event mix unavailable: {evState.error || evState.data?.error}
            </div>
          )}
          <div
            className={`grid grid-cols-2 gap-4 px-4 py-4 sm:grid-cols-4 ${inventoryView ? 'lg:grid-cols-8' : 'lg:grid-cols-6'}`}
          >
            <BigStat
              value={totals ? `${totals.activeProjectCount}/${totals.projectCount}` : '—'}
              label={
                totals
                  ? `Active / total (${totals.inactiveThresholdDays}d)`
                  : 'Active / total projects'
              }
            />
            <BigStat value={totals ? totals.builderCount : '—'} label="Builders (people)" />
            <BigStat
              value={totals ? totals.avgPeoplePerProject.toFixed(1) : '—'}
              label="Avg people / project"
            />
            <BigStat value={totals ? totals.commitCount.toLocaleString() : '—'} label="Commits" />
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
            <BigStat
              value={repeat ? repeat.repeat : '—'}
              sub={repeat ? `/${repeat.total}` : undefined}
              label="Returning builders"
            />
            {inventoryView && (
              <>
                <BigStat
                  value={inventoryView.objectsBuilt.toLocaleString()}
                  label="Objects built (surviving)"
                />
                <BigStat value={inventoryView.allTimeCreators} label="All-time creators" />
              </>
            )}
          </div>
        </motion.div>

        {/* Flagship: multi-month adoption trend (complete months only; the
            running month lives in the heat grid, dashed). */}
        <motion.div {...blockProps} className="chart-container">
          <div className="chart-header flex items-center justify-between gap-3">
            <h4 title="Distinct people committing per month + commit volume, back to each project's oldest commit. Complete months only — the in-progress month appears dashed in the calendar below.">
              Adoption trend — active builders &amp; commit volume
            </h4>
            <PersistentPill />
          </div>
          <AdoptionTrendChart points={gitTrendComplete} />
          <ActivityHeatGrid points={trend} currentMonthKey={currentMonthKey} />
        </motion.div>

        {/* Long spine: monthly object creation mined from config-tree tags.
            Units differ from the git trend (objects created vs commits) so
            the two are never merged — the audit window is only shaded as a
            band for scale. */}
        {inventoryView && invTrendShown.length > 0 && (
          <motion.div {...blockProps} className="chart-container">
            <div className="chart-header flex items-center justify-between gap-3">
              <h4
                title={`Objects created per month across the surviving config history — ${inventoryView.taggedObjects.toLocaleString()} of ${inventoryView.objectsBuilt.toLocaleString()} objects carry creation tags. Complete months only.${auditWindow ? ' The shaded band marks the much shorter audit-log window.' : ''}`}
              >
                Monthly creation trend — what got built, by family
              </h4>
              <ConfigHistoryPill />
            </div>
            <CreationTrendChart points={invTrendShown} auditWindow={auditWindow} />
            {invTrendHiddenMonths > 0 && (
              <div className="border-t border-[var(--border-glass)] px-4 py-2 text-[10px] text-[var(--text-tertiary)]">
                Showing the last {invTrendShown.length} complete months — {invTrendHiddenMonths}{' '}
                earlier {invTrendHiddenMonths === 1 ? 'month' : 'months'} (since{' '}
                {monthLabel(invTrendComplete[0].month)}) omitted; all-time composition below covers
                them.
              </div>
            )}
          </motion.div>
        )}

        {/* Built vs used — the config-tree composition next to the audit-window
            event mix. */}
        {(inventoryView || auditMix) && (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            {inventoryView && (
              <motion.div {...blockProps} className="chart-container">
                <div className="chart-header flex items-center justify-between gap-3">
                  <h4 title="Every surviving config-tree object, grouped by family and colored by family group (same palette as the trend chart). Tag coverage varies: scenarios and notebooks carry no creation tags, so they count here but not in the creation trend.">
                    What gets built here
                  </h4>
                  <ConfigHistoryPill />
                </div>
                <div className="px-4 py-3">
                  <LinkedMix items={compositionItems} />
                  <FamilyGroupLegend className="mt-3 border-t border-[var(--border-glass)] pt-2" />
                </div>
              </motion.div>
            )}
            {auditMix && (
              <motion.div {...blockProps} className="chart-container">
                <div className="chart-header flex items-center justify-between gap-3">
                  <h4 title="Human audit events classified by msgType: build = config writes (saves/creates), run = jobs & scenarios, explore = reads & lists, consume = dashboards, business apps, exports. Heuristic buckets — hover the hot list for raw event types. Automation actors are excluded server-side.">
                    What's hot this window — human event mix
                  </h4>
                  <AuditWindowPill coverageDays={coverageDays} />
                </div>
                <div className="px-4 py-3">
                  <LinkedMix items={bucketItems} />
                  {auditMix.authTotal > 0 && (
                    <>
                      <div className="mt-4 mb-1 flex items-center justify-between text-[11px] text-[var(--text-secondary)]">
                        <span>
                          <span className="font-mono text-[var(--accent)]">
                            {pctLabel(auditMix.fromUi, auditMix.authTotal)}
                          </span>{' '}
                          via UI
                        </span>
                        <span className="text-[10px] text-[var(--text-tertiary)]">
                          {auditMix.viaApi.toLocaleString()} events via API keys (human-attributed)
                        </span>
                      </div>
                      <SegmentBar
                        height={6}
                        segments={[
                          {
                            value: auditMix.fromUi,
                            color: 'var(--accent)',
                            title: `${auditMix.fromUi.toLocaleString()} events from the UI (USER_FROM_UI)`,
                          },
                          {
                            value: auditMix.viaApi,
                            color: 'var(--text-tertiary)',
                            title: `${auditMix.viaApi.toLocaleString()} events via API keys attributed to a human`,
                          },
                        ]}
                      />
                    </>
                  )}
                  <div className="mt-4 mb-2 text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
                    Top event types (human) — dot = bucket
                  </div>
                  <div className="space-y-0.5">
                    {auditMix.hot.map(([msgType, count]) => {
                      const bucket = classifyMsgType(msgType);
                      return (
                        <div
                          key={msgType}
                          title={AUDIT_EVENT_BUCKET_LABELS[bucket]}
                          className="adk-hover-row -mx-1 flex items-center gap-2 px-1 py-0.5"
                        >
                          <span
                            className="h-2 w-2 flex-shrink-0 rounded-[2px]"
                            style={{ background: BUCKET_COLORS[bucket] }}
                          />
                          <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-[var(--text-secondary)]">
                            {msgType}
                          </span>
                          <span className="h-1 w-14 flex-shrink-0 overflow-hidden rounded-full bg-[var(--bg-elevated)]">
                            <span
                              className="block h-full rounded-full transition-[width] duration-500"
                              style={{
                                width: `${(count / hotMax) * 100}%`,
                                background: BUCKET_COLORS[bucket],
                              }}
                            />
                          </span>
                          <span className="w-16 flex-shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-primary)]">
                            {count.toLocaleString()}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </motion.div>
            )}
          </div>
        )}

        {/* Config health & knowledge risk — derived analytics; the
            survivorship caveat is stamped once here for all six cards. */}
        {inventoryView && (
          <motion.div {...blockProps} className="flex flex-col gap-4">
            <SectionHeader
              title="Config health & knowledge risk"
              caption="derived from surviving objects only — deleted work is invisible"
              right={<ConfigHistoryPill />}
            />
            <InventoryHealthCards
              view={inventoryView}
              nowMs={invNowMs}
              hasSessionData={hasSessionData}
            />
          </motion.div>
        )}

        {/* Who drives the activity: DSS groups + individual builders */}
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <motion.div {...blockProps} className="chart-container">
            <div className="chart-header flex items-center justify-between gap-3">
              <h4 title="Git activity rolled up to DSS groups. A builder in several groups counts in each, so shares can overlap. Builders whose account was deleted are not attributable to a group.">
                Most active groups
              </h4>
              <PersistentPill />
            </div>
            <div className="px-4 py-3">
              {topGroups.length === 0 ? (
                <div className="text-xs text-[var(--text-muted)]">No group activity yet.</div>
              ) : (
                <div className="max-h-64 space-y-1 overflow-y-auto">
                  {topGroups.map((g) => (
                    <div
                      key={g.name}
                      className="adk-hover-row -mx-1 flex items-center gap-2 px-1 py-0.5"
                    >
                      <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--text-secondary)]">
                        {g.name}
                        <span className="ml-1.5 text-[10px] text-[var(--text-tertiary)]">
                          {g.builderCount}/{g.memberCount} building · {g.projectCount}{' '}
                          {g.projectCount === 1 ? 'project' : 'projects'} ·{' '}
                          {relDays(g.lastCommitMs, nowMs).text}
                        </span>
                      </span>
                      <span className="h-1 w-14 flex-shrink-0 overflow-hidden rounded-full bg-[var(--bg-elevated)]">
                        <span
                          className="block h-full rounded-full bg-[var(--accent)] transition-[width] duration-500"
                          style={{ width: `${(g.commits / groupCommitsMax) * 100}%` }}
                        />
                      </span>
                      <span className="w-14 flex-shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-primary)]">
                        {g.commits.toLocaleString()}
                      </span>
                    </div>
                  ))}
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
              <h4 title="Individual builders ranked by human commits across all projects. Chips show the audit-window build/consume mix and the config-history persona; the thin bar under a row is that builder's family mix (legend below).">
                Top builders
              </h4>
              <PersistentPill />
            </div>
            <div className="max-h-[19rem] space-y-1.5 overflow-y-auto px-4 py-3">
              {topBuilders.length === 0 && (
                <div className="text-xs text-[var(--text-muted)]">No builder activity yet.</div>
              )}
              {topBuilders.map((b, i) => {
                const persona = inventoryView?.personas[b.login];
                const builderBuckets = humans[b.login]?.buckets;
                return (
                  <div key={b.login} className="adk-hover-row -mx-1 px-1 py-0.5">
                    <div className="flex items-center gap-2">
                      <span className="w-5 flex-shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-tertiary)]">
                        {i + 1}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--text-secondary)]">
                        {b.displayName}
                        <span className="ml-1.5 text-[10px] text-[var(--text-tertiary)]">
                          {b.projectCount} proj · {b.activeMonths} mo ·{' '}
                          {relDays(b.lastCommitMs, nowMs).text}
                        </span>
                      </span>
                      {builderBuckets && <BuilderBucketChip buckets={builderBuckets} />}
                      {persona && <BuilderPersonaChip persona={persona} />}
                      <span className="h-1 w-14 flex-shrink-0 overflow-hidden rounded-full bg-[var(--bg-elevated)]">
                        <span
                          className="block h-full rounded-full bg-[var(--accent)] transition-[width] duration-500"
                          style={{ width: `${(b.commits / builderCommitsMax) * 100}%` }}
                        />
                      </span>
                      <span className="w-14 flex-shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-primary)]">
                        {b.commits.toLocaleString()}
                      </span>
                    </div>
                    {persona && (
                      <div className="ml-7 mt-1">
                        <SegmentBar height={4} segments={familyMixSegments(persona)} />
                      </div>
                    )}
                  </div>
                );
              })}
              {builders.length > topBuilders.length && (
                <div className="pt-0.5 text-[10px] text-[var(--text-tertiary)]">
                  +{builders.length - topBuilders.length} more builders
                </div>
              )}
            </div>
            {anyPersonaShown && (
              <div className="border-t border-[var(--border-glass)] px-4 py-2">
                <FamilyGroupLegend />
              </div>
            )}
          </motion.div>
        </div>

        {/* Cohorts + time-to-first-build + returning builders + recency */}
        <div
          className={`grid grid-cols-1 gap-6 ${ttfbPoints.length > 0 ? 'lg:grid-cols-3' : 'lg:grid-cols-2'}`}
        >
          {/* Onboarding cohorts — a real column chart over complete months */}
          <motion.div {...blockProps} className="chart-container">
            <div className="chart-header flex items-center justify-between gap-3">
              <h4 title="New user accounts created per complete month (from each user's creationDate). The running month is footnoted, never plotted.">
                Onboarding cohorts
              </h4>
              <PersistentPill />
            </div>
            <div className="px-4 py-3">
              {cohortPoints.length === 0 ? (
                <div className="text-xs text-[var(--text-muted)]">No user creation dates.</div>
              ) : (
                <>
                  <MiniColumns
                    points={cohortPoints}
                    height={96}
                    axisLeft={monthLabel(cohortPoints[0].key)}
                    axisRight={monthLabel(cohortPoints[cohortPoints.length - 1].key)}
                  />
                  <div className="pt-2 text-[10px] text-[var(--text-tertiary)]">
                    new accounts / month, last {cohortPoints.length} complete months
                    {cohortsThisMonth > 0 &&
                      ` · +${cohortsThisMonth} so far in ${monthLabel(currentMonthKey)}`}
                  </div>
                </>
              )}
            </div>
          </motion.div>

          {/* Time to first build — activation: account creation → first
              surviving created object. Cohorts predating the config history
              are excluded rather than measured dishonestly. */}
          {ttfb && ttfbPoints.length > 0 && (
            <motion.div {...blockProps} className="chart-container">
              <div className="chart-header flex items-center justify-between gap-3">
                <h4 title="Median days from account creation to a user's first surviving created object (activation), per monthly cohort. Cohorts older than the surviving config history — and the running month — are excluded.">
                  Time to first build
                </h4>
                <ConfigHistoryPill />
              </div>
              <div className="px-4 py-3">
                <div className="mb-3 flex items-baseline gap-4">
                  <BigStat
                    value={ttfb.overallMedianDays == null ? '—' : `${ttfb.overallMedianDays}d`}
                    label="Median, all measured users"
                  />
                  <span className="text-[10px] text-[var(--text-tertiary)]">
                    {ttfb.usersMeasured} {ttfb.usersMeasured === 1 ? 'user' : 'users'} measured
                    {ttfb.usersMeasured < 5 ? ' — small sample' : ''}
                  </span>
                </div>
                <MiniColumns
                  points={ttfbPoints}
                  height={80}
                  valueSuffix="d"
                  axisLeft={monthLabel(ttfbPoints[0].key)}
                  axisRight={monthLabel(ttfbPoints[ttfbPoints.length - 1].key)}
                />
                <div className="pt-2 text-[10px] text-[var(--text-tertiary)]">
                  median days to first build, by signup cohort
                  {ttfb.excludedCohorts > 0 &&
                    ` · ${ttfb.excludedCohorts} older ${ttfb.excludedCohorts === 1 ? 'cohort' : 'cohorts'} excluded (predate surviving history)`}
                </div>
              </div>
            </motion.div>
          )}

          {/* Returning builders + recently active */}
          <motion.div {...blockProps} className="chart-container">
            <div className="chart-header flex items-center justify-between gap-3">
              <h4 title="Builders active in multiple distinct months vs. a single month.">
                Returning builders &amp; recency
              </h4>
              <PersistentPill />
            </div>
            <div className="px-4 py-3">
              {repeat && repeat.total > 0 ? (
                <LinkedMix
                  items={[
                    {
                      key: 'returning',
                      label: 'Returning (active in 2+ months)',
                      value: repeat.repeat,
                      color: 'var(--neon-green)',
                    },
                    {
                      key: 'single',
                      label: 'One-month builders',
                      value: repeat.single,
                      color: 'var(--text-tertiary)',
                    },
                  ]}
                />
              ) : (
                <div className="text-xs text-[var(--text-muted)]">No builder activity yet.</div>
              )}

              <div className="mt-4 mb-2 text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
                Recently active people
              </div>
              <div className="space-y-0.5">
                {topRecency.length === 0 && (
                  <div className="text-xs text-[var(--text-muted)]">
                    No login activity recorded.
                  </div>
                )}
                {topRecency.map((u) => (
                  <div
                    key={u.login}
                    className="adk-hover-row -mx-1 flex items-center justify-between gap-2 px-1 py-0.5 text-[11px]"
                  >
                    <span className="min-w-0 flex-1 truncate text-[var(--text-secondary)]">
                      {u.displayName}
                    </span>
                    <span className="flex-shrink-0 font-mono tabular-nums text-[var(--text-tertiary)]">
                      {relDays(u.lastSessionActivity, nowMs).text}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </motion.div>
        </div>

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
            countBadge={{ total: projects.length }}
            lifecycle={isLoading ? lifecycle : null}
            rows={projects}
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
        </motion.div>
      </div>
    </div>
  );
}
