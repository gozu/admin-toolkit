// Users → Churn derivations — pure functions over the /api/users/churn
// payload (per-account lifecycle facts). Everything here is wall-clock-free:
// "now" is always the payload's own generatedAtMs.
//
// Honesty notes baked into the model:
// - DSS stores no disable date. A disabled account's end-of-life is proxied
//   by its last recorded activity (activity → login → creation, in that
//   order of evidence) — the proxy SOURCE rides along on every row.
// - Deleted accounts are invisible: churn here counts disabled-but-kept
//   accounts only, so every churn number is a floor.

import type { ChurnAccount, AdoptionLicensing } from '../types';

const DAY_MS = 86_400_000;

export interface ChurnYearPoint {
  year: number;
  created: number;
  /** Disabled accounts whose end-of-life proxy fell in this year. */
  churned: number;
  net: number;
  /** Running account balance (created − churned) at the end of this year. */
  cumulative: number;
  /** Creations matched to a previously-freed seat of the same profile — the
   * seat-reassignment estimate (running-pool model). */
  reassigned: number;
  /** Creations that needed a brand-new seat. */
  fresh: number;
}

export interface ProfileSeatRow {
  profile: string;
  enabled: number;
  disabled: number;
  /** Licensed seat cap for this profile — null/≤0 means "no limit". */
  licensedLimit: number | null;
  created: number;
  churned: number;
  reassigned: number;
}

export interface DormantAccount {
  account: ChurnAccount;
  /** Latest recorded use (max of session activity / successful login). */
  lastActiveMs: number | null;
  /** Days since last use — since creation for never-used accounts. */
  idleDays: number;
  neverUsed: boolean;
}

export interface ChurnView {
  years: ChurnYearPoint[];
  profiles: ProfileSeatRow[];
  totalAccounts: number;
  enabledCount: number;
  disabledCount: number;
  churnedLast365: number;
  reassignedTotal: number;
  /** Median lifespan (creation → end proxy) of churned accounts, in days. */
  medianTenureDays: number | null;
  /** Enabled accounts idle ≥ the dormancy threshold, oldest first. */
  dormant: DormantAccount[];
  /** Accounts missing a creationDate — excluded from the year buckets. */
  undatedCount: number;
}

const UNKNOWN_PROFILE = 'UNKNOWN';

function utcYear(ms: number): number {
  return new Date(ms).getUTCFullYear();
}

/** Latest recorded use of an account (session activity ≥ login). */
export function lastActiveMs(a: ChurnAccount): number | null {
  const act = a.lastSessionActivityMs ?? 0;
  const log = a.lastSuccessfulLoginMs ?? 0;
  const best = Math.max(act, log);
  return best > 0 ? best : null;
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/** 'FULL_DESIGNER' → 'Full Designer' (profile ids are SCREAMING_SNAKE). */
export function profileLabel(profile: string | null | undefined): string {
  if (!profile) return 'Unknown';
  return profile
    .toLowerCase()
    .split('_')
    .map((w) => (w === 'ai' ? 'AI' : w.charAt(0).toUpperCase() + w.slice(1)))
    .join(' ');
}

export function buildChurnView(
  accounts: readonly ChurnAccount[],
  nowMs: number,
  dormantDays: number,
  licensing?: AdoptionLicensing | null,
): ChurnView {
  const currentYear = nowMs > 0 ? utcYear(nowMs) : new Date(0).getUTCFullYear();

  // ── Per-(year, profile) creation/churn buckets ────────────────────────────
  const createdBy = new Map<number, Map<string, number>>();
  const churnedBy = new Map<number, Map<string, number>>();
  const bump = (map: Map<number, Map<string, number>>, year: number, profile: string) => {
    const inner = map.get(year) ?? new Map<string, number>();
    inner.set(profile, (inner.get(profile) ?? 0) + 1);
    map.set(year, inner);
  };

  let undatedCount = 0;
  let enabledCount = 0;
  let disabledCount = 0;
  let churnedLast365 = 0;
  const tenures: number[] = [];
  let minYear = currentYear;

  // Chronological seat events for the reassignment match (exact-ms ordering —
  // a creation can only take over a seat freed BEFORE it).
  type SeatEvent = { ms: number; kind: 'freed' | 'created'; profile: string; year: number };
  const seatEvents: SeatEvent[] = [];

  const profileRows = new Map<string, ProfileSeatRow>();
  const profileRow = (profile: string): ProfileSeatRow => {
    let row = profileRows.get(profile);
    if (!row) {
      row = {
        profile,
        enabled: 0,
        disabled: 0,
        licensedLimit: null,
        created: 0,
        churned: 0,
        reassigned: 0,
      };
      profileRows.set(profile, row);
    }
    return row;
  };

  for (const a of accounts) {
    const profile = a.userProfile || UNKNOWN_PROFILE;
    const row = profileRow(profile);
    if (a.enabled) {
      enabledCount++;
      row.enabled++;
    } else {
      disabledCount++;
      row.disabled++;
    }

    const created = a.creationDateMs;
    if (created == null) {
      undatedCount++;
    } else {
      const y = Math.min(utcYear(created), currentYear);
      minYear = Math.min(minYear, y);
      bump(createdBy, y, profile);
      row.created++;
      seatEvents.push({ ms: created, kind: 'created', profile, year: y });
    }

    if (!a.enabled && a.effectiveEndMs != null) {
      // An activity snapshot can never predate the account, but clamp anyway
      // so a malformed pair can't bucket churn before its own creation.
      const endMs = created != null ? Math.max(a.effectiveEndMs, created) : a.effectiveEndMs;
      const y = Math.min(utcYear(endMs), currentYear);
      minYear = Math.min(minYear, y);
      bump(churnedBy, y, profile);
      row.churned++;
      if (nowMs - endMs <= 365 * DAY_MS) churnedLast365++;
      if (created != null) tenures.push((endMs - created) / DAY_MS);
      seatEvents.push({ ms: endMs, kind: 'freed', profile, year: y });
    }
  }

  // ── Seat-reassignment match: walk the seat events in exact-ms order. A
  // disable adds a freed seat to its profile's pool; a creation drains the
  // pool — so a creation can only take over a seat freed BEFORE it. On an
  // exact-ms tie the creation sorts first: a never-used account (end proxy ==
  // creation date) emits both events at one instant and must not be counted
  // as taking over its own seat. Deleted accounts never free a visible seat.
  seatEvents.sort(
    (a, b) => a.ms - b.ms || (a.kind === b.kind ? 0 : a.kind === 'created' ? -1 : 1),
  );
  const pool = new Map<string, number>();
  const reassignedByYear = new Map<number, number>();
  let reassignedTotal = 0;
  for (const ev of seatEvents) {
    const avail = pool.get(ev.profile) ?? 0;
    if (ev.kind === 'freed') {
      pool.set(ev.profile, avail + 1);
    } else if (avail > 0) {
      pool.set(ev.profile, avail - 1);
      reassignedByYear.set(ev.year, (reassignedByYear.get(ev.year) ?? 0) + 1);
      profileRow(ev.profile).reassigned++;
      reassignedTotal++;
    }
  }

  // ── Yearly flow roll-up ───────────────────────────────────────────────────
  const years: ChurnYearPoint[] = [];
  let cumulative = 0;
  for (let year = minYear; year <= currentYear; year++) {
    let created = 0;
    let churned = 0;
    for (const n of (createdBy.get(year) ?? new Map()).values()) created += n;
    for (const n of (churnedBy.get(year) ?? new Map()).values()) churned += n;
    const reassigned = reassignedByYear.get(year) ?? 0;
    cumulative += created - churned;
    years.push({
      year,
      created,
      churned,
      net: created - churned,
      cumulative,
      reassigned,
      fresh: created - reassigned,
    });
  }

  // ── Licensed seat caps joined onto the per-profile rows ──────────────────
  for (const p of licensing?.profiles ?? []) {
    const limit = p.licensedLimit;
    profileRow(p.profile).licensedLimit = limit != null && limit > 0 ? limit : null;
  }
  const profiles = [...profileRows.values()]
    .filter((r) => r.enabled + r.disabled + r.created > 0 || r.licensedLimit != null)
    .sort((a, b) => b.enabled - a.enabled || a.profile.localeCompare(b.profile));

  // ── Dormant enabled accounts (reclaimable seats) ──────────────────────────
  const dormant: DormantAccount[] = [];
  for (const a of accounts) {
    if (!a.enabled) continue;
    const last = lastActiveMs(a);
    const idleSince = last ?? a.creationDateMs;
    if (idleSince == null) continue; // no evidence either way
    const idleDays = Math.floor((nowMs - idleSince) / DAY_MS);
    if (idleDays >= dormantDays) {
      dormant.push({ account: a, lastActiveMs: last, idleDays, neverUsed: last == null });
    }
  }
  dormant.sort((a, b) => b.idleDays - a.idleDays);

  return {
    years,
    profiles,
    totalAccounts: accounts.length,
    enabledCount,
    disabledCount,
    churnedLast365,
    reassignedTotal,
    medianTenureDays: median(tenures),
    dormant,
    undatedCount,
  };
}
