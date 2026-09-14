import type { ScenarioRow, ScenarioRun } from '../types';
import { rangeDef, type AxisTick, type RangeKey, type ScheduleSegment } from './scenarioSchedule';

export const OUTCOME_COLORS: Record<string, string> = {
  SUCCESS: 'var(--neon-green)',
  WARNING: 'var(--neon-amber)',
  FAILED: 'var(--neon-red)',
  ABORTED: 'var(--neon-red)',
};

export interface RunSegment extends ScheduleSegment {
  outcome: string;
}

function recentRuns(scenario: ScenarioRow): ScenarioRun[] {
  // Existing session data can still expose the latest run before a rescan.
  return (
    scenario.recentRuns ??
    (scenario.lastRunOutcome
      ? [
          {
            outcome: scenario.lastRunOutcome,
            start: scenario.lastRunStart,
            end: scenario.lastRunEnd,
          },
        ]
      : [])
  );
}

export function historyWindow(range: RangeKey, nowMs: number) {
  const span = rangeDef(range).spanDays * 86_400_000;
  return { start: nowMs - span, end: nowMs, span };
}

export function runSegments(scenario: ScenarioRow, range: RangeKey, nowMs: number): RunSegment[] {
  const window = historyWindow(range, nowMs);
  const hour = 3_600_000;
  // Round the display to browser-local hours; leave recorded runtime untouched.
  // Subtract minutes instead of setHours so repeated DST hours stay distinct.
  const hourStart = (ms: number) => {
    const date = new Date(ms);
    return ms - date.getMinutes() * 60_000 - date.getSeconds() * 1000 - date.getMilliseconds();
  };
  return recentRuns(scenario).flatMap((run) => {
    if (run.start == null || run.start >= window.end) return [];
    const validEnd = run.end != null && run.end >= run.start;
    const end = validEnd ? run.end! : run.start;
    if (end < window.start || (end === window.start && run.start < end)) return [];
    const duration = validEnd
      ? `${((end - run.start) / 1000).toLocaleString()} s`
      : 'duration unavailable';
    const displayStart = hourStart(run.start);
    const displayEnd = hourStart(Math.max(run.start, end - 1)) + hour;
    return [
      {
        start: Math.max(0, (displayStart - window.start) / window.span),
        end: Math.min(1, (displayEnd - window.start) / window.span),
        point: false,
        outcome: run.outcome,
        label: `${run.outcome} · ${new Date(run.start).toLocaleString()} · ${duration}`,
      },
    ];
  });
}

/** Sum each run's exact overlap with a bucket. Dividing by bucket length
 * yields average concurrent sampled runs, never a whole-hour charge. */
export function runtimeLoad(scenarios: ScenarioRow[], range: RangeKey, nowMs: number) {
  const window = historyWindow(range, nowMs);
  const count = rangeDef(range).buckets;
  const width = window.span / count;
  const runtime = new Array<number>(count).fill(0);
  for (const scenario of scenarios) {
    for (const run of recentRuns(scenario)) {
      if (run.start == null || run.end == null || run.end <= run.start) continue;
      const start = Math.max(window.start, run.start);
      const end = Math.min(window.end, run.end);
      if (end <= start) continue;
      const first = Math.max(0, Math.floor((start - window.start) / width));
      const last = Math.min(count - 1, Math.ceil((end - window.start) / width) - 1);
      for (let i = first; i <= last; i++) {
        runtime[i] += Math.max(
          0,
          Math.min(end, window.start + (i + 1) * width) - Math.max(start, window.start + i * width),
        );
      }
    }
  }
  return runtime.map((runtimeMs, index) => ({
    index,
    count: runtimeMs / width,
    runtimeMs,
    label: `${new Date(window.start + index * width).toLocaleString()} – ${new Date(window.start + (index + 1) * width).toLocaleString()}`,
  }));
}

export function historyTicks(range: RangeKey, nowMs: number): AxisTick[] {
  const window = historyWindow(range, nowMs);
  const divisions = range === '24h' ? 8 : range === 'quarter' ? 6 : 7;
  return Array.from({ length: divisions + 1 }, (_, i) => {
    const date = new Date(window.start + (window.span * i) / divisions);
    return {
      pos: i / divisions,
      major: true,
      label:
        range === '24h'
          ? date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
          : date.toLocaleDateString([], { month: 'short', day: 'numeric' }),
    };
  });
}
