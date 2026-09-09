import { createModuleScanStore } from './createModuleScanStore';
import type { LlmOption } from '../types';

const scan = createModuleScanStore<{ llms: LlmOption[]; error?: string }, never>({
  loadingField: 'reportLoading', fallbackEndpoint: '/api/llms', timeoutMs: 20_000,
});
const EMPTY_LLMS: LlmOption[] = [];
const state = (s: ReturnType<typeof scan.store.get>) => ({
  llms: s.data?.llms ?? EMPTY_LLMS, loading: s.loading, error: s.error || s.data?.error || null,
  loaded: !!s.finishedAt, startedAt: s.startedAt, finishedAt: s.finishedAt,
});
export const reportLlmsStore = {
  use: () => state(scan.use()),
  get: () => state(scan.store.get()),
  load: scan.load,
};
