/**
 * Post-load tail of the live-mode loader: derived users-by-projects stats,
 * the endpoint timing table, and the deferred page-scan autostarts
 * (container execs, image-cleaner detect, managed folders, dir-tree, SQL
 * pushdown, sanity check). Bodies moved verbatim from the old monolithic
 * useApiDataLoader.ts.
 */
import { containerExecsScan } from '../../state/containerExecsStore';
import { startSqlPushdownScan } from '../../state/sqlPushdownScan';
import { runSanityCheck } from '../../state/sanityCheckScan';
import { imageCleanerDetectScan } from '../../state/imageCleanerStore';
import { managedFoldersScan } from '../../state/managedFoldersStore';
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

// Auto-start scans for pages that previously waited for first user visit.
// Fire-and-forget: each store manages its own state, errors, and cancellation.
export function autostartDeferredScans(
  ctx: LoaderCtx,
  loadDirTreeRoot: (() => void) | undefined,
): void {
  const { dispatch, cancelled, log, getErrorMessage } = ctx;
  log(
    'Auto-starting deferred page scans (container execs, SQL pushdown, sanity check, image-cleaner detect, managed folders, dir-tree)',
  );
  void containerExecsScan.load();
  void imageCleanerDetectScan.load();
  // managedFoldersScan owns codeEnvCleanerLoading (a code-envs aggregate
  // field). Autostart it so that field reaches `done` honestly via its
  // scan store — and the global "Analysis complete" can resolve — without
  // requiring a visit to the Code Envs page.
  void managedFoldersScan.load();
  // Dir-tree loads in the background only — it deliberately does NOT join the
  // "Analysis complete" aggregator (cold scans can exceed 40s on large hosts).
  void loadDirTreeRoot?.();
  startSqlPushdownScan();
  const sanityStartedAt = new Date().toISOString();
  dispatch({
    type: 'SET_PARSED_DATA',
    payload: {
      sanityCheckLoading: {
        phase: 'running',
        startedAt: sanityStartedAt,
        progressPct: 0,
        message: 'Running sanity check',
        updatedAt: sanityStartedAt,
      },
    },
  });
  runSanityCheck()
    .then((result) => {
      if (cancelled()) return;
      dispatch({
        type: 'SET_PARSED_DATA',
        payload: {
          sanityCheck: result.messages,
          sanityCheckMaxSeverity: result.maxSeverity,
          sanityCheckLoading: {
            phase: 'done',
            startedAt: sanityStartedAt,
            finishedAt: new Date().toISOString(),
            isEmpty: result.messages.length === 0,
            message: `${result.messages.length} message(s)`,
          },
        },
      });
      log(`Auto sanity check completed (${result.messages.length} messages)`);
    })
    .catch((err) => {
      const msg = getErrorMessage(err);
      dispatch({
        type: 'SET_PARSED_DATA',
        payload: {
          sanityCheckLoading: {
            phase: 'error',
            startedAt: sanityStartedAt,
            finishedAt: new Date().toISOString(),
            error: msg,
            progressPct: 0,
          },
        },
      });
      log(`Auto sanity check failed: ${msg}`, 'warn');
    });
}
