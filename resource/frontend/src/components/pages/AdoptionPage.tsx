import { useEffect, useState } from 'react';
import { useDiag } from '../../context/DiagContext';
import { adoptionScan } from '../../state/adoptionScan';
import { resolveLifecycleById } from '../../utils/pageLifecycle';
import { DataGrid } from '../common/DataGrid';
import { ProgressIndicator } from '../common/ProgressIndicator';
import { BigStat, BarRow, SegmentBar, UsageBar } from './missionControl/microViz';
import { AdoptionTrendChart } from './AdoptionTrendChart';
import type { ColumnDef } from '../../utils/dataGridTypes';
import type { AdoptionProjectRow } from '../../types';

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
  const monthsSpan = trend.length;
  const peopleMax = Math.max(1, ...projects.map((p) => p.authorCount));
  const cohortMax = Math.max(1, ...cohorts.map((c) => c.newUsers));
  const expandedRowKeys = new Set(selectedKey ? [selectedKey] : []);
  const topRecency = [...recency]
    .filter((r) => r.lastSessionActivity != null)
    .sort((a, b) => (b.lastSessionActivity ?? 0) - (a.lastSessionActivity ?? 0))
    .slice(0, 10);

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
            <BigStat value={monthsSpan || '—'} label="Months tracked" />
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
