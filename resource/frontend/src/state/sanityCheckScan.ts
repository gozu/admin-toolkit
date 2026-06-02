import type { SanityCheckMessage } from '../types';
import { fetchRaw } from '../utils/api';

export interface SanityCheckResult {
  messages: SanityCheckMessage[];
  maxSeverity: string | null;
}

interface SanityCheckResponse {
  messages?: SanityCheckMessage[];
  maxSeverity?: string | null;
  error?: string;
}

let inflight: Promise<SanityCheckResult> | null = null;

export function runSanityCheck(
  opts: { force?: boolean; signal?: AbortSignal } = {},
): Promise<SanityCheckResult> {
  if (inflight && !opts.force) return inflight;
  const promise = (async () => {
    const response = await fetchRaw('/api/sanity-check', { signal: opts.signal });
    const body = (await response.json()) as SanityCheckResponse;
    if (!response.ok) {
      throw new Error(body.error || `Scan failed: ${response.status} ${response.statusText}`);
    }
    return {
      messages: body.messages || [],
      maxSeverity: body.maxSeverity ?? null,
    };
  })();
  inflight = promise;
  promise.finally(() => {
    if (inflight === promise) inflight = null;
  });
  return promise;
}
