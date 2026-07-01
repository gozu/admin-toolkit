import { Fragment, useEffect, useState } from 'react';
import { useDiag } from '../../context/DiagContext';
import { adoptionScan } from '../../state/adoptionScan';
import { resolveLifecycleById } from '../../utils/pageLifecycle';
import { DataGrid } from '../common/DataGrid';
import { ProgressIndicator } from '../common/ProgressIndicator';
import { BigStat, BarRow, SegmentBar, UsageBar } from './missionControl/microViz';
import { CATEGORICAL_COLORS } from './missionControl/tokens';
import { AdoptionTrendChart } from './AdoptionTrendChart';
import type { ColumnDef } from '../../utils/dataGridTypes';
import type { AdoptionMonthPoint, AdoptionProjectRow } from '../../types';

const EMPTY: never[] = [];

// Window-honesty pill — v1 metrics are all persistent (git history / user
// snapshot span the full history). v1.1 audit-window cards get an
// "AUDIT · last N days" pill instead, never this one.
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

const MONTH_INITIALS = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'];

/** GitHub-style calendar of monthly commit intensity: one row per year, one
 * cell per month. Same data as the trend line, different lens — the grid makes
 * seasonal rhythm and dormant stretches readable at a glance. */
function ActivityHeatGrid({ points }: { points: AdoptionMonthPoint[] }) {
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
                  title={`${key} · ${p.commits.toLocaleString()} commits · ${p.activeBuilders} builders`}
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

function ProjectAuthorsPanel({ row }: { row: AdoptionProjectRow }) {
  const authors = row.authors ?? EMPTY;
  return (
    <div className="border-t border-[var(--border-glass)] bg-[var(--bg-glass)] px-4 py-3">
      <div className="mb-2 flex items-center gap-3 text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
        <span>
          {authors.length} distinct {authors.length === 1 ? 'builder' : 'builders'}
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
    </div>
  );
}

export function AdoptionPage() {
  const { state } = useDiag();
  const { data, scanStarted, error } = adoptionScan.use();
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  useEffect(() => {
    if (!scanStarted) void adoptionScan.load();
  }, [scanStarted]);

  const toggleSelect = (key: string) => setSelectedKey((cur) => (cur === key ? null : key));

  const lifecycle = resolveLifecycleById('adoption', state.parsedData);
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
  ];

  return (
    <div className="page-fill">
      <div className="flex flex-col gap-6 flex-1 min-h-0">
        {/* Summary band */}
        <div className="chart-container">
          <div className="chart-header flex items-center justify-between gap-3">
            <h4>Adoption &amp; Engagement</h4>
            <PersistentPill />
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
              <h4 title="Individual builders ranked by human commits across all projects.">
                Top builders
              </h4>
              <PersistentPill />
            </div>
            <div className="max-h-[19rem] space-y-1.5 overflow-y-auto px-4 py-3">
              {topBuilders.length === 0 && (
                <div className="text-xs text-[var(--text-muted)]">No builder activity yet.</div>
              )}
              {topBuilders.map((b, i) => (
                <div key={b.login} className="flex items-center gap-2">
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
              ))}
              {builders.length > topBuilders.length && (
                <div className="pt-0.5 text-[10px] text-[var(--text-tertiary)]">
                  +{builders.length - topBuilders.length} more builders
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Project leaderboard — people per project */}
        <DataGrid
          title="Projects — people & activity"
          countBadge={{ total: projects.length }}
          lifecycle={isLoading ? lifecycle : null}
          rows={projects}
          columns={columns}
          rowKey={(row) => row.projectKey}
          defaultSortColumnId="commits"
          defaultSortDir="desc"
          renderExpandedRow={(row) => <ProjectAuthorsPanel row={row} />}
          expandedRowKeys={expandedRowKeys}
          emptyMessage="Waiting for git history…"
          scroll="card"
        />

        {/* Cohorts + returning builders + recency */}
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
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
