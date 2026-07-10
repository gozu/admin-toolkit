import { Fragment, useEffect, useState } from 'react';
import { useDiag } from '../../context/DiagContext';
import { adoptionScan } from '../../state/adoptionScan';
import { adoptionInventoryScan } from '../../state/adoptionInventoryScan';
import { adoptionEventsScan } from '../../state/adoptionEventsScan';
import { resolveLifecycleFromFields } from '../../utils/pageLifecycle';
import {
  buildInventoryView,
  sumFamilies,
  MATURITY_DIMENSIONS,
  TREND_GROUPS,
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
import { BigStat, BarRow, SegmentBar, UsageBar } from './missionControl/microViz';
import { CATEGORICAL_COLORS } from './missionControl/tokens';
import { AdoptionTrendChart } from './AdoptionTrendChart';
import { CreationTrendChart } from './CreationTrendChart';
import type { ColumnDef } from '../../utils/dataGridTypes';
import type { AdoptionMonthPoint, AdoptionProjectRow } from '../../types';

const EMPTY: never[] = [];

// Window-honesty pills — three provenances on this page, each stamped:
// - Persistent (git history / user snapshot): spans the full history.
// - Config (inventory macro): full history but only SURVIVING objects.
// - Audit (events macro): whatever window the rotated audit files still cover.
function PersistentPill() {
  return (
    <span
      className="badge badge-info font-mono text-[10px] uppercase tracking-[0.1em]"
      title="Spans each project's full git history — not a short audit window."
    >
      Persistent · full history
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
      Config · full history of surviving objects
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

/** Persona chip for a Top-Builders row, when the inventory knows this login
 * as a creator. */
function BuilderPersonaChip({ persona }: { persona: InventoryPersonaRow }) {
  return (
    <span
      className="rounded border border-[var(--border-glass)] bg-[var(--bg-elevated)] px-1 py-px font-mono text-[9px] uppercase tracking-[0.08em] text-[var(--text-tertiary)]"
      title={`${persona.created.toLocaleString()} surviving objects created · ${persona.shareSummary}`}
    >
      {persona.persona ?? `${persona.created} obj`}
    </span>
  );
}

const catColor = (i: number) =>
  i < CATEGORICAL_COLORS.length ? CATEGORICAL_COLORS[i] : 'var(--text-tertiary)';

const BUCKET_COLORS: Record<AuditEventBucket, string> = {
  build: 'var(--neon-green)',
  run: catColor(0),
  explore: catColor(1),
  consume: catColor(2),
  other: 'var(--text-tertiary)',
};

/** Compact build-share chip for a Top-Builders row (audit msgType buckets).
 * Tooltip carries the full bucket breakdown. */
function BuilderBucketChip({ buckets }: { buckets: Record<string, number> }) {
  const total = Object.values(buckets).reduce((s, v) => s + v, 0);
  if (total === 0) return null;
  const pct = (k: AuditEventBucket) => Math.round(((buckets[k] ?? 0) / total) * 100);
  return (
    <span
      className="rounded border border-[var(--border-glass)] bg-[var(--bg-elevated)] px-1 py-px font-mono text-[9px] uppercase tracking-[0.08em] text-[var(--text-tertiary)]"
      title={AUDIT_EVENT_BUCKETS.map((k) => `${k} ${pct(k)}%`).join(' · ')}
    >
      {pct('build')}% build · {pct('consume')}% consume
    </span>
  );
}

/** Derived health cards: edit intensity, staleness, bus factor, maturity,
 * seat types, dormant creators — all view-time collapses of the inventory
 * macro's accumulator, every one stamped with the survivorship caveat. */
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

  const editRows = [
    { label: 'Saved once', value: editIntensity.editBuckets.v1, color: 'var(--text-tertiary)' },
    { label: '2–5 saves', value: editIntensity.editBuckets.v2to5, color: catColor(0) },
    { label: '6–20 saves', value: editIntensity.editBuckets.v6to20, color: catColor(1) },
    { label: '21+ saves', value: editIntensity.editBuckets.v21plus, color: catColor(2) },
  ];
  const editMax = Math.max(1, ...editRows.map((r) => r.value));

  const staleSegments = [
    { label: 'Fresh (≤3 mo)', value: staleness.freshCount, color: 'var(--neon-green)' },
    { label: 'Aging (3–12 mo)', value: staleness.agingCount, color: 'var(--neon-yellow)' },
    { label: 'Stale (>12 mo)', value: staleness.staleCount, color: 'var(--neon-red)' },
    { label: 'Undated', value: staleness.unknownCount, color: 'var(--text-tertiary)' },
  ];
  const staleMax = Math.max(1, ...staleSegments.map((s) => s.value));

  const busRows = [
    { label: 'Single creator', value: busFactor.singleCreator, color: 'var(--neon-red)' },
    { label: '2–3 creators', value: busFactor.twoToThree, color: 'var(--neon-yellow)' },
    { label: '4+ creators', value: busFactor.fourPlus, color: 'var(--neon-green)' },
  ];
  const busMax = Math.max(1, ...busRows.map((r) => r.value));
  const singleSharePct =
    busFactor.measuredProjects > 0
      ? Math.round((busFactor.singleCreator / busFactor.measuredProjects) * 100)
      : null;

  const maturityMax = Math.max(1, ...maturityHistogram);
  const maturityTop = MATURITY_DIMENSIONS.length;
  const seatMax = Math.max(1, ...seatTypes.map((s) => Math.max(s.users, s.creators)));
  const topDormant = dormantCreators.slice(0, 8);

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
      {/* Edit intensity */}
      <div className="chart-container">
        <div className="chart-header flex items-center justify-between gap-3">
          <h4 title="How often surviving objects were re-saved after creation (versionTag save counts). Objects without a version tag are not measured.">
            Edit intensity
          </h4>
          <ConfigHistoryPill />
        </div>
        <div className="px-4 py-3">
          {editIntensity.versionedObjects === 0 ? (
            <div className="text-xs text-[var(--text-muted)]">No version tags found.</div>
          ) : (
            <>
              <SegmentBar
                height={6}
                segments={editRows.map((r) => ({
                  value: r.value,
                  color: r.color,
                  title: `${r.label} · ${r.value.toLocaleString()}`,
                }))}
              />
              <div className="mt-3 space-y-1">
                {editRows.map((r) => (
                  <BarRow
                    key={r.label}
                    label={r.label}
                    value={r.value.toLocaleString()}
                    pct={(r.value / editMax) * 100}
                    tone="info"
                  />
                ))}
              </div>
              <div className="pt-1.5 text-[10px] text-[var(--text-tertiary)]">
                {editIntensity.savedOnce.toLocaleString()} objects never re-saved after creation ·{' '}
                {editIntensity.versionedObjects.toLocaleString()} versioned objects measured
              </div>
            </>
          )}
        </div>
      </div>

      {/* Staleness / zombies */}
      <div className="chart-container">
        <div className="chart-header flex items-center justify-between gap-3">
          <h4 title="Age of each object's last config edit, measured against the newest edit anywhere in the config tree (never the wall clock). Zombie = a project whose newest config edit is over 12 months old.">
            Content staleness
          </h4>
          <ConfigHistoryPill />
        </div>
        <div className="px-4 py-3">
          <SegmentBar
            height={6}
            segments={staleSegments.map((s) => ({
              value: s.value,
              color: s.color,
              title: `${s.label} · ${s.value.toLocaleString()}`,
            }))}
          />
          <div className="mt-3 space-y-1">
            {staleSegments.map((s) => (
              <BarRow
                key={s.label}
                label={s.label}
                value={s.value.toLocaleString()}
                pct={(s.value / staleMax) * 100}
                tone="info"
              />
            ))}
          </div>
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
        <div className="chart-header flex items-center justify-between gap-3">
          <h4 title="Knowledge concentration: how many distinct creators each project's surviving objects have. Single-creator projects lose all context if that person leaves.">
            Bus factor
          </h4>
          <ConfigHistoryPill />
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
                  tone={
                    singleSharePct == null ? undefined : singleSharePct >= 50 ? 'warn' : 'ok'
                  }
                />
                <span className="text-[10px] text-[var(--text-tertiary)]">
                  of {busFactor.measuredProjects} projects with tagged objects
                </span>
              </div>
              <SegmentBar
                height={6}
                segments={busRows.map((r) => ({
                  value: r.value,
                  color: r.color,
                  title: `${r.label} · ${r.value.toLocaleString()} projects`,
                }))}
              />
              <div className="mt-3 space-y-1">
                {busRows.map((r) => (
                  <BarRow
                    key={r.label}
                    label={r.label}
                    value={r.value.toLocaleString()}
                    pct={(r.value / busMax) * 100}
                    tone="info"
                  />
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Maturity distribution */}
      <div className="chart-container">
        <div className="chart-header flex items-center justify-between gap-3">
          <h4
            title={`One point per practice present in a project's surviving objects: ${MATURITY_DIMENSIONS.map((d) => d.label).join(' · ')}.`}
          >
            Project maturity (0–{maturityTop})
          </h4>
          <ConfigHistoryPill />
        </div>
        <div className="space-y-1 px-4 py-3">
          {maturityHistogram.map((count, score) => (
            <BarRow
              key={score}
              label={
                <span title={score === 0 ? 'No practice dimension present' : undefined}>
                  score {score}
                </span>
              }
              value={`${count.toLocaleString()} proj`}
              pct={(count / maturityMax) * 100}
              tone="info"
            />
          ))}
        </div>
      </div>

      {/* Seat types × creators */}
      <div className="chart-container">
        <div className="chart-header flex items-center justify-between gap-3">
          <h4 title="Seat profiles from the user snapshot crossed with config-history creators. 'unknown profile' groups creators whose account no longer exists.">
            Seat types — who actually builds
          </h4>
          <ConfigHistoryPill />
        </div>
        <div className="max-h-72 space-y-1.5 overflow-y-auto px-4 py-3">
          {seatTypes.length === 0 && (
            <div className="text-xs text-[var(--text-muted)]">No user snapshot available.</div>
          )}
          {seatTypes.map((s) => {
            const pct = s.users > 0 ? (s.creators / s.users) * 100 : null;
            return (
              <div key={s.profile} className="flex items-center gap-2">
                <span
                  className="min-w-0 flex-1 truncate text-[11px] text-[var(--text-secondary)]"
                  title={
                    s.users > 0
                      ? `${s.creators} of ${s.users} ${s.profile} accounts created at least one surviving object`
                      : `${s.creators} creators are not in the current user snapshot (likely deleted accounts)`
                  }
                >
                  {s.profile}
                  <span className="ml-1.5 text-[10px] text-[var(--text-tertiary)]">
                    {s.creators} {s.creators === 1 ? 'creator' : 'creators'}
                    {s.users > 0 ? ` / ${s.users} users` : ''}
                  </span>
                </span>
                <span className="h-1 w-14 flex-shrink-0 overflow-hidden rounded-full bg-[var(--bg-elevated)]">
                  <span
                    className="block h-full rounded-full bg-[var(--accent)]"
                    style={{ width: `${(Math.max(s.users, s.creators) / seatMax) * 100}%` }}
                  />
                </span>
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
        <div className="chart-header flex items-center justify-between gap-3">
          <h4
            title={`Creators with surviving objects but no session in the last ${view.dormantThresholdDays} days (measured against the newest session in the snapshot) — knowledge that may be walking out the door.`}
          >
            Dormant creators
          </h4>
          <ConfigHistoryPill />
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
                <div key={d.login} className="flex items-center gap-2 text-[11px]">
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
function InventoryCreatorsSection({ inv, invNowMs }: { inv: InventoryProjectViewRow; invNowMs: number }) {
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
      <div className="mb-2 max-w-md">
        <SegmentBar
          height={4}
          segments={TREND_GROUPS.map((g, gi) => ({
            value: inv.groups[gi],
            color: catColor(gi),
            title: `${g.label} · ${inv.groups[gi].toLocaleString()}`,
          }))}
        />
      </div>
      <div className="flex flex-wrap gap-1.5">
        {creators.length === 0 && (
          <span className="text-xs text-[var(--text-muted)]">
            No creation tags in this project.
          </span>
        )}
        {shown.map(([login, count]) => (
          <span
            key={login}
            className="rounded border border-[var(--border-glass)] bg-[var(--bg-elevated)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--text-secondary)]"
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

const MONTH_INITIALS = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'];

/** GitHub-style calendar of monthly activity intensity: one row per year, one
 * cell per month. Same data as the trend line, different lens — the grid makes
 * seasonal rhythm and dormant stretches readable at a glance. */
function ActivityHeatGrid({
  points,
  unitWord = 'commits',
  peopleWord = 'builders',
}: {
  points: AdoptionMonthPoint[];
  /** Tooltip nouns — the inventory reuse maps objects/creators onto the same
   * shape (never "active users": MAU is owned by another tool). */
  unitWord?: string;
  peopleWord?: string;
}) {
  if (points.length < 2) return null;
  const maxCommits = Math.max(1, ...points.map((p) => p.commits));
  const byMonth = new Map(points.map((p) => [p.month, p]));
  const years = [...new Set(points.map((p) => Number(p.month.slice(0, 4))))].sort();
  return (
    <div className="border-t border-[var(--border-glass)] px-4 pb-3 pt-2.5">
      <div
        className="grid items-center gap-[3px]"
        style={{ gridTemplateColumns: 'auto repeat(12, minmax(0, 1fr))' }}
      >
        <span />
        {MONTH_INITIALS.map((m, i) => (
          <span
            key={i}
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
            {MONTH_INITIALS.map((_, i) => {
              const key = `${y}-${String(i + 1).padStart(2, '0')}`;
              const p = byMonth.get(key);
              if (!p) {
                // outside the tracked span — visually absent, not "zero"
                return <span key={key} className="h-3 rounded-[2px]" />;
              }
              const alpha = p.commits === 0 ? 0 : 0.14 + 0.86 * Math.sqrt(p.commits / maxCommits);
              return (
                <span
                  key={key}
                  title={`${key} · ${p.commits.toLocaleString()} ${unitWord} · ${p.activeBuilders} ${peopleWord}`}
                  className="h-3 rounded-[2px]"
                  style={{
                    background:
                      p.commits === 0 ? 'var(--bg-elevated)' : `rgba(109, 163, 224, ${alpha})`,
                  }}
                />
              );
            })}
          </Fragment>
        ))}
      </div>
    </div>
  );
}

function fmtDate(ms: number | null | undefined): string {
  if (ms == null) return '—';
  const d = new Date(ms);
  return Number.isFinite(d.getTime()) ? d.toISOString().slice(0, 10) : '—';
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
            className="rounded border border-[var(--border-glass)] bg-[var(--bg-elevated)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--text-secondary)]"
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
  const nowMs = data?.generatedAtMs ?? 0;
  const projects = data?.projectRows ?? EMPTY;
  const groups = data?.groups ?? EMPTY;
  const builders = data?.builderStats ?? EMPTY;
  const peopleMax = Math.max(1, ...projects.map((p) => p.authorCount));
  const cohortMax = Math.max(1, ...cohorts.map((c) => c.newUsers));
  const expandedRowKeys = new Set(selectedKey ? [selectedKey] : []);
  const topRecency = [...recency]
    .filter((r) => r.lastSessionActivity != null)
    .sort((a, b) => (b.lastSessionActivity ?? 0) - (a.lastSessionActivity ?? 0))
    .slice(0, 10);

  // Momentum (customer's "is usage increasing?"): mean active builders over the
  // last 3 COMPLETE months vs the 3 before — the current month is partial and
  // would always read as a dip, so it is excluded.
  const completeTrend = trend.slice(0, -1);
  let momentumPct: number | null = null;
  if (completeTrend.length >= 6) {
    const mean = (pts: AdoptionMonthPoint[]) =>
      pts.reduce((s, p) => s + p.activeBuilders, 0) / pts.length;
    const recent = mean(completeTrend.slice(-3));
    const prior = mean(completeTrend.slice(-6, -3));
    momentumPct = prior > 0 ? ((recent - prior) / prior) * 100 : null;
  }

  const topGroups = groups.slice(0, 8);
  const groupCommitsMax = Math.max(1, ...topGroups.map((g) => g.commits));
  const groupColor = (i: number) =>
    i < CATEGORICAL_COLORS.length ? CATEGORICAL_COLORS[i] : 'var(--text-tertiary)';
  const topBuilders = builders.slice(0, 12);
  const builderCommitsMax = Math.max(1, ...topBuilders.map((b) => b.commits));

  // ── inventory-fed derivations (all null-safe) ─────────────────────────────
  // Config-tree object inventory (full history of surviving objects) — absent
  // until the macro returns; every section fed by it null-guards.
  const inventoryView = buildInventoryView(invState.data, recency);
  const composition = inventoryView?.composition ?? EMPTY;
  const topComposition = composition.slice(0, 8);
  const compositionMax = Math.max(1, ...topComposition.map((c) => c.count));
  const ttfb = inventoryView?.ttfb;
  const ttfbCohorts = (ttfb?.cohorts ?? EMPTY).slice(-12);
  const ttfbMaxDays = Math.max(1, ...ttfbCohorts.map((c) => c.medianDays ?? 0));
  const familyMixSegments = (persona: InventoryPersonaRow) =>
    TREND_GROUPS.map((group, gi) => {
      const value = sumFamilies(persona.byFamily, group.families);
      return {
        value,
        color: catColor(gi),
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
                        color: catColor(gi),
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
              if (!inv) return <span className="text-[var(--text-muted)]">—</span>;
              const pct = Math.round(inv.stalePct);
              return (
                <span
                  className={pct >= 50 ? 'text-[var(--neon-red)]' : 'text-[var(--text-secondary)]'}
                  title="Share of dated objects whose last config edit is over 12 months old."
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

  return (
    <div className="page-fill">
      <div className="flex flex-col gap-6 flex-1 min-h-0">
        {/* Summary band */}
        <div className="chart-container">
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
            <BigStat
              value={
                momentumPct == null
                  ? '—'
                  : `${momentumPct >= 0 ? '+' : ''}${momentumPct.toFixed(0)}%`
              }
              label="Momentum (3m vs prior 3m)"
              tone={
                momentumPct == null ? undefined : momentumPct >= 2 ? 'ok' : momentumPct <= -2 ? 'warn' : undefined
              }
            />
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
        </div>

        {/* Flagship: multi-month adoption trend */}
        <div className="chart-container">
          <div className="chart-header flex items-center justify-between gap-3">
            <h4 title="Distinct people committing per month + commit volume, back to each project's oldest commit.">
              Adoption trend — active builders &amp; commit volume
            </h4>
            <PersistentPill />
          </div>
          <AdoptionTrendChart points={trend} />
          <ActivityHeatGrid points={trend} />
        </div>

        {/* What humans actually do in the audit window — msgType buckets,
            UI-vs-API split, and the raw hot list. Heuristic classification;
            automation actors are excluded server-side. */}
        {auditMix && (
          <div className="chart-container">
            <div className="chart-header flex items-center justify-between gap-3">
              <h4 title="Human audit events classified by msgType: build = config writes (saves/creates), run = jobs & scenarios, explore = reads & lists, consume = dashboards, business apps, exports. Heuristic buckets — hover the hot list for raw event types.">
                What's hot this window — human event mix
              </h4>
              <AuditWindowPill coverageDays={coverageDays} />
            </div>
            <div className="grid grid-cols-1 gap-x-8 gap-y-4 px-4 py-3 lg:grid-cols-2">
              <div>
                <SegmentBar
                  height={6}
                  segments={AUDIT_EVENT_BUCKETS.map((b) => ({
                    value: auditMix.buckets[b] ?? 0,
                    color: BUCKET_COLORS[b],
                    title: `${AUDIT_EVENT_BUCKET_LABELS[b]} · ${(auditMix.buckets[b] ?? 0).toLocaleString()}`,
                  }))}
                />
                <div className="mt-3 space-y-1">
                  {AUDIT_EVENT_BUCKETS.map((b) => {
                    const value = auditMix.buckets[b] ?? 0;
                    return (
                      <div key={b} className="flex items-center gap-2">
                        <span
                          className="h-2 w-2 flex-shrink-0 rounded-[2px]"
                          style={{ background: BUCKET_COLORS[b] }}
                        />
                        <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--text-secondary)]">
                          {AUDIT_EVENT_BUCKET_LABELS[b]}
                        </span>
                        <span className="w-10 flex-shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-tertiary)]">
                          {Math.round((value / auditMix.classified) * 100)}%
                        </span>
                        <span className="w-16 flex-shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-primary)]">
                          {value.toLocaleString()}
                        </span>
                      </div>
                    );
                  })}
                </div>
                {auditMix.authTotal > 0 && (
                  <>
                    <div className="mt-4 mb-1 flex items-center justify-between text-[11px] text-[var(--text-secondary)]">
                      <span>
                        <span className="font-mono text-[var(--neon-green)]">
                          {Math.round((auditMix.fromUi / auditMix.authTotal) * 100)}%
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
                          color: 'var(--neon-green)',
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
              </div>
              <div className="space-y-1">
                <div className="mb-2 text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
                  Top event types (human)
                </div>
                {auditMix.hot.map(([msgType, count]) => (
                  <BarRow
                    key={msgType}
                    label={
                      <span className="inline-flex items-center gap-1.5">
                        <span
                          className="h-2 w-2 flex-shrink-0 rounded-[2px]"
                          style={{ background: BUCKET_COLORS[classifyMsgType(msgType)] }}
                        />
                        <span className="font-mono" title={AUDIT_EVENT_BUCKET_LABELS[classifyMsgType(msgType)]}>
                          {msgType}
                        </span>
                      </span>
                    }
                    value={count.toLocaleString()}
                    pct={(count / hotMax) * 100}
                    tone="info"
                  />
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Long spine: monthly object creation mined from config-tree tags.
            Units differ from the git trend (objects created vs commits) so
            the two are never merged — the audit window is only shaded as a
            band for scale. */}
        {inventoryView && (
          <div className="chart-container">
            <div className="chart-header flex items-center justify-between gap-3">
              <h4
                title={`Objects created per month across the full (surviving) config history — ${inventoryView.taggedObjects.toLocaleString()} of ${inventoryView.objectsBuilt.toLocaleString()} objects carry creation tags.${auditWindow ? ' The shaded band marks the much shorter audit-log window.' : ''}`}
              >
                Monthly creation trend — what got built, by family
              </h4>
              <ConfigHistoryPill />
            </div>
            <CreationTrendChart points={inventoryView.trendPoints} auditWindow={auditWindow} />
            <ActivityHeatGrid
              points={inventoryView.heatPoints}
              unitWord="objects created"
              peopleWord="creators that month"
            />
          </div>
        )}

        {/* Composition: what kinds of objects this instance builds */}
        {inventoryView && (
          <div className="chart-container">
            <div className="chart-header flex items-center justify-between gap-3">
              <h4 title="Every surviving config-tree object, grouped by family. Tag coverage varies: scenarios and notebooks carry no creation tags, so they count here but not in the creation trend.">
                What gets built here
              </h4>
              <ConfigHistoryPill />
            </div>
            <div className="px-4 py-3">
              <SegmentBar
                height={6}
                segments={topComposition.map((c, i) => ({
                  value: c.count,
                  color: catColor(i),
                  title: `${c.label} · ${c.count.toLocaleString()}`,
                }))}
              />
              <div className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
                {topComposition.map((c, i) => (
                  <div key={c.family} className="flex items-center gap-2">
                    <span
                      className="h-2 w-2 flex-shrink-0 rounded-[2px]"
                      style={{ background: catColor(i) }}
                    />
                    <span
                      className="min-w-0 flex-1 truncate text-[11px] text-[var(--text-secondary)]"
                      title={
                        c.topSubtypes.length > 0
                          ? `Tagged ${c.tagged.toLocaleString()} of ${c.count.toLocaleString()} · top types: ${c.topSubtypes.map((s) => `${s.subtype} (${s.count.toLocaleString()})`).join(', ')}`
                          : `Tagged ${c.tagged.toLocaleString()} of ${c.count.toLocaleString()}`
                      }
                    >
                      {c.label}
                    </span>
                    <span className="h-1 w-14 flex-shrink-0 overflow-hidden rounded-full bg-[var(--bg-elevated)]">
                      <span
                        className="block h-full rounded-full"
                        style={{
                          width: `${(c.count / compositionMax) * 100}%`,
                          background: catColor(i),
                        }}
                      />
                    </span>
                    <span className="w-16 flex-shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-primary)]">
                      {c.count.toLocaleString()}
                    </span>
                  </div>
                ))}
              </div>
              {composition.length > topComposition.length && (
                <div className="pt-1.5 text-[10px] text-[var(--text-tertiary)]">
                  +{composition.length - topComposition.length} more{' '}
                  {composition.length - topComposition.length === 1 ? 'family' : 'families'} ·{' '}
                  {composition
                    .slice(topComposition.length)
                    .map((c) => `${c.label} (${c.count.toLocaleString()})`)
                    .join(', ')}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Config health & knowledge risk — derived analytics */}
        {inventoryView && (
          <InventoryHealthCards
            view={inventoryView}
            nowMs={invNowMs}
            hasSessionData={hasSessionData}
          />
        )}

        {/* Who drives the activity: DSS groups + individual builders */}
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <div className="chart-container">
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
                <>
                  <SegmentBar
                    height={6}
                    segments={topGroups.map((g, i) => ({
                      value: g.commits,
                      color: groupColor(i),
                      title: `${g.name} · ${g.commits.toLocaleString()} commits`,
                    }))}
                  />
                  <div className="mt-3 max-h-64 space-y-1.5 overflow-y-auto">
                    {topGroups.map((g, i) => (
                      <div key={g.name} className="flex items-center gap-2">
                        <span
                          className="h-2 w-2 flex-shrink-0 rounded-[2px]"
                          style={{ background: groupColor(i) }}
                        />
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
                            className="block h-full rounded-full"
                            style={{
                              width: `${(g.commits / groupCommitsMax) * 100}%`,
                              background: groupColor(i),
                            }}
                          />
                        </span>
                        <span className="w-14 flex-shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-primary)]">
                          {g.commits.toLocaleString()}
                        </span>
                      </div>
                    ))}
                    {groups.length > topGroups.length && (
                      <div className="pt-0.5 text-[10px] text-[var(--text-tertiary)]">
                        +{groups.length - topGroups.length} more{' '}
                        {groups.length - topGroups.length === 1 ? 'group' : 'groups'}
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>

          <div className="chart-container">
            <div className="chart-header flex items-center justify-between gap-3">
              <h4 title="Individual builders ranked by human commits across all projects. Chips show the audit-window build/consume mix and the config-history persona, when known.">
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
                  <div key={b.login}>
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
                          className="block h-full rounded-full bg-[var(--accent)]"
                          style={{ width: `${(b.commits / builderCommitsMax) * 100}%` }}
                        />
                      </span>
                      <span className="w-14 flex-shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--text-primary)]">
                        {b.commits.toLocaleString()}
                      </span>
                    </div>
                    {persona && (
                      <div className="ml-7 mt-0.5">
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
          </div>
        </div>

        {/* Project leaderboard — people per project (+ config history) */}
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

        {/* Cohorts + time-to-first-build + returning builders + recency */}
        <div
          className={`grid grid-cols-1 gap-6 ${ttfbCohorts.length > 0 ? 'lg:grid-cols-3' : 'lg:grid-cols-2'}`}
        >
          {/* Onboarding cohorts */}
          <div className="chart-container">
            <div className="chart-header flex items-center justify-between gap-3">
              <h4 title="New user accounts created per month (from each user's creationDate).">
                Onboarding cohorts
              </h4>
              <PersistentPill />
            </div>
            <div className="max-h-72 space-y-1 overflow-y-auto px-4 py-3">
              {cohorts.length === 0 && (
                <div className="text-xs text-[var(--text-muted)]">No user creation dates.</div>
              )}
              {[...cohorts].reverse().map((c) => (
                <BarRow
                  key={c.month}
                  label={c.month}
                  value={`${c.newUsers}`}
                  pct={(c.newUsers / cohortMax) * 100}
                  tone="info"
                />
              ))}
            </div>
          </div>

          {/* Time to first build — activation: account creation → first
              surviving created object. Cohorts predating the config history
              are excluded rather than measured dishonestly. */}
          {ttfb && ttfbCohorts.length > 0 && (
            <div className="chart-container">
              <div className="chart-header flex items-center justify-between gap-3">
                <h4 title="Median days from account creation to a user's first surviving created object (activation). Cohorts older than the surviving config history are excluded — their first build may have been deleted since.">
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
                  </span>
                </div>
                <div className="max-h-56 space-y-1 overflow-y-auto">
                  {[...ttfbCohorts].reverse().map((c) => (
                    <BarRow
                      key={c.month}
                      label={
                        <span title={`${c.builders} of ${c.cohortUsers} users in this cohort built something that survives`}>
                          {c.month}
                          <span className="ml-1.5 text-[10px] text-[var(--text-tertiary)]">
                            {c.builders}/{c.cohortUsers} built
                          </span>
                        </span>
                      }
                      value={c.medianDays == null ? '—' : `${c.medianDays}d`}
                      pct={c.medianDays == null ? 0 : (c.medianDays / ttfbMaxDays) * 100}
                      tone="info"
                    />
                  ))}
                </div>
                {ttfb.excludedCohorts > 0 && (
                  <div className="pt-1.5 text-[10px] text-[var(--text-tertiary)]">
                    {ttfb.excludedCohorts} older{' '}
                    {ttfb.excludedCohorts === 1 ? 'cohort' : 'cohorts'} excluded (predate surviving
                    history).
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Returning builders + recently active */}
          <div className="chart-container">
            <div className="chart-header flex items-center justify-between gap-3">
              <h4 title="Builders active in multiple distinct months vs. a single month.">
                Returning builders &amp; recency
              </h4>
              <PersistentPill />
            </div>
            <div className="px-4 py-3">
              {repeat && repeat.total > 0 ? (
                <>
                  <div className="mb-1 flex items-center justify-between text-[11px] text-[var(--text-secondary)]">
                    <span>
                      <span className="font-mono text-[var(--neon-green)]">{repeat.repeat}</span>{' '}
                      returning
                    </span>
                    <span>
                      <span className="font-mono text-[var(--text-tertiary)]">{repeat.single}</span>{' '}
                      one-month · <span className="font-mono">{repeat.total}</span> total
                    </span>
                  </div>
                  <SegmentBar
                    segments={[
                      {
                        value: repeat.repeat,
                        color: 'var(--neon-green)',
                        title: `${repeat.repeat} returning`,
                      },
                      {
                        value: repeat.single,
                        color: 'var(--text-tertiary)',
                        title: `${repeat.single} one-month`,
                      },
                    ]}
                    height={6}
                  />
                </>
              ) : (
                <div className="text-xs text-[var(--text-muted)]">No builder activity yet.</div>
              )}

              <div className="mt-4 mb-2 text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
                Recently active people
              </div>
              <div className="space-y-1">
                {topRecency.length === 0 && (
                  <div className="text-xs text-[var(--text-muted)]">
                    No login activity recorded.
                  </div>
                )}
                {topRecency.map((u) => (
                  <div
                    key={u.login}
                    className="flex items-center justify-between gap-2 text-[11px]"
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
          </div>
        </div>
      </div>
    </div>
  );
}
