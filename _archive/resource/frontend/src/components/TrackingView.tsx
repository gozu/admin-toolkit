import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Container } from './Container';
import { fetchJson } from '../utils/api';
import { useDiag } from '../context/DiagContext';
import { fetchWithSessionCache, subscribeSessionEpoch } from '../state/sessionCache';

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface CampaignRow {
  campaign_id: string;
  open_issues: number;
  resolved_issues: number;
  regressed_issues: number;
  total_emails_sent: number;
  last_emailed: string | null;
  earliest_issue: string | null;
}

interface UserRow {
  login: string;
  email: string | null;
  campaigns: CampaignRow[];
}

interface AggregatedUser {
  login: string;
  email: string | null;
  open: number;
  resolved: number;
  regressed: number;
  emailed: number;
  lastEmailed: string | null;
  earliest: string | null;
  campaigns: CampaignRow[];
}

interface Issue {
  issue_id: number;
  campaign_id: string;
  entity_type: string;
  entity_key: string;
  entity_name: string | null;
  status: 'open' | 'resolved' | 'regressed';
  metrics_json: Record<string, unknown> | null;
  first_detected_at: string;
  last_detected_at: string | null;
  resolved_at: string | null;
  resolution_reason: string | null;
  times_emailed: number;
  times_regressed: number;
  last_emailed_at: string | null;
}

type SortKey = 'login' | 'email' | 'open' | 'resolved' | 'regressed' | 'emailed' | 'lastEmailed' | 'earliest';

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const CAMPAIGN_LABELS: Record<string, string> = {
  project: 'Multiple Code Envs',
  code_env: 'Mismatched Code Env Ownership',
  code_studio: 'Excessive Code Studios',
  auto_scenario: 'Auto-Start Scenarios',
  scenario_frequency: 'High-Frequency Scenarios',
  scenario_failing: 'Failing Scenarios',
  disabled_user: 'Disabled User Projects',
  deprecated_code_env: 'Deprecated Python Version',
  default_code_env: 'No Default Code Env',
  empty_project: 'Empty Project',
  large_flow: 'Large Flow (>100 objects)',
  orphan_notebooks: 'Orphan Notebooks',
  overshared_project: 'Overshared Project',
  inactive_project: 'Inactive Projects',
};

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function relativeTime(iso: string | null): string {
  if (!iso) return '\u2014';
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 0) return 'just now';
  const mins = Math.floor(diff / 60_000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  return `${months}mo ago`;
}

function shortDate(iso: string | null): string {
  if (!iso) return '\u2014';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function aggregate(users: UserRow[]): AggregatedUser[] {
  return users.map((u) => {
    let open = 0, resolved = 0, regressed = 0, emailed = 0;
    let lastEmailed: string | null = null;
    let earliest: string | null = null;
    for (const c of u.campaigns) {
      open += c.open_issues;
      resolved += c.resolved_issues;
      regressed += c.regressed_issues;
      emailed += c.total_emails_sent;
      if (c.last_emailed && (!lastEmailed || c.last_emailed > lastEmailed)) lastEmailed = c.last_emailed;
      if (c.earliest_issue && (!earliest || c.earliest_issue < earliest)) earliest = c.earliest_issue;
    }
    return { login: u.login, email: u.email, open, resolved, regressed, emailed, lastEmailed, earliest, campaigns: u.campaigns };
  });
}

function campaignLabel(id: string): string {
  return CAMPAIGN_LABELS[id] || id;
}

function formatMetrics(campaignId: string, metrics: Record<string, unknown> | null): string {
  if (!metrics) return '';
  switch (campaignId) {
    case 'orphan_notebooks':
      return [
        metrics.notebook_count != null ? `${metrics.notebook_count} notebooks` : null,
        metrics.recipe_count != null ? `${metrics.recipe_count} recipes` : null,
      ].filter(Boolean).join(', ') || '';
    case 'empty_project':
      return metrics.disk_size_gb != null ? `${metrics.disk_size_gb} GB` : '';
    case 'project':
      return metrics.code_env_count != null ? `${metrics.code_env_count} code envs` : '';
    case 'deprecated_code_env':
      return (metrics.python_version as string) || (metrics.version as string) || '';
    case 'scenario_failing':
      return metrics.last_outcome ? `Last: ${metrics.last_outcome}` : '';
    case 'scenario_frequency':
      return metrics.min_period_minutes != null ? `Every ${metrics.min_period_minutes}m` : '';
    case 'code_studio':
      return metrics.code_studio_count != null ? `${metrics.code_studio_count} studios` : '';
    case 'large_flow':
      return metrics.object_count != null ? `${metrics.object_count} objects` : '';
    case 'overshared_project':
      return metrics.group_count != null ? `${metrics.group_count} groups` : '';
    case 'auto_scenario':
      return metrics.active_count != null ? `${metrics.active_count} active` : '';
    default:
      return '';
  }
}

function statusPill(status: string) {
  switch (status) {
    case 'open':
      return <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-500/15 text-red-400">open</span>;
    case 'resolved':
      return <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-500/15 text-emerald-400">resolved</span>;
    case 'regressed':
      return <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-amber-500/15 text-amber-400">regressed</span>;
    default:
      return <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-gray-500/15 text-gray-400">{status}</span>;
  }
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function TrackingView() {
  const { addDebugLog } = useDiag();
  const logRef = useRef(addDebugLog);
  logRef.current = addDebugLog;
  const log = useCallback((msg: string, level: 'info' | 'warn' | 'error' = 'info') => {
    logRef.current(msg, 'tracking', level);
  }, []);

  const [users, setUsers] = useState<AggregatedUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('open');
  const [sortAsc, setSortAsc] = useState(false);
  const [expandedLogin, setExpandedLogin] = useState<string | null>(null);

  const loadUsers = useCallback(() => {
    const t0 = performance.now();
    log('GET /api/tracking/users (session-cached)');
    return fetchWithSessionCache('tracking:users', () =>
      fetchJson<{ users: UserRow[] }>('/api/tracking/users'),
    )
      .then((data) => {
        const ms = (performance.now() - t0).toFixed(0);
        const agg = aggregate(data.users);
        const totalOpen = agg.reduce((s, u) => s + u.open, 0);
        const totalResolved = agg.reduce((s, u) => s + u.resolved, 0);
        log(`GET /api/tracking/users OK (${ms}ms) — ${data.users.length} raw → ${agg.length} users, open=${totalOpen} resolved=${totalResolved}`);
        if (data.users.length > 0) {
          log(`Sample user: ${JSON.stringify(data.users[0]).slice(0, 200)}`);
        } else {
          log('No users returned (DB may be empty, waiting for refresh)', 'warn');
        }
        setUsers(agg);
        setError(null);
      })
      .catch((err) => {
        const ms = (performance.now() - t0).toFixed(0);
        const status = (err as { status?: number }).status ?? '?';
        const body = (err as { bodySnippet?: string }).bodySnippet ?? '';
        log(`GET /api/tracking/users FAILED (${ms}ms) status=${status} body=${body}`, 'error');
        // Auto-probe debug endpoint to diagnose DB state
        log('Probing /api/tracking/debug for DB diagnostics...');
        fetchJson<Record<string, unknown>>('/api/tracking/debug')
          .then((dbg) => log(`DB debug: ${JSON.stringify(dbg).slice(0, 500)}`))
          .catch((dbgErr) => log(`DB debug probe also failed: ${dbgErr instanceof Error ? dbgErr.message : dbgErr}`, 'error'));
        setError(err instanceof Error ? err.message : 'Failed to load tracking data');
      });
  }, [log]);

  // On mount: load existing data from DB (session-cached — revisits are free).
  useEffect(() => {
    let cancelled = false;
    log('=== TrackingView MOUNTED ===');
    log('Loading user data (session-cached)...');
    loadUsers().finally(() => {
      if (!cancelled) {
        log('User data loaded, spinner dismissed');
        setLoading(false);
      }
    });
    const unsubscribe = subscribeSessionEpoch(() => {
      if (cancelled) return;
      setLoading(true);
      loadUsers().finally(() => {
        if (!cancelled) setLoading(false);
      });
    });
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [loadUsers, log]);

  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    if (!q) return users;
    return users.filter((u) =>
      u.login.toLowerCase().includes(q) || (u.email ?? '').toLowerCase().includes(q),
    );
  }, [users, search]);

  const sorted = useMemo(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      let cmp = 0;
      switch (sortKey) {
        case 'login': cmp = a.login.localeCompare(b.login); break;
        case 'email': cmp = (a.email ?? '').localeCompare(b.email ?? ''); break;
        case 'open': cmp = a.open - b.open; break;
        case 'resolved': cmp = a.resolved - b.resolved; break;
        case 'regressed': cmp = a.regressed - b.regressed; break;
        case 'emailed': cmp = a.emailed - b.emailed; break;
        case 'lastEmailed': cmp = (a.lastEmailed ?? '').localeCompare(b.lastEmailed ?? ''); break;
        case 'earliest': cmp = (a.earliest ?? '').localeCompare(b.earliest ?? ''); break;
      }
      return sortAsc ? cmp : -cmp;
    });
    return arr;
  }, [filtered, sortKey, sortAsc]);

  const totals = useMemo(() => {
    let open = 0, resolved = 0, emailed = 0;
    for (const u of users) { open += u.open; resolved += u.resolved; emailed += u.emailed; }
    return { userCount: users.length, open, resolved, emailed };
  }, [users]);

  function handleSort(key: SortKey) {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(false); }
  }

  function sortIndicator(key: SortKey) {
    if (sortKey !== key) return null;
    return <span className="ml-1 text-[var(--accent)]">{sortAsc ? '\u25B2' : '\u25BC'}</span>;
  }

  /* ---------------------------------------------------------------- */
  /*  Render                                                           */
  /* ---------------------------------------------------------------- */

  return (
    <Container className="py-6">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-[var(--text-primary)]">User Compliance</h1>
        <p className="mt-1 text-sm text-[var(--text-secondary)]">
          Track open, resolved, and regressed issues per user across campaigns.
        </p>
      </div>

      {/* Error */}
      {error && (
        <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center py-20">
          <div className="w-6 h-6 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {!loading && !error && (
        <>
          {/* Summary cards */}
          <div className="grid grid-cols-4 gap-4 mb-6">
            {[
              { label: 'Users with Issues', value: totals.userCount },
              { label: 'Total Open', value: totals.open, color: totals.open > 0 ? 'text-red-400' : undefined },
              { label: 'Total Resolved', value: totals.resolved, color: totals.resolved > 0 ? 'text-emerald-400' : undefined },
              { label: 'Emails Sent', value: totals.emailed },
            ].map((card) => (
              <div
                key={card.label}
                className="glass-card rounded-xl p-4 bg-[var(--bg-glass)] border border-[var(--border-default)]"
              >
                <div className="text-xs font-medium text-[var(--text-tertiary)] uppercase tracking-wide">
                  {card.label}
                </div>
                <div className={`mt-1 text-2xl font-semibold ${card.color || 'text-[var(--text-primary)]'}`}>
                  {card.value.toLocaleString()}
                </div>
              </div>
            ))}
          </div>

          {/* Search */}
          <div className="mb-4">
            <input
              type="text"
              placeholder="Filter by login or email"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full max-w-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-glass)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder-[var(--text-tertiary)] outline-none focus:border-[var(--accent)] transition-colors"
            />
          </div>

          {/* Empty */}
          {users.length === 0 ? (
            <div className="text-center py-16 text-[var(--text-tertiary)] text-sm">
              No tracking data available.
            </div>
          ) : (
            /* Table */
            <div className="rounded-xl border border-[var(--border-default)] overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-[var(--bg-glass)] text-[var(--text-secondary)]">
                    {([
                      ['login', 'Login'],
                      ['email', 'Email'],
                      ['open', 'Open'],
                      ['resolved', 'Resolved'],
                      ['regressed', 'Regressed'],
                      ['emailed', 'Emailed'],
                      ['lastEmailed', 'Last Emailed'],
                      ['earliest', 'Since'],
                    ] as [SortKey, string][]).map(([key, label]) => (
                      <th
                        key={key}
                        onClick={() => handleSort(key)}
                        className="px-3 py-2.5 text-left font-medium cursor-pointer select-none hover:text-[var(--text-primary)] transition-colors whitespace-nowrap"
                      >
                        {label}{sortIndicator(key)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((u) => (
                    <UserRowGroup
                      key={u.login}
                      user={u}
                      expanded={expandedLogin === u.login}
                      onToggle={() => setExpandedLogin(expandedLogin === u.login ? null : u.login)}
                    />
                  ))}
                </tbody>
              </table>
              {sorted.length === 0 && (
                <div className="text-center py-8 text-[var(--text-tertiary)] text-sm">
                  No users match the filter.
                </div>
              )}
            </div>
          )}
        </>
      )}
    </Container>
  );
}

/* ------------------------------------------------------------------ */
/*  Expandable user row with per-issue detail                          */
/* ------------------------------------------------------------------ */

function UserRowGroup({ user, expanded, onToggle }: { user: AggregatedUser; expanded: boolean; onToggle: () => void }) {
  const { addDebugLog } = useDiag();
  const logRef = useRef(addDebugLog);
  logRef.current = addDebugLog;
  const [issues, setIssues] = useState<Issue[]>([]);
  const [issuesLoading, setIssuesLoading] = useState(false);

  const handleToggle = useCallback(() => {
    logRef.current(`Expand user=${user.login} → ${!expanded ? 'open' : 'close'}`, 'tracking');
    if (!expanded) setIssuesLoading(true);
    onToggle();
  }, [expanded, onToggle, user.login]);

  useEffect(() => {
    if (!expanded) return;
    const t0 = performance.now();
    const url = `/api/tracking/issues?owner_login=${encodeURIComponent(user.login)}&limit=500`;
    logRef.current(`GET issues for ${user.login}`, 'tracking');
    fetchJson<{ issues: Issue[] }>(url)
      .then((data) => {
        logRef.current(`Got ${data.issues.length} issues for ${user.login} (${(performance.now() - t0).toFixed(0)}ms)`, 'tracking');
        setIssues(data.issues);
      })
      .catch((err) => {
        logRef.current(`FAILED issues for ${user.login}: ${err instanceof Error ? err.message : err}`, 'tracking', 'error');
        setIssues([]);
      })
      .finally(() => setIssuesLoading(false));
  }, [expanded, user.login]);

  return (
    <>
      <tr
        onClick={handleToggle}
        className="border-t border-[var(--border-default)] cursor-pointer hover:bg-[var(--bg-hover)] transition-colors"
      >
        <td className="px-3 py-2 font-medium text-[var(--text-primary)] whitespace-nowrap">
          <span className="mr-1.5 text-[var(--text-tertiary)] text-xs">{expanded ? '\u25BC' : '\u25B6'}</span>
          {user.login}
        </td>
        <td className="px-3 py-2 text-[var(--text-secondary)] truncate max-w-[200px]">{user.email ?? '\u2014'}</td>
        <td className={`px-3 py-2 font-medium ${user.open > 0 ? 'text-red-400' : 'text-[var(--text-secondary)]'}`}>
          {user.open}
        </td>
        <td className={`px-3 py-2 font-medium ${user.resolved > 0 ? 'text-emerald-400' : 'text-[var(--text-secondary)]'}`}>
          {user.resolved}
        </td>
        <td className={`px-3 py-2 font-medium ${user.regressed > 0 ? 'text-amber-400' : 'text-[var(--text-secondary)]'}`}>
          {user.regressed}
        </td>
        <td className="px-3 py-2 text-[var(--text-secondary)]">{user.emailed}</td>
        <td className="px-3 py-2 text-[var(--text-secondary)] whitespace-nowrap">{relativeTime(user.lastEmailed)}</td>
        <td className="px-3 py-2 text-[var(--text-secondary)] whitespace-nowrap">{shortDate(user.earliest)}</td>
      </tr>

      <AnimatePresence>
        {expanded && (
          <tr>
            <td colSpan={8} className="p-0">
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="overflow-hidden"
              >
                <div className="px-6 py-3 bg-[var(--bg-glass)]/50">
                  {issuesLoading ? (
                    <div className="flex items-center justify-center py-4">
                      <div className="w-4 h-4 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin" />
                    </div>
                  ) : issues.length === 0 ? (
                    <div className="text-center py-4 text-[var(--text-tertiary)] text-xs">
                      No individual issues found.
                    </div>
                  ) : (
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="text-[var(--text-tertiary)]">
                          <th className="px-2 py-1.5 text-left font-medium">Status</th>
                          <th className="px-2 py-1.5 text-left font-medium">Campaign</th>
                          <th className="px-2 py-1.5 text-left font-medium">Entity</th>
                          <th className="px-2 py-1.5 text-left font-medium">Detail</th>
                          <th className="px-2 py-1.5 text-left font-medium">Detected</th>
                          <th className="px-2 py-1.5 text-left font-medium">Emailed</th>
                        </tr>
                      </thead>
                      <tbody>
                        {issues.map((issue) => {
                          const metricsText = formatMetrics(issue.campaign_id, issue.metrics_json);
                          const reasonLabel: Record<string, string> = {
                            entity_deleted: 'deleted',
                            condition_cleared: 'whitelisted',
                          };
                          const detail = [
                            metricsText,
                            issue.status === 'resolved' && issue.resolution_reason
                              ? reasonLabel[issue.resolution_reason] ?? issue.resolution_reason.replace(/_/g, ' ')
                              : null,
                          ].filter(Boolean).join(' \u2014 ');

                          return (
                            <tr key={issue.issue_id} className="border-t border-[var(--border-default)]/50">
                              <td className="px-2 py-1.5">{statusPill(issue.status)}</td>
                              <td className="px-2 py-1.5 text-[var(--text-primary)] font-medium whitespace-nowrap">
                                {campaignLabel(issue.campaign_id)}
                              </td>
                              <td className="px-2 py-1.5 text-[var(--text-secondary)] font-mono">
                                {issue.entity_name || issue.entity_key}
                              </td>
                              <td className="px-2 py-1.5 text-[var(--text-tertiary)]">
                                {detail || '\u2014'}
                              </td>
                              <td className="px-2 py-1.5 text-[var(--text-secondary)] whitespace-nowrap">
                                {shortDate(issue.first_detected_at)}
                              </td>
                              <td className="px-2 py-1.5 text-[var(--text-secondary)]">
                                {issue.times_emailed > 0 ? `${issue.times_emailed}x` : '\u2014'}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  )}
                </div>
              </motion.div>
            </td>
          </tr>
        )}
      </AnimatePresence>
    </>
  );
}
