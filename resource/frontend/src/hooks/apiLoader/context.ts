/**
 * Shared closure helpers for the live-mode API loader, bundled into one
 * context object built per load effect. Bodies moved verbatim from the old
 * monolithic useApiDataLoader.ts — this is the "everything the chains need"
 * grab bag: debug logging, abort/error classification, bench-line formatting,
 * endpoint timing capture, and the timeout/settle fetch wrappers.
 */
import type { DiagActionWithComparison } from '../../types';
import { fetchJson } from '../../utils/api';
import type { BenchEventLike } from './types';

export const LIVE_PROGRESS_TIMEOUT_MS = 120000;

export interface EndpointTiming {
  label: string;
  durationMs: number;
  status: 'ok' | 'fail' | 'skip';
}

export type LoaderDispatch = (action: DiagActionWithComparison) => void;

export interface LoaderCtx {
  dispatch: LoaderDispatch;
  /** True once the effect tore down (reload/unmount) — chains bail out on it. */
  cancelled: () => boolean;
  log: (message: string, level?: 'info' | 'warn' | 'error') => void;
  nowMs: () => number;
  fmtMs: (start: number) => string;
  getErrorMessage: (err: unknown) => string;
  isAbortError: (err: unknown) => boolean;
  abortPendingRequest: (controller: unknown) => void;
  cleanToken: (value: unknown) => string;
  benchMs: (value: unknown) => string;
  benchEventLine: (code: 'ce' | 'pjft', event: BenchEventLike) => string;
  benchSummaryLine: (code: 'ce' | 'pjft', parts: string[]) => string;
  benchStepLine: (
    code: 'ce' | 'pjft',
    kind: 'step' | 'api',
    name: string,
    calls: number,
    elapsedMs: number,
    qps: number,
  ) => string;
  shouldLogProgressEvent: (event: {
    level?: 'info' | 'warn' | 'error';
    step?: string;
    projectKey?: string;
  }) => boolean;
  basicProjectsEnabled: boolean;
  withTimeout: <T>(promise: Promise<T>, label: string, ms: number) => Promise<T>;
  endpointTimings: EndpointTiming[];
  recordTiming: (label: string, durationMs: number, status?: 'ok' | 'fail' | 'skip') => void;
  timedFetch: <T>(label: string, promise: Promise<T>) => Promise<T>;
  /** Logged + timed + timeout-bounded fetchJson — the Phase-3 fetch wrapper. */
  timed: <T>(path: string, timeoutMs: number) => Promise<T>;
  settle: <T>(promise: Promise<T>) => Promise<PromiseSettledResult<T>>;
  settledError: (result: PromiseSettledResult<unknown>) => string;
}

export function createLoaderContext(dispatch: LoaderDispatch, cancelled: () => boolean): LoaderCtx {
  const log = (message: string, level: 'info' | 'warn' | 'error' = 'info') => {
    dispatch({ type: 'ADD_DEBUG_LOG', payload: { message, scope: 'api-loader', level } });
  };
  const nowMs = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());
  const fmtMs = (start: number) => `${Math.round(nowMs() - start)}ms`;
  const getErrorMessage = (err: unknown) => (err instanceof Error ? err.message : String(err));
  const isAbortError = (err: unknown) => {
    if (err instanceof DOMException && err.name === 'AbortError') return true;
    if (err instanceof Error && err.name === 'AbortError') return true;
    const message = getErrorMessage(err);
    return /aborted|aborterror/i.test(message);
  };
  const abortPendingRequest = (controller: unknown) => {
    if (
      controller &&
      typeof controller === 'object' &&
      'abort' in controller &&
      typeof controller.abort === 'function'
    ) {
      controller.abort();
    }
  };
  const cleanToken = (value: unknown): string => {
    const raw = value == null ? '' : String(value);
    const compact = raw.replace(/\s+/g, ' ').trim();
    if (!compact) return '-';
    return compact.replace(/;/g, ',');
  };
  const benchMs = (value: unknown): string => {
    if (typeof value === 'number' && Number.isFinite(value)) return `${value.toFixed(2)}ms`;
    return '-';
  };
  const benchEventLine = (code: 'ce' | 'pjft', event: BenchEventLike): string => {
    const t = benchMs(event.tMs);
    const key = cleanToken(event.projectKey);
    const step = cleanToken(event.step);
    const message = cleanToken(event.message);
    const elapsed = benchMs(event.elapsedMs);
    if (elapsed !== '-' && message !== '-' && message !== step) {
      return `bench;${code};${t};${key};${step};${elapsed};${message}`;
    }
    if (elapsed !== '-') {
      return `bench;${code};${t};${key};${step};${elapsed}`;
    }
    if (message !== '-' && message !== step) {
      return `bench;${code};${t};${key};${step};${message}`;
    }
    return `bench;${code};${t};${key};${step}`;
  };
  const benchSummaryLine = (code: 'ce' | 'pjft', parts: string[]): string =>
    `bench;${code};summary;${parts.join(';')}`;
  const benchStepLine = (
    code: 'ce' | 'pjft',
    kind: 'step' | 'api',
    name: string,
    calls: number,
    elapsedMs: number,
    qps: number,
  ): string =>
    `bench;${code};${kind};${cleanToken(name)};calls=${calls};elapsed=${benchMs(elapsedMs)};qps=${Number(qps || 0).toFixed(2)}`;
  const shouldLogProgressEvent = (event: {
    level?: 'info' | 'warn' | 'error';
    step?: string;
    projectKey?: string;
  }): boolean => {
    // Always show warnings and errors (including per-project ones)
    const level = event.level === 'warn' || event.level === 'error' ? event.level : 'info';
    if (level !== 'info') return true;
    // Suppress all per-project/per-env info-level events
    if (event.projectKey) return false;
    // Suppress per-env usage check lines (too spammy: 238 lines)
    const step = (event.step || '').replace(/[-\s]/g, '_').toLowerCase();
    if (step === 'code_env_usage_check') return false;
    return true;
  };
  const basicProjectsEnabled = (() => {
    if (typeof window === 'undefined') return false;
    try {
      const query = new URLSearchParams(window.location.search);
      return query.get('basicProjects') === '1';
    } catch {
      return false;
    }
  })();
  const withTimeout = <T>(promise: Promise<T>, label: string, ms: number): Promise<T> =>
    new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
      promise.then(
        (value) => {
          clearTimeout(timer);
          resolve(value);
        },
        (error) => {
          clearTimeout(timer);
          reject(error);
        },
      );
    });

  const endpointTimings: EndpointTiming[] = [];
  const recordTiming = (
    label: string,
    durationMs: number,
    status: 'ok' | 'fail' | 'skip' = 'ok',
  ) => {
    endpointTimings.push({ label, durationMs: Math.round(durationMs), status });
  };
  const timedFetch = <T>(label: string, promise: Promise<T>): Promise<T> => {
    const s = nowMs();
    return promise.then(
      (v) => {
        recordTiming(label, nowMs() - s);
        return v;
      },
      (e) => {
        recordTiming(label, nowMs() - s, 'fail');
        throw e;
      },
    );
  };
  const timed = <T>(path: string, timeoutMs: number): Promise<T> => {
    const started = nowMs();
    const startTs = new Date().toISOString().slice(11, 19);
    log(`GET ${path}`);
    return withTimeout(fetchJson<T>(path), path, timeoutMs).then(
      (value) => {
        log(
          `GET ${path} OK (${fmtMs(started)}) [${startTs}→${new Date().toISOString().slice(11, 19)}]`,
        );
        recordTiming(path, nowMs() - started);
        return value;
      },
      (err) => {
        log(
          `GET ${path} FAIL (${fmtMs(started)}) [${startTs}→${new Date().toISOString().slice(11, 19)}]`,
        );
        recordTiming(path, nowMs() - started, 'fail');
        throw err;
      },
    );
  };
  const settle = async <T>(promise: Promise<T>): Promise<PromiseSettledResult<T>> => {
    try {
      const value = await promise;
      return { status: 'fulfilled', value };
    } catch (reason) {
      return { status: 'rejected', reason };
    }
  };
  const settledError = (result: PromiseSettledResult<unknown>) =>
    result.status === 'rejected' ? getErrorMessage(result.reason) : 'no payload';

  return {
    dispatch,
    cancelled,
    log,
    nowMs,
    fmtMs,
    getErrorMessage,
    isAbortError,
    abortPendingRequest,
    cleanToken,
    benchMs,
    benchEventLine,
    benchSummaryLine,
    benchStepLine,
    shouldLogProgressEvent,
    basicProjectsEnabled,
    withTimeout,
    endpointTimings,
    recordTiming,
    timedFetch,
    timed,
    settle,
    settledError,
  };
}
