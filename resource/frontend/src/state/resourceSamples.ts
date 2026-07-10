import { fetchJson, fetchSse } from '../utils/api';
import { createSyncStore } from './createSyncStore';
import { getActiveHostId } from './hostStore';
import { subscribeSessionEpoch } from './sessionCache';
import {
  applyStreamedProcessSnapshot,
  restartProcessMetricsScan,
  type StreamedProcessSnapshot,
} from './processMetrics';
import { refreshHostSummary } from './hostSummary';
import { formatMemory } from '../utils/formatters';
import type { MemoryInfo, ParsedData } from '../types';

// ─────────────────────────────────────────────────────────────────────────
// Live resource sampling for the Resources page.
//
// LOCAL host: one long-lived SSE connection to /api/host/resource-stream.
// The server samples /proc every second and pushes `sample` (raw cumulative
// counters — diffed client-side into CPU%/MEM%, same as the polled path) and
// `processes` (per-PID snapshot from /proc tick deltas) frames. No macro
// runs, no per-second HTTP churn.
//
// REMOTE host: the pre-stream polling architecture — a setTimeout chain on
// /api/host/resource-sample (15s) plus a slower "heavy tier" (60s) that
// re-runs the full `ps` macro + host summary.
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
  /** How samples arrive: pushed over SSE (local) or polled (remote). */
  mode: 'stream' | 'poll';
  error: string | null;
}

// 120 intervals + the seed sample = 2 min of history at the 1s stream cadence.
export const MAX_SAMPLE_SLOTS = 121;
// The server-side stream tick (display only — the server drives the cadence).
const LOCAL_STREAM_MS = 1_000;
const REMOTE_INTERVAL_MS = 15_000;
// Heavy tier (full `ps` macro + host summary), remote hosts only.
const REMOTE_HEAVY_MS = 60_000;
const MAX_CONSECUTIVE_FAILURES = 2;
const MAX_STREAM_RETRIES = 2;
const STREAM_RETRY_DELAY_MS = 2_000;

const INITIAL_STATE: ResourceSamplesState = {
  status: 'idle',
  samples: [],
  intervalMs: LOCAL_STREAM_MS,
  mode: 'stream',
  error: null,
};

export const resourceSamplesStore = createSyncStore<ResourceSamplesState>(INITIAL_STATE, {
  sessionScoped: true,
});

let _active = false;
// Chain token: every (re)start/resume bumps it so a stale in-flight tick or a
// dying stream consumer from a previous chain can never double-schedule.
let _chain = 0;
let _timer: ReturnType<typeof setTimeout> | null = null;
let _failures = 0;
let _streamRetries = 0;
let _isLocal = true;
let _lastHeavyAt = 0;
let _streamAbort: AbortController | null = null;
let _applyParsedData: ((data: Partial<ParsedData>) => void) | null = null;
let _visibilityHooked = false;

function clearTimer(): void {
  if (_timer != null) {
    clearTimeout(_timer);
    _timer = null;
  }
}

function abortStream(): void {
  _streamAbort?.abort();
  _streamAbort = null;
}

/** Launch the transport that matches the active host. */
function launch(chainId: number): void {
  if (_isLocal) void runStream(chainId);
  else void tick(chainId);
}

function handleVisibility(): void {
  if (!_active) return;
  if (document.hidden) {
    _chain += 1;
    clearTimer();
    abortStream();
    if (resourceSamplesStore.get().status === 'polling') {
      resourceSamplesStore.patch({ status: 'paused' });
    }
  } else if (resourceSamplesStore.get().status === 'paused') {
    resourceSamplesStore.patch({ status: 'polling' });
    launch(++_chain);
  }
}

function appendSample(data: ResourceSampleResponse): void {
  if (!data.cpu || !data.mem) return;
  const prev = resourceSamplesStore.get();
  const samples = [
    ...prev.samples,
    { ts: data.ts || Date.now() / 1000, cpu: data.cpu, mem: data.mem },
  ].slice(-MAX_SAMPLE_SLOTS);
  resourceSamplesStore.patch({ samples, status: 'polling', error: null });
}

/** Streamed meminfo counters → the `free -m`-shaped strings the Memory
 * doughnut/summary render, so they go live without re-running host commands.
 * Key set and semantics match sysinfo._parse_memory_info (used = total −
 * free − buff/cache). */
function memoryInfoFromSample(mem: ResourceMemCounters): MemoryInfo {
  const fmt = (kb: number) => formatMemory(Math.max(0, Math.round(kb / 1024)));
  const buffCacheKb = mem.buffersKb + mem.cachedKb;
  const info: MemoryInfo = {
    total: fmt(mem.totalKb),
    used: fmt(mem.totalKb - mem.freeKb - buffCacheKb),
    free: fmt(mem.freeKb),
    available: fmt(mem.availableKb),
    'buff/cache': fmt(buffCacheKb),
  };
  if (mem.swapTotalKb > 0) {
    info['Swap total'] = fmt(mem.swapTotalKb);
    info['Swap used'] = fmt(mem.swapTotalKb - mem.swapFreeKb);
    info['Swap free'] = fmt(mem.swapFreeKb);
  } else {
    info['Swap'] = 'Not configured';
  }
  return info;
}

function giveUp(err: unknown): void {
  // Stale backend without the stream endpoint, or a host that can't serve
  // /proc — give up quietly; the page hides the live chart.
  resourceSamplesStore.patch({
    status: 'unsupported',
    error: err instanceof Error ? err.message : String(err),
  });
  stopResourcePolling();
}

// ── Local transport: one SSE connection ─────────────────────────────────

async function runStream(chainId: number): Promise<void> {
  const controller = new AbortController();
  _streamAbort = controller;
  try {
    for await (const frame of fetchSse('/api/host/resource-stream', {
      signal: controller.signal,
    })) {
      if (!_active || chainId !== _chain) return;
      if (frame.event === 'sample') {
        const data = frame.payload as ResourceSampleResponse;
        if (!data.ok) throw new Error(data.error || 'resource sample unavailable');
        _streamRetries = 0;
        appendSample(data);
        if (data.mem && _applyParsedData) {
          _applyParsedData({ memoryInfo: memoryInfoFromSample(data.mem) });
        }
      } else if (frame.event === 'processes') {
        applyStreamedProcessSnapshot(frame.payload as StreamedProcessSnapshot);
      }
    }
    // Server closed a live stream (backend restart, proxy timeout) — retry.
    throw new Error('resource stream closed');
  } catch (err) {
    if (!_active || chainId !== _chain || controller.signal.aborted) return;
    _streamRetries += 1;
    if (_streamRetries > MAX_STREAM_RETRIES) {
      giveUp(err);
      return;
    }
    _timer = setTimeout(() => void runStream(chainId), STREAM_RETRY_DELAY_MS);
  } finally {
    if (_streamAbort === controller) _streamAbort = null;
  }
}

// ── Remote transport: setTimeout poll chain + heavy tier ────────────────

async function tick(chainId: number): Promise<void> {
  if (!_active || chainId !== _chain || document.hidden) return;
  try {
    const data = await fetchJson<ResourceSampleResponse>('/api/host/resource-sample');
    if (!_active || chainId !== _chain) return;
    if (!data.ok || !data.cpu || !data.mem) {
      throw new Error(data.error || 'resource sample unavailable');
    }
    _failures = 0;
    appendSample(data);
  } catch (err) {
    if (!_active || chainId !== _chain) return;
    _failures += 1;
    if (_failures >= MAX_CONSECUTIVE_FAILURES) {
      giveUp(err);
      return;
    }
  }
  const now = Date.now();
  if (now - _lastHeavyAt >= REMOTE_HEAVY_MS) {
    _lastHeavyAt = now;
    restartProcessMetricsScan();
    if (_applyParsedData) void refreshHostSummary(_applyParsedData);
  }
  if (!_active || chainId !== _chain || document.hidden) return;
  _timer = setTimeout(() => void tick(chainId), REMOTE_INTERVAL_MS);
}

/** Start (or re-start) live sampling for the active host. Idempotent while
 * active. Existing same-session samples are kept so navigating away and back
 * doesn't lose the history window. */
export function startResourcePolling(applyParsedData: (data: Partial<ParsedData>) => void): void {
  _applyParsedData = applyParsedData;
  if (_active) return;
  _active = true;
  _failures = 0;
  _streamRetries = 0;
  // Don't fire the remote heavy tier on mount — the page already starts the
  // process scan itself and the host summary is fresh from startup.
  _lastHeavyAt = Date.now();
  _isLocal = getActiveHostId() === 'local';
  resourceSamplesStore.patch({
    status: 'polling',
    intervalMs: _isLocal ? LOCAL_STREAM_MS : REMOTE_INTERVAL_MS,
    mode: _isLocal ? 'stream' : 'poll',
    error: null,
  });
  if (!_visibilityHooked) {
    _visibilityHooked = true;
    document.addEventListener('visibilitychange', handleVisibility);
  }
  launch(++_chain);
}

export function stopResourcePolling(): void {
  if (!_active) return;
  _active = false;
  _chain += 1;
  clearTimer();
  abortStream();
  const status = resourceSamplesStore.get().status;
  if (status === 'polling' || status === 'paused') {
    resourceSamplesStore.patch({ status: 'idle' });
  }
}

// Host switch / cache refresh resets the store (sessionScoped); make sure the
// controller's timer chain and stream connection die with it.
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
