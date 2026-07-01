import { createModuleScanStore } from './createModuleScanStore';
import type { AdoptionData } from '../types';

// v1 (MVP-A): persistent spine only — a single cached GET, no SSE, no macro.
// The recent-window audit layer (v1.1) adds a /api/adoption/stream endpoint and
// switches this to the streaming reduce pattern (see projectCostScan).
export const adoptionScan = createModuleScanStore<AdoptionData, never>({
  loadingField: 'adoptionLoading',
  fallbackEndpoint: '/api/adoption',
});
