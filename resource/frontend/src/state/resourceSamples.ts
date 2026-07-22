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
// `processes` (per-PID snapshot) frames. The cadence is server-driven: local
// is a fixed 1s /proc read; remote hosts default to 1s too but each remote
// sample is a DSS macro job, so the period is user-configurable (persisted,
// sent as ?period= — the Live usage header owns the picker). The `ps`
// snapshot stays on its own ~60s server-side cadence either way.
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
  /** Active host is remote → the period picker applies (local is pinned at 1s). */
  remote: boolean;
  error: string | null;
}

// 120 intervals + the seed sample = 2 min of history at the 1s stream cadence.
export const MAX_SAMPLE_SLOTS = 121;
const LOCAL_STREAM_MS = 1_000;
const MAX_STREAM_RETRIES = 2;
const STREAM_RETRY_DELAY_MS = 2_000;

// Remote sampling period (each remote tick = a macro job on the target host).
export const REMOTE_PERIOD_OPTIONS_S: number[] = [1, 2, 5, 15, 30, 60];
const REMOTE_PERIOD_DEFAULT_S = 1;
const REMOTE_PERIOD_KEY = 'admin-toolkit:resourceStreamPeriodS';

export function getRemoteStreamPeriodS(): number {
  try {
    const raw = Number(localStorage.getItem(REMOTE_PERIOD_KEY));
    if (REMOTE_PERIOD_OPTIONS_S.includes(raw)) return raw;
  } catch {
    /* storage unavailable — fall through to default */
  }
  return REMOTE_PERIOD_DEFAULT_S;
}

const INITIAL_STATE: ResourceSamplesState = {
  status: 'idle',
  samples: [],
  intervalMs: LOCAL_STREAM_MS,
  remote: false,
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
  const path =
    getActiveHostId() === 'local'
      ? '/api/host/resource-stream'
      : `/api/host/resource-stream?period=${getRemoteStreamPeriodS()}`;
  try {
    for await (const frame of fetchSse(path, {
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
  const remote = getActiveHostId() !== 'local';
  resourceSamplesStore.patch({
    status: 'polling',
    intervalMs: remote ? getRemoteStreamPeriodS() * 1000 : LOCAL_STREAM_MS,
    remote,
    error: null,
  });
  if (!_visibilityHooked) {
    _visibilityHooked = true;
    document.addEventListener('visibilitychange', handleVisibility);
  }
  void runStream(++_chain);
}

/** Change the remote sampling period (the Live usage header picker). Persists
 * the choice and, when a remote stream is live, kills the current connection
 * and reopens it so the server picks up the new cadence immediately. Existing
 * samples are kept — the window label self-heals as new ticks slide in. */
export function setRemoteStreamPeriodS(seconds: number): void {
  if (!REMOTE_PERIOD_OPTIONS_S.includes(seconds)) return;
  try {
    localStorage.setItem(REMOTE_PERIOD_KEY, String(seconds));
  } catch {
    /* storage unavailable — the pick still applies for this session */
  }
  if (getActiveHostId() === 'local') return;
  resourceSamplesStore.patch({ intervalMs: seconds * 1000 });
  if (_active && !document.hidden) {
    clearTimer();
    abortStream();
    _streamRetries = 0;
    void runStream(++_chain);
  }
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
