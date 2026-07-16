// Users → Churn page payload — shape mirrors routes/user_churn.py exactly.
// Per-account lifecycle FACTS only; every roll-up (yearly churn, seat
// reassignment, dormancy) is derived client-side in utils/userChurn.ts.

import type { AdoptionLicensing } from './adoption';

/** Which evidence backed a disabled account's end-of-life proxy. */
export type ChurnEndSource = 'activity' | 'login' | 'created';

export interface ChurnAccount {
  login: string;
  displayName: string;
  email?: string | null;
  userProfile?: string | null;
  groups: string[];
  enabled: boolean;
  sourceType?: string | null;
  creationDateMs: number | null;
  lastSuccessfulLoginMs: number | null;
  lastFailedLoginMs: number | null;
  lastSessionActivityMs: number | null;
  /** Disabled accounts only: best-available end-of-life proxy (DSS stores no
   * disable date — this is last session activity, else last login, else the
   * creation date for never-used accounts). */
  effectiveEndMs?: number | null;
  endSource?: ChurnEndSource | null;
}

export interface UserChurnData {
  ok?: boolean;
  error?: string;
  generatedAtMs?: number;
  accounts?: ChurnAccount[];
  /** Same licensing summary as the adoption payload (null when the API key
   * can't read licensing). */
  licensing?: AdoptionLicensing | null;
}
