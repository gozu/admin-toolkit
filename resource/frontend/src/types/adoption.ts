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

/** DSS group activity roll-up — a builder in N groups counts toward all N. */
export interface AdoptionGroupRow {
  name: string;
  memberCount: number; // users carrying this group
  builderCount: number; // members with git activity
  commits: number; // sum of member builders' commits (shares can overlap)
  projectCount: number; // distinct projects touched by member builders
  lastCommitMs: number | null;
  /** 'YYYY-MM' → member commits that month (summed over member builders). */
  monthlyCommits: Record<string, number>;
}

/** Per-builder leaderboard row (sorted by commits desc server-side). */
export interface AdoptionBuilderRow {
  login: string;
  displayName: string;
  commits: number;
  projectCount: number;
  activeMonths: number;
  firstCommitMs: number | null;
  lastCommitMs: number | null;
}

/** Per-user recency from list_users_activity() — "last active", never a count. */
export interface AdoptionBuilderRecency {
  login: string;
  displayName: string;
  lastSuccessfulLogin: number | null;
  lastSessionActivity: number | null;
  creationDate: number | null;
  userProfile?: string | null; // seat type, for the seat-type × creators card
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

/** One licensed profile's seat cap — licensedLimit ≤ 0 means "no limit". */
export interface AdoptionLicenseProfile {
  profile: string;
  licensedLimit: number | null;
}

/** Licensed seat limits from get_licensing_status() (verified live: no usage
 * counts in that payload — usage comes from profileCounts below). Null when
 * the API key can't read licensing. */
export interface AdoptionLicensing {
  valid: boolean;
  expired: boolean;
  expiresOnMs: number | null;
  licenseKind: string | null;
  communityEdition: boolean;
  profiles: AdoptionLicenseProfile[];
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
  /** login → 'YYYY-MM' → commits — feeds the new/returning/lapsed lifecycle. */
  builderMonthly?: Record<string, Record<string, number>>;
  builderRecency?: AdoptionBuilderRecency[];
  groups?: AdoptionGroupRow[];
  builderStats?: AdoptionBuilderRow[];
  licensing?: AdoptionLicensing | null;
  /** userProfile → seat count, from the same list_users snapshot. */
  profileCounts?: Record<string, number>;
}

// ── Config-tree object inventory (macro layer) ────────────────────────────────
// Shape mirrors python-runnables/adoption-inventory/runnable.py exactly, which
// is itself a port of the diag-parser inventory accumulator. Covers the
// instance's full multi-year history — but only for objects that still exist
// (survivorship bias; deleted work is invisible).

export type ObjectFamily =
  | 'dataset'
  | 'recipe-visual'
  | 'recipe-python'
  | 'recipe-sql'
  | 'recipe-r'
  | 'recipe-ml'
  | 'recipe-plugin'
  | 'recipe-other'
  | 'notebook'
  | 'sql-notebook'
  | 'webapp'
  | 'dashboard'
  | 'insight'
  | 'saved-model'
  | 'scenario'
  | 'prompt-studio'
  | 'wiki'
  | 'zone'
  | 'analysis'
  | 'mes'
  | 'other';

/** Save-count histogram from versionTag.versionNumber (0 = never re-saved
 * after creation). Buckets are inclusive: v1 ≤ 1 save, v2to5 = 2–5 saves, … */
export interface InventoryEditBuckets {
  v1: number;
  v2to5: number;
  v6to20: number;
  v21plus: number;
}

export interface InventoryFamilyStats {
  count: number;
  /** Objects carrying a usable creationTag (login + timestamp). Coverage is
   * surfaced as "tagged N of M" — older/programmatic objects may lack tags. */
  tagged: number;
  /** Raw object subtype counts (recipe `type`, webapp BOKEH/DASH, …). */
  subtypes: Record<string, number>;
  /** Sum of versionTag.versionNumber across tagged objects (save volume). */
  versionSum: number;
  editBuckets: InventoryEditBuckets;
}

/** One 'YYYY-MM' creation bucket. `creators` maps login → objects created that
 * month; views derive the distinct-creator count. */
export interface InventoryCreationMonth {
  month: string;
  total: number;
  byFamily: Partial<Record<ObjectFamily, number>>;
  creators: Record<string, number>;
}

export interface InventoryCreatorStats {
  /** Objects whose creationTag.lastModifiedBy is this login. */
  created: number;
  byFamily: Partial<Record<ObjectFamily, number>>;
  firstCreatedMs: number | null;
  lastCreatedMs: number | null;
  /** Latest creation or last-save timestamp attributed to this login. */
  lastEditMs: number | null;
  /** Objects this login last saved (versionTag) but did not create. */
  editedNotCreated: number;
  /** Objects whose most recent save (versionTag) was by this login. */
  saves: number;
}

export interface InventoryProjectStats {
  objectCount: number;
  byFamily: Partial<Record<ObjectFamily, number>>;
  /** login → objects created in this project. */
  creators: Record<string, number>;
  /** Objects whose last editor differs from their creator (both tagged). */
  handoffCount: number;
  /** Tagged objects never re-saved after creation (versionNumber ≤ 0). */
  savedOnce: number;
  versionSum: number;
  lastHumanEditMs: number | null;
  lastEditor: string | null;
  /** 'YYYY-MM' → objects whose last edit falls in that month. Staleness is
   * NOT pre-collapsed — views bucket this against `lastEditMs` at render
   * time so thresholds stay tunable without re-scanning. */
  lastEditMonthCounts: Record<string, number>;
}

export interface ObjectInventory {
  families: Partial<Record<ObjectFamily, InventoryFamilyStats>>;
  creationMonths: InventoryCreationMonth[];
  creators: Record<string, InventoryCreatorStats>;
  projects: Record<string, InventoryProjectStats>;
  /** Earliest surviving creationTag timestamp (TTFB cohort floor). */
  firstCreationMs: number | null;
  /** Latest edit anywhere — the reference "now" for staleness collapse. */
  lastEditMs: number | null;
  /** Objects ingested (config JSONs + meta-only paths). */
  scanned: number;
  /** Objects with a usable creationTag. */
  taggedObjects: number;
  errors: number;
  /** Always true from the macro (single blocking pass). */
  complete: boolean;
}

/** /api/adoption/inventory payload: the inventory plus the macro envelope. */
export interface AdoptionInventoryData extends Partial<ObjectInventory> {
  ok?: boolean;
  error?: string;
  generatedAtMs?: number;
  projectCount?: number;
}

// ── Audit-tail recent-activity pulse (macro layer, mode=recent) ───────────────
// Shape mirrors python-runnables/adoption-events/runnable.py `_parse_recent`.
// Window-honesty: firstEventMs/lastEventMs are the MEASURED span — rotated
// audit files often cover far less than the requested window.

export interface AdoptionPulseHour {
  hourMs: number; // epoch ms, floored to the hour
  events: number; // human events that hour
  humans: number; // distinct humans that hour
}

export interface AdoptionPulseData {
  ok?: boolean;
  error?: string;
  mode?: string;
  generatedAtMs?: number;
  windowHours?: number; // requested window
  firstEventMs?: number | null; // measured span start
  lastEventMs?: number | null;
  coverageHours?: number | null;
  hours?: AdoptionPulseHour[];
  /** build / run / explore / consume / other totals (classified server-side). */
  buckets?: Record<string, number>;
  /** Run-bucket msgType counts (jobs, scenarios, macros…), capped server-side. */
  runTypes?: Record<string, number>;
  topHumans?: Array<{ login: string; events: number }>;
  humansActive?: number;
  /** True when the rotation set ran out before reaching the requested window. */
  exhaustedFiles?: boolean;
  filesRead?: number;
  linesScanned?: number;
}
