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
// (`webappruns/<project>/<webapp>/run_…`) — the rest is noise. For every other
// command, strip the host boilerplate: every occurrence of `<dipHome>/` (cleans
// both the leading interpreter path and inline args like `-f<dipHome>/jupyter-run/…`)
// then the `code-envs/python/` segment, so e.g.
//   /data/dataiku/dss_data/code-envs/python/plugin_x_managed/bin/python -m foo
// reads as `plugin_x_managed/bin/python -m foo`. The full argv stays in the
// row tooltip. `dipHome` comes from the target host (process-metrics macro);
// when absent, fall back to a conservative strip of any `…/dss_data/` prefix.
export function displayCommand(command: string, dipHome?: string | null): string {
  const idx = command.indexOf('webappruns/');
  if (idx !== -1) {
    const tail = command.slice(idx);
    const run = tail.match(/^webappruns\/\S*?\/run_[^/\s]+/);
    if (run) return run[0];
    return tail.replace(/\/start_command\.json\s*$/, '');
  }
  let out = command;
  if (dipHome) {
    out = out.split(dipHome.replace(/\/+$/, '') + '/').join('');
  } else {
    out = out.replace(/\S*\/dss_data\//g, '');
  }
  return out.split('code-envs/python/').join('');
}

// Webapp processes embed their DSS object in the argv as
// `…/webappruns/<PROJECT_KEY>/<WEBAPP_ID>/run_…`. Pull those two segments out so
// the row can deep-link to the webapp. Returns null for any other process —
// notebook/recipe kernels carry no project key in their command line.
export function webappRefFromCommand(
  command: string,
): { projectKey: string; webappId: string } | null {
  const m = command.match(/webappruns\/([^/\s]+)\/([^/\s]+)\//);
  if (!m) return null;
  return { projectKey: m[1], webappId: m[2] };
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
