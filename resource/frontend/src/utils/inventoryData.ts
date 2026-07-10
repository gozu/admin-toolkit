import type {
  AdoptionBuilderRecency,
  AdoptionInventoryData,
  AdoptionMonthPoint,
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
const MIN_PERSONA_OBJECTS = 5;
// Zero-fill cap: a span wider than this is clock-skew garbage — fall back to
// the sparse month list instead of fabricating a 50-year axis.
const FILL_MAX_SPAN_BUCKETS = 600;
// Staleness thresholds (Tableau-style stale-content lens): fresh ≤ 3 months,
// stale > 12 months, aging in between. Collapsed at view time vs the
// inventory's own lastEditMs, never the wall clock.
const FRESH_MONTHS = 3;
const STALE_MONTHS = 12;
const DORMANT_THRESHOLD_DAYS = 90;

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
    families: ['recipe-python', 'recipe-sql', 'recipe-r', 'recipe-ml', 'recipe-plugin', 'recipe-other'],
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

export type BuilderPersona =
  | 'SQL analyst'
  | 'Python DS'
  | 'Dashboard author'
  | 'GenAI builder'
  | 'Visual flow builder'
  | 'Generalist';

export interface InventoryPersonaRow {
  login: string;
  persona: BuilderPersona | null; // null below the MIN_PERSONA_OBJECTS floor
  created: number;
  byFamily: Partial<Record<ObjectFamily, number>>;
  /** Human-readable share summary for the tooltip. */
  shareSummary: string;
}

export interface InventoryTtfbCohort {
  month: string; // account-creation cohort ('YYYY-MM')
  cohortUsers: number; // accounts created that month (with creationDate)
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

/** Objects bucketed by age of their last edit vs the inventory's own "now"
 * (lastEditMs). Collapsed at view time so thresholds stay tunable. */
export interface InventoryStaleness {
  freshCount: number; // last edit ≤ 3 months ago
  agingCount: number; // 3–12 months
  staleCount: number; // > 12 months
  unknownCount: number; // objects without any usable edit timestamp
  /** Projects whose newest config edit is > 12 months old (zombies). */
  zombieProjects: number;
  measuredProjects: number;
}

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

export interface InventoryDormantCreator {
  login: string;
  created: number;
  lastEditMs: number | null; // last config edit attributed to this login
  lastSessionMs: number | null; // from list_users_activity, if present
  inUserSnapshot: boolean; // false → account likely deleted
}

export interface InventoryEditIntensity {
  editBuckets: { v1: number; v2to5: number; v6to20: number; v21plus: number };
  savedOnce: number;
  versionedObjects: number; // objects with a usable versionNumber
}

export interface InventoryProjectViewRow {
  projectKey: string;
  objectCount: number;
  creatorCount: number;
  topCreator: string | null;
  topCreatorShare: number; // 0–1 of created objects attributed to topCreator
  lastEditMs: number | null;
  lastEditor: string | null;
  stalePct: number; // % of dated objects last edited > 12 months ago
  maturityScore: number; // 0–6, see MATURITY_DIMENSIONS
  /** notebooks per recipe — per PROJECT only (.ipynb carries no creator). */
  notebookRecipeRatio: number | null;
  /** Stacked family-group values aligned with TREND_GROUPS. */
  groups: number[];
  creators: Record<string, number>;
}

export interface InventoryView {
  inventory: ObjectInventory;
  objectsBuilt: number;
  taggedObjects: number;
  allTimeCreators: number;
  complete: boolean;
  composition: InventoryCompositionRow[];
  trendPoints: InventoryTrendPoint[];
  /** Same trend re-shaped for the existing ActivityHeatGrid (commits = objects
   * created, activeBuilders = distinct creators that month). */
  heatPoints: AdoptionMonthPoint[];
  personas: Record<string, InventoryPersonaRow>;
  ttfb: InventoryTtfb;
  staleness: InventoryStaleness;
  busFactor: InventoryBusFactor;
  /** Histogram: index = maturity score 0–6, value = project count. */
  maturityHistogram: number[];
  seatTypes: InventorySeatTypeRow[];
  dormantCreators: InventoryDormantCreator[];
  dormantThresholdDays: number;
  editIntensity: InventoryEditIntensity;
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

function monthKeyUTC(ms: number): string {
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}

/** Zero-filled 'YYYY-MM' axis between the first and last observed months. */
function fillMonthRange(monthKeys: string[]): string[] {
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

function classifyPersona(
  byFamily: Partial<Record<ObjectFamily, number>>,
  created: number,
): { persona: BuilderPersona; shareSummary: string } {
  const share = (families: ObjectFamily[]) => sumFamilies(byFamily, families) / created;
  const sql = share(['recipe-sql', 'sql-notebook']);
  const py = share(['recipe-python', 'recipe-ml', 'recipe-r']);
  const dash = share(['dashboard', 'insight']);
  const genai = share(['prompt-studio']);
  const visual = share(['recipe-visual', 'dataset']);
  const pct = (v: number) => `${Math.round(v * 100)}%`;
  const shareSummary = `SQL ${pct(sql)} · Python/ML ${pct(py)} · dashboards ${pct(dash)} · GenAI ${pct(genai)} · visual flow ${pct(visual)}`;

  // First-match rules. "Automation engineer" is deferred — scenarios carry no
  // creator tags.
  let persona: BuilderPersona = 'Generalist';
  if (sql >= 0.5) persona = 'SQL analyst';
  else if (py >= 0.5) persona = 'Python DS';
  else if (dash >= 0.5) persona = 'Dashboard author';
  else if (genai >= 0.3) persona = 'GenAI builder';
  else if (visual >= 0.6) persona = 'Visual flow builder';
  return { persona, shareSummary };
}

function buildTtfb(inventory: ObjectInventory, users: AdoptionBuilderRecency[]): InventoryTtfb {
  const floorMonth = inventory.firstCreationMs !== null ? monthKeyUTC(inventory.firstCreationMs) : null;
  const cohortMap = new Map<string, { cohortUsers: number; days: number[] }>();
  let excludedCohorts = 0;
  const allDays: number[] = [];

  for (const user of users) {
    if (typeof user.creationDate !== 'number' || !Number.isFinite(user.creationDate)) continue;
    const cohortMonth = monthKeyUTC(user.creationDate);
    // Cohorts predating the surviving-object history can't be measured
    // honestly — their first build may have been deleted since.
    if (floorMonth === null || cohortMonth < floorMonth) continue;
    let cohort = cohortMap.get(cohortMonth);
    if (!cohort) {
      cohort = { cohortUsers: 0, days: [] };
      cohortMap.set(cohortMonth, cohort);
    }
    cohort.cohortUsers++;
    const firstCreatedMs = inventory.creators[user.login]?.firstCreatedMs;
    if (firstCreatedMs != null) {
      const days = Math.max(0, (firstCreatedMs - user.creationDate) / DAY_MS);
      cohort.days.push(days);
      allDays.push(days);
    }
  }

  if (floorMonth !== null) {
    const seen = new Set<string>();
    for (const user of users) {
      if (typeof user.creationDate !== 'number' || !Number.isFinite(user.creationDate)) continue;
      const cohortMonth = monthKeyUTC(user.creationDate);
      if (cohortMonth < floorMonth) seen.add(cohortMonth);
    }
    excludedCohorts = seen.size;
  }

  const round1 = (v: number | null) => (v === null ? null : Math.round(v * 10) / 10);
  const cohorts: InventoryTtfbCohort[] = [...cohortMap.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([month, c]) => ({
      month,
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

/** Whole months between a 'YYYY-MM' key and a reference 'YYYY-MM' key. */
function monthsBefore(month: string, refMonth: string): number {
  const y = Number(month.slice(0, 4));
  const m = Number(month.slice(5, 7));
  const ry = Number(refMonth.slice(0, 4));
  const rm = Number(refMonth.slice(5, 7));
  return (ry - y) * 12 + (rm - m);
}

interface StalenessAndProjects {
  staleness: InventoryStaleness;
  busFactor: InventoryBusFactor;
  maturityHistogram: number[];
  projectRows: InventoryProjectViewRow[];
}

function collapseProjects(inventory: ObjectInventory, refMonth: string): StalenessAndProjects {
  let freshCount = 0;
  let agingCount = 0;
  let staleCount = 0;
  let zombieProjects = 0;
  let measuredProjects = 0;
  const busFactor: InventoryBusFactor = {
    singleCreator: 0,
    twoToThree: 0,
    fourPlus: 0,
    measuredProjects: 0,
  };
  const maturityHistogram = new Array<number>(MATURITY_DIMENSIONS.length + 1).fill(0);
  const projectRows: InventoryProjectViewRow[] = [];

  for (const [projectKey, p] of Object.entries(inventory.projects)) {
    let fresh = 0;
    let aging = 0;
    let stale = 0;
    for (const [month, count] of Object.entries(p.lastEditMonthCounts)) {
      const age = monthsBefore(month, refMonth);
      if (age <= FRESH_MONTHS) fresh += count;
      else if (age <= STALE_MONTHS) aging += count;
      else stale += count;
    }
    freshCount += fresh;
    agingCount += aging;
    staleCount += stale;
    const dated = fresh + aging + stale;
    if (dated > 0) {
      measuredProjects++;
      if (fresh === 0 && aging === 0) zombieProjects++;
    }

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
    maturityHistogram[maturityScore]++;

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
      stalePct: dated > 0 ? (stale / dated) * 100 : 0,
      maturityScore,
      notebookRecipeRatio: recipes > 0 ? notebooks / recipes : null,
      groups: TREND_GROUPS.map((g) => sumFamilies(p.byFamily, g.families)),
      creators: p.creators,
    });
  }
  projectRows.sort((a, b) => b.objectCount - a.objectCount || a.projectKey.localeCompare(b.projectKey));

  return {
    staleness: {
      freshCount,
      agingCount,
      staleCount,
      unknownCount: Math.max(0, inventory.scanned - freshCount - agingCount - staleCount),
      zombieProjects,
      measuredProjects,
    },
    busFactor,
    maturityHistogram,
    projectRows,
  };
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

function buildDormantCreators(
  inventory: ObjectInventory,
  users: AdoptionBuilderRecency[],
  referenceMs: number,
): InventoryDormantCreator[] {
  const sessionByLogin = new Map(users.map((r) => [r.login, r.lastSessionActivity]));
  // Reference "now" for session recency: the newest session anywhere, falling
  // back to the inventory's own lastEditMs (never the wall clock).
  let sessionRef = 0;
  for (const record of users) {
    if (record.lastSessionActivity != null) sessionRef = Math.max(sessionRef, record.lastSessionActivity);
  }
  if (sessionRef === 0) sessionRef = referenceMs;
  const userLogins = new Set(users.map((u) => u.login));

  const dormant: InventoryDormantCreator[] = [];
  for (const [login, stats] of Object.entries(inventory.creators)) {
    if (stats.created <= 0) continue;
    const lastSessionMs = sessionByLogin.get(login) ?? null;
    const isDormant =
      lastSessionMs === null || sessionRef - lastSessionMs > DORMANT_THRESHOLD_DAYS * DAY_MS;
    if (!isDormant) continue;
    dormant.push({
      login,
      created: stats.created,
      lastEditMs: stats.lastEditMs,
      lastSessionMs,
      inUserSnapshot: userLogins.has(login),
    });
  }
  return dormant.sort((a, b) => b.created - a.created);
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
  const heatPoints: AdoptionMonthPoint[] = trendPoints.map((p) => ({
    month: p.month,
    commits: p.total,
    activeBuilders: p.distinctCreators,
  }));

  const personas: Record<string, InventoryPersonaRow> = {};
  for (const [login, stats] of Object.entries(inventory.creators)) {
    if (stats.created <= 0) continue;
    const classified =
      stats.created >= MIN_PERSONA_OBJECTS ? classifyPersona(stats.byFamily, stats.created) : null;
    personas[login] = {
      login,
      persona: classified?.persona ?? null,
      created: stats.created,
      byFamily: stats.byFamily,
      shareSummary:
        classified?.shareSummary ??
        `${stats.created} object${stats.created === 1 ? '' : 's'} created (persona needs ≥${MIN_PERSONA_OBJECTS})`,
    };
  }

  // Derived analytics — all vs the inventory's own reference "now".
  const referenceMs = inventory.lastEditMs ?? Date.now();
  const { staleness, busFactor, maturityHistogram, projectRows } = collapseProjects(
    inventory,
    monthKeyUTC(referenceMs),
  );

  const editBuckets = { v1: 0, v2to5: 0, v6to20: 0, v21plus: 0 };
  for (const stats of Object.values(inventory.families)) {
    editBuckets.v1 += stats.editBuckets.v1;
    editBuckets.v2to5 += stats.editBuckets.v2to5;
    editBuckets.v6to20 += stats.editBuckets.v6to20;
    editBuckets.v21plus += stats.editBuckets.v21plus;
  }
  const editIntensity: InventoryEditIntensity = {
    editBuckets,
    savedOnce: Object.values(inventory.projects).reduce((sum, p) => sum + p.savedOnce, 0),
    versionedObjects:
      editBuckets.v1 + editBuckets.v2to5 + editBuckets.v6to20 + editBuckets.v21plus,
  };

  return {
    inventory,
    objectsBuilt: inventory.scanned,
    taggedObjects: inventory.taggedObjects,
    allTimeCreators: Object.values(inventory.creators).filter((c) => c.created > 0).length,
    complete: inventory.complete,
    composition,
    trendPoints,
    heatPoints,
    personas,
    ttfb: buildTtfb(inventory, recency),
    staleness,
    busFactor,
    maturityHistogram,
    seatTypes: buildSeatTypes(inventory, recency),
    dormantCreators: buildDormantCreators(inventory, recency, referenceMs),
    dormantThresholdDays: DORMANT_THRESHOLD_DAYS,
    editIntensity,
    projectRows,
  };
}
