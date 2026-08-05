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
  };
}

/** One scenario, joined from the live listing (active/running/nextRun — state
 *  a diagnostic dump never carried) and the per-scenario settings fetch
 *  (structured triggers, reporters, versionTag). */
export interface ScenarioRow {
  projectKey: string;
  id: string;
  name: string;
  scenarioType: string; // 'step_based' | 'custom_python'
  active: boolean;
  running: boolean;
  /** DSS's own next-fire computation (ms). Truth, unlike the timeline
   *  projection, which ignores per-trigger timezones. Null = none scheduled. */
  nextRun: number | null;
  markedAsTest: boolean;
  automationLocal: boolean;
  /** DSS's own human summary of the triggers, e.g. "Every day at 07:00". */
  triggerDigest: string;
  triggers: ScenarioTrigger[];
  /** Any active temporal trigger. */
  hasTimeSchedule: boolean;
  runAsUser: string | null;
  reporters: number;
  activeReporters: number;
  lastModifiedOn: number | null;
  lastModifiedBy: string | null;
  /** Set when the settings fetch failed — the row degrades to listing-only
   *  (live columns render, the timeline/category math has no triggers). */
  settingsError: string | null;
}

export interface ScenariosResult {
  scenarios: ScenarioRow[];
  projectsToScan: number;
  projectsScanned: number;
  failedProjects: { projectKey: string; error: string }[];
}
