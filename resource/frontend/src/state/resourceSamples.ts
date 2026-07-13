import { fetchSse } from '../utils/api';
import { createSyncStore } from './createSyncStore';
import { getActiveHostId } from './hostStore';
import { subscribeSessionEpoch } from './sessionCache';
import { applyStreamedProcessSnapshot, type StreamedProcessSnapshot } from './processMetrics';
import { formatMemory } from '../utils/formatters';
import type { MemoryInfo } from '../types';

// ─────────────────────────────────────────────────────────────────────────
// Live resource sampling for the Resources page: one long-lived SSE
// connection to /api/host/resource-stream for EVERY host. The server pushes
// `sample` (raw cumulative counters — diffed client-side into CPU%/MEM%) and
// `processes` (per-PID snapshot) frames. The cadence is server-driven: 1s
// /proc reads locally, 15s/60s macro runs on remote hosts (each remote
// sample is a DSS macro job — the interval constants here are display-only).
// Samples stay in this store; parsedData.memoryInfo remains the one-shot
// diagnostic snapshot used by configuration analysis and reports.
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

// 120 intervals + the seed sample = 2 min of history at the 1s stream cadence.
export const MAX_SAMPLE_SLOTS = 121;
// Server-side stream ticks (display only — the server drives the cadence).
const LOCAL_STREAM_MS = 1_000;
const REMOTE_STREAM_MS = 15_000;
const MAX_STREAM_RETRIES = 2;
const STREAM_RETRY_DELAY_MS = 2_000;

const INITIAL_STATE: ResourceSamplesState = {
  status: 'idle',
  samples: [],
  intervalMs: LOCAL_STREAM_MS,
  error: null,
};

export const resourceSamplesStore = createSyncStore<ResourceSamplesState>(INITIAL_STATE, {
  sessionScoped: true,
});

let _active = false;
// Chain token: every (re)start/resume bumps it so a dying stream consumer
// from a previous chain can never double-schedule its retry timer.
let _chain = 0;
let _timer: ReturnType<typeof setTimeout> | null = null;
let _streamRetries = 0;
let _streamAbort: AbortController | null = null;
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
    void runStream(++_chain);
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
export function memoryInfoFromSample(mem: ResourceMemCounters): MemoryInfo {
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

/** Start (or re-start) live sampling for the active host. Idempotent while
 * active. Existing same-session samples are kept so navigating away and back
 * doesn't lose the history window. */
export function startResourcePolling(): void {
  if (_active) return;
  _active = true;
  _streamRetries = 0;
  resourceSamplesStore.patch({
    status: 'polling',
    intervalMs: getActiveHostId() === 'local' ? LOCAL_STREAM_MS : REMOTE_STREAM_MS,
    error: null,
  });
  if (!_visibilityHooked) {
    _visibilityHooked = true;
    document.addEventListener('visibilitychange', handleVisibility);
  }
  void runStream(++_chain);
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
