import { createModuleScanStore } from './createModuleScanStore';
import type { AdoptionEventsData } from '../types';

// Audit-log msgType event mix — a blocking macro pass over the rotated audit
// files (cached server-side), single GET. Covers only whatever window the
// audit rotations still hold; the page stamps every card fed by this with the
// "Audit · last N days" pill, never the persistent one.
export const adoptionEventsScan = createModuleScanStore<AdoptionEventsData, never>({
  loadingField: 'adoptionEventsLoading',
  fallbackEndpoint: '/api/adoption/events',
});
