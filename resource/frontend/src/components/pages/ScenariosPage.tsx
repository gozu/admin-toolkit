import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { scenariosScan } from '../../state/scenariosStore';
import { ProgressIndicator } from '../common/ProgressIndicator';
import { dssUrls } from '../../utils/codeEnvUsageLinks';
import {
  RANGES,
  type RangeKey,
  type ScenarioCategory,
  type ScheduleSegment,
  SCENARIO_CATEGORIES,
  projectSegments,
  bucketLoad,
  peakTimeOfDay,
  axisTicks,
  countScenarioCategories,
  filterScenariosByCategories,
  scenarioTriggerSummary,
  minActiveIntervalMs,
  overlapRisk,
} from '../../utils/scenarioSchedule';
import type { ScenarioRow } from '../../types';

const GRID_COLS =
  'minmax(6rem,0.85fr) minmax(9rem,1.3fr) minmax(8rem,1.15fr) minmax(4.5rem,0.55fr) minmax(5rem,0.7fr) minmax(16rem,3fr)';

const CATEGORY_META: Record<
  ScenarioCategory,
  { label: string; title: string; className: string }
> = {
  'active-time-based': {
    label: 'Active time-based',
    title: 'Enabled scenarios with at least one active time-based trigger.',
    className:
      'bg-[var(--neon-green)]/20 text-[var(--neon-green)] border border-[var(--neon-green)]/40',
  },
  'inactive-time-based': {
    label: 'Inactive time-based',
    title: 'Disabled scenarios that still contain at least one active time-based trigger.',
    className:
      'bg-[var(--text-tertiary)]/15 text-[var(--text-secondary)] border border-[var(--text-tertiary)]/30',
  },
  'event-based': {
    label: 'Event-based',
    title: 'Scenarios with an active event trigger and no active time-based trigger.',
    className:
      'bg-[var(--neon-purple)]/20 text-[var(--neon-purple)] border border-[var(--neon-purple)]/40',
  },
  'no-trigger': {
    label: 'No trigger',
    title: 'Scenarios with no active trigger; they can only be started manually or through the API.',
    className:
      'bg-[var(--neon-amber)]/15 text-[var(--neon-amber)] border border-[var(--neon-amber)]/35',
  },
};

const OUTCOME_COLORS: Record<string, string> = {
  SUCCESS: 'var(--neon-green)',
  WARNING: 'var(--neon-amber)',
  FAILED: 'var(--neon-red)',
  ABORTED: 'var(--neon-red)',
};

/** "in 34 min" / "in 2 h" / "in 3 d" — DSS's own nextRun vs the scan finish
 *  time (never the wall clock: the React Compiler purity rule bans Date.now()
 *  in render). */
function fmtUntil(ms: number | null, nowMs: number): string {
  if (!ms) return '—';
  if (!nowMs) return new Date(ms).toLocaleString();
  const mins = Math.round((ms - nowMs) / 60_000);
  if (mins <= 0) return 'due';
  if (mins < 60) return `in ${mins} min`;
  if (mins < 48 * 60) return `in ${Math.round(mins / 60)} h`;
  return `in ${Math.round(mins / 1440)} d`;
}

function fmtAgo(ms: number | null, nowMs: number): string {
  if (!ms) return '—';
  if (!nowMs) return new Date(ms).toLocaleString();
  const mins = Math.round((nowMs - ms) / 60_000);
  if (mins <= 0) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  if (mins < 48 * 60) return `${Math.round(mins / 60)} h ago`;
  return `${Math.round(mins / 1440)} d ago`;
}

function fmtDuration(ms: number | null): string {
  if (ms == null) return '?';
  if (ms < 1000) return `${(ms / 1000).toFixed(1)} s`;
  if (ms < 120_000) return `${Math.round(ms / 1000)} s`;
  if (ms < 2 * 3_600_000) return `${Math.round(ms / 60_000)} min`;
  return `${(ms / 3_600_000).toFixed(1)} h`;
}

export function ScenariosPage() {
  const { data, loading, error, scanStarted, scanPhase, scanMessage, startedAt, finishedAt } =
    scenariosScan.use();

  // Preserve the diag-parser page's initial "Active" view: an explicit selected
  // category, ORed with the others, empty selection = show all.
  const [categoryFilter, setCategoryFilter] = useState<Set<ScenarioCategory>>(
    () => new Set<ScenarioCategory>(['active-time-based']),
  );
  const [range, setRange] = useState<RangeKey>('month');

  useEffect(() => {
    if (!scanStarted) void scenariosScan.load();
  }, [scanStarted]);

  const lifecycle = scenariosScan.lifecycle();
  const complete = scanPhase === 'complete' && !!data;
  const aborted = scanPhase === 'aborted' && !loading;
  const nowMs = Date.parse(finishedAt ?? startedAt ?? '') || 0;

  const allScenarios = useMemo(() => data?.scenarios ?? [], [data]);
  const categoryCounts = useMemo(() => countScenarioCategories(allScenarios), [allScenarios]);
  const runningNow = useMemo(
    () => allScenarios.filter((s) => s.running).length,
    [allScenarios],
  );
  // Health signals across ALL scenarios (not just the filtered view) — the
  // filter defaults to active-time-based, and a broken chain on an event-based
  // row must not be invisible.
  const signals = useMemo(() => {
    let failing = 0;
    let silent = 0;
    let overlap = 0;
    let badRunAs = 0;
    for (const s of allScenarios) {
      if (s.failureStreak >= 1) {
        failing++;
        if (s.activeReporters === 0) silent++;
      }
      if (overlapRisk(s)) overlap++;
      if (s.runAsInvalid) badRunAs++;
    }
    const chains = data?.chainIssues ?? [];
    return {
      failing,
      silent,
      overlap,
      badRunAs,
      chainsBroken: chains.filter((c) => c.kind === 'missing').length,
      chainsDormant: chains.filter((c) => c.kind === 'dormant').length,
      any: failing + overlap + badRunAs + chains.length > 0,
    };
  }, [allScenarios, data]);

  const shownRows = useMemo(
    () =>
      filterScenariosByCategories(allScenarios, categoryFilter).sort(
        (a, b) => a.projectKey.localeCompare(b.projectKey) || a.id.localeCompare(b.id),
      ),
    [allScenarios, categoryFilter],
  );
  const shownTimeBased = useMemo(
    () => shownRows.filter((scenario) => scenario.hasTimeSchedule),
    [shownRows],
  );
  const shownActiveTimeBased = useMemo(
    () => shownTimeBased.filter((scenario) => scenario.active),
    [shownTimeBased],
  );

  const load = useMemo(() => bucketLoad(shownRows, range), [shownRows, range]);
  const maxLoad = useMemo(() => Math.max(1, ...load.map((b) => b.count)), [load]);
  const peakBucket = useMemo(
    () => load.reduce((mi, b, i, arr) => (b.count > arr[mi].count ? i : mi), 0),
    [load],
  );
  const peak = useMemo(() => peakTimeOfDay(shownActiveTimeBased), [shownActiveTimeBased]);
  const ticks = useMemo(() => axisTicks(range), [range]);
  const rows = useMemo(
    () => shownRows.map((s) => ({ scenario: s, segments: projectSegments(s, range) })),
    [shownRows, range],
  );

  const showAdvisor = peak.count >= 3;
  const toggleCategoryFilter = (category: ScenarioCategory) => {
    setCategoryFilter((previous) => {
      const next = new Set(previous);
      if (next.has(category)) next.delete(category);
      else next.add(category);
      return next;
    });
  };

  return (
    <div className="space-y-4">
      <div className="glass-card space-y-3 p-4">
        <div className="flex flex-wrap items-start gap-3">
          <div>
            <h4 className="text-sm font-semibold text-[var(--text-primary)]">
              Scenario schedules
            </h4>
            <p className="mt-0.5 max-w-3xl text-xs text-[var(--text-muted)]">
              When scenarios are <em>configured</em> to fire, projected onto one shared timeline —
              clustering (dozens of scenarios at 02:00) shows up as a load spike. Trigger times are
              normalized to server time{data?.serverTz ? ` (${data.serverTz})` : ''};{' '}
              <strong>Next run</strong> is DSS&apos;s own computation, and{' '}
              <strong>Last run</strong> comes from the real run history.
            </p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            {loading ? (
              <button
                type="button"
                onClick={() => scenariosScan.abort()}
                className="rounded border border-[var(--neon-red)]/40 px-3 py-1 text-xs font-mono text-[var(--neon-red)] hover:bg-[var(--neon-red)]/10"
              >
                Abort
              </button>
            ) : (
              <button
                type="button"
                onClick={() => void scenariosScan.load(true)}
                className="rounded px-3 py-1 text-xs text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)]"
              >
                Rescan
              </button>
            )}
          </div>
        </div>

        {loading && (
          <div>
            <ProgressIndicator lifecycle={lifecycle} />
            <div className="mt-1 font-mono text-xs text-[var(--text-muted)]">{scanMessage}</div>
          </div>
        )}

        {data && allScenarios.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-xs text-[var(--text-muted)]">Filter:</span>
            {SCENARIO_CATEGORIES.map((id) => {
              const active = categoryFilter.has(id);
              return (
                <button
                  key={id}
                  type="button"
                  title={CATEGORY_META[id].title}
                  aria-pressed={active}
                  onClick={() => toggleCategoryFilter(id)}
                  className={`rounded px-2 py-0.5 text-xs font-semibold transition-all ${CATEGORY_META[id].className} ${
                    active ? 'ring-2 ring-[var(--neon-cyan)]/60' : 'opacity-70 hover:opacity-100'
                  }`}
                >
                  {CATEGORY_META[id].label} {categoryCounts[id]}
                </button>
              );
            })}
            {runningNow > 0 && (
              <span className="badge badge-warning" title="Scenarios executing right now">
                {runningNow} running now
              </span>
            )}
            <span className="ml-auto text-[var(--text-secondary)]">
              Showing {shownRows.length} of {allScenarios.length}
            </span>
          </div>
        )}

        {data && signals.any && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-xs text-[var(--text-muted)]">Signals:</span>
            {signals.failing > 0 && (
              <span
                className="badge badge-critical"
                title="Newest completed run FAILED or ABORTED"
              >
                {signals.failing} failing
              </span>
            )}
            {signals.silent > 0 && (
              <span
                className="badge badge-warning"
                title="Failing with no active reporter — nobody hears about it"
              >
                {signals.silent} failing silently
              </span>
            )}
            {signals.overlap > 0 && (
              <span
                className="badge badge-warning"
                title="Average run outlasts the shortest trigger interval — the scenario cannot keep up with its own schedule"
              >
                {signals.overlap} overlap risk
              </span>
            )}
            {signals.chainsBroken > 0 && (
              <span
                className="badge badge-critical"
                title="follow-scenario triggers whose target scenario no longer exists"
              >
                {signals.chainsBroken} broken chain{signals.chainsBroken === 1 ? '' : 's'}
              </span>
            )}
            {signals.chainsDormant > 0 && (
              <span
                className="badge badge-warning"
                title="follow-scenario triggers whose target is disabled or has no active trigger — the chain only moves on manual runs"
              >
                {signals.chainsDormant} dormant chain{signals.chainsDormant === 1 ? '' : 's'}
              </span>
            )}
            {signals.badRunAs > 0 && (
              <span
                className="badge badge-critical"
                title="Run-as login no longer exists or is disabled"
              >
                {signals.badRunAs} bad run-as
              </span>
            )}
          </div>
        )}

        {complete && data && !data.usersChecked && (
          <div className="text-xs text-[var(--text-muted)]">
            User list unavailable — run-as validity was not checked.
          </div>
        )}

        {complete && data && data.failedProjects.length > 0 && (
          <div className="text-xs text-[var(--neon-yellow)]">
            {data.failedProjects.length} project
            {data.failedProjects.length === 1 ? '' : 's'} could not be read — the schedule below is
            a floor, not a total, and chain verdicts are off rather than guessed.
          </div>
        )}

        {aborted && (
          <div className="text-xs text-[var(--neon-yellow)]">
            Scan aborted — showing partial results. Rescan for the full picture.
          </div>
        )}
      </div>

      {error && (
        <div className="glass-card flex items-center gap-3 border border-[var(--neon-red)]/40 p-3 text-sm text-[var(--neon-red)]">
          <span className="flex-1">{error}</span>
          <button
            type="button"
            onClick={() => void scenariosScan.load(true)}
            className="rounded px-2 py-1 text-xs font-medium text-[var(--accent)] hover:underline"
          >
            Retry
          </button>
        </div>
      )}

      {allScenarios.length > 0 && (
        <div className="glass-card overflow-hidden">
          {/* Timeline legend + range controls */}
          <div className="px-4 py-2.5 border-b border-[var(--border-glass)] flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center gap-3 text-[10px] text-[var(--text-secondary)]">
              <span className="flex items-center gap-1">
                <span
                  className="inline-block w-3.5 h-[3px] rounded-full"
                  style={{ backgroundColor: 'var(--neon-green)' }}
                />
                active schedule
              </span>
              <span className="flex items-center gap-1">
                <span
                  className="inline-block w-3.5 h-[3px] rounded-full"
                  style={{
                    backgroundColor: 'color-mix(in srgb, var(--text-muted) 70%, transparent)',
                  }}
                />
                inactive
              </span>
            </div>
            <div className="flex gap-1 bg-[var(--bg-elevated)] rounded-lg p-1">
              {RANGES.map((r) => (
                <SegButton key={r.key} active={range === r.key} onClick={() => setRange(r.key)}>
                  {r.label}
                </SegButton>
              ))}
            </div>
          </div>

          {/* Advisor callout */}
          {showAdvisor && (
            <div className="px-4 pt-3">
              <div
                className="p-3 rounded text-xs leading-relaxed"
                style={{
                  backgroundColor: 'color-mix(in srgb, var(--neon-amber) 10%, transparent)',
                  border: '1px solid color-mix(in srgb, var(--neon-amber) 30%, transparent)',
                  color: 'var(--neon-amber)',
                }}
              >
                <strong>{peak.count} scenarios</strong> are scheduled to fire around{' '}
                <strong>{String(peak.hour).padStart(2, '0')}:00</strong>. Clustering many scenarios
                at the same time-of-day causes avoidable load spikes — stagger their{' '}
                <strong>temporal trigger times</strong> in each scenario&apos;s{' '}
                <em>Settings → Triggers</em> to flatten the peak across the day.
              </div>
            </div>
          )}

          {shownRows.length === 0 ? (
            <div className="px-4 py-6 text-sm text-[var(--text-secondary)] text-center">
              No scenarios match the selected filters.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <div className="min-w-[70rem]">
                {/* Load-distribution strip, aligned to the timeline axis */}
                <div
                  className="grid items-end px-4 pt-3 pb-2 border-b border-[var(--border-glass)] gap-x-2"
                  style={{ gridTemplateColumns: GRID_COLS }}
                >
                  <div
                    className="text-[10px] uppercase tracking-wide text-[var(--text-muted)] self-center"
                    style={{ gridColumn: '1 / 6' }}
                  >
                    Load distribution
                    <span className="ml-1 normal-case tracking-normal">
                      ({shownTimeBased.length} time-based)
                    </span>
                  </div>
                  <div className="relative h-10 flex items-end gap-px">
                    {load.map((b) => (
                      <div
                        key={b.index}
                        className="flex-1 rounded-t-sm"
                        title={`${b.label}: ${b.count} scenario${b.count === 1 ? '' : 's'}`}
                        style={{
                          height: `${Math.max(b.count > 0 ? 8 : 0, (b.count / maxLoad) * 100)}%`,
                          backgroundColor:
                            b.index === peakBucket && b.count > 0
                              ? 'var(--neon-magenta)'
                              : 'var(--neon-cyan-dim)',
                        }}
                      />
                    ))}
                  </div>
                </div>

                {/* Column headers + axis ticks */}
                <div
                  className="grid items-end px-4 py-2 border-b border-[var(--border-glass)] gap-x-2 text-[10px] uppercase tracking-wide text-[var(--text-muted)]"
                  style={{ gridTemplateColumns: GRID_COLS }}
                >
                  <div>Project</div>
                  <div>Scenario</div>
                  <div>Trigger</div>
                  <div>Next run</div>
                  <div>Last run</div>
                  <div className="relative h-4">
                    {ticks
                      .filter((t) => t.major && t.label)
                      .map((t, i) => (
                        <span
                          key={i}
                          className="absolute -translate-x-1/2 whitespace-nowrap"
                          style={{ left: `${t.pos * 100}%` }}
                        >
                          {t.label}
                        </span>
                      ))}
                  </div>
                </div>

                {/* Timeline rows */}
                <div className="max-h-[520px] overflow-y-auto">
                  {rows.map(({ scenario, segments }) => (
                    <ScenarioTimelineRow
                      key={`${scenario.projectKey}/${scenario.id}`}
                      scenario={scenario}
                      segments={segments}
                      ticks={ticks}
                      nowMs={nowMs}
                    />
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {complete && data && allScenarios.length === 0 && (
        <div className="glass-card p-6 text-center">
          <div className="mb-1 text-2xl text-[var(--neon-green)]">&#10003;</div>
          <div className="text-sm text-[var(--text-secondary)]">No scenarios on this host.</div>
        </div>
      )}
    </div>
  );
}

function outcomeInitials(outcomes: string[]): string {
  return outcomes.map((o) => o.charAt(0)).join(' ');
}

function ScenarioTimelineRow({
  scenario,
  segments,
  ticks,
  nowMs,
}: {
  scenario: ScenarioRow;
  segments: ScheduleSegment[];
  ticks: ReturnType<typeof axisTicks>;
  nowMs: number;
}) {
  // A row whose settings fetch failed carries no triggers; DSS's own digest
  // from the listing still tells the truth about its schedule.
  const triggerText = scenario.settingsError
    ? scenario.triggerDigest || 'settings unreadable'
    : scenarioTriggerSummary(scenario);
  const detail = [
    scenario.runAsUser ? `runs as ${scenario.runAsUser}` : null,
    scenario.lastModifiedBy ? `last modified by ${scenario.lastModifiedBy}` : null,
    scenario.markedAsTest ? 'marked as test' : null,
  ]
    .filter(Boolean)
    .join(' · ');

  const hasOverlap = overlapRisk(scenario);
  const overlapTitle = hasOverlap
    ? `Average run ${fmtDuration(scenario.avgDurationMs)} outlasts the shortest trigger interval ${fmtDuration(minActiveIntervalMs(scenario))} — fires queue up behind runs that cannot keep pace`
    : undefined;

  const lastRunTitle = scenario.lastRunOutcome
    ? `${scenario.lastRunOutcome} · took ${fmtDuration(
        scenario.lastRunEnd && scenario.lastRunStart
          ? scenario.lastRunEnd - scenario.lastRunStart
          : null,
      )} · avg ${fmtDuration(scenario.avgDurationMs)} over ${scenario.runsSampled} run${
        scenario.runsSampled === 1 ? '' : 's'
      } · recent: ${outcomeInitials(scenario.recentOutcomes)}`
    : scenario.runsError
      ? `Run history unreadable: ${scenario.runsError}`
      : 'No completed runs on record';

  return (
    <div
      className="grid items-center px-4 py-1.5 gap-x-2 border-b border-[var(--border-glass)]/40 hover:bg-[var(--bg-glass-hover)] text-sm"
      style={{ gridTemplateColumns: GRID_COLS }}
    >
      <div
        className="truncate text-[var(--text-secondary)] font-mono text-xs"
        title={scenario.projectKey}
      >
        {scenario.projectKey}
      </div>
      <div className="flex min-w-0 items-center gap-1.5">
        <a
          href={dssUrls.scenario(scenario.projectKey, scenario.id)}
          target="_blank"
          rel="noopener noreferrer"
          className={
            'truncate font-medium hover:underline ' +
            (scenario.active ? 'text-[var(--neon-cyan)]' : 'text-[var(--text-muted)]')
          }
          title={
            (scenario.active ? scenario.name : `${scenario.name} (inactive)`) +
            (detail ? ` — ${detail}` : '')
          }
        >
          {scenario.name}
        </a>
        {scenario.running && (
          <span className="badge badge-warning shrink-0" title="Executing right now">
            running
          </span>
        )}
        {scenario.markedAsTest && (
          <span
            className="shrink-0 font-mono text-[10px] text-[var(--text-tertiary)]"
            title="Marked as test scenario"
          >
            test
          </span>
        )}
        {scenario.failureStreak >= 2 && (
          <span
            className="badge badge-critical shrink-0"
            title={`The last ${scenario.failureStreak} completed runs failed or were aborted`}
          >
            {scenario.failureStreak}&times; failed
          </span>
        )}
        {scenario.failureStreak >= 1 && scenario.activeReporters === 0 && (
          <span
            className="badge badge-warning shrink-0"
            title="Failing with no active reporter configured — nobody hears about it"
          >
            silent
          </span>
        )}
        {hasOverlap && (
          <span className="badge badge-warning shrink-0" title={overlapTitle}>
            overlap
          </span>
        )}
        {scenario.chainIssue && (
          <span
            className={
              scenario.chainIssue.kind === 'missing'
                ? 'badge badge-critical shrink-0'
                : 'badge badge-warning shrink-0'
            }
            title={
              scenario.chainIssue.kind === 'missing'
                ? `Follows ${scenario.chainIssue.target}, which no longer exists — this trigger will never fire`
                : `Follows ${scenario.chainIssue.target}, which is disabled or has no active trigger — the chain only moves on manual runs`
            }
          >
            {scenario.chainIssue.kind === 'missing' ? 'chain broken' : 'chain dormant'}
          </span>
        )}
        {scenario.runAsInvalid && (
          <span
            className="badge badge-critical shrink-0"
            title={`Runs as ${scenario.runAsUser}, which ${
              scenario.runAsInvalid === 'missing' ? 'no longer exists' : 'is disabled'
            }`}
          >
            run-as {scenario.runAsInvalid}
          </span>
        )}
      </div>
      <div
        className={
          'truncate text-xs ' +
          (scenario.settingsError ? 'text-[var(--neon-yellow)]' : 'text-[var(--text-secondary)]')
        }
        title={scenario.settingsError ? `Settings unreadable: ${scenario.settingsError}` : triggerText}
      >
        {triggerText}
      </div>
      <div
        className={
          'truncate text-xs ' +
          (scenario.nextRun ? 'text-[var(--text-secondary)]' : 'text-[var(--text-tertiary)]')
        }
        title={scenario.nextRun ? new Date(scenario.nextRun).toLocaleString() : 'Nothing scheduled'}
      >
        {fmtUntil(scenario.nextRun, nowMs)}
      </div>
      <div className="flex items-center gap-1.5 truncate text-xs" title={lastRunTitle}>
        {scenario.lastRunOutcome ? (
          <>
            <span
              className="inline-block h-2 w-2 shrink-0 rounded-full"
              style={{
                backgroundColor:
                  OUTCOME_COLORS[scenario.lastRunOutcome] ?? 'var(--text-tertiary)',
              }}
            />
            <span
              className={
                'truncate ' +
                (scenario.failureStreak >= 1
                  ? 'text-[var(--neon-red)]'
                  : 'text-[var(--text-secondary)]')
              }
            >
              {fmtAgo(scenario.lastRunStart, nowMs)}
            </span>
          </>
        ) : (
          <span className="text-[var(--text-tertiary)]">
            {scenario.runsError ? 'unreadable' : 'never'}
          </span>
        )}
      </div>
      <ScheduleTrack ticks={ticks} segments={segments} active={scenario.active} />
    </div>
  );
}

function SegButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        'px-3 py-1 text-xs font-medium rounded transition-colors ' +
        (active
          ? 'bg-[var(--neon-cyan)]/20 text-[var(--neon-cyan)]'
          : 'text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)]')
      }
    >
      {children}
    </button>
  );
}

function ScheduleTrack({
  ticks,
  segments,
  active,
}: {
  ticks: ReturnType<typeof axisTicks>;
  segments: ScheduleSegment[];
  active: boolean;
}) {
  const color = active
    ? 'var(--neon-green)'
    : 'color-mix(in srgb, var(--text-muted) 70%, transparent)';
  return (
    <div className="relative h-5">
      {/* vertical gridlines */}
      {ticks.map((t, i) => (
        <div
          key={i}
          className="absolute top-0 bottom-0 w-px"
          style={{
            left: `${t.pos * 100}%`,
            backgroundColor: t.major
              ? 'color-mix(in srgb, var(--text-muted) 26%, transparent)'
              : 'color-mix(in srgb, var(--text-muted) 11%, transparent)',
          }}
        />
      ))}
      {/* Schedule marks. Calendar ranges: one short bar per firing day cell.
          24h point fires: a fixed-width pill centered on the clock time. */}
      {segments.map((s, i) =>
        s.point ? (
          <div
            key={i}
            className="absolute rounded-full"
            title={s.label}
            style={{
              left: `${s.start * 100}%`,
              width: '14px',
              top: '50%',
              height: '4px',
              transform: 'translate(-50%, -50%)',
              backgroundColor: color,
              boxShadow: active
                ? '0 0 5px color-mix(in srgb, var(--neon-green) 55%, transparent)'
                : 'none',
            }}
          />
        ) : (
          <div
            key={i}
            className="absolute rounded-full"
            title={s.label}
            style={{
              left: `${s.start * 100}%`,
              width: `${(s.end - s.start) * 100}%`,
              minWidth: '3px',
              top: '50%',
              height: '3px',
              transform: 'translateY(-50%)',
              backgroundColor: color,
              boxShadow: active
                ? '0 0 5px color-mix(in srgb, var(--neon-green) 55%, transparent)'
                : 'none',
            }}
          />
        ),
      )}
    </div>
  );
}
