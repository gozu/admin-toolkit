import { fetchJson } from '../utils/api';
import { createSyncStore } from './createSyncStore';
import { getActiveHostId } from './hostStore';
import { subscribeSessionEpoch } from './sessionCache';
import { restartProcessMetricsScan } from './processMetrics';
import { refreshHostSummary, type HostSummaryData } from './hostSummary';

// ─────────────────────────────────────────────────────────────────────────
// Live resource sampling for the Resources page. Polls the uncached
// /api/host/resource-sample endpoint on a setTimeout chain (no overlap),
// keeps a ring buffer of raw cumulative /proc counters, and derives CPU%/
// MEM% by diffing consecutive samples. A slower "heavy tier" re-runs the
// full `ps` scan + host summary so the doughnuts/table stay current too.
// ─────────────────────────────────────────────────────────────────────────

export interface ResourceCpuCounters {
  user: number;
  nice: number;
  system: number;
  idle: number;
  iowait: number;
  irq: number;
  softirq: number;
  steal: number;
  cpuCount: number;
}

export interface ResourceMemCounters {
  totalKb: number;
  freeKb: number;
  availableKb: number;
  buffersKb: number;
  cachedKb: number;
  swapTotalKb: number;
  swapFreeKb: number;
}

export interface ResourceSample {
  ts: number;
  cpu: ResourceCpuCounters;
  mem: ResourceMemCounters;
}

interface ResourceSampleResponse {
  ok: boolean;
  ts?: number;
  cpu?: ResourceCpuCounters;
  mem?: ResourceMemCounters;
  error?: string;
}

export type ResourcePollStatus = 'idle' | 'polling' | 'paused' | 'unsupported';

export interface ResourceSamplesState {
  status: ResourcePollStatus;
  samples: ResourceSample[];
  intervalMs: number;
  error: string | null;
}

// 120 intervals + the seed sample = 10 min of history locally, 30 min remote.
export const MAX_SAMPLE_SLOTS = 121;
const LOCAL_INTERVAL_MS = 5_000;
const REMOTE_INTERVAL_MS = 15_000;
// Heavy tier (full `ps` + host summary) at a slower multiple of the light tick.
const LOCAL_HEAVY_MS = 30_000;
const REMOTE_HEAVY_MS = 60_000;
const MAX_CONSECUTIVE_FAILURES = 2;

const INITIAL_STATE: ResourceSamplesState = {
  status: 'idle',
  samples: [],
  intervalMs: LOCAL_INTERVAL_MS,
  error: null,
};

export const resourceSamplesStore = createSyncStore<ResourceSamplesState>(INITIAL_STATE, {
  sessionScoped: true,
});

let _active = false;
// Chain token: every (re)start/resume bumps it so a stale in-flight tick from
// a previous chain can never double-schedule.
let _chain = 0;
let _timer: ReturnType<typeof setTimeout> | null = null;
let _failures = 0;
let _intervalMs = LOCAL_INTERVAL_MS;
let _heavyMs = LOCAL_HEAVY_MS;
let _lastHeavyAt = 0;
let _applyHostSummary: ((data: HostSummaryData) => void) | null = null;
let _visibilityHooked = false;

function clearTimer(): void {
  if (_timer != null) {
    clearTimeout(_timer);
    _timer = null;
  }
}

function handleVisibility(): void {
  if (!_active) return;
  if (document.hidden) {
    _chain += 1;
    clearTimer();
    if (resourceSamplesStore.get().status === 'polling') {
      resourceSamplesStore.patch({ status: 'paused' });
    }
  } else if (resourceSamplesStore.get().status === 'paused') {
    resourceSamplesStore.patch({ status: 'polling' });
    void tick(++_chain);
  }
}

async function tick(chainId: number): Promise<void> {
  if (!_active || chainId !== _chain || document.hidden) return;
  try {
    const data = await fetchJson<ResourceSampleResponse>('/api/host/resource-sample');
    if (!_active || chainId !== _chain) return;
    if (!data.ok || !data.cpu || !data.mem) {
      throw new Error(data.error || 'resource sample unavailable');
    }
    _failures = 0;
    const prev = resourceSamplesStore.get();
    const samples = [
      ...prev.samples,
      { ts: data.ts || Date.now() / 1000, cpu: data.cpu, mem: data.mem },
    ].slice(-MAX_SAMPLE_SLOTS);
    resourceSamplesStore.patch({ samples, status: 'polling', error: null });
  } catch (err) {
    if (!_active || chainId !== _chain) return;
    _failures += 1;
    if (_failures >= MAX_CONSECUTIVE_FAILURES) {
      // Older remote toolkit without the macro, or a host that can't serve
      // /proc — give up quietly; the page hides the live chart.
      resourceSamplesStore.patch({
        status: 'unsupported',
        error: err instanceof Error ? err.message : String(err),
      });
      stopResourcePolling();
      return;
    }
  }
  const now = Date.now();
  if (now - _lastHeavyAt >= _heavyMs) {
    _lastHeavyAt = now;
    restartProcessMetricsScan();
    if (_applyHostSummary) void refreshHostSummary(_applyHostSummary);
  }
  if (!_active || chainId !== _chain || document.hidden) return;
  _timer = setTimeout(() => void tick(chainId), _intervalMs);
}

/** Start (or re-start) polling for the active host. Idempotent while active.
 * Existing same-session samples are kept so navigating away and back doesn't
 * lose the history window. */
export function startResourcePolling(applyHostSummary: (data: HostSummaryData) => void): void {
  _applyHostSummary = applyHostSummary;
  if (_active) return;
  _active = true;
  _failures = 0;
  // Don't fire the heavy tier on mount — the page already starts the process
  // scan itself and the host summary is fresh from startup.
  _lastHeavyAt = Date.now();
  const remote = getActiveHostId() !== 'local';
  _intervalMs = remote ? REMOTE_INTERVAL_MS : LOCAL_INTERVAL_MS;
  _heavyMs = remote ? REMOTE_HEAVY_MS : LOCAL_HEAVY_MS;
  resourceSamplesStore.patch({ status: 'polling', intervalMs: _intervalMs, error: null });
  if (!_visibilityHooked) {
    _visibilityHooked = true;
    document.addEventListener('visibilitychange', handleVisibility);
  }
  void tick(++_chain);
}

export function stopResourcePolling(): void {
  if (!_active) return;
  _active = false;
  _chain += 1;
  clearTimer();
  const status = resourceSamplesStore.get().status;
  if (status === 'polling' || status === 'paused') {
    resourceSamplesStore.patch({ status: 'idle' });
  }
}

// Host switch / cache refresh resets the store (sessionScoped); make sure the
// controller's timer chain dies with it.
subscribeSessionEpoch(() => stopResourcePolling());

export interface ResourcePoint {
  ts: number;
  cpuPct: number;
  memPct: number;
}

/** Diff consecutive raw samples into CPU%/MEM% points. Pure — safe to call at
 * render time. Pairs with a non-positive total delta (counter reset after a
 * reboot) are skipped. */
export function computeResourceSeries(samples: readonly ResourceSample[]): ResourcePoint[] {
  const out: ResourcePoint[] = [];
  for (let i = 1; i < samples.length; i++) {
    const a = samples[i - 1];
    const b = samples[i];
    const busy =
      b.cpu.user - a.cpu.user +
      (b.cpu.nice - a.cpu.nice) +
      (b.cpu.system - a.cpu.system) +
      (b.cpu.irq - a.cpu.irq) +
      (b.cpu.softirq - a.cpu.softirq) +
      (b.cpu.steal - a.cpu.steal);
    const idle = b.cpu.idle - a.cpu.idle + (b.cpu.iowait - a.cpu.iowait);
    const total = busy + idle;
    if (total <= 0) continue;
    const cpuPct = Math.min(100, Math.max(0, (100 * busy) / total));
    const memPct =
      b.mem.totalKb > 0
        ? Math.min(100, Math.max(0, 100 * (1 - b.mem.availableKb / b.mem.totalKb)))
        : 0;
    out.push({ ts: b.ts, cpuPct, memPct });
  }
  return out;
}
