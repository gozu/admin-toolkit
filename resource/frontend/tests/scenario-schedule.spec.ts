import { expect, test } from '@playwright/test';
import type { ScenarioRow, ScenarioTrigger } from '../src/types';
import {
  countScenarioCategories,
  filterScenariosByCategories,
  minActiveIntervalMs,
  overlapRisk,
  projectScenario,
  scenarioCategory,
  scenarioTriggerSummary,
  temporalSummary,
  type ScenarioCategory,
} from '../src/utils/scenarioSchedule';

const temporal = (
  active = true,
  extra: Partial<NonNullable<ScenarioTrigger['temporal']>> = {},
): ScenarioTrigger => ({
  type: 'temporal',
  active,
  temporal: {
    frequency: 'Daily',
    repeatFrequency: 1,
    hour: 2,
    minute: 30,
    ...extra,
  },
});

const event = (active = true): ScenarioTrigger => ({
  type: 'ds_modified',
  active,
});

function scenario(
  id: string,
  active: boolean,
  triggers: ScenarioTrigger[],
  extra: Partial<ScenarioRow> = {},
): ScenarioRow {
  return {
    projectKey: 'PROJECT',
    id,
    name: id,
    scenarioType: 'step_based',
    active,
    running: false,
    nextRun: null,
    markedAsTest: false,
    automationLocal: false,
    triggerDigest: '',
    triggers,
    hasTimeSchedule: triggers.some((trigger) => trigger.type === 'temporal' && trigger.active),
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
    runsSampled: 0,
    recentOutcomes: [],
    runsError: null,
    ...extra,
  };
}

const rows = [
  scenario('active-time', true, [temporal()]),
  scenario('inactive-time', false, [temporal()]),
  scenario('event', false, [event()]),
  scenario('no-trigger', false, [event(false)]),
];

test.describe('scenario schedule categories', () => {
  test('partitions scenarios into the four displayed filters', () => {
    expect(rows.map(scenarioCategory)).toEqual([
      'active-time-based',
      'inactive-time-based',
      'event-based',
      'no-trigger',
    ]);
    expect(countScenarioCategories(rows)).toEqual({
      'active-time-based': 1,
      'inactive-time-based': 1,
      'event-based': 1,
      'no-trigger': 1,
    });
  });

  test('time-based wins when a scenario also has an event trigger', () => {
    expect(scenarioCategory(scenario('mixed', true, [event(), temporal()]))).toBe('active-time-based');
  });

  test('ORs multiple selections and treats no selections as all', () => {
    const selected = new Set<ScenarioCategory>(['inactive-time-based', 'event-based']);
    expect(filterScenariosByCategories(rows, selected).map((row) => row.id)).toEqual(['inactive-time', 'event']);

    const all = filterScenariosByCategories(rows, new Set());
    expect(all.map((row) => row.id)).toEqual(rows.map((row) => row.id));
    expect(all).not.toBe(rows);
  });

  test('describes event and no-trigger rows in the shared table', () => {
    expect(scenarioTriggerSummary(rows[0])).toBe('Daily 02:30');
    expect(scenarioTriggerSummary(rows[2])).toBe('Dataset / folder modified');
    expect(scenarioTriggerSummary(rows[3])).toBe('No active trigger');
  });
});

test.describe('live enrichments', () => {
  test('follow triggers name their target scenario', () => {
    const follower = scenario('follower', true, [
      { type: 'follow_scenariorun', active: true, follow: { projectKey: 'OTHER', scenarioId: 'upstream' } },
    ]);
    expect(scenarioTriggerSummary(follower)).toBe('After OTHER.upstream');
  });

  test('non-server timezones are named in the summary and shift the timeline', () => {
    // Asia/Tokyo (+9) on an EDT server (−4): shift = −240 − 540 = −780 min,
    // so a 09:00 Tokyo fire sits at 20:00 server time on the 24h axis.
    const tokyo = scenario('tokyo', true, [
      temporal(true, { hour: 9, minute: 0, timezone: 'Asia/Tokyo', serverShiftMinutes: -780 }),
    ]);
    expect(scenarioTriggerSummary(tokyo)).toBe('Daily 09:00 (Asia/Tokyo)');
    const proj = projectScenario(tokyo, '24h');
    expect(proj.markers).toHaveLength(1);
    expect(proj.markers[0].pos).toBeCloseTo((20 * 60) / 1440, 5);
    expect(temporalSummary(tokyo.triggers[0])).toContain('(Asia/Tokyo)');
  });

  test('overlap risk fires only when the average run outlasts the tightest interval', () => {
    const sleeper = scenario(
      'sleeper',
      false,
      [temporal(true, { frequency: 'Minutely', hour: 0, minute: 0 })],
      { avgDurationMs: 75_367 },
    );
    expect(minActiveIntervalMs(sleeper)).toBe(60_000);
    expect(overlapRisk(sleeper)).toBe(true);

    const daily = scenario('daily', true, [temporal()], { avgDurationMs: 75_367 });
    expect(minActiveIntervalMs(daily)).toBe(86_400_000);
    expect(overlapRisk(daily)).toBe(false);

    const neverRan = scenario('never', true, [temporal(true, { frequency: 'Minutely' })]);
    expect(overlapRisk(neverRan)).toBe(false);
  });
});
