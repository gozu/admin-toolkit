import type {
  ConnectionCounts,
  ConnectionHealthResult,
  DirTreeData,
  FilesystemInfo,
  MemoryInfo,
  ParsedData,
  ProcessMetric,
  ProjectFootprintRow,
  SanityCheckMessage,
} from '../../../types';
import { parseNumericValue, parseSizeToGB } from '../../../utils/formatters';
import type { Tone } from './tokens';

// Pure per-tile derive functions. Each takes the narrow ParsedData slices it
// needs so the page can memoize on slice identity, not the whole ParsedData.

// ── Filesystem ────────────────────────────────────────────────────────────
export interface MountVm {
  mount: string;
  usePct: number;
  size: string;
}

export function selectMounts(filesystemInfo: FilesystemInfo[] | undefined): MountVm[] {
  return (filesystemInfo || [])
    .map((f) => ({
      mount: f['Mounted on'] || f.Filesystem,
      usePct: parseInt(f['Use%'], 10) || 0,
      size: f.Size,
      sizeGb: parseSizeToGB(f.Size),
    }))
    .sort((a, b) => b.sizeGb - a.sizeGb)
    .slice(0, 3)
    .map(({ mount, usePct, size }) => ({ mount, usePct, size }));
}

export function selectTreemapItems(
  dirTree: DirTreeData | undefined,
): { name: string; size: number }[] {
  const children = dirTree?.root?.children || [];
  return [...children]
    .filter((c) => c.size > 0)
    .sort((a, b) => b.size - a.size)
    .slice(0, 8)
    .map((c) => ({ name: c.name, size: c.size }));
}

// ── Memory ────────────────────────────────────────────────────────────────
export interface MemoryVm {
  usedMb: number;
  freeMb: number;
  buffMb: number;
  usedLabel: string;
  totalLabel: string;
  usedPct: number;
  swapLabel: string | null;
}

// Mirrors MemoryChart's parse: values are MB unless suffixed with GB.
function parseMemMb(value: string | undefined): number {
  if (!value) return 0;
  const n = parseNumericValue(value);
  return value.includes('GB') ? n * 1024 : n;
}

export function selectMemory(memoryInfo: MemoryInfo | undefined): MemoryVm | null {
  if (!memoryInfo || !memoryInfo.total) return null;
  const usedMb = parseMemMb(memoryInfo.used);
  const freeMb = parseMemMb(memoryInfo.free);
  const buffMb = parseMemMb(memoryInfo['buff/cache']);
  const totalMb = parseMemMb(memoryInfo.total) || usedMb + freeMb + buffMb;
  const hasSwap =
    Boolean(memoryInfo['Swap total']) && memoryInfo['Swap total'] !== 'Not configured';
  return {
    usedMb,
    freeMb,
    buffMb,
    usedLabel: memoryInfo.used || '—',
    totalLabel: memoryInfo.total,
    usedPct: totalMb > 0 ? (usedMb / totalMb) * 100 : 0,
    swapLabel: hasSwap
      ? `${memoryInfo['Swap used'] || '?'} / ${memoryInfo['Swap total']}`
      : null,
  };
}

// ── CPU ───────────────────────────────────────────────────────────────────
export interface CpuRowVm {
  label: string;
  cpu: number;
}

export function selectTopCpu(processes: ProcessMetric[]): CpuRowVm[] {
  return [...processes]
    .sort((a, b) => b.cpuPercent - a.cpuPercent)
    .slice(0, 3)
    .map((p) => {
      const head = p.command.split(/\s+/)[0] || '';
      const base = head.split('/').pop() || head || `pid ${p.pid}`;
      return { label: base, cpu: p.cpuPercent };
    });
}

// ── Connections inventory ─────────────────────────────────────────────────
export interface ConnTypesVm {
  entries: [string, number][];
  otherCount: number;
  total: number;
}

export function selectConnTypes(connections: ConnectionCounts | undefined): ConnTypesVm {
  const entries = Object.entries(connections || {}).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((s, [, c]) => s + c, 0);
  const top = entries.slice(0, 5);
  const shown = top.reduce((s, [, c]) => s + c, 0);
  return { entries: top, otherCount: total - shown, total };
}

// ── Connections health ────────────────────────────────────────────────────
export interface ConnHealthVm {
  ok: number;
  fail: number;
  skipped: number;
  cells: { tone: Tone; title: string }[];
}

export function selectConnHealth(health: ConnectionHealthResult[] | undefined): ConnHealthVm {
  const rows = health || [];
  const toneOf = (s: ConnectionHealthResult['status']): Tone =>
    s === 'ok' ? 'ok' : s === 'fail' ? 'crit' : 'neutral';
  return {
    ok: rows.filter((r) => r.status === 'ok').length,
    fail: rows.filter((r) => r.status === 'fail').length,
    skipped: rows.filter((r) => r.status === 'skipped').length,
    // Failures first so they are never pushed past the strip's display cap.
    cells: [...rows]
      .sort((a, b) => (a.status === 'fail' ? -1 : 0) - (b.status === 'fail' ? -1 : 0))
      .map((r) => ({ tone: toneOf(r.status), title: `${r.name} — ${r.status}` })),
  };
}

// ── Connections usage ─────────────────────────────────────────────────────
export interface ConnUsageVm {
  datasetCount: number;
  llmRecipeCount: number;
  scanned: number | null;
  total: number | null;
  coveragePct: number;
}

export function selectConnUsage(
  d: Pick<
    ParsedData,
    'connectionDatasetUsages' | 'connectionLlmUsages' | 'connectionUsageScanned' | 'connectionUsageTotal'
  >,
): ConnUsageVm {
  const datasetCount = (d.connectionDatasetUsages || []).reduce(
    (s, u) => s + (u.datasetCount ?? u.projects.length),
    0,
  );
  const llmRecipeCount = (d.connectionLlmUsages || []).reduce(
    (s, u) => s + (u.recipeCount ?? u.projects.length),
    0,
  );
  const scanned = d.connectionUsageScanned ?? null;
  const total = d.connectionUsageTotal ?? null;
  return {
    datasetCount,
    llmRecipeCount,
    scanned,
    total,
    coveragePct: total && total > 0 ? ((scanned ?? 0) / total) * 100 : 0,
  };
}

// ── Projects ──────────────────────────────────────────────────────────────
export interface ProjectsVm {
  count: number;
  totalBytes: number;
  avgGb: number;
  top: ProjectFootprintRow[];
  maxBytes: number;
}

export function selectProjects(
  d: Pick<ParsedData, 'projects' | 'projectFootprint' | 'projectFootprintSummary'>,
): ProjectsVm {
  const fp = d.projectFootprint || [];
  const top = [...fp].sort((a, b) => (b.totalBytes || 0) - (a.totalBytes || 0)).slice(0, 5);
  return {
    // Live mode skips the basic projects[] load — fall back to the footprint scan.
    count: d.projects?.length || d.projectFootprintSummary?.projectCount || fp.length,
    totalBytes: fp.reduce((s, r) => s + (r.totalBytes || 0), 0),
    avgGb: d.projectFootprintSummary?.instanceAvgProjectGB ?? 0,
    top,
    maxBytes: top[0]?.totalBytes || 0,
  };
}

export function footprintTone(health: ProjectFootprintRow['projectSizeHealth']): Tone {
  if (health === 'red' || health === 'angry-red') return 'crit';
  if (health === 'orange' || health === 'yellow') return 'warn';
  return 'ok';
}

// ── Users ─────────────────────────────────────────────────────────────────
export interface UsersVm {
  total: number;
  enabled: number;
  profiles: [string, number][];
  topOwners: [string, number][];
}

export function selectUsers(
  d: Pick<ParsedData, 'users' | 'projects' | 'projectFootprint'>,
): UsersVm {
  const users = d.users || [];
  const profileCounts: Record<string, number> = {};
  for (const u of users) {
    const key = u.userProfile || 'unknown';
    profileCounts[key] = (profileCounts[key] || 0) + 1;
  }
  // Live mode skips the basic projects[] load — footprint rows carry the owner.
  const ownerSource = d.projects?.length
    ? d.projects.map((p) => p.owner)
    : (d.projectFootprint || []).map((r) => r.owner);
  const ownerCounts: Record<string, number> = {};
  for (const owner of ownerSource) {
    if (!owner) continue;
    ownerCounts[owner] = (ownerCounts[owner] || 0) + 1;
  }
  return {
    total: users.length,
    enabled: users.filter((u) => u.enabled !== false).length,
    profiles: Object.entries(profileCounts).sort((a, b) => b[1] - a[1]),
    topOwners: Object.entries(ownerCounts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 4),
  };
}

// ── Sanity check ──────────────────────────────────────────────────────────
export interface SanityVm {
  total: number;
  errors: number;
  warnings: number;
  infos: number;
  maxTone: Tone;
  maxLabel: string;
}

export function selectSanity(
  messages: SanityCheckMessage[] | undefined,
  maxSeverity: string | null | undefined,
): SanityVm {
  const rows = messages || [];
  const sev = (maxSeverity || '').toUpperCase();
  const maxTone: Tone =
    sev === 'ERROR' ? 'crit' : sev === 'WARNING' ? 'warn' : sev === 'INFO' ? 'info' : 'ok';
  return {
    total: rows.length,
    errors: rows.filter((m) => m.severity === 'ERROR').length,
    warnings: rows.filter((m) => m.severity === 'WARNING').length,
    infos: rows.filter((m) => m.severity === 'INFO').length,
    maxTone,
    maxLabel: sev || 'OK',
  };
}

// ── Code envs ─────────────────────────────────────────────────────────────
export interface CodeEnvsVm {
  count: number;
  totalSizeBytes: number;
  pyVersions: [string, number][];
}

export function selectCodeEnvs(
  d: Pick<ParsedData, 'codeEnvs' | 'codeEnvSizes' | 'pythonVersionCounts'>,
): CodeEnvsVm {
  return {
    count: d.codeEnvs?.length ?? 0,
    totalSizeBytes: Object.values(d.codeEnvSizes || {}).reduce((s, v) => s + (v || 0), 0),
    pyVersions: Object.entries(d.pythonVersionCounts || {})
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3),
  };
}
