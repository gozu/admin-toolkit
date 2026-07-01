// ── Adoption / engagement analytics (persistent spine, v1) ────────────────────
// Shape mirrors python-lib/adk_backend/routes/adoption.py JSON output exactly.
// Every field here is PERSISTENT (spans each project's whole git history / the
// user snapshot), never a short audit window — the UI frames it as such.

/** One month of the persistent adoption curve. */
export interface AdoptionMonthPoint {
  month: string; // 'YYYY-MM'
  activeBuilders: number; // distinct human git authors active that month
  commits: number; // human commit volume that month
}

/** Per-project people & activity, from distinct git-commit authors. */
export interface AdoptionProjectRow {
  projectKey: string;
  name: string;
  owner: string;
  authors: string[]; // distinct human logins (== DSS login)
  authorCount: number; // people per project (#6)
  commits: number; // human commits
  activeMonths: number; // distinct months with human activity
  firstCommitMs: number | null;
  lastCommitMs: number | null; // newest non-migration commit (matches Inactive-Projects)
  active: boolean; // last activity within the inactive-project threshold
  truncated: boolean; // history deeper than the paginated fetch cap — counts are floors
}

/** Onboarding cohort: users created in a given month. */
export interface AdoptionCohort {
  month: string; // 'YYYY-MM'
  newUsers: number;
}

/** Returning-builder split: authors active in exactly one vs. multiple months. */
export interface AdoptionRepeatBuilders {
  total: number;
  single: number; // active in exactly 1 distinct month
  repeat: number; // active in >= 2 distinct months
}

/** Per-user recency from list_users_activity() — "last active", never a count. */
export interface AdoptionBuilderRecency {
  login: string;
  displayName: string;
  lastSuccessfulLogin: number | null;
  lastSessionActivity: number | null;
  creationDate: number | null;
}

export interface AdoptionTotals {
  projectCount: number;
  activeProjectCount: number; // active vs total projects (#5)
  builderCount: number; // distinct human authors across all projects
  automationCount: number; // distinct api:/no:auth identities (excluded from people)
  commitCount: number;
  firstCommitMs: number | null;
  lastCommitMs: number | null;
  avgPeoplePerProject: number;
  inactiveThresholdDays: number;
  truncatedProjectCount: number; // projects whose history exceeded the fetch cap
}

export interface AdoptionData {
  ok?: boolean;
  error?: string;
  generatedAtMs?: number;
  totals?: AdoptionTotals;
  monthlyTrend?: AdoptionMonthPoint[];
  projectRows?: AdoptionProjectRow[];
  cohorts?: AdoptionCohort[];
  repeatBuilders?: AdoptionRepeatBuilders;
  builderRecency?: AdoptionBuilderRecency[];
}
