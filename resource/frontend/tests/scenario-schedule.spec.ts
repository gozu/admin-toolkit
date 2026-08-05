import { expect, test } from '@playwright/test';
import type { ScenarioRow, ScenarioTrigger } from '../src/types';
import {
  countScenarioCategories,
  filterScenariosByCategories,
  scenarioCategory,
  scenarioTriggerSummary,
  type ScenarioCategory,
} from '../src/utils/scenarioSchedule';

const temporal = (active = true): ScenarioTrigger => ({
  type: 'temporal',
  active,
  temporal: {
    frequency: 'Daily',
    repeatFrequency: 1,
    hour: 2,
    minute: 30,
  },
});

const event = (active = true): ScenarioTrigger => ({
  type: 'ds_modified',
  active,
});

function scenario(id: string, active: boolean, triggers: ScenarioTrigger[]): ScenarioRow {
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
    reporters: 0,
    activeReporters: 0,
    lastModifiedOn: null,
    lastModifiedBy: null,
    settingsError: null,
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
