import { expect, test } from '@playwright/test';
import type { ScenarioRow, ScenarioRun } from '../src/types';
import { historyTicks, runSegments, runtimeLoad } from '../src/utils/scenarioHistory';

const HOUR = 3_600_000;
const end = new Date(2026, 8, 14).getTime();
const start = end - 24 * HOUR;
const row = (recentRuns: ScenarioRun[], extra: Partial<ScenarioRow> = {}): ScenarioRow => ({
  projectKey: 'DEMO',
  id: 'mixed',
  name: 'Mixed outcomes',
  scenarioType: 'step_based',
  active: false,
  running: false,
  nextRun: null,
  markedAsTest: false,
  automationLocal: false,
  triggerDigest: '',
  triggers: [],
  hasTimeSchedule: false,
  runAsUser: null,
  runAsInvalid: null,
  reporters: 0,
  activeReporters: 0,
  lastModifiedOn: null,
  lastModifiedBy: null,
  settingsError: null,
  lastRunOutcome: null,
  lastRunStart: null,
  lastRunEnd: null,
  failureStreak: 0,
  avgDurationMs: null,
  runsSampled: recentRuns.length,
  recentOutcomes: recentRuns.map((r) => r.outcome),
  runsError: null,
  recentRuns,
  ...extra,
});

test('preserves outcomes but displays short runs as full-hour bars', () => {
  const segments = runSegments(
    row([
      { outcome: 'FAILED', start: start + 2 * HOUR, end: start + 3 * HOUR },
      { outcome: 'SUCCESS', start: start + 4 * HOUR, end: start + 4.5 * HOUR },
      { outcome: 'ABORTED', start: start + 6 * HOUR, end: start + 6.25 * HOUR },
    ]),
    '24h',
    end,
  );
  expect(segments.map((r) => r.outcome)).toEqual(['FAILED', 'SUCCESS', 'ABORTED']);
  expect(segments[0].end - segments[0].start).toBeCloseTo(1 / 24);
  expect(segments[1].end - segments[1].start).toBeCloseTo(1 / 24);
  expect(segments[2].end - segments[2].start).toBeCloseTo(1 / 24);
  expect(segments.every((r) => !r.point)).toBe(true);
  expect(segments[1].label).toContain((1800).toLocaleString() + ' s');
});

test('fills every touched hour without rounding the runtime calculation', () => {
  const runs = row([
    { outcome: 'SUCCESS', start: start + 2.5 * HOUR, end: start + 2.5 * HOUR + 10_000 },
    { outcome: 'FAILED', start: start + 4.75 * HOUR, end: start + 5.25 * HOUR },
  ]);
  const segments = runSegments(runs, '24h', end);
  expect(segments[0].start).toBeCloseTo(2 / 24);
  expect(segments[0].end).toBeCloseTo(3 / 24);
  expect(segments[1].start).toBeCloseTo(4 / 24);
  expect(segments[1].end).toBeCloseTo(6 / 24);
  expect(runtimeLoad([runs], '24h', end).reduce((sum, b) => sum + b.runtimeMs, 0)).toBe(
    0.5 * HOUR + 10_000,
  );
});

test('charges exact runtime across hour boundaries and includes failed runs', () => {
  const buckets = runtimeLoad(
    [
      row([
        { outcome: 'FAILED', start: start + 0.75 * HOUR, end: start + 1.25 * HOUR },
        { outcome: 'SUCCESS', start: start + 2 * HOUR, end: start + 2 * HOUR + 10_000 },
      ]),
    ],
    '24h',
    end,
  );
  expect(buckets[0].count).toBe(0.25);
  expect(buckets[1].count).toBe(0.25);
  expect(buckets[2].runtimeMs).toBe(10_000);
  expect(buckets[3].count).toBe(0);
  expect(buckets.reduce((sum, b) => sum + b.runtimeMs, 0)).toBe(0.5 * HOUR + 10_000);
});

test('clips to the real window and sums overlapping runs', () => {
  const runs = row([
    { outcome: 'SUCCESS', start: start - HOUR, end: start + HOUR },
    { outcome: 'WARNING', start, end: start + HOUR },
    { outcome: 'FAILED', start: end - HOUR, end: end + HOUR },
    { outcome: 'SUCCESS', start: end + HOUR, end: end + 2 * HOUR },
  ]);
  const buckets = runtimeLoad([runs], '24h', end);
  expect(buckets[0].count).toBe(2);
  expect(buckets[23].count).toBe(1);
  const segments = runSegments(runs, '24h', end);
  expect(segments).toHaveLength(3);
  expect(segments[0].start).toBe(0);
  expect(segments[2].end).toBe(1);
});

test('missing durations still get hour bars without inventing load', () => {
  const runs = row([
    { outcome: 'FAILED', start: start + HOUR, end: null },
    { outcome: 'ABORTED', start: start + 2 * HOUR, end: start + HOUR },
    { outcome: 'WARNING', start: null, end: start + HOUR },
  ]);
  const segments = runSegments(runs, '24h', end);
  expect(segments).toHaveLength(2);
  expect(segments.every((r) => !r.point && r.label.includes('duration unavailable'))).toBe(true);
  expect(segments[0].end - segments[0].start).toBeCloseTo(1 / 24);
  expect(runtimeLoad([runs], '24h', end).every((b) => b.count === 0)).toBe(true);
});

test('older scans fall back to the known last run; historical windows use real dates', () => {
  const old = row([], {
    recentRuns: undefined,
    lastRunOutcome: 'FAILED',
    lastRunStart: start,
    lastRunEnd: start + HOUR,
  });
  expect(runSegments(old, '24h', end)[0].outcome).toBe('FAILED');
  expect(runtimeLoad([old], '24h', end)[0].count).toBe(1);
  for (const range of ['24h', '7d', 'month', 'quarter'] as const) {
    const ticks = historyTicks(range, end);
    expect(ticks[0].pos).toBe(0);
    expect(ticks.at(-1)?.pos).toBe(1);
    expect(runtimeLoad([old], range, end).reduce((sum, b) => sum + b.runtimeMs, 0)).toBe(HOUR);
  }
});
