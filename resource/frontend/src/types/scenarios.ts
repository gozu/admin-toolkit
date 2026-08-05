/** One scenario trigger, normalized to the same shape the diag-parser dump
 *  build used so the ported schedule math consumes it unchanged. */
export interface ScenarioTrigger {
  type: string; // 'temporal' | 'ds_modified' | 'follow_scenariorun' | ...
  name?: string | null;
  active: boolean;
  temporal?: {
    // present only when type === 'temporal'
    frequency: string; // Daily | Weekly | Monthly | Hourly | Minutely
    repeatFrequency: number;
    daysOfWeek?: string[] | null;
    monthlyRunOn?: string | null;
    hour?: number | null;
    minute?: number | null;
    timezone?: string | null;
    startingFrom?: string | null;
    /** (server UTC offset − trigger-tz UTC offset) in minutes, computed
     *  server-side at current DST rules; 0 for SERVER/unknown zones. Added to
     *  the configured clock time so the shared timeline is in server time. */
    serverShiftMinutes?: number | null;
  };
  /** Present only on type === 'follow_scenariorun': the one scenario this
   *  trigger follows (verified against FollowScenarioRunTriggerParams). */
  follow?: {
    projectKey: string;
    scenarioId: string;
    outcomeFilter?: string | null;
  };
}

/** A follow_scenariorun trigger whose chain cannot start on its own.
 *  'missing' = the followed scenario no longer exists; 'dormant' = it exists
 *  but is disabled or has no active trigger, so the chain only moves when
 *  someone runs the target by hand. */
export interface ScenarioChainIssue {
  projectKey: string;
  id: string;
  targetProjectKey: string;
  targetScenarioId: string;
  kind: 'missing' | 'dormant';
}

/** One scenario, joined from the live listing (active/running/nextRun — state
 *  a diagnostic dump never carried), the per-scenario settings fetch
 *  (structured triggers, reporters, versionTag) and the per-scenario run
 *  history (outcomes, durations, streaks). */
export interface ScenarioRow {
  projectKey: string;
  id: string;
  name: string;
  scenarioType: string; // 'step_based' | 'custom_python'
  active: boolean;
  running: boolean;
  /** DSS's own next-fire computation (ms). Truth, unlike the timeline
   *  projection. Null = none scheduled. */
  nextRun: number | null;
  markedAsTest: boolean;
  automationLocal: boolean;
  /** DSS's own human summary of the triggers, e.g. "Every day at 07:00". */
  triggerDigest: string;
  triggers: ScenarioTrigger[];
  /** Any active temporal trigger. */
  hasTimeSchedule: boolean;
  runAsUser: string | null;
  /** Set when the explicit run-as login no longer exists / is disabled.
   *  Null = fine, not set, or the user list was unavailable. */
  runAsInvalid: 'missing' | 'disabled' | null;
  reporters: number;
  activeReporters: number;
  lastModifiedOn: number | null;
  lastModifiedBy: string | null;
  /** Set when the settings fetch failed — the row degrades to listing-only
   *  (live columns render, the timeline/category math has no triggers). */
  settingsError: string | null;

  // Run history (last 10 completed runs, newest-first).
  lastRunOutcome: string | null; // SUCCESS | WARNING | FAILED | ABORTED
  lastRunStart: number | null;
  lastRunEnd: number | null;
  /** Consecutive FAILED/ABORTED runs counted from the newest completed run. */
  failureStreak: number;
  avgDurationMs: number | null;
  runsSampled: number;
  recentOutcomes: string[];
  runsError: string | null;

  /** Patched onto the row from the done event; undefined until then, null
   *  when the sweep was incomplete (verdicts unknowable) or the chain is fine. */
  chainIssue?: { kind: 'missing' | 'dormant'; target: string } | null;
}

export interface ScenariosResult {
  scenarios: ScenarioRow[];
  projectsToScan: number;
  projectsScanned: number;
  failedProjects: { projectKey: string; error: string }[];
  /** Server timezone label (e.g. "EDT") — what the timeline is normalized to. */
  serverTz: string | null;
  /** False when list_users failed: run-as validity was not checked. */
  usersChecked: boolean;
  /** Null until done, and null on an incomplete sweep — never silently []. */
  chainIssues: ScenarioChainIssue[] | null;
}
