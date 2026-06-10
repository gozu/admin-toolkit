/**
 * Live-progress event replay shared by the code-envs ('ce') and
 * project-footprint ('pjft') pollers. The old monolithic useApiDataLoader.ts
 * carried two verbatim copies of this machinery (event dedup keys,
 * usage-check message parsing, provisional-row synthesis, expected-count
 * signal) differing only in the bench-line code — unified here.
 */
import type { ProvisionalCodeEnv } from '../../types';
import type { LoaderCtx } from './context';
import type { LifecycleTracker } from './lifecycle';
import type { BenchEventLike } from './types';

export interface ProgressReplay {
  replay: (events: BenchEventLike[]) => void;
  /** Reset all dedup/count state (on a runId change). */
  reset: () => void;
  eventsSeen: () => number;
  setExpectedCodeEnvCount: (nextCount: number | null | undefined) => void;
}

export function createProgressReplay(
  ctx: LoaderCtx,
  tracker: LifecycleTracker,
  code: 'ce' | 'pjft',
): ProgressReplay {
  const { dispatch, log, benchEventLine, shouldLogProgressEvent } = ctx;
  const seenEventKeys = new Set<string>();
  let usageScanTotal: number | null = null;
  let eventsSeen = 0;

  const progressEventKey = (event: BenchEventLike) =>
    `${event.tMs ?? ''}|${event.step ?? ''}|${event.projectKey ?? ''}|${event.message ?? ''}|${event.elapsedMs ?? ''}`;

  const setExpectedCodeEnvCount = (nextCount: number | null | undefined) => {
    const normalized =
      typeof nextCount === 'number' && Number.isFinite(nextCount) && nextCount >= 0
        ? Math.floor(nextCount)
        : undefined;
    if (tracker.data.codeEnvsExpectedCount === normalized) return;
    tracker.data = {
      ...tracker.data,
      codeEnvsExpectedCount: normalized,
    };
    dispatch({ type: 'SET_PARSED_DATA', payload: { codeEnvsExpectedCount: normalized } });
  };

  const parseUsageCheckMessage = (message: string) => {
    const match = message.match(/^\[(\d+)\/(\d+)\]\s+(.+?)\s+[—–-]\s+(.+)$/u);
    if (!match) return null;
    const scanIndex = Number.parseInt(match[1], 10);
    const scanTotal = Number.parseInt(match[2], 10);
    const name = match[3].trim();
    const status = match[4].trim();
    const isSkipped = /skipped/i.test(status);
    const usageMatch = status.match(/(\d+)\s+usage\(s\)/i);
    const usageCount = /unused/i.test(status)
      ? 0
      : usageMatch
        ? Number.parseInt(usageMatch[1], 10)
        : NaN;
    return {
      scanIndex: Number.isFinite(scanIndex) ? scanIndex : undefined,
      scanTotal: Number.isFinite(scanTotal) ? scanTotal : undefined,
      name,
      status,
      isSkipped,
      usageCount: Number.isFinite(usageCount) ? Math.max(0, usageCount) : null,
    };
  };

  const toProvisionalRow = (parsed: {
    scanIndex?: number;
    scanTotal?: number;
    name: string;
    status: string;
    isSkipped: boolean;
    usageCount: number | null;
  }): ProvisionalCodeEnv | null => {
    if (!parsed.name) return null;
    if (parsed.isSkipped) {
      return {
        name: parsed.name,
        usageCount: -1,
        statusLabel: parsed.status,
        isSkipped: true,
        scanIndex: parsed.scanIndex,
        scanTotal: parsed.scanTotal,
        updatedAt: new Date().toISOString(),
      };
    }
    if (parsed.usageCount == null) return null;
    return {
      name: parsed.name,
      usageCount: parsed.usageCount,
      statusLabel: parsed.status,
      scanIndex: parsed.scanIndex,
      scanTotal: parsed.scanTotal,
      updatedAt: new Date().toISOString(),
    };
  };

  const replay = (events: BenchEventLike[]) => {
    const provisionalRows: ProvisionalCodeEnv[] = [];
    events.forEach((event) => {
      const key = progressEventKey(event);
      if (seenEventKeys.has(key)) return;
      seenEventKeys.add(key);
      const normalizedStep = String(event.step || '')
        .trim()
        .toLowerCase();
      if (normalizedStep === 'code_env_usage_scan_start') {
        const startMatch = String(event.message || '').match(/checking\s+(\d+)\s+code envs/i);
        const scannedTotal = startMatch ? Number.parseInt(startMatch[1], 10) : NaN;
        if (Number.isFinite(scannedTotal) && scannedTotal > 0) {
          usageScanTotal = scannedTotal;
        }
      }
      if (normalizedStep === 'code_env_usage_check') {
        const parsed = parseUsageCheckMessage(String(event.message || '').trim());
        if (parsed) {
          if (typeof parsed.scanTotal === 'number' && parsed.scanTotal > 0) {
            usageScanTotal = parsed.scanTotal;
          }
          const provisional = toProvisionalRow(parsed);
          if (provisional) provisionalRows.push(provisional);
        }
      }
      if (shouldLogProgressEvent(event)) {
        eventsSeen += 1;
        const eventLevel = event.level === 'warn' || event.level === 'error' ? event.level : 'info';
        log(benchEventLine(code, event), eventLevel);
      }
    });
    if (usageScanTotal != null) {
      const expectedFromScan = Math.max(0, usageScanTotal);
      setExpectedCodeEnvCount(expectedFromScan);
    }
    if (provisionalRows.length > 0) {
      dispatch({ type: 'UPSERT_PROVISIONAL_CODE_ENVS', payload: provisionalRows });
    }
  };

  return {
    replay,
    reset: () => {
      seenEventKeys.clear();
      usageScanTotal = null;
      eventsSeen = 0;
    },
    eventsSeen: () => eventsSeen,
    setExpectedCodeEnvCount,
  };
}
