import { expect, test } from '@playwright/test';
import { createUsageTracker, USAGE_SCAN_FIELDS, type UsageEventName, type UsageProperties } from '../src/utils/usageEvents';
import type { Lifecycle } from '../src/types';
import { MODULES } from '../src/utils/moduleRegistry';

const running: Lifecycle = { phase: 'running', startedAt: '2026-09-14T12:00:00Z', updatedAt: '2026-09-14T12:00:00Z', progressPct: 0 };
const done: Lifecycle = { phase: 'done', startedAt: running.startedAt, finishedAt: '2026-09-14T12:00:04Z', isEmpty: false };
function setup() {
  const events: { name: UsageEventName; properties: UsageProperties }[] = [];
  let id = 0;
  const tracker = createUsageTracker((name, properties) => events.push({ name, properties }), () => `id-${++id}`);
  return { events, tracker };
}

test('render repeats do not duplicate visits; returning to a module starts a new visit', () => {
  const { events, tracker } = setup();
  tracker.openModule('projects');
  tracker.openModule('projects');
  tracker.openModule('users');
  tracker.openModule('projects');
  expect(events.map((e) => e.properties.module_id)).toEqual(['projects', 'users', 'projects']);
  expect(new Set(events.map((e) => e.properties.visit_id)).size).toBe(3);
});

test('100 percent is still running, terminal errors never become successful scans', () => {
  const { events, tracker } = setup();
  tracker.observeScan('projectFootprintLoading', running, 'automatic');
  tracker.observeScan('projectFootprintLoading', { ...running, progressPct: 100 });
  tracker.observeScan('projectFootprintLoading', { ...done, phase: 'error', error: 'secret error', progressPct: 100 });
  expect(events.map((e) => e.name)).toEqual(['adtk_scan_started', 'adtk_scan_failed']);
  expect(events[1].properties.trigger).toBe('automatic');
  expect(events[1].properties.duration_ms).toBe(4000);
  expect(JSON.stringify(events)).not.toContain('secret error');
});

test('background completions do not count as results viewed until the corresponding module is visible', () => {
  const { events, tracker } = setup();
  tracker.observeScan('projectFootprintLoading', running);
  tracker.observeScan('projectFootprintLoading', done);
  tracker.viewResults('projects', { projectFootprintLoading: done });
  expect(events.map((e) => e.name)).toEqual(['adtk_scan_started', 'adtk_scan_completed']);
  tracker.openModule('projects');
  tracker.viewResults('projects', { projectFootprintLoading: done });
  tracker.viewResults('projects', { projectFootprintLoading: done });
  expect(events.filter((e) => e.name === 'adtk_results_viewed')).toHaveLength(1);
  const viewed = events.find((e) => e.name === 'adtk_scan_results_viewed')!;
  expect(viewed.properties.scan_id).toBe(events[0].properties.scan_id);
  expect(viewed.properties.visit_id).toBe(events.find((e) => e.name === 'adtk_module_opened')!.properties.visit_id);
});

test('instant completion is marked inferred; retried scans have independent IDs', () => {
  const { events, tracker } = setup();
  tracker.observeScan('codeEnvsBrokenLoading', done);
  tracker.observeScan('codeEnvsBrokenLoading', done);
  expect(events[0].properties.start_observed).toBe(false);
  tracker.observeScan('codeEnvsBrokenLoading', { ...running, startedAt: '2026-09-14T12:01:00Z' }, 'manual');
  expect(events.filter((e) => e.name === 'adtk_scan_started')).toHaveLength(2);
  expect(events[2].properties.scan_id).not.toBe(events[0].properties.scan_id);
  expect(events[2].properties.trigger).toBe('manual');
});

test('paused scan emits cancellation once; unvisited/shared fields are not duplicated', () => {
  const { events, tracker } = setup();
  tracker.observeScan('adoptionEventsLoading', running, 'automatic');
  const aborted: Lifecycle = { ...running, subPhase: 'aborted' };
  tracker.observeScan('adoptionEventsLoading', aborted);
  tracker.observeScan('adoptionEventsLoading', aborted);
  expect(events.map((e) => e.name)).toEqual(['adtk_scan_started', 'adtk_scan_cancelled']);
  expect(USAGE_SCAN_FIELDS.length).toBe(new Set(MODULES.flatMap((m) => m.lifecycle.fields)).size);
});

test('new session cannot attach old scans to a new host visit', () => {
  const { tracker } = setup();
  tracker.observeScan('projectFootprintLoading', done);
  const fresh = setup();
  fresh.tracker.openModule('projects');
  fresh.tracker.viewResults('projects', {});
  expect(fresh.events.map((e) => e.name)).toEqual(['adtk_module_opened']);
  const afterSwitch: UsageEventName[] = [];
  const switched = createUsageTracker((name) => afterSwitch.push(name), () => 'new-id', Date.parse('2026-09-14T12:01:00Z'));
  switched.openModule('projects');
  switched.observeScan('projectFootprintLoading', done);
  switched.viewResults('projects', { projectFootprintLoading: done });
  expect(afterSwitch).toEqual(['adtk_module_opened']);
});
