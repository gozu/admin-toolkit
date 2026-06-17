import type { ProcessMetric } from '../types';

/** One row of the "usage by user" ranking. */
export interface UserUsageRow {
  /** Raw Linux username (e.g. `dssuser_akaos`); use `displayUser` for labels. */
  user: string;
  /** Aggregated metric: Σ rssKb (memory) or Σ cpuPercent (cpu). */
  value: number;
  /** Host share, %: Σ memPercent (memory) or Σ cpuPercent (cpu, may exceed 100). */
  share: number;
  /** Number of processes owned by this user. */
  count: number;
}

// DSS impersonation accounts all share the `dssuser_` prefix; strip it for
// legibility (full username stays in the data + tooltip). Other accounts
// (root, dataiku, …) are shown unchanged.
export function displayUser(user: string): string {
  return user.startsWith('dssuser_') ? user.slice('dssuser_'.length) : user;
}

// Webapp-backend processes carry a very long argv: the python interpreter, the
// `-m dataiku.webapps.backend` module, the absolute webappruns run-dir path, and
// a trailing `start_command.json`. Collapse all that to just the run directory
// (`webappruns/<project>/<webapp>/run_…`) — the rest is noise. Other commands
// are returned unchanged; the full argv stays in the row tooltip.
export function displayCommand(command: string): string {
  const idx = command.indexOf('webappruns/');
  if (idx === -1) return command;
  const tail = command.slice(idx);
  const run = tail.match(/^webappruns\/\S*?\/run_[^/\s]+/);
  if (run) return run[0];
  return tail.replace(/\/start_command\.json\s*$/, '');
}

/**
 * Aggregate per-PID process metrics into a per-user ranking, sorted by `value`
 * descending. `metric` selects what `value` measures:
 *   - 'rssKb'      (memory) → value = Σ rssKb, share = Σ memPercent (% of host RAM)
 *   - 'cpuPercent' (cpu)    → value = Σ cpuPercent (== share; may exceed 100 across cores)
 */
export function aggregateByUser(
  processes: ProcessMetric[],
  metric: 'rssKb' | 'cpuPercent',
): UserUsageRow[] {
  const byUser = new Map<string, UserUsageRow>();
  for (const p of processes) {
    let row = byUser.get(p.user);
    if (!row) {
      row = { user: p.user, value: 0, share: 0, count: 0 };
      byUser.set(p.user, row);
    }
    row.value += p[metric];
    row.share += metric === 'rssKb' ? p.memPercent : p.cpuPercent;
    row.count += 1;
  }
  return Array.from(byUser.values()).sort((a, b) => b.value - a.value);
}
