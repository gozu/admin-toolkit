// Scenario schedule expansion. Ported from the diag-parser Scenario Schedules
// card (utils/scenarioSchedule.ts there — keep the math in sync by hand).
//
// Plots *configured* schedules (when scenarios are set to fire), not past runs.
// All math is done in UTC against a normalized reference window because, for
// recurring schedules, the absolute calendar date is cosmetic; only the
// relative layout matters. Per-trigger timezones are ignored by this synthetic
// projection — the page pairs it with DSS's own `nextRun` for ground truth.

import type { ScenarioRow, ScenarioTrigger } from '../types';

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

// Beyond this many fires in the window we render a continuous band instead of markers.
const DENSE_THRESHOLD = 48;

// 2024-01-01 is a Monday — anchoring here lets us "anchor week to Monday" cleanly.
const ANCHOR = Date.UTC(2024, 0, 1);

const DAY_INDEX: Record<string, number> = {
  Sunday: 0, Monday: 1, Tuesday: 2, Wednesday: 3,
  Thursday: 4, Friday: 5, Saturday: 6,
};
const DAY_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

export type RangeKey = '24h' | '7d' | 'month' | 'quarter';

export interface RangeDef {
  key: RangeKey;
  label: string;
  spanDays: number; // window length in days (24h => 1, handled as time-of-day)
  buckets: number;  // load-distribution bucket count
}

export const RANGES: RangeDef[] = [
  { key: '24h', label: '24h', spanDays: 1, buckets: 24 },
  { key: '7d', label: '7-day', spanDays: 7, buckets: 7 },
  { key: 'month', label: 'Month', spanDays: 30, buckets: 30 },
  { key: 'quarter', label: 'Quarter', spanDays: 90, buckets: 13 },
];

export function rangeDef(key: RangeKey): RangeDef {
  return RANGES.find((r) => r.key === key) ?? RANGES[0];
}

export interface ScheduleMarker {
  pos: number;   // [0,1] along the axis
  label: string; // tooltip text
}

export interface ScheduleBand {
  start: number; // [0,1]
  end: number;   // [0,1]
  label: string;
}

export interface RowProjection {
  markers: ScheduleMarker[];
  bands: ScheduleBand[];
  occurrences: number; // total fires across the window (estimate when dense)
}

export interface AxisTick {
  pos: number;
  label: string;
  major: boolean; // major ticks carry a visible label
}

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

function pad2(n: number): string {
  return String(n).padStart(2, '0');
}

function timeOfDayLabel(hour: number, minute: number): string {
  return `${pad2(hour)}:${pad2(minute)}`;
}

// ---------------------------------------------------------------------------
// Trigger classification
// ---------------------------------------------------------------------------

export type TriggerKind = 'temporal' | 'event' | 'manual';
export type ScenarioCategory = 'active-time-based' | 'inactive-time-based' | 'event-based' | 'no-trigger';

export const SCENARIO_CATEGORIES: readonly ScenarioCategory[] = [
  'active-time-based',
  'inactive-time-based',
  'event-based',
  'no-trigger',
];

export type ScenarioCategoryCounts = Record<ScenarioCategory, number>;

/** A single trigger is either time-based ('temporal') or event-based ('event').
 *  'manual' is a scenario-level state (no active triggers) — see classifyScenario. */
export function classifyTrigger(t: ScenarioTrigger): TriggerKind {
  return t.type === 'temporal' ? 'temporal' : 'event';
}

export function classifyScenario(scenario: ScenarioRow): TriggerKind {
  if (scenario.triggers.some((t) => t.type === 'temporal' && t.active)) return 'temporal';
  if (scenario.triggers.some((t) => t.active)) return 'event';
  return 'manual';
}

export function scenarioCategory(scenario: ScenarioRow): ScenarioCategory {
  const triggerKind = classifyScenario(scenario);
  if (triggerKind === 'temporal') {
    return scenario.active ? 'active-time-based' : 'inactive-time-based';
  }
  return triggerKind === 'event' ? 'event-based' : 'no-trigger';
}

export function countScenarioCategories(scenarios: ScenarioRow[]): ScenarioCategoryCounts {
  const counts: ScenarioCategoryCounts = {
    'active-time-based': 0,
    'inactive-time-based': 0,
    'event-based': 0,
    'no-trigger': 0,
  };
  for (const scenario of scenarios) counts[scenarioCategory(scenario)]++;
  return counts;
}

/**
 * Match the app's existing multi-selector semantics: selected categories are
 * ORed together, while an empty selection means "show all".
 */
export function filterScenariosByCategories(
  scenarios: ScenarioRow[],
  selected: ReadonlySet<ScenarioCategory>,
): ScenarioRow[] {
  if (selected.size === 0) return [...scenarios];
  return scenarios.filter((scenario) => selected.has(scenarioCategory(scenario)));
}

const EVENT_TRIGGER_LABELS: Record<string, string> = {
  ds_modified: 'Dataset / folder modified',
  follow_scenariorun: 'After scenario run',
  sql_query: 'SQL query change',
  custom_python: 'Custom Python',
  dataset_modified: 'Dataset modified',
};

export function eventTriggerLabel(type: string): string {
  return EVENT_TRIGGER_LABELS[type] ?? type;
}

// ---------------------------------------------------------------------------
// Human-readable summary of a temporal trigger
// ---------------------------------------------------------------------------

export function temporalSummary(t: ScenarioTrigger): string {
  const p = t.temporal;
  if (!p) return 'Scheduled';
  const rf = Math.max(1, p.repeatFrequency || 1);
  const hour = p.hour ?? 0;
  const minute = p.minute ?? 0;
  const at = timeOfDayLabel(hour, minute);

  switch (p.frequency) {
    case 'Daily':
      return rf === 1 ? `Daily ${at}` : `Every ${rf} days ${at}`;
    case 'Weekly': {
      const days = (p.daysOfWeek ?? [])
        .map((d) => DAY_SHORT[DAY_INDEX[d] ?? 1])
        .join(', ') || 'weekly';
      return rf === 1 ? `${days} ${at}` : `Every ${rf} wks ${days} ${at}`;
    }
    case 'Monthly': {
      const dom = monthlyDayOfMonth(t);
      return rf === 1 ? `Monthly (day ${dom}) ${at}` : `Every ${rf} mo (day ${dom}) ${at}`;
    }
    case 'Hourly':
      return rf === 1 ? `Hourly :${pad2(minute)}` : `Every ${rf} hrs :${pad2(minute)}`;
    case 'Minutely':
      return rf === 1 ? 'Every minute' : `Every ${rf} min`;
    default:
      return `${p.frequency} ${at}`;
  }
}

/** Combined trigger summary for a scenario row (active temporal triggers). */
export function scenarioScheduleSummary(scenario: ScenarioRow): string {
  const parts = scenario.triggers
    .filter((t) => t.type === 'temporal' && t.active)
    .map((t) => temporalSummary(t));
  return parts.join('; ');
}

export function scenarioTriggerSummary(scenario: ScenarioRow): string {
  const kind = classifyScenario(scenario);
  if (kind === 'temporal') return scenarioScheduleSummary(scenario);
  if (kind === 'event') {
    return scenario.triggers
      .filter((t) => t.type !== 'temporal' && t.active)
      .map((t) => eventTriggerLabel(t.type))
      .join('; ');
  }
  return 'No active trigger';
}

function monthlyDayOfMonth(t: ScenarioTrigger): number {
  const p = t.temporal;
  if (p?.startingFrom) {
    // Prefix match: the live API hands full ISO timestamps
    // ("2026-07-19T00:00:00.000-0400"), the dump had date-only strings.
    const m = /^\d{4}-\d{2}-(\d{2})/.exec(p.startingFrom);
    if (m) return parseInt(m[1], 10);
  }
  return 1;
}

// ---------------------------------------------------------------------------
// Occurrence expansion
// ---------------------------------------------------------------------------

interface RawOccurrences {
  timestamps: number[]; // sorted, within [start,end); empty when dense
  count: number;        // total in window (estimate when dense)
  dense: boolean;
  bandStart?: number;   // ms, first fire (when dense)
  bandEnd?: number;     // ms, last fire (when dense)
}

/** Enumerate fire timestamps for a temporal trigger across
 *  [windowStart, windowEnd). Returns [] for non-temporal triggers. Capped. */
export function expandOccurrences(
  trigger: ScenarioTrigger,
  windowStart: number,
  windowEnd: number,
): number[] {
  if (trigger.type !== 'temporal' || !trigger.temporal) return [];
  return computeWindowOccurrences(trigger, windowStart, windowEnd).timestamps;
}

function computeWindowOccurrences(
  trigger: ScenarioTrigger,
  start: number,
  end: number,
): RawOccurrences {
  const p = trigger.temporal!;
  const rf = Math.max(1, p.repeatFrequency || 1);
  const hour = p.hour ?? 0;
  const minute = p.minute ?? 0;
  const todMs = hour * HOUR + minute * MINUTE;
  const windowMs = end - start;
  const cap = 1000;

  const dayStart = (ts: number) => Math.floor((ts - ANCHOR) / DAY) * DAY + ANCHOR;

  switch (p.frequency) {
    case 'Daily': {
      const ts: number[] = [];
      const day = dayStart(start);
      // step back to a fire aligned to rf days, then forward into the window
      let fire = day + todMs;
      while (fire >= start) fire -= rf * DAY;
      fire += rf * DAY;
      while (fire < end && ts.length < cap) {
        if (fire >= start) ts.push(fire);
        fire += rf * DAY;
      }
      return { timestamps: ts, count: ts.length, dense: false };
    }

    case 'Weekly': {
      const days = (p.daysOfWeek ?? [])
        .map((d) => DAY_INDEX[d])
        .filter((d) => d !== undefined);
      const wanted = new Set(days.length ? days : [1]);
      const ts: number[] = [];
      for (let day = dayStart(start); day < end && ts.length < cap; day += DAY) {
        const dow = new Date(day).getUTCDay();
        if (!wanted.has(dow)) continue;
        const weekIndex = Math.floor((day - ANCHOR) / (7 * DAY));
        if (weekIndex % rf !== 0) continue;
        const fire = day + todMs;
        if (fire >= start && fire < end) ts.push(fire);
      }
      ts.sort((a, b) => a - b);
      return { timestamps: ts, count: ts.length, dense: false };
    }

    case 'Monthly': {
      const dom = monthlyDayOfMonth(trigger);
      const ts: number[] = [];
      const startDate = new Date(start);
      let y = startDate.getUTCFullYear();
      let m = startDate.getUTCMonth();
      // Walk a few months past the window end to be safe.
      for (let i = 0; i < 6 && ts.length < cap; i++) {
        const monthIndex = (y - 2024) * 12 + m;
        if (monthIndex % rf === 0) {
          const daysInMonth = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
          const d = Math.min(dom, daysInMonth);
          const fire = Date.UTC(y, m, d) + todMs;
          if (fire >= start && fire < end) ts.push(fire);
        }
        m += 1;
        if (m > 11) { m = 0; y += 1; }
        if (Date.UTC(y, m, 1) >= end) break;
      }
      ts.sort((a, b) => a - b);
      return { timestamps: ts, count: ts.length, dense: false };
    }

    case 'Hourly': {
      const step = rf * HOUR;
      const estimate = Math.floor(windowMs / step) + 1;
      // align first fire to :minute
      let fire = dayStart(start) + minute * MINUTE;
      while (fire < start) fire += step;
      if (estimate > DENSE_THRESHOLD) {
        const last = (() => {
          let f = fire;
          while (f + step < end) f += step;
          return f;
        })();
        return { timestamps: [], count: estimate, dense: true, bandStart: fire, bandEnd: last };
      }
      const ts: number[] = [];
      while (fire < end && ts.length < cap) { ts.push(fire); fire += step; }
      return { timestamps: ts, count: ts.length, dense: false };
    }

    case 'Minutely': {
      const step = rf * MINUTE;
      const estimate = Math.floor(windowMs / step) + 1;
      let fire = dayStart(start);
      while (fire < start) fire += step;
      if (estimate > DENSE_THRESHOLD) {
        const last = (() => {
          let f = fire;
          while (f + step < end) f += step;
          return f;
        })();
        return { timestamps: [], count: estimate, dense: true, bandStart: fire, bandEnd: last };
      }
      const ts: number[] = [];
      while (fire < end && ts.length < cap) { ts.push(fire); fire += step; }
      return { timestamps: ts, count: ts.length, dense: false };
    }

    default: {
      // Unknown frequency — place a single fire at the configured time-of-day.
      const fire = dayStart(start) + todMs;
      return { timestamps: fire >= start && fire < end ? [fire] : [], count: 1, dense: false };
    }
  }
}

// 24h view: project onto a single time-of-day axis, ignoring the date entirely so
// that clustering (e.g. dozens of scenarios at 02:00) is immediately obvious.
interface TimeOfDayResult {
  minutes: number[]; // minutes-from-midnight (0..1440); empty when dense
  dense: boolean;
  count: number;     // occurrences per day
}

function computeTimeOfDay(trigger: ScenarioTrigger): TimeOfDayResult {
  const p = trigger.temporal!;
  const rf = Math.max(1, p.repeatFrequency || 1);
  const hour = p.hour ?? 0;
  const minute = p.minute ?? 0;
  const base = hour * 60 + minute;

  switch (p.frequency) {
    case 'Daily':
    case 'Weekly':
    case 'Monthly':
      // All fire at a single clock time each day they run.
      return { minutes: [base], dense: false, count: 1 };
    case 'Hourly':
      // Runs throughout the day — render as a full-day band.
      return { minutes: [], dense: true, count: Math.max(1, Math.round(24 / rf)) };
    case 'Minutely':
      return { minutes: [], dense: true, count: Math.max(1, Math.round(1440 / rf)) };
    default:
      return { minutes: [base], dense: false, count: 1 };
  }
}

// ---------------------------------------------------------------------------
// Per-scenario projection onto a [0,1] axis for the selected range
// ---------------------------------------------------------------------------

export function projectScenario(scenario: ScenarioRow, range: RangeKey): RowProjection {
  const def = rangeDef(range);
  const triggers = scenario.triggers.filter((t) => t.type === 'temporal' && t.active && t.temporal);
  const markers: ScheduleMarker[] = [];
  const bands: ScheduleBand[] = [];
  let occurrences = 0;

  if (range === '24h') {
    for (const t of triggers) {
      const r = computeTimeOfDay(t);
      occurrences += r.count;
      if (r.dense) {
        bands.push({ start: 0, end: 1, label: temporalSummary(t) });
      } else {
        for (const min of r.minutes) {
          markers.push({ pos: clamp(min / 1440, 0, 1), label: temporalSummary(t) });
        }
      }
    }
  } else {
    const start = ANCHOR;
    const end = ANCHOR + def.spanDays * DAY;
    const span = end - start;
    for (const t of triggers) {
      const r = computeWindowOccurrences(t, start, end);
      occurrences += r.count;
      if (r.dense && r.bandStart != null && r.bandEnd != null) {
        bands.push({
          start: clamp((r.bandStart - start) / span, 0, 1),
          end: clamp((r.bandEnd - start) / span, 0, 1),
          label: temporalSummary(t),
        });
      } else {
        for (const ts of r.timestamps) {
          markers.push({ pos: clamp((ts - start) / span, 0, 1), label: temporalSummary(t) });
        }
      }
    }
  }

  return { markers, bands, occurrences };
}

// A horizontal segment along the [0,1] axis. Calendar ranges (7d/month/quarter)
// emit one short inset dash per firing day — matching how DSS draws a dash per day
// cell with a gap at each gridline (daily ⇒ a row of evenly-gapped dashes; sparse ⇒
// isolated dashes). 24h time-of-day fires become short point dashes; dense sub-daily
// schedules become a solid band.
export interface ScheduleSegment {
  start: number;
  end: number;
  label: string;
  // True for a point-in-time fire (24h view): a single clock-time mark rather than
  // a span. Rendered as a fixed-width pill centered on `start` (== `end`).
  point?: boolean;
}

export function projectSegments(scenario: ScenarioRow, range: RangeKey): ScheduleSegment[] {
  const proj = projectScenario(scenario, range);
  const def = rangeDef(range);
  const segs: ScheduleSegment[] = [];

  if (range === '24h') {
    // Each daily/weekly/monthly schedule fires once at a clock time — a single point
    // on the time-of-day axis. Mark it as a `point` so the track draws a fixed-width
    // pill centered there (a tiny fractional dash would look lost on this wide axis).
    for (const m of proj.markers) {
      segs.push({ start: m.pos, end: m.pos, label: m.label, point: true });
    }
    // Dense sub-daily schedules (hourly/minutely) run all day — a continuous band.
    for (const b of proj.bands) segs.push({ start: b.start, end: b.end, label: b.label });
    return segs;
  }

  // Day-based ranges: collect the set of firing days, then draw one inset dash per
  // day cell. The inset leaves a small gap at each day gridline so adjacent dashes
  // read as separate marks (a daily schedule becomes a dashed rule, not a solid bar).
  const spanDays = def.spanDays;
  const cell = 1 / spanDays;
  const inset = 0.07 * cell;
  const dayLabel = new Map<number, string>();

  // Sparse fires (daily/weekly/monthly) land on the day their marker sits in.
  for (const m of proj.markers) {
    const d = clamp(Math.floor(m.pos * spanDays), 0, spanDays - 1);
    if (!dayLabel.has(d)) dayLabel.set(d, m.label);
  }
  // Dense fires (hourly/minutely) run all day — mark every day the band spans.
  for (const b of proj.bands) {
    const ds = clamp(Math.floor(b.start * spanDays), 0, spanDays - 1);
    const de = clamp(Math.floor(b.end * spanDays), 0, spanDays - 1);
    for (let d = ds; d <= de; d++) {
      if (!dayLabel.has(d)) dayLabel.set(d, b.label);
    }
  }

  const days = [...dayLabel.keys()].sort((a, b) => a - b);
  for (const d of days) {
    segs.push({ start: d * cell + inset, end: (d + 1) * cell - inset, label: dayLabel.get(d)! });
  }
  return segs;
}

// ---------------------------------------------------------------------------
// Load distribution — distinct scenarios scheduled per time bucket
// ---------------------------------------------------------------------------

export interface LoadBucket {
  index: number;
  label: string;
  count: number;
}

export function bucketLoad(scenarios: ScenarioRow[], range: RangeKey): LoadBucket[] {
  const def = rangeDef(range);
  const nb = def.buckets;
  const counts = new Array<number>(nb).fill(0);

  for (const scn of scenarios) {
    if (!scn.hasTimeSchedule) continue;
    const proj = projectScenario(scn, range);
    const hit = new Set<number>();
    for (const m of proj.markers) hit.add(clamp(Math.floor(m.pos * nb), 0, nb - 1));
    for (const b of proj.bands) {
      const s = clamp(Math.floor(b.start * nb), 0, nb - 1);
      const e = clamp(Math.floor(b.end * nb), 0, nb - 1);
      for (let i = s; i <= e; i++) hit.add(i);
    }
    for (const i of hit) counts[i]++;
  }

  return counts.map((count, index) => ({ index, label: bucketLabel(range, index), count }));
}

function bucketLabel(range: RangeKey, index: number): string {
  switch (range) {
    case '24h':
      return `${pad2(index)}:00`;
    case '7d':
      return DAY_SHORT[(1 + index) % 7]; // Mon-anchored
    case 'month':
      return `Day ${index + 1}`;
    case 'quarter':
      return `Wk ${index + 1}`;
    default:
      return String(index);
  }
}

/** Peak time-of-day clustering across point-scheduled (non-dense) triggers — the
 *  signal behind the advisor callout ("N scenarios fire at 02:00"). */
export interface PeakHour {
  hour: number;
  count: number;
  scheduledScenarios: number; // scenarios with at least one active temporal trigger
}

export function peakTimeOfDay(scenarios: ScenarioRow[]): PeakHour {
  const hours = new Array<number>(24).fill(0);
  let scheduled = 0;
  for (const scn of scenarios) {
    if (!scn.hasTimeSchedule) continue;
    scheduled++;
    const seen = new Set<number>();
    for (const t of scn.triggers) {
      if (t.type !== 'temporal' || !t.active || !t.temporal) continue;
      const r = computeTimeOfDay(t);
      if (r.dense) continue; // continuous schedules don't form a clock-time spike
      for (const min of r.minutes) seen.add(Math.floor(min / 60));
    }
    for (const h of seen) hours[h]++;
  }
  let hour = 0;
  let count = 0;
  for (let h = 0; h < 24; h++) {
    if (hours[h] > count) { count = hours[h]; hour = h; }
  }
  return { hour, count, scheduledScenarios: scheduled };
}

// ---------------------------------------------------------------------------
// Axis ticks / gridlines
// ---------------------------------------------------------------------------

export function axisTicks(range: RangeKey): AxisTick[] {
  switch (range) {
    case '24h': {
      const ticks: AxisTick[] = [];
      for (let h = 0; h <= 24; h++) {
        ticks.push({ pos: h / 24, label: pad2(h), major: h % 3 === 0 });
      }
      return ticks;
    }
    case '7d': {
      const ticks: AxisTick[] = [];
      for (let d = 0; d < 7; d++) {
        ticks.push({ pos: d / 7, label: DAY_SHORT[(1 + d) % 7], major: true });
      }
      ticks.push({ pos: 1, label: '', major: false });
      return ticks;
    }
    case 'month': {
      const ticks: AxisTick[] = [];
      for (let d = 0; d < 30; d++) {
        ticks.push({ pos: d / 30, label: `${d + 1}`, major: d % 7 === 0 });
      }
      ticks.push({ pos: 1, label: '', major: false });
      return ticks;
    }
    case 'quarter': {
      const ticks: AxisTick[] = [];
      for (let w = 0; w < 13; w++) {
        ticks.push({ pos: (w * 7) / 90, label: `W${w + 1}`, major: w % 2 === 0 });
      }
      ticks.push({ pos: 1, label: '', major: false });
      return ticks;
    }
    default:
      return [];
  }
}
