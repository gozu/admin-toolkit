/** Post-load derived user statistics and endpoint timings. */
import type { LoaderCtx } from './context';
import type { LifecycleTracker } from './lifecycle';

// Compute users by project count
export function computeUsersByProjects(ctx: LoaderCtx, tracker: LifecycleTracker): void {
  const { dispatch, log } = ctx;
  if (tracker.data.projects?.length && tracker.data.users?.length) {
    const userEmailMap: Record<string, string> = {};
    tracker.data.users.forEach((u) => {
      userEmailMap[u.login] = u.email || u.login;
    });

    const projectCounts: Record<string, number> = {};
    tracker.data.projects.forEach((p) => {
      projectCounts[p.owner] = (projectCounts[p.owner] || 0) + 1;
    });

    const usersByProjects: Record<string, string> = {};
    Object.entries(projectCounts)
      .sort(([, a], [, b]) => b - a)
      .forEach(([login, count]) => {
        const email = userEmailMap[login] || login;
        usersByProjects[email] = String(count);
      });

    if (Object.keys(usersByProjects).length > 0) {
      tracker.data = {
        ...tracker.data,
        usersByProjects,
      };
      dispatch({ type: 'SET_PARSED_DATA', payload: tracker.data });
      log(`Computed users-by-projects (${Object.keys(usersByProjects).length} users)`);
    }
  }
}

// Emit timing summary table
export function emitTimingTable(ctx: LoaderCtx): void {
  const { log, endpointTimings } = ctx;
  if (endpointTimings.length > 0) {
    const rows = endpointTimings.map((t) => {
      const dur =
        t.durationMs >= 1000 ? `${(t.durationMs / 1000).toFixed(1)}s` : `${t.durationMs}ms`;
      const flag = t.status === 'fail' ? ' FAIL' : t.status === 'skip' ? ' SKIP' : '';
      return `${t.label}|${dur}${flag}`;
    });
    log(`TIMING_TABLE:${rows.join(';;')}`);
  }
}
