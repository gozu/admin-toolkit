import { createModuleScanStore } from './createModuleScanStore';
import type { AdoptionPulseData } from '../types';

// Recent-activity pulse — a cheap reverse tail-scan of the newest audit files
// (macro mode=recent, ~60s server cache), single GET. Covers the last ~72h or
// however far the rotated files actually reach; the card shows the MEASURED
// window, never the requested one.
export const adoptionEventsScan = createModuleScanStore<AdoptionPulseData, never>({
  loadingField: 'adoptionEventsLoading',
  fallbackEndpoint: '/api/adoption/events',
});
