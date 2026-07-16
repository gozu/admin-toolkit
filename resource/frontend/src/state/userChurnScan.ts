import { createModuleScanStore } from './createModuleScanStore';
import type { UserChurnData } from '../types';

// Users → Churn: one cached GET of per-account lifecycle facts (no SSE, no
// macro — pure DSS API on the backend). All analytics derive client-side in
// utils/userChurn.ts so threshold toggles never refetch.
export const userChurnScan = createModuleScanStore<UserChurnData, never>({
  loadingField: 'userChurnLoading',
  fallbackEndpoint: '/api/users/churn',
});
