import type {
  AdoptionBuilderRecency,
  AdoptionInventoryData,
  ObjectFamily,
  ObjectInventory,
} from '../types';

// View-model builder for the config-tree object inventory (Adoption page).
// Ported from the diag-parser plugin's inventoryData.ts — pure derivation over
// the /api/adoption/inventory payload; every metric here maps to an aggregate
// the macro already collected (see types/adoption.ts), nothing re-scans.
// Inputs that came from ParsedData there (users.json, user activity) come from
// the /api/adoption builderRecency rows here — one array feeds TTFB, seat
// types and dormant creators.

const DAY_MS = 24 * 60 * 60 * 1000;
// Zero-fill cap: a span wider than this is clock-skew garbage — fall back to
// the sparse month list instead of fabricating a 50-year axis.
const FILL_MAX_SPAN_BUCKETS = 600;

/** Maturity ladder (Power BI / Fabric adoption-roadmap flavored): one point
 * per practice dimension present in a project's surviving objects. */
export const MATURITY_DIMENSIONS: Array<{ label: string; families: ObjectFamily[] }> = [
  { label: 'Automation (scenarios)', families: ['scenario'] },
  { label: 'Documentation (wiki)', families: ['wiki'] },
  { label: 'Flow organization (zones)', families: ['zone'] },
  {
    label: 'Code',
    families: ['recipe-python', 'recipe-r', 'recipe-sql', 'notebook', 'sql-notebook'],
  },
  { label: 'ML', families: ['recipe-ml', 'saved-model', 'mes', 'analysis'] },
  { label: 'GenAI (prompt studios)', families: ['prompt-studio'] },
];

const RECIPE_FAMILIES: ObjectFamily[] = [
  'recipe-visual',
  'recipe-python',
  'recipe-sql',
  'recipe-r',
  'recipe-ml',
  'recipe-plugin',
  'recipe-other',
];

export const FAMILY_LABELS: Record<ObjectFamily, string> = {
  dataset: 'Datasets',
  'recipe-visual': 'Visual recipes',
  'recipe-python': 'Python recipes',
  'recipe-sql': 'SQL recipes',
  'recipe-r': 'R recipes',
  'recipe-ml': 'ML recipes',
  'recipe-plugin': 'Plugin recipes',
  'recipe-other': 'Other recipes',
  notebook: 'Notebooks',
  'sql-notebook': 'SQL notebooks',
  webapp: 'Webapps',
  dashboard: 'Dashboards',
  insight: 'Insights',
  'saved-model': 'Saved models',
  scenario: 'Scenarios',
  'prompt-studio': 'Prompt studios',
  wiki: 'Wiki articles',
  zone: 'Flow zones',
  analysis: 'Visual analyses',
  mes: 'Model eval stores',
  other: 'Other objects',
};

// Display groups for the stacked creation trend — at most 6 series so the
// categorical palette holds. Families that never carry creationTags
// (notebooks, wiki, zones, …) can't appear in creationMonths anyway; they are
// still listed so the grouping is total.
export interface TrendGroupDef {
  key: string;
  label: string;
  families: ObjectFamily[];
}

export const TREND_GROUPS: TrendGroupDef[] = [
  { key: 'datasets', label: 'Datasets', families: ['dataset'] },
  { key: 'visual', label: 'Visual recipes', families: ['recipe-visual'] },
  {
    key: 'code',
    label: 'Code & ML recipes',
    families: [
      'recipe-python',
      'recipe-sql',
      'recipe-r',
      'recipe-ml',
      'recipe-plugin',
      'recipe-other',
    ],
  },
  { key: 'bi', label: 'Dashboards & insights', families: ['dashboard', 'insight'] },
  { key: 'genai', label: 'Webapps & GenAI', families: ['webapp', 'prompt-studio'] },
  {
    key: 'other',
    label: 'Other',
    families: [
      'saved-model',
      'mes',
      'scenario',
      'analysis',
      'wiki',
      'zone',
      'notebook',
      'sql-notebook',
      'other',
    ],
  },
];

// THE family-group palette — one fixed color per TREND_GROUPS slot, used
// identically everywhere a family group appears (trend chart, composition,
// grid segment bars, persona mixes). "Other" is deliberately grey: red stays
// reserved for warning states, and the catch-all bucket should recede.
export const TREND_GROUP_COLORS = [
  'var(--viz-cat-1)', // Datasets — blue
  'var(--viz-cat-2)', // Visual recipes — aqua
  'var(--viz-cat-3)', // Code & ML recipes — yellow
  'var(--viz-cat-4)', // Dashboards & insights — green
  'var(--viz-cat-5)', // Webapps & GenAI — violet
  'var(--text-tertiary)', // Other — grey (recedes; red = warnings only)
];

const FAMILY_GROUP_INDEX = new Map<ObjectFamily, number>();
TREND_GROUPS.forEach((g, gi) => g.families.forEach((f) => FAMILY_GROUP_INDEX.set(f, gi)));

/** TREND_GROUPS slot a family belongs to — colors resolve through this so a
 * family is never colored by sort rank. */
export function familyGroupIndex(family: ObjectFamily): number {
  return FAMILY_GROUP_INDEX.get(family) ?? TREND_GROUPS.length - 1;
}

export interface InventoryCompositionRow {
  family: ObjectFamily;
  label: string;
  count: number;
  tagged: number;
  /** Top subtypes (desc) for the tooltip, e.g. recipe types or webapp kinds. */
  topSubtypes: Array<{ subtype: string; count: number }>;
}

export interface InventoryTrendPoint {
  month: string;
  total: number;
  distinctCreators: number;
  /** Stacked series values aligned with TREND_GROUPS order. */
  groups: number[];
}

export interface InventoryTtfbCohort {
  quarter: string; // account-creation cohort ('YYYY-Qn')
  cohortUsers: number; // accounts created that quarter (with creationDate)
  builders: number; // of those, users with at least one surviving created object
  medianDays: number | null;
}

export interface InventoryTtfb {
  overallMedianDays: number | null;
  usersMeasured: number;
  cohorts: InventoryTtfbCohort[];
  /** Cohorts predating the surviving-object history — excluded (their "first
   * build" may simply have been deleted since). */
  excludedCohorts: number;
}

// ── Derived analytics (view-time collapse of the macro accumulator) ─────────

/** Knowledge-concentration split: projects by distinct-creator count. */
export interface InventoryBusFactor {
  singleCreator: number;
  twoToThree: number;
  fourPlus: number;
  measuredProjects: number;
}

export interface InventorySeatTypeRow {
  profile: string; // userProfile, or 'unknown profile' for logins not in the user snapshot
  users: number; // accounts with this profile (0 for 'unknown profile')
  creators: number; // of those, logins with ≥1 surviving created object
}

export interface InventoryProjectViewRow {
  projectKey: string;
  objectCount: number;
  creatorCount: number;
  topCreator: string | null;
  topCreatorShare: number; // 0–1 of created objects attributed to topCreator
  lastEditMs: number | null;
  lastEditor: string | null;
  maturityScore: number; // 0–6, see MATURITY_DIMENSIONS
  /** notebooks per recipe — per PROJECT only (.ipynb carries no creator). */
  notebookRecipeRatio: number | null;
  /** Stacked family-group values aligned with TREND_GROUPS. */
  groups: number[];
  creators: Record<string, number>;
}

/** Top creators for one TREND_GROUPS slot (per-family builder leaderboards). */
export interface InventoryGroupCreators {
  key: string; // TREND_GROUPS key
  creators: Array<{ login: string; created: number }>;
}

export interface InventoryView {
  inventory: ObjectInventory;
  objectsBuilt: number;
  taggedObjects: number;
  allTimeCreators: number;
  complete: boolean;
  composition: InventoryCompositionRow[];
  trendPoints: InventoryTrendPoint[];
  ttfb: InventoryTtfb;
  busFactor: InventoryBusFactor;
  seatTypes: InventorySeatTypeRow[];
  /** Aligned with TREND_GROUPS — top creators per family group. */
  topCreatorsByGroup: InventoryGroupCreators[];
  projectRows: InventoryProjectViewRow[];
}

/** Narrow the /api/adoption/inventory envelope to a usable ObjectInventory, or
 * null while it is absent/errored/empty. */
export function asObjectInventory(data: AdoptionInventoryData | null): ObjectInventory | null {
  if (!data || data.ok === false) return null;
  if (!data.families || !data.creationMonths || !data.creators || !data.projects) return null;
  if (!data.scanned) return null;
  return {
    families: data.families,
    creationMonths: data.creationMonths,
    creators: data.creators,
    projects: data.projects,
    firstCreationMs: data.firstCreationMs ?? null,
    lastEditMs: data.lastEditMs ?? null,
    scanned: data.scanned,
    taggedObjects: data.taggedObjects ?? 0,
    errors: data.errors ?? 0,
    complete: data.complete ?? true,
  };
}

export function monthKeyUTC(ms: number): string {
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}

// ── Quarter buckets ('YYYY-Qn') — onboarding runs on trimesters, not months ──

export function quarterKeyUTC(ms: number): string {
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-Q${Math.floor(d.getUTCMonth() / 3) + 1}`;
}

/** 'YYYY-MM' → 'YYYY-Qn'. String compare stays chronological for both forms. */
export function monthToQuarter(month: string): string {
  const m = Number(month.slice(5, 7));
  return `${month.slice(0, 4)}-Q${Math.floor((m - 1) / 3) + 1}`;
}

/** 'YYYY-Qn' → "Q3 '26". */
export function quarterLabel(q: string): string {
  return `Q${q.slice(6)} '${q.slice(2, 4)}`;
}

/** Zero-filled 'YYYY-Qn' axis between the first and last observed quarters. */
export function fillQuarterRange(quarterKeys: string[]): string[] {
  if (quarterKeys.length === 0) return [];
  const sorted = [...quarterKeys].sort();
  const first = sorted[0];
  const last = sorted[sorted.length - 1];
  let y = Number(first.slice(0, 4));
  let q = Number(first.slice(6));
  const ly = Number(last.slice(0, 4));
  const lq = Number(last.slice(6));
  const span = (ly - y) * 4 + (lq - q) + 1;
  if (span < 1 || span > FILL_MAX_SPAN_BUCKETS) return sorted;
  const out: string[] = [];
  while (y < ly || (y === ly && q <= lq)) {
    out.push(`${y}-Q${q}`);
    q++;
    if (q > 4) {
      y++;
      q = 1;
    }
  }
  return out;
}

/** Drop trailing in-progress-month points: a 10-day July plotted next to full
 * months always reads as a collapse. Keyed by month — never `slice(0, -1)`,
 * which throws away a COMPLETE month whenever the current one has no data. */
export function completeMonthsOnly<T extends { month: string }>(points: T[], nowMs: number): T[] {
  if (!Number.isFinite(nowMs) || nowMs <= 0) return points;
  const currentKey = monthKeyUTC(nowMs);
  return points.filter((p) => p.month < currentKey);
}

/** Zero-filled 'YYYY-MM' axis between the first and last observed months. */
export function fillMonthRange(monthKeys: string[]): string[] {
  if (monthKeys.length === 0) return [];
  const sorted = [...monthKeys].sort();
  const first = sorted[0];
  const last = sorted[sorted.length - 1];
  let y = Number(first.slice(0, 4));
  let m = Number(first.slice(5, 7));
  const ly = Number(last.slice(0, 4));
  const lm = Number(last.slice(5, 7));
  const span = (ly - y) * 12 + (lm - m) + 1;
  if (span < 1 || span > FILL_MAX_SPAN_BUCKETS) return sorted;

  const out: string[] = [];
  while (y < ly || (y === ly && m <= lm)) {
    out.push(`${y}-${String(m).padStart(2, '0')}`);
    m++;
    if (m > 12) {
      y++;
      m = 1;
    }
  }
  return out;
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

export function sumFamilies(
  byFamily: Partial<Record<ObjectFamily, number>>,
  families: ObjectFamily[],
): number {
  let total = 0;
  for (const f of families) total += byFamily[f] ?? 0;
  return total;
}

function buildTtfb(inventory: ObjectInventory, users: AdoptionBuilderRecency[]): InventoryTtfb {
  const floorQuarter =
    inventory.firstCreationMs !== null ? quarterKeyUTC(inventory.firstCreationMs) : null;
  const cohortMap = new Map<string, { cohortUsers: number; days: number[] }>();
  let excludedCohorts = 0;
  const allDays: number[] = [];

  for (const user of users) {
    if (typeof user.creationDate !== 'number' || !Number.isFinite(user.creationDate)) continue;
    const cohortQuarter = quarterKeyUTC(user.creationDate);
    // Cohorts predating the surviving-object history can't be measured
    // honestly — their first build may have been deleted since.
    if (floorQuarter === null || cohortQuarter < floorQuarter) continue;
    let cohort = cohortMap.get(cohortQuarter);
    if (!cohort) {
      cohort = { cohortUsers: 0, days: [] };
      cohortMap.set(cohortQuarter, cohort);
    }
    cohort.cohortUsers++;
    const firstCreatedMs = inventory.creators[user.login]?.firstCreatedMs;
    if (firstCreatedMs != null) {
      const days = Math.max(0, (firstCreatedMs - user.creationDate) / DAY_MS);
      cohort.days.push(days);
      allDays.push(days);
    }
  }

  if (floorQuarter !== null) {
    const seen = new Set<string>();
    for (const user of users) {
      if (typeof user.creationDate !== 'number' || !Number.isFinite(user.creationDate)) continue;
      const cohortQuarter = quarterKeyUTC(user.creationDate);
      if (cohortQuarter < floorQuarter) seen.add(cohortQuarter);
    }
    excludedCohorts = seen.size;
  }

  const round1 = (v: number | null) => (v === null ? null : Math.round(v * 10) / 10);
  const cohorts: InventoryTtfbCohort[] = [...cohortMap.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([quarter, c]) => ({
      quarter,
      cohortUsers: c.cohortUsers,
      builders: c.days.length,
      medianDays: round1(median(c.days)),
    }));

  return {
    overallMedianDays: round1(median(allDays)),
    usersMeasured: allDays.length,
    cohorts,
    excludedCohorts,
  };
}

interface BusFactorAndProjects {
  busFactor: InventoryBusFactor;
  projectRows: InventoryProjectViewRow[];
}

function collapseProjects(inventory: ObjectInventory): BusFactorAndProjects {
  const busFactor: InventoryBusFactor = {
    singleCreator: 0,
    twoToThree: 0,
    fourPlus: 0,
    measuredProjects: 0,
  };
  const projectRows: InventoryProjectViewRow[] = [];

  for (const [projectKey, p] of Object.entries(inventory.projects)) {
    const creatorEntries = Object.entries(p.creators);
    let topCreator: string | null = null;
    let topCreated = 0;
    let createdTotal = 0;
    for (const [login, count] of creatorEntries) {
      createdTotal += count;
      if (count > topCreated) {
        topCreated = count;
        topCreator = login;
      }
    }
    if (creatorEntries.length > 0) {
      busFactor.measuredProjects++;
      if (creatorEntries.length === 1) busFactor.singleCreator++;
      else if (creatorEntries.length <= 3) busFactor.twoToThree++;
      else busFactor.fourPlus++;
    }

    const maturityScore = MATURITY_DIMENSIONS.filter(
      (d) => sumFamilies(p.byFamily, d.families) > 0,
    ).length;

    const notebooks = sumFamilies(p.byFamily, ['notebook', 'sql-notebook']);
    const recipes = sumFamilies(p.byFamily, RECIPE_FAMILIES);

    projectRows.push({
      projectKey,
      objectCount: p.objectCount,
      creatorCount: creatorEntries.length,
      topCreator,
      topCreatorShare: createdTotal > 0 ? topCreated / createdTotal : 0,
      lastEditMs: p.lastHumanEditMs,
      lastEditor: p.lastEditor,
      maturityScore,
      notebookRecipeRatio: recipes > 0 ? notebooks / recipes : null,
      groups: TREND_GROUPS.map((g) => sumFamilies(p.byFamily, g.families)),
      creators: p.creators,
    });
  }
  projectRows.sort(
    (a, b) => b.objectCount - a.objectCount || a.projectKey.localeCompare(b.projectKey),
  );

  return { busFactor, projectRows };
}

function buildSeatTypes(
  inventory: ObjectInventory,
  users: AdoptionBuilderRecency[],
): InventorySeatTypeRow[] {
  const byProfile = new Map<string, { users: number; creators: number }>();
  const profileByLogin = new Map<string, string>();
  for (const user of users) {
    const profile = user.userProfile || '(no profile)';
    profileByLogin.set(user.login, profile);
    let row = byProfile.get(profile);
    if (!row) {
      row = { users: 0, creators: 0 };
      byProfile.set(profile, row);
    }
    row.users++;
  }
  for (const [login, stats] of Object.entries(inventory.creators)) {
    if (stats.created <= 0) continue;
    // Creators missing from the user snapshot (deleted accounts) get their own bucket.
    const profile = profileByLogin.get(login) ?? 'unknown profile';
    let row = byProfile.get(profile);
    if (!row) {
      row = { users: 0, creators: 0 };
      byProfile.set(profile, row);
    }
    row.creators++;
  }
  return [...byProfile.entries()]
    .map(([profile, row]) => ({ profile, users: row.users, creators: row.creators }))
    .sort((a, b) => b.users - a.users || b.creators - a.creators);
}

export function buildInventoryView(
  data: AdoptionInventoryData | null,
  recency: AdoptionBuilderRecency[],
): InventoryView | null {
  const inventory = asObjectInventory(data);
  if (!inventory) return null;

  const composition: InventoryCompositionRow[] = (
    Object.entries(inventory.families) as Array<
      [ObjectFamily, NonNullable<ObjectInventory['families'][ObjectFamily]>]
    >
  )
    .map(([family, stats]) => ({
      family,
      label: FAMILY_LABELS[family],
      count: stats.count,
      tagged: stats.tagged,
      topSubtypes: Object.entries(stats.subtypes)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 6)
        .map(([subtype, count]) => ({ subtype, count })),
    }))
    .filter((row) => row.count > 0)
    .sort((a, b) => b.count - a.count);

  // Zero-filled monthly creation trend (fillMonthRange caps implausible spans).
  const byMonth = new Map(inventory.creationMonths.map((m) => [m.month, m]));
  const filled = fillMonthRange(inventory.creationMonths.map((m) => m.month));
  const trendPoints: InventoryTrendPoint[] = filled.map((month) => {
    const m = byMonth.get(month);
    return {
      month,
      total: m?.total ?? 0,
      distinctCreators: m ? Object.keys(m.creators).length : 0,
      groups: TREND_GROUPS.map((g) => (m ? sumFamilies(m.byFamily, g.families) : 0)),
    };
  });
  const { busFactor, projectRows } = collapseProjects(inventory);

  // Per-family-group builder leaderboards — one ranked list per TREND_GROUPS
  // slot, from each creator's byFamily counts.
  const topCreatorsByGroup: InventoryGroupCreators[] = TREND_GROUPS.map((group) => ({
    key: group.key,
    creators: Object.entries(inventory.creators)
      .map(([login, stats]) => ({ login, created: sumFamilies(stats.byFamily, group.families) }))
      .filter((c) => c.created > 0)
      .sort((a, b) => b.created - a.created || a.login.localeCompare(b.login))
      .slice(0, 5),
  }));

  return {
    inventory,
    objectsBuilt: inventory.scanned,
    taggedObjects: inventory.taggedObjects,
    allTimeCreators: Object.values(inventory.creators).filter((c) => c.created > 0).length,
    complete: inventory.complete,
    composition,
    trendPoints,
    ttfb: buildTtfb(inventory, recency),
    busFactor,
    seatTypes: buildSeatTypes(inventory, recency),
    topCreatorsByGroup,
    projectRows,
  };
}
