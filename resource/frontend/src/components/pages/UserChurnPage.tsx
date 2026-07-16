import { useEffect, useState, type ReactNode } from 'react';
import { motion } from 'framer-motion';
import { userChurnScan } from '../../state/userChurnScan';
import { useDiag } from '../../context/DiagContext';
import { resolveLifecycleFromFields } from '../../utils/pageLifecycle';
import { buildChurnView, profileLabel, type DormantAccount } from '../../utils/userChurn';
import { DataGrid } from '../common/DataGrid';
import { ProgressIndicator } from '../common/ProgressIndicator';
import { BigStat, UsageBar } from './missionControl/microViz';
import { TILE_VARIANTS } from './missionControl/tokens';
import {
  UserChurnFlowChart,
  UserChurnReassignmentChart,
  type ChurnFlowMode,
} from './UserChurnCharts';
import type { ColumnDef } from '../../utils/dataGridTypes';
import type { ChurnAccount, ChurnEndSource } from '../../types';

const EMPTY: never[] = [];
const DAY_MS = 86_400_000;

// Below this floor a yearly chart is noise dressed as a trend — the numbers
// still show in the KPI band and the grids, just not as slab bars.
const MIN_DATED_ACCOUNTS = 3;

const DORMANT_CHOICES = [90, 180, 365] as const;
type DormantDays = (typeof DORMANT_CHOICES)[number];

const MONTH_ABBR = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

function fmtDate(ms: number | null | undefined): string {
  if (ms == null || ms <= 0) return '—';
  const d = new Date(ms);
  return `${d.getUTCDate()} ${MONTH_ABBR[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

function fmtDays(days: number | null): string {
  if (days == null) return '—';
  if (days >= 365) {
    const years = days / 365;
    return `${years >= 10 ? Math.round(years) : Math.round(years * 10) / 10}y`;
  }
  if (days >= 60) return `${Math.round(days / 30)}mo`;
  return `${Math.round(days)}d`;
}

/** Share label that never rounds a real count down to "0%". */
function pctLabel(value: number, total: number): string {
  if (value <= 0 || total <= 0) return '0%';
  const pct = (value / total) * 100;
  return pct < 1 ? '<1%' : `${Math.round(pct)}%`;
}

/** Chapter header — same three-questions pattern as the Activity page: the
 * question is the loudest text in its section and never goes unanswered. */
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
          <span className="font-mono text-[13px] tracking-[0.2em] text-[var(--text-muted)]">
            {no}
          </span>
          <h3 className="text-[17px] font-semibold text-[var(--text-primary)]">{title}</h3>
          {caption && <span className="text-xs text-[var(--text-tertiary)]">{caption}</span>}
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

function SegmentedToggle<T extends string | number>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <div className="flex flex-shrink-0 overflow-hidden rounded border border-[var(--border-glass)]">
      {options.map((opt) => (
        <button
          key={String(opt.value)}
          type="button"
          aria-pressed={value === opt.value}
          onClick={() => onChange(opt.value)}
          className={`px-2 py-0.5 font-mono text-xs uppercase tracking-[0.08em] transition-colors ${
            value === opt.value
              ? 'bg-[var(--bg-elevated)] text-[var(--text-primary)]'
              : 'text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]'
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

const END_SOURCE_META: Record<ChurnEndSource, { label: string; hint: string }> = {
  activity: {
    label: 'last activity',
    hint: 'Disable date proxied by the account’s last recorded session activity.',
  },
  login: {
    label: 'last login',
    hint: 'No session activity on record — proxied by the last successful login.',
  },
  created: {
    label: 'never used',
    hint: 'No login or activity ever recorded — proxied by the creation date.',
  },
};

function AccountCell({ account }: { account: ChurnAccount }) {
  return (
    <div className="min-w-0">
      <div className="truncate font-medium text-[var(--text-primary)]">{account.displayName}</div>
      {account.displayName !== account.login && (
        <div className="truncate font-mono text-[11px] text-[var(--text-tertiary)]">
          {account.login}
        </div>
      )}
    </div>
  );
}

export function UserChurnPage() {
  const { state } = useDiag();
  const { data, scanStarted, error } = userChurnScan.use();
  const [flowMode, setFlowMode] = useState<ChurnFlowMode>('flow');
  const [dormantDays, setDormantDays] = useState<DormantDays>(180);

  useEffect(() => {
    if (!scanStarted) void userChurnScan.load();
  }, [scanStarted]);

  const lifecycle = resolveLifecycleFromFields(['userChurnLoading'], state.parsedData);
  const isLoading = lifecycle.phase === 'running' || lifecycle.phase === 'queued';

  // Plain derivations — the React Compiler auto-memoizes. Reference "now" is
  // the payload's own timestamp, never the wall clock (purity rule).
  const accounts = data?.accounts ?? EMPTY;
  const nowMs = data?.generatedAtMs ?? 0;
  const view = buildChurnView(accounts, nowMs, dormantDays, data?.licensing);
  const disabledRows = accounts.filter((a) => !a.enabled);

  const datedCount = view.totalAccounts - view.undatedCount;
  const totalCreated = view.years.reduce((s, y) => s + y.created, 0);
  const totalChurned = view.years.reduce((s, y) => s + y.churned, 0);
  const firstYear = view.years[0]?.year;
  const spanYears = view.years.length;
  const chartsAsChart = datedCount >= MIN_DATED_ACCOUNTS && view.years.length > 0;

  // ── Computed chapter answers ────────────────────────────────────────────
  const ch1Answer =
    totalCreated === 0 ? null : (
      <>
        Since <strong>{firstYear}</strong>: <strong>{totalCreated.toLocaleString()}</strong>{' '}
        accounts created, <strong>{totalChurned.toLocaleString()}</strong> disabled — net{' '}
        <strong>
          {totalCreated - totalChurned >= 0 ? '+' : '−'}
          {Math.abs(totalCreated - totalChurned).toLocaleString()}
        </strong>
        {spanYears > 1 && totalChurned > 0 && (
          <>
            {' '}
            (~<strong>{Math.round((totalChurned / spanYears) * 10) / 10}</strong> disabled per
            year).
          </>
        )}
      </>
    );

  const ch2Answer =
    view.disabledCount === 0 ? (
      <>No — no account here has ever been disabled, so every seat handed out was brand new.</>
    ) : view.reassignedTotal > 0 ? (
      <>
        Yes — ≈<strong>{view.reassignedTotal.toLocaleString()}</strong> of{' '}
        <strong>{totalCreated.toLocaleString()}</strong> created accounts (
        {pctLabel(view.reassignedTotal, totalCreated)}) took over a seat freed by a disabled
        account of the same profile.
      </>
    ) : (
      <>
        Not yet — <strong>{view.disabledCount}</strong> freed{' '}
        {view.disabledCount === 1 ? 'seat has' : 'seats have'} no matching later account of the
        same profile.
      </>
    );

  const ch3Answer =
    view.enabledCount === 0 ? null : (
      <>
        <strong>{view.dormant.length.toLocaleString()}</strong> of{' '}
        <strong>{view.enabledCount.toLocaleString()}</strong> enabled accounts (
        {pctLabel(view.dormant.length, view.enabledCount)}) show no activity in ≥
        <strong>{dormantDays}d</strong> — disabling them frees their seats.
      </>
    );

  // ── Grid columns ────────────────────────────────────────────────────────
  const dormantColumns: ColumnDef<DormantAccount>[] = [
    {
      id: 'account',
      label: 'Account',
      render: (row) => <AccountCell account={row.account} />,
      sortValue: (row) => row.account.displayName.toLowerCase(),
      defaultSortDir: 'asc',
    },
    {
      id: 'profile',
      label: 'Profile',
      render: (row) => profileLabel(row.account.userProfile),
      sortValue: (row) => row.account.userProfile ?? '',
      cellClassName: 'text-[var(--text-secondary)]',
    },
    {
      id: 'groups',
      label: 'Groups',
      render: (row) => (
        <span
          className="text-[var(--text-tertiary)]"
          title={row.account.groups.join(', ') || undefined}
        >
          {row.account.groups.length > 2
            ? `${row.account.groups.slice(0, 2).join(', ')} +${row.account.groups.length - 2}`
            : row.account.groups.join(', ') || '—'}
        </span>
      ),
    },
    {
      id: 'created',
      label: 'Created',
      align: 'right',
      mono: true,
      cellClassName: 'text-[var(--text-secondary)]',
      render: (row) => fmtDate(row.account.creationDateMs),
      sortValue: (row) => row.account.creationDateMs ?? 0,
    },
    {
      id: 'lastActive',
      label: 'Last active',
      align: 'right',
      mono: true,
      render: (row) =>
        row.neverUsed ? (
          <span
            className="text-[11px] uppercase tracking-wide text-[var(--neon-amber)]"
            title="No login or session activity ever recorded for this account."
          >
            never
          </span>
        ) : (
          <span className="text-[var(--text-secondary)]">{fmtDate(row.lastActiveMs)}</span>
        ),
      sortValue: (row) => row.lastActiveMs ?? 0,
    },
    {
      id: 'idle',
      label: 'Idle',
      align: 'right',
      mono: true,
      defaultSortDir: 'desc',
      render: (row) => fmtDays(row.idleDays),
      sortValue: (row) => row.idleDays,
    },
  ];

  const disabledColumns: ColumnDef<ChurnAccount>[] = [
    {
      id: 'account',
      label: 'Account',
      render: (row) => <AccountCell account={row} />,
      sortValue: (row) => row.displayName.toLowerCase(),
      defaultSortDir: 'asc',
    },
    {
      id: 'profile',
      label: 'Profile',
      render: (row) => profileLabel(row.userProfile),
      sortValue: (row) => row.userProfile ?? '',
      cellClassName: 'text-[var(--text-secondary)]',
    },
    {
      id: 'created',
      label: 'Created',
      align: 'right',
      mono: true,
      cellClassName: 'text-[var(--text-secondary)]',
      render: (row) => fmtDate(row.creationDateMs),
      sortValue: (row) => row.creationDateMs ?? 0,
    },
    {
      id: 'disabled',
      label: 'Disabled (proxy)',
      align: 'right',
      defaultSortDir: 'desc',
      headerTooltip:
        'DSS stores no disable date — this is the account’s last recorded activity before it was disabled.',
      headerTooltipMarker: true,
      render: (row) => {
        const meta = row.endSource ? END_SOURCE_META[row.endSource] : null;
        return (
          <div className="flex items-baseline justify-end gap-2">
            {meta && (
              <span
                className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]"
                title={meta.hint}
              >
                {meta.label}
              </span>
            )}
            <span className="font-mono tabular-nums text-[var(--text-secondary)]">
              {fmtDate(row.effectiveEndMs)}
            </span>
          </div>
        );
      },
      sortValue: (row) => row.effectiveEndMs ?? 0,
    },
    {
      id: 'lifespan',
      label: 'Lifespan',
      align: 'right',
      mono: true,
      headerTooltip: 'Creation → disable proxy.',
      cellClassName: 'text-[var(--text-secondary)]',
      render: (row) =>
        row.creationDateMs != null && row.effectiveEndMs != null
          ? fmtDays(Math.max(0, (row.effectiveEndMs - row.creationDateMs) / DAY_MS))
          : '—',
      sortValue: (row) =>
        row.creationDateMs != null && row.effectiveEndMs != null
          ? row.effectiveEndMs - row.creationDateMs
          : -1,
    },
  ];

  // Entrance: each block fades up as it mounts. Static variants — no re-run
  // on data updates.
  const blockProps = {
    variants: TILE_VARIANTS,
    initial: 'hidden' as const,
    animate: 'show' as const,
  };

  if (!isLoading && !error && accounts.length === 0 && data != null) {
    return (
      <div className="w-full py-4">
        <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface)] p-6 text-sm text-[var(--text-secondary)]">
          No user accounts in this snapshot.
        </div>
      </div>
    );
  }

  return (
    <div className="page-fill">
      <div className="flex flex-col gap-6 flex-1 min-h-0">
        {/* Verdict + KPI band — the page states its own conclusion. */}
        <motion.div {...blockProps} className="chart-container">
          <div className="chart-header flex items-center justify-between gap-3">
            <h4>User Churn & Seats</h4>
            <span
              className="hidden font-mono text-xs uppercase tracking-[0.1em] text-[var(--text-tertiary)] sm:block"
              title="Built from the user snapshot (creation dates, enabled flags) and the login/session activity snapshot. DSS stores no disable date — a disabled account's end-of-life is proxied by its last recorded activity. Accounts deleted outright are invisible, so every churn number is a floor."
            >
              as of {fmtDate(nowMs)} · user + activity snapshot
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
          {data != null && accounts.length > 0 && (
            <>
              <div className="border-b border-[var(--border-glass)] px-4 py-3 text-[13px] leading-relaxed text-[var(--text-secondary)] [&_strong]:font-semibold [&_strong]:text-[var(--text-primary)]">
                <strong>{view.totalAccounts.toLocaleString()}</strong> accounts on the instance —{' '}
                <strong>{view.enabledCount.toLocaleString()}</strong> enabled,{' '}
                <strong>{view.disabledCount.toLocaleString()}</strong> disabled.
                {view.reassignedTotal > 0 && (
                  <>
                    {' '}
                    An estimated <strong>≈{view.reassignedTotal.toLocaleString()}</strong> seats
                    have been reassigned since {firstYear}.
                  </>
                )}
              </div>
              <div className="flex flex-wrap items-end gap-x-6 gap-y-4 px-4 py-4">
                <div title="Accounts in the current user snapshot.">
                  <BigStat
                    value={view.totalAccounts}
                    label="Accounts"
                    sub={`${view.enabledCount} enabled · ${view.disabledCount} disabled`}
                  />
                </div>
                <div className="hidden h-8 w-px bg-[var(--border-glass)] sm:block" />
                <div title="Disabled accounts whose end-of-life proxy falls in the last 365 days.">
                  <BigStat value={view.churnedLast365} label="Churned (12 mo)" />
                </div>
                <div title="Creations matched to a previously-freed seat of the same profile (running-pool estimate).">
                  <BigStat
                    value={view.reassignedTotal > 0 ? `≈${view.reassignedTotal}` : 0}
                    label="Seats reassigned"
                    sub="est., all time"
                  />
                </div>
                <div
                  title={`Enabled accounts with no login or session activity in ${dormantDays}+ days.`}
                >
                  <BigStat
                    value={view.dormant.length}
                    label="Reclaimable now"
                    sub={`idle ≥${dormantDays}d`}
                  />
                </div>
                <div title="Median creation → disable-proxy lifespan across disabled accounts.">
                  <BigStat
                    value={fmtDays(view.medianTenureDays)}
                    label="Median lifespan"
                    sub="of churned accounts"
                  />
                </div>
              </div>
            </>
          )}
        </motion.div>

        {data != null && accounts.length > 0 && (
          <>
            {/* ── 01 · How do accounts come and go? ──────────────────────── */}
            <motion.div {...blockProps} className="flex flex-col gap-4">
              <ChapterHeader
                no="01"
                title="How do accounts come and go?"
                answer={ch1Answer}
                caption="per calendar year · disable dates proxied by last activity"
              />
              {chartsAsChart ? (
                <div className="chart-container">
                  <div className="chart-header flex items-center justify-between gap-3">
                    <h4
                      title={
                        flowMode === 'cumulative'
                          ? 'Running balance of accounts (created minus disabled) at the end of each year. Deleted accounts are invisible, so the earliest years can undercount.'
                          : 'Accounts created (up) and disabled (down) per calendar year, with the net delta as a line. A disabled account is bucketed by its last recorded activity — DSS stores no disable date.'
                      }
                    >
                      {flowMode === 'cumulative'
                        ? 'Account balance — running total'
                        : 'Account flow — created vs disabled, per year'}
                    </h4>
                    <SegmentedToggle
                      value={flowMode}
                      options={[
                        { value: 'flow', label: 'Per year' },
                        { value: 'cumulative', label: 'Cumulative' },
                      ]}
                      onChange={setFlowMode}
                    />
                  </div>
                  <UserChurnFlowChart years={view.years} mode={flowMode} />
                  <div className="border-t border-[var(--border-glass)] px-4 py-2 text-xs text-[var(--text-tertiary)]">
                    deleted accounts are invisible — churn is a floor · the first year can be
                    partial where the instance&rsquo;s history begins
                    {view.undatedCount > 0 &&
                      ` · ${view.undatedCount} account${view.undatedCount === 1 ? '' : 's'} without a creation date excluded`}
                  </div>
                </div>
              ) : (
                <div className="px-4 text-[13px] text-[var(--text-secondary)]">
                  Only {datedCount} dated account{datedCount === 1 ? '' : 's'} — too few for a
                  yearly trend to mean anything. The grids below still list every account.
                </div>
              )}
            </motion.div>

            {/* ── 02 · Are seats being recycled? ─────────────────────────── */}
            <motion.div {...blockProps} className="flex flex-col gap-4">
              <ChapterHeader
                no="02"
                title="Are seats being recycled?"
                answer={ch2Answer}
                caption="freed seats matched to later creations of the same profile"
              />
              {chartsAsChart && view.disabledCount > 0 && (
                <div className="chart-container">
                  <div className="chart-header">
                    <h4 title="Each year's created accounts, split by where the seat came from: reassigned = matched to a seat freed EARLIER by a disabled account of the same profile (running-pool estimate in exact date order); brand-new = no freed seat was available yet. The stack always sums to the year's created total.">
                      Where new accounts got their seat
                    </h4>
                  </div>
                  <UserChurnReassignmentChart years={view.years} />
                  <div className="border-t border-[var(--border-glass)] px-4 py-2 text-xs text-[var(--text-tertiary)]">
                    estimate — a freed seat joins its profile&rsquo;s pool when an account is
                    disabled; the next creation of that profile drains the pool · deleted accounts
                    never free a visible seat
                  </div>
                </div>
              )}
              {/* Per-profile seat ledger — utilization joins the licensed caps
                  when the API key can read licensing. */}
              <div className="chart-container">
                <div className="chart-header">
                  <h4 title="Seat ledger per user profile: enabled seats today (vs the licensed cap where readable), all-time created / disabled accounts, and the reassignment estimate.">
                    Seats by profile
                  </h4>
                </div>
                <div className="px-4 py-3">
                  <div className="grid grid-cols-[minmax(120px,1.4fr)_minmax(140px,2fr)_repeat(3,minmax(64px,1fr))] items-center gap-x-4 gap-y-2">
                    <span className="text-[11px] uppercase tracking-[0.08em] text-[var(--text-tertiary)]">
                      Profile
                    </span>
                    <span className="text-[11px] uppercase tracking-[0.08em] text-[var(--text-tertiary)]">
                      Enabled seats
                    </span>
                    <span className="text-right text-[11px] uppercase tracking-[0.08em] text-[var(--text-tertiary)]">
                      Created
                    </span>
                    <span className="text-right text-[11px] uppercase tracking-[0.08em] text-[var(--text-tertiary)]">
                      Disabled
                    </span>
                    <span className="text-right text-[11px] uppercase tracking-[0.08em] text-[var(--text-tertiary)]">
                      Reassigned
                    </span>
                    {view.profiles.map((p) => {
                      const nearCap =
                        p.licensedLimit != null && p.enabled >= p.licensedLimit * 0.9;
                      return (
                        <div key={p.profile} className="contents">
                          <span className="truncate text-[13px] text-[var(--text-primary)]">
                            {profileLabel(p.profile)}
                          </span>
                          <span className="flex items-center gap-2">
                            <span className="w-16 flex-shrink-0 text-right font-mono text-xs tabular-nums text-[var(--text-primary)]">
                              {p.enabled}
                              {p.licensedLimit != null && (
                                <span className="text-[var(--text-tertiary)]">
                                  /{p.licensedLimit}
                                </span>
                              )}
                            </span>
                            <span className="min-w-[60px] flex-1">
                              {p.licensedLimit != null ? (
                                <UsageBar
                                  pct={(p.enabled / p.licensedLimit) * 100}
                                  tone={nearCap ? 'warn' : 'info'}
                                />
                              ) : (
                                <span
                                  className="text-[11px] text-[var(--text-muted)]"
                                  title="No licensed cap readable for this profile."
                                >
                                  no cap
                                </span>
                              )}
                            </span>
                          </span>
                          <span className="text-right font-mono text-xs tabular-nums text-[var(--text-secondary)]">
                            {p.created}
                          </span>
                          <span className="text-right font-mono text-xs tabular-nums text-[var(--text-secondary)]">
                            {p.churned}
                          </span>
                          <span className="text-right font-mono text-xs tabular-nums text-[var(--text-secondary)]">
                            {p.reassigned > 0 ? `≈${p.reassigned}` : '—'}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
                {data.licensing == null && (
                  <div className="border-t border-[var(--border-glass)] px-4 py-2 text-xs text-[var(--text-tertiary)]">
                    licensed seat caps unreadable with this API key — showing usage only
                  </div>
                )}
              </div>
            </motion.div>

            {/* ── 03 · What can be reclaimed today? ──────────────────────── */}
            <motion.div {...blockProps} className="flex flex-col gap-4">
              <ChapterHeader
                no="03"
                title="What can be reclaimed today?"
                answer={ch3Answer}
                caption="dormant = enabled but unused · disabling an account frees its seat"
                right={
                  <SegmentedToggle
                    value={dormantDays}
                    options={DORMANT_CHOICES.map((d) => ({ value: d, label: `${d}d` }))}
                    onChange={setDormantDays}
                  />
                }
              />
              <DataGrid
                rows={view.dormant}
                columns={dormantColumns}
                rowKey={(row) => row.account.login}
                defaultSortColumnId="idle"
                defaultSortDir="desc"
                title={`Dormant accounts — enabled, idle ≥${dormantDays}d`}
                countBadge={{ total: view.dormant.length }}
                emptyMessage={`No enabled account has been idle for ${dormantDays}+ days.`}
                scroll={{ maxH: '360px' }}
              />
              <DataGrid
                rows={disabledRows}
                columns={disabledColumns}
                rowKey={(row) => row.login}
                defaultSortColumnId="disabled"
                defaultSortDir="desc"
                title="Disabled accounts — the churn record"
                countBadge={{ total: disabledRows.length }}
                emptyMessage="No disabled accounts on this instance."
                scroll={{ maxH: '360px' }}
              />
            </motion.div>
          </>
        )}
      </div>
    </div>
  );
}
