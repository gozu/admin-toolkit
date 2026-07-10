import { fetchJson } from '../utils/api';
import { createSyncStore } from './createSyncStore';
import { getActiveHostId } from './hostStore';
import { subscribeSessionEpoch } from './sessionCache';
import { getProcessMetrics, restartProcessMetricsScan } from './processMetrics';
import {
  getHostSummary,
  refreshHostSummary,
  type HostSummaryData,
} from './hostSummary';

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

// 120 one-second intervals + the seed sample = 2 min of history.
export const MAX_SAMPLE_SLOTS = 121;
const REFRESH_INTERVAL_MS = 1_000;
const LOCAL_SUMMARY_INTERVAL_MS = 30_000;
const REMOTE_SUMMARY_INTERVAL_MS = 60_000;
const MAX_CONSECUTIVE_FAILURES = 2;

const INITIAL_STATE: ResourceSamplesState = {
  status: 'idle',
  samples: [],
  intervalMs: REFRESH_INTERVAL_MS,
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
let _intervalMs = REFRESH_INTERVAL_MS;
let _summaryIntervalMs = LOCAL_SUMMARY_INTERVAL_MS;
let _lastProcessRefreshAt = 0;
let _lastSummaryRefreshAt = 0;
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
  if (now - _lastProcessRefreshAt >= REFRESH_INTERVAL_MS) {
    _lastProcessRefreshAt = now;
    if (getProcessMetrics().status !== 'loading') restartProcessMetricsScan();
  }
  if (now - _lastSummaryRefreshAt >= _summaryIntervalMs) {
    _lastSummaryRefreshAt = now;
    if (_applyHostSummary && getHostSummary().status !== 'loading') {
      void refreshHostSummary(_applyHostSummary);
    }
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
  _lastProcessRefreshAt = Date.now();
  _lastSummaryRefreshAt = Date.now();
  // Keep both the graph sample and process table on the same cadence for
  // local and remote hosts.
  _intervalMs = REFRESH_INTERVAL_MS;
  _summaryIntervalMs =
    getActiveHostId() === 'local' ? LOCAL_SUMMARY_INTERVAL_MS : REMOTE_SUMMARY_INTERVAL_MS;
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
