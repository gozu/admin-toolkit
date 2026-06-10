/**
 * Phase 1 of the live-mode load: /api/overview plus the raw settings /
 * project-standards payloads the parsers consume in Phase 2. Bodies moved
 * verbatim from the old monolithic useApiDataLoader.ts.
 */
import { fetchJson } from '../../utils/api';
import type { LoaderCtx } from './context';
import type { OverviewResponse } from './types';

export interface Phase1Result {
  overview: OverviewResponse;
  rawSettings: Record<string, unknown>;
  rawProjectStandards: Record<string, unknown>;
}

export async function loadPhase1(ctx: LoaderCtx): Promise<Phase1Result> {
  const { log, nowMs, fmtMs, recordTiming } = ctx;
  const overviewStart = nowMs();
  const overviewStartTs = new Date().toISOString().slice(11, 19);
  log('GET /api/overview');
  const overview = await fetchJson<OverviewResponse>('/api/overview');
  log(
    `GET /api/overview OK (${fmtMs(overviewStart)}) [${overviewStartTs}→${new Date().toISOString().slice(11, 19)}]`,
  );
  recordTiming('/api/overview', nowMs() - overviewStart);
  let rawSettings: Record<string, unknown> = {};
  let rawProjectStandards: Record<string, unknown> = {};
  {
    const settingsStart = nowMs();
    const settingsStartTs = new Date().toISOString().slice(11, 19);
    log('GET /api/settings/raw');
    const psStart = nowMs();
    log('GET /api/project-standards/raw');
    const [settingsRes, psRes] = await Promise.allSettled([
      fetchJson<Record<string, unknown>>('/api/settings/raw'),
      fetchJson<Record<string, unknown>>('/api/project-standards/raw'),
    ]);
    if (settingsRes.status === 'fulfilled') {
      rawSettings = settingsRes.value;
      log(
        `GET /api/settings/raw OK (${fmtMs(settingsStart)}) [${settingsStartTs}→${new Date().toISOString().slice(11, 19)}]`,
      );
      recordTiming('/api/settings/raw', nowMs() - settingsStart);
    } else {
      log('GET /api/settings/raw failed, continuing with defaults', 'warn');
      rawSettings = {};
    }
    if (psRes.status === 'fulfilled') {
      rawProjectStandards = psRes.value;
      log(`GET /api/project-standards/raw OK (${fmtMs(psStart)})`);
      recordTiming('/api/project-standards/raw', nowMs() - psStart);
    } else {
      log('GET /api/project-standards/raw failed, defaulting to NONE modes', 'warn');
      rawProjectStandards = {};
    }
  }
  return { overview, rawSettings, rawProjectStandards };
}
